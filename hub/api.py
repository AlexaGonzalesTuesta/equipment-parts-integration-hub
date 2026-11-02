"""REST API for the integration hub.

Run:   uvicorn hub.api:create_app --factory --reload
Docs:  http://127.0.0.1:8000/docs  (interactive OpenAPI page generated from this file)
"""
from typing import Any, Dict

from fastapi import Body, FastAPI, HTTPException, Response

from . import config as C
from .db import connect
from .metrics import summary
from .pipeline import run
from .recommender import train_from_db
from .service import IntegrationHub

STATUS_CODES = {"ORDER_CREATED": 201, "DUPLICATE": 200, "NO_ACTION": 202, "MANUAL_REVIEW": 202,
                "DEAD_LETTER": 202, "REJECTED": 422}


def create_app(db_path=C.DB_PATH) -> FastAPI:
    if not db_path.exists():
        run(db_path, quiet=True)
    conn = connect(db_path)
    hub = IntegrationHub(conn, train_from_db(conn))

    app = FastAPI(title="Connected Equipment Parts Integration Hub", version="1.0.0",
                  description="Turns machine fault events and storefront orders into ERP sales orders, "
                              "with dealer inventory sourcing, AI diagnosis, retries and a dead letter queue.")

    @app.get("/health", tags=["Operations"])
    def health():
        conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "ai_model": "tfidf-logreg", "confidence_threshold": C.AI_CONFIDENCE_THRESHOLD}

    @app.post("/v1/telematics/events", tags=["Inbound"])
    def telematics_event(response: Response, payload: Dict[str, Any] = Body(...)):
        result = hub.process_telematics(payload)
        response.status_code = STATUS_CODES[result.outcome]
        return result

    @app.post("/v1/storefront/orders", tags=["Inbound"])
    def storefront_order(response: Response, payload: Dict[str, Any] = Body(...)):
        result = hub.process_web_order(payload)
        response.status_code = STATUS_CODES[result.outcome]
        return result

    @app.get("/v1/orders/{order_id}", tags=["Orders"])
    def get_order(order_id: str):
        header = conn.execute("SELECT * FROM erp_orders WHERE order_id = ?", (order_id,)).fetchone()
        if header is None:
            raise HTTPException(404, f"Order {order_id} not found")
        lines = conn.execute("SELECT * FROM erp_order_lines WHERE order_id = ? ORDER BY line_no",
                             (order_id,)).fetchall()
        return {**dict(header), "lines": [dict(r) for r in lines]}

    @app.get("/v1/messages/{message_id}", tags=["Troubleshooting"])
    def trace_message(message_id: str):
        """Full audit trail for one message: the first place to look when a customer asks 'where is my order?'"""
        inbound = conn.execute("SELECT message_id, channel, received_at, status FROM hub_inbound "
                               "WHERE message_id = ?", (message_id,)).fetchone()
        if inbound is None:
            raise HTTPException(404, f"Message {message_id} was never received")
        steps = conn.execute("SELECT step, status, detail, logged_at FROM hub_audit WHERE message_id = ? "
                             "ORDER BY id", (message_id,)).fetchall()
        return {**dict(inbound), "audit_trail": [dict(s) for s in steps]}

    @app.get("/v1/dead-letters", tags=["Troubleshooting"])
    def dead_letters(open_only: bool = True, limit: int = 100):
        sql = "SELECT id, message_id, channel, reason_code, detail, attempts, replayable, created_at, resolved_at " \
              "FROM hub_dead_letter" + (" WHERE resolved_at IS NULL" if open_only else "") + " ORDER BY id DESC LIMIT ?"
        return [dict(r) for r in conn.execute(sql, (limit,)).fetchall()]

    @app.post("/v1/dead-letters/replay", tags=["Troubleshooting"])
    def replay():
        results = hub.replay_dead_letters()
        return {"replayed": len(results), "recovered": sum(r.outcome != "DEAD_LETTER" for r in results)}

    @app.get("/v1/metrics", tags=["Operations"])
    def metrics():
        return summary(conn)

    return app
