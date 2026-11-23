"""One support turn: authenticated customer id plus a message."""

from __future__ import annotations

from typing import Any

from commerce_mock.store import CommerceStore
from support.graph import build_graph
from support.ledger import CASES, Ledger, REVIEWS
from support.model import MockModel, ModelClient


class SupportService:
    def __init__(self, store: CommerceStore, model: ModelClient | None = None) -> None:
        self.store = store
        self.ledger = Ledger(store)
        self.model = model or MockModel()
        self.graph = build_graph(store, self.ledger, self.model)

    def clear(self) -> None:
        self.store.clear_runtime()
        self.ledger.reset()

    def handle(self, customer_id: str, message: str) -> dict[str, Any]:
        text = message.strip()
        if not text:
            return self._finish(
                customer_id,
                message,
                reply="Send the order question you want help with.",
                action="clarify",
                explanation_code="empty_message",
                grounding="template",
                decision={},
                observations=[],
                review_reason=None,
            )
        known = {row["customer_id"] for row in self.store.list_customers()}
        if customer_id not in known:
            return self._finish(
                customer_id,
                message,
                reply="I can't find that customer account.",
                action="clarify",
                explanation_code="unknown_customer",
                grounding="template",
                decision={},
                observations=[],
                review_reason=None,
            )

        state = self.graph.invoke(
            {
                "customer_id": customer_id,
                "message": text,
                "denylist": _other_customer_markers(self.store, customer_id),
                "explanation_hint": "none",
                "tool_calls": [],
                "observations": [],
                "decision": {},
                "reply": "",
                "grounding": "",
                "review_reason": None,
                "rejected_draft": None,
            }
        )
        decision = state.get("decision") or {}
        if state.get("review_reason"):
            self.ledger.enqueue_review(
                {
                    "customer_id": customer_id,
                    "reason": state["review_reason"],
                    "rejected_draft": state.get("rejected_draft"),
                }
            )
        return self._finish(
            customer_id,
            text,
            reply=state.get("reply") or "",
            action=decision.get("action") or "clarify",
            explanation_code=decision.get("explanation_code") or "invalid_decision",
            grounding=state.get("grounding") or "",
            decision=decision,
            observations=list(state.get("observations") or []),
            review_reason=state.get("review_reason"),
        )

    def _finish(
        self,
        customer_id: str,
        message: str,
        *,
        reply: str,
        action: str,
        explanation_code: str,
        grounding: str,
        decision: dict[str, Any],
        observations: list[dict[str, Any]],
        review_reason: str | None,
    ) -> dict[str, Any]:
        case = self.ledger.record_case(
            {
                "customer_id": customer_id,
                "message": message,
                "action": action,
                "explanation_code": explanation_code,
                "reply": reply,
                "grounding": grounding,
            }
        )
        return {
            "case_id": case["case_id"],
            "reply": reply,
            "action": action,
            "explanation_code": explanation_code,
            "grounding": grounding,
            "amount": decision.get("amount"),
            "proposal_id": decision.get("proposal_id"),
            "refund_id": decision.get("refund_id"),
            "debug": {
                "tool_trace": observations,
                "review_reason": review_reason,
            },
        }


def _other_customer_markers(store: CommerceStore, customer_id: str) -> list[str]:
    markers: list[str] = []
    for row in store.list_customers():
        if row["customer_id"] == customer_id:
            continue
        markers.append(row["name"])
        markers.append(row["email"])
        markers.extend(part for part in str(row["name"]).split() if len(part) > 3)
    return markers


def runtime_snapshot(store: CommerceStore) -> dict[str, list]:
    return {
        "refunds": store.runtime_records("refunds"),
        "proposals": store.runtime_records("proposals"),
        "cases": store.runtime_records(CASES),
        "reviews": store.runtime_records(REVIEWS),
    }
