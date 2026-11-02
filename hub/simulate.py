"""Generates a synthetic but realistic enterprise landscape:
master data (dealers, customers, machines, parts, fault catalog), dealer inventory,
historical service notes for AI training, and two inbound message feeds
(telematics fault events and e-commerce orders) that include the messy cases
real integrations see: duplicates, malformed payloads, unknown machines,
missing fault codes, wrong prices and unknown part numbers."""
import json
import random
from datetime import datetime, timedelta

from . import config as C
from .catalog import DEALERS, FAULTS, FILLER, GENERIC_SYMPTOMS, INDUSTRIES, MACHINE_MODELS, STOREFRONT_EXTRAS
from .db import connect, create_schema

COMPANY_WORDS = ["Summit", "Ridge", "Iron", "Granite", "River", "Prairie", "Eagle", "Canyon", "Delta",
                 "Pioneer", "Northstar", "Bedrock", "Frontier", "Keystone", "Redline", "Timber"]
COMPANY_SUFFIX = ["Construction", "Mining", "Aggregates", "Earthworks", "Contractors", "Resources",
                  "Excavating", "Paving", "Site Services", "Land Co."]


def build_parts():
    """Return {description: (part_number, category, price)} with stable part numbers."""
    parts, n = {}, 10001
    for _, _, _, kit, _ in FAULTS:
        for desc, cat, price, _ in kit:
            if desc not in parts:
                parts[desc] = (f"PN-{n}", cat, price)
                n += 1
    for desc, cat, price in STOREFRONT_EXTRAS:
        if desc not in parts:
            parts[desc] = (f"PN-{n}", cat, price)
            n += 1
    return parts


def make_note(rng, fault_idx):
    """Compose a free-text symptom note the way an operator might type it."""
    _, system, _, _, phrases = FAULTS[fault_idx]
    chosen = []
    if rng.random() < 0.85:
        chosen = rng.sample(phrases, k=rng.choice([1, 1, 2]))
    # Real notes are ambiguous: they mention generic or neighboring symptoms too
    siblings = [i for i, f in enumerate(FAULTS) if f[1] == system and i != fault_idx]
    if siblings and rng.random() < 0.35:
        chosen.append(rng.choice(FAULTS[rng.choice(siblings)][4]))
    if not chosen or rng.random() < 0.5:
        chosen.append(rng.choice(GENERIC_SYMPTOMS.get(system, ["machine not working right"])))
    if rng.random() < 0.6:
        chosen.insert(0, rng.choice(FILLER))
    rng.shuffle(chosen)
    words = ", ".join(chosen).split()
    words = [w for w in words if rng.random() > 0.08]                  # operators abbreviate and skip words
    words = [typo(rng, w) if rng.random() < 0.06 else w for w in words]
    text = " ".join(words) or "machine down"
    return text.upper() if rng.random() < 0.1 else text


def typo(rng, w):
    if len(w) < 4:
        return w
    i = rng.randrange(1, len(w) - 1)
    return w[:i] + w[i + 1:]  # dropped letter


def generate(db_path=C.DB_PATH, inbound_dir=None, seed=C.SEED):
    rng = random.Random(seed)
    inbound_dir = inbound_dir or (C.DATA_DIR / "inbound")
    inbound_dir.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = connect(db_path)
    create_schema(conn)

    # ---------- master data (ERP) ----------
    conn.executemany("INSERT INTO erp_dealers VALUES (?,?,?,?)", DEALERS)
    region_dealers = {}
    for d in DEALERS:
        region_dealers.setdefault(d[2], []).append(d[0])

    customers = []
    for i in range(1, C.N_CUSTOMERS + 1):
        region = rng.choice(list(region_dealers))
        name = f"{rng.choice(COMPANY_WORDS)} {rng.choice(COMPANY_SUFFIX)}"
        customers.append((f"C{i:04d}", name, rng.choice(INDUSTRIES), region))
    conn.executemany("INSERT INTO erp_customers VALUES (?,?,?,?)", customers)
    cust_dealer = {c[0]: rng.choice(region_dealers[c[3]]) for c in customers}

    parts = build_parts()
    conn.executemany("INSERT INTO erp_parts VALUES (?,?,?,?)",
                     [(pn, desc, cat, price) for desc, (pn, cat, price) in parts.items()])

    for code, system, desc, kit, _ in FAULTS:
        conn.execute("INSERT INTO erp_fault_catalog VALUES (?,?,?)", (code, system, desc))
        for pdesc, _, _, qty in kit:
            conn.execute("INSERT INTO erp_fault_parts VALUES (?,?,?)", (code, parts[pdesc][0], qty))

    machines = []
    for i in range(1, C.N_MACHINES + 1):
        cust = rng.choice(customers)[0]
        model, mtype = rng.choice(MACHINE_MODELS)
        machines.append((f"M{i:05d}", model, mtype, cust, cust_dealer[cust], rng.randint(2012, 2026)))
    conn.executemany("INSERT INTO erp_machines VALUES (?,?,?,?,?,?)", machines)

    # ---------- dealer inventory (DMS) ----------
    inv = []
    for d in DEALERS:
        for desc, (pn, cat, price) in parts.items():
            if price > 2000:
                on_hand = rng.choice([0, 0, 1, 1, 2, 3])          # big-ticket items: thin stock
            elif price > 500:
                on_hand = rng.choice([0, 1, 2, 3, 4, 6])
            else:
                on_hand = rng.randint(0, 40) if rng.random() > 0.08 else 0
            target = max(on_hand, 1 if price > 2000 else 3 if price > 500 else 25)
            if rng.random() < 0.05:
                target = 0                                        # dealer does not stock this part
            inv.append((d[0], pn, min(on_hand, target) if target else 0, 0, max(target // 4, 0), target))
    conn.executemany("INSERT INTO dms_inventory VALUES (?,?,?,?,?,?)", inv)

    # ---------- labeled history for the AI model ----------
    notes = []
    for i in range(C.N_HISTORY_NOTES):
        idx = rng.randrange(len(FAULTS))
        notes.append((f"N{i:05d}", FAULTS[idx][0], make_note(rng, idx)))
    conn.executemany("INSERT INTO ml_history_notes VALUES (?,?,?)", notes)
    conn.commit()

    # ---------- inbound feed 1: telematics events ----------
    start = datetime.fromisoformat(C.SIM_START)
    events, labels = [], []
    times = sorted(start + timedelta(seconds=rng.randint(0, C.SIM_DAYS * 86400))
                   for _ in range(C.N_TELEMATICS_EVENTS))
    for i, ts in enumerate(times):
        idx = rng.randrange(len(FAULTS))
        machine = rng.choice(machines)[0]
        ev = {
            "event_id": f"TE-{i + 1:06d}",
            "machine_id": machine,
            "occurred_at": ts.isoformat(),
            "fault_code": FAULTS[idx][0],
            "severity": rng.choices([1, 2, 3], weights=[20, 55, 25])[0],
            "symptom_note": make_note(rng, idx),
            "engine_hours": round(rng.uniform(150, 18000), 1),
        }
        r = rng.random()
        if r < 0.15:
            ev["fault_code"] = None                         # older machine, note only
        elif r < 0.16:
            ev["fault_code"] = FAULTS[idx][0][:4] + "999"   # code not in catalog yet
        elif r < 0.175:
            ev["machine_id"] = f"M9{rng.randint(1000, 9999)}"  # machine missing from ERP master data
        elif r < 0.19:
            if rng.random() < 0.5:
                ev["severity"] = 7                          # malformed: out of range
            else:
                ev["machine_id"] = "UNKNOWN"                # malformed: bad identifier
        events.append(ev)
        labels.append({"event_id": ev["event_id"], "true_fault": FAULTS[idx][0]})
        if rng.random() < 0.02:
            events.append(dict(ev))                         # duplicate resend from the platform

    with open(inbound_dir / "telematics_events.jsonl", "w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    with open(inbound_dir / "telematics_labels.jsonl", "w") as f:
        for lb in labels:
            f.write(json.dumps(lb) + "\n")

    # ---------- inbound feed 2: e-commerce storefront orders ----------
    part_list = list(parts.items())
    web = []
    wtimes = sorted(start + timedelta(seconds=rng.randint(0, C.SIM_DAYS * 86400)) for _ in range(C.N_WEB_ORDERS))
    for i, ts in enumerate(wtimes):
        cust = rng.choice(customers)[0]
        lines = []
        for desc, (pn, cat, price) in rng.sample(part_list, k=rng.randint(1, 4)):
            lines.append({"part_number": pn, "quantity": rng.randint(1, 6), "unit_price": price})
        order = {"web_order_id": f"WO-{i + 1:06d}", "customer_id": cust, "dealer_id": cust_dealer[cust],
                 "submitted_at": ts.isoformat(), "lines": lines}
        r = rng.random()
        if r < 0.03:
            order["lines"][0]["unit_price"] = round(order["lines"][0]["unit_price"] * 0.8, 2)  # stale cached price
        elif r < 0.05:
            order["lines"][0]["part_number"] = "PN-99999"   # discontinued part still in a saved cart
        elif r < 0.06:
            order["lines"][0]["quantity"] = 0               # malformed
        web.append(order)
        if rng.random() < 0.015:
            web.append(json.loads(json.dumps(order)))       # customer double-clicked submit

    with open(inbound_dir / "web_orders.jsonl", "w") as f:
        for o in web:
            f.write(json.dumps(o) + "\n")

    conn.close()
    return {"telematics_events": len(events), "web_orders": len(web), "parts": len(parts),
            "machines": len(machines), "dealers": len(DEALERS)}


if __name__ == "__main__":
    print(generate())
