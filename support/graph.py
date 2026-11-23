"""LangGraph workflow: plan, tools, policy, draft, then a grounding check."""

from __future__ import annotations

import json
from typing import TypedDict

from pydantic import ValidationError
from langgraph.graph import END, START, StateGraph

from commerce_mock.store import CommerceStore
from support.grounding import check_reply
from support.ledger import Ledger
from support.model import ModelClient
from support.policy import assemble_decision
from support.prompts import DRAFT_SYSTEM, PLAN_SYSTEM
from support.reply import render_template
from support.schemas import Decision, DraftOutput, ModelRequest, PlanOutput
from support.tools import dispatch

PROMPT_FIELDS = (
    "action",
    "explanation_code",
    "order_id",
    "order_status",
    "carrier",
    "tracking_number",
    "item_name",
    "items_summary",
    "quantity",
    "amount",
    "claimed_amount",
    "currency",
    "proposal_id",
    "refund_id",
    "reason",
)

_SAFE_REPLY = "I couldn't safely answer from the order record. A specialist will review the case."


class SupportState(TypedDict, total=False):
    customer_id: str
    message: str
    denylist: list[str]
    explanation_hint: str
    tool_calls: list[dict]
    observations: list[dict]
    decision: dict
    reply: str
    grounding: str
    review_reason: str | None
    rejected_draft: str | None


def build_graph(store: CommerceStore, ledger: Ledger, model: ModelClient):
    def interpret(state: SupportState) -> dict:
        payload = {
            "message": state["message"],
            "open_proposals": ledger.prompt_view(state["customer_id"]),
        }
        response = model.complete(
            ModelRequest(
                stage="plan",
                messages=[
                    {"role": "system", "content": PLAN_SYSTEM},
                    {"role": "user", "content": json.dumps(payload)},
                ],
            )
        )
        try:
            plan = PlanOutput.model_validate(response.content or {})
            calls = [call.model_dump() for call in response.tool_calls]
        except ValidationError:
            return {"tool_calls": [], "explanation_hint": "missing_order_reference"}
        return {"tool_calls": calls, "explanation_hint": plan.explanation_hint}

    def route(state: SupportState) -> str:
        if state.get("tool_calls"):
            return "tools"
        return "policy"

    def run_tools(state: SupportState) -> dict:
        observations = [
            dispatch(store, ledger, state["customer_id"], state["message"], call)
            for call in state.get("tool_calls") or []
        ]
        return {"observations": observations}

    def apply_policy(state: SupportState) -> dict:
        raw = assemble_decision(
            store,
            state["customer_id"],
            state.get("explanation_hint") or "none",
            state.get("observations") or [],
        )
        try:
            decision = Decision.model_validate(raw).model_dump()
        except ValidationError:
            decision = Decision(action="clarify", explanation_code="invalid_decision").model_dump()
        return {"decision": decision}

    def draft(state: SupportState) -> dict:
        decision = state.get("decision") or {}
        denylist = list(state.get("denylist") or [])
        visible = {
            key: decision[key]
            for key in PROMPT_FIELDS
            if decision.get(key) not in (None, False, "")
        }
        try:
            response = model.complete(
                ModelRequest(
                    stage="draft",
                    messages=[
                        {"role": "system", "content": DRAFT_SYSTEM},
                        {"role": "user", "content": json.dumps(visible)},
                    ],
                )
            )
            reply = DraftOutput.model_validate(response.content or {}).reply
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            return _fallback(decision, denylist, f"invalid draft: {exc}", None)

        issues = check_reply(reply, decision, denylist)
        if issues:
            return _fallback(decision, denylist, "; ".join(issues), reply)
        return {
            "reply": reply,
            "grounding": "model",
            "review_reason": None,
            "rejected_draft": None,
        }

    graph = StateGraph(SupportState)
    graph.add_node("interpret", interpret)
    graph.add_node("tools", run_tools)
    graph.add_node("policy", apply_policy)
    graph.add_node("draft", draft)
    graph.add_edge(START, "interpret")
    graph.add_conditional_edges("interpret", route, {"tools": "tools", "policy": "policy"})
    graph.add_edge("tools", "policy")
    graph.add_edge("policy", "draft")
    graph.add_edge("draft", END)
    return graph.compile()


def _fallback(decision: dict, denylist: list[str], reason: str, rejected: str | None) -> dict:
    reply = render_template(decision)
    if check_reply(reply, decision, denylist):
        reply = _SAFE_REPLY
    return {
        "reply": reply,
        "grounding": "template",
        "review_reason": reason,
        "rejected_draft": rejected,
    }
