"""Local HTTP entry. The customer channel is the reply; debug is for review."""

from __future__ import annotations

from fastapi import FastAPI

from commerce_mock import default_seed_dir
from commerce_mock.store import CommerceStore
from support.schemas import IncomingMessage
from support.service import SupportService, runtime_snapshot


def create_app(store: CommerceStore | None = None) -> FastAPI:
    commerce = store or CommerceStore(default_seed_dir())
    service = SupportService(commerce)
    app = FastAPI(title="Commerce support assistant", version="0.1.0")
    app.state.service = service

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/support/messages")
    def post_message(body: IncomingMessage) -> dict:
        return service.handle(body.authenticated_customer_id, body.message)

    @app.post("/runtime/clear")
    def clear_runtime() -> dict[str, bool]:
        service.clear()
        return {"cleared": True}

    @app.get("/runtime")
    def runtime() -> dict:
        return runtime_snapshot(service.store)

    return app


app = create_app()
