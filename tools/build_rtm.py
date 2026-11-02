"""Build the Requirements Traceability Matrix (RTM).

Joins docs/requirements_catalog.csv (what stakeholders asked for) with
reports/test_results.json (what pytest verified) and writes:
  docs/requirements_traceability.csv
  docs/05_requirements_traceability.md
Exit code 1 if any Must-have requirement has no passing test.

Run after pytest:  python tools/build_rtm.py
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
catalog = list(csv.DictReader(open(ROOT / "docs" / "requirements_catalog.csv")))
results = json.loads((ROOT / "reports" / "test_results.json").read_text())

rows, gaps = [], []
for req in catalog:
    tests = [(nid, t) for nid, t in results["tests"].items() if req["req_id"] in t["requirements"]]
    passed = sum(t["outcome"] == "passed" for _, t in tests)
    failed = sum(t["outcome"] == "failed" for _, t in tests)
    levels = sorted({t["level"] for _, t in tests})
    names = sorted({nid.split("::")[1].split("[")[0] for nid, _ in tests})
    status = "VERIFIED" if passed and not failed else "FAILING" if failed else "NOT COVERED"
    if status != "VERIFIED" and req["priority"] == "Must":
        gaps.append(req["req_id"])
    rows.append({"req_id": req["req_id"], "type": req["type"], "priority": req["priority"],
                 "requirement": req["requirement"], "test_levels": ", ".join(levels),
                 "tests": "; ".join(names), "tests_passed": passed, "tests_failed": failed, "status": status})

with open(ROOT / "docs" / "requirements_traceability.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)

md = ["# 05 Requirements Traceability Matrix", "",
      f"Generated automatically by `tools/build_rtm.py` from the test run at {results['run_at']}.", "",
      f"**{sum(r['status'] == 'VERIFIED' for r in rows)} of {len(rows)} requirements verified** by passing tests.",
      "", "| ID | Priority | Requirement | Test levels | Tests | Passed | Status |", "|---|---|---|---|---|---|---|"]
for r in rows:
    md.append(f"| {r['req_id']} | {r['priority']} | {r['requirement']} | {r['test_levels']} | "
              f"{r['tests'].replace('; ', '<br>')} | {r['tests_passed']} | {r['status']} |")
(ROOT / "docs" / "05_requirements_traceability.md").write_text("\n".join(md) + "\n")

print(f"RTM written: {len(rows)} requirements, {len(gaps)} Must-have gaps {gaps or ''}")
sys.exit(1 if gaps else 0)
