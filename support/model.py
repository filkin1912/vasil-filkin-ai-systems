"""Model boundary.

MockModel stands in for a tool-calling chat model. It sees the same messages
a live model would see, and it returns tool calls or a JSON reply. It does
not read the order store. OpenAICompatibleClient shows the payload a real
provider would get; this exercise does not call one.
"""

from __future__ import annotations

import json
from typing import Protocol

from support.prompts import DRAFT_SYSTEM, PLAN_SYSTEM
from support.schemas import ModelRequest, ModelResponse, ToolCall
from support.slots import (
    choose_proposal,
    extract_amount,
    extract_item_query,
    extract_order_ids,
    extract_quantity,
    format_money,
    is_affirmation,
    message_has_refund_intent,
    message_requests_execution,
)
from support.tools import OPENAI_TOOLS

DRAFT_SCHEMA = {
    "name": "support_reply",
    "schema": {
        "type": "object",
        "properties": {"reply": {"type": "string"}},
        "required": ["reply"],
        "additionalProperties": False,
    },
}


class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...


class MockModel:
    def complete(self, request: ModelRequest) -> ModelResponse:
        payload = json.loads(request.messages[-1]["content"])
        if request.stage == "plan":
            return self._plan(str(payload["message"]), list(payload.get("open_proposals") or []))
        return ModelResponse(content={"reply": verbalize(payload)})

    def _plan(self, message: str, proposals: list[dict]) -> ModelResponse:
        if is_affirmation(message):
            chosen, code = choose_proposal(message, proposals)
            if chosen is not None and code in {"ok", "mismatch"}:
                return ModelResponse(
                    tool_calls=[
                        ToolCall(
                            name="confirm_proposal",
                            arguments={
                                "proposal_id": chosen["proposal_id"],
                                "confirmed_amount": extract_amount(message),
                            },
                        )
                    ],
                    content={"explanation_hint": "none"},
                )
            hint = {
                "ambiguous_proposal": "ambiguous_proposal",
                "nothing_to_confirm": "nothing_to_confirm",
                "no_open_match": "nothing_to_confirm",
                "mismatch": "confirm_amount_mismatch",
            }.get(code, "nothing_to_confirm")
            return ModelResponse(tool_calls=[], content={"explanation_hint": hint})

        order_ids = extract_order_ids(message)
        if len(order_ids) > 1:
            return ModelResponse(tool_calls=[], content={"explanation_hint": "multiple_orders"})
        if len(order_ids) == 1:
            calls = [ToolCall(name="get_order", arguments={"order_id": order_ids[0]})]
            if message_has_refund_intent(message):
                calls.append(
                    ToolCall(
                        name="quote_refund",
                        arguments={
                            "order_id": order_ids[0],
                            "item_query": extract_item_query(message) or "",
                            "quantity": extract_quantity(message),
                            "claimed_amount": extract_amount(message),
                            "execute": message_requests_execution(message),
                        },
                    )
                )
            return ModelResponse(tool_calls=calls, content={"explanation_hint": "none"})
        return ModelResponse(tool_calls=[], content={"explanation_hint": "missing_order_reference"})


class OpenAICompatibleClient:
    """Same complete() contract as MockModel. Live calls are intentionally disabled."""

    def __init__(self, *, base_url: str, model_name: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name

    def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        raise RuntimeError(
            "Live model calls are disabled for this exercise. "
            "Construct the payload with payload() and keep MockModel as the runtime."
        )

    def payload(self, request: ModelRequest) -> dict:
        body: dict = {
            "model": self.model_name,
            "messages": request.messages,
            "temperature": 0,
        }
        if request.stage == "plan":
            body["tools"] = OPENAI_TOOLS
            body["tool_choice"] = "auto"
        else:
            body["response_format"] = {"type": "json_schema", "json_schema": DRAFT_SCHEMA}
        return body

    @staticmethod
    def system_prompt(stage: str) -> str:
        return PLAN_SYSTEM if stage == "plan" else DRAFT_SYSTEM


def verbalize(decision: dict) -> str:
    action = decision.get("action")
    code = decision.get("explanation_code")
    order_id = decision.get("order_id")
    item = decision.get("item_name")
    amount = _money(decision.get("amount"), decision.get("currency"))
    claimed = _money(decision.get("claimed_amount"), decision.get("currency"))

    if action == "order_status" and order_id and decision.get("order_status"):
        status = str(decision["order_status"]).replace("_", " ")
        parts = [f"Order {order_id} is {status}."]
        if decision.get("tracking_number"):
            parts.append(f"Carrier {decision.get('carrier')}, tracking {decision['tracking_number']}.")
        if decision.get("items_summary"):
            parts.append(f"Items: {decision['items_summary']}.")
        return " ".join(parts)

    if action == "not_found" and order_id:
        return f"I can't find order {order_id} on your account."

    if action == "refund_proposed" and order_id and item and amount and decision.get("quantity"):
        lead = ""
        if decision.get("claimed_amount") and decision.get("claimed_amount") != decision.get("amount"):
            lead = f"You mentioned {claimed}, which does not match this order. "
        received = "The return is already received. " if decision.get("reason") == "return_received" else ""
        return (
            f"{lead}{received}I can refund {amount} for {decision['quantity']} × {item} "
            f"on order {order_id}. I have not submitted that refund. Reply yes, confirm {amount}."
        )

    if action == "refund_executed" and order_id and item and amount and decision.get("refund_id"):
        return (
            f"I have submitted a refund of {amount} for {decision['quantity']} × {item} "
            f"on order {order_id}. Reference {decision['refund_id']}."
        )

    if action == "already_refunded" and order_id and item and amount:
        reference = f" as {decision['refund_id']}" if decision.get("refund_id") else ""
        quantity = f"{decision['quantity']} × " if decision.get("quantity") else ""
        return (
            f"A refund of {amount} for {quantity}{item} on order {order_id} was already submitted{reference}. "
            "I have not submitted another one."
        )

    if action == "refund_declined" and code == "not_eligible_in_transit" and order_id:
        tracking = ""
        if decision.get("tracking_number"):
            tracking = f" Tracking {decision['tracking_number']} with {decision.get('carrier')}."
        return f"Order {order_id} is still in transit, so I can't refund it yet.{tracking} I have not submitted a refund."

    if action == "refund_declined" and code == "return_not_received" and order_id:
        return (
            f"The return for {item or 'that item'} on order {order_id} is not received yet. "
            "I have not submitted a refund."
        )

    if action == "refund_declined" and code == "item_not_on_order" and order_id:
        return f"I can't match that item on order {order_id}. I have not submitted a refund."

    if action == "refund_declined" and code == "quantity_not_refundable" and order_id:
        return f"That quantity can't be refunded on order {order_id}. I have not submitted a refund."

    if action == "clarify" and code == "missing_order_reference":
        return "Which order should I look at? Please send the order id."

    if action == "clarify" and code == "multiple_orders":
        return "Please send one order id so I can look at the right order."

    if action == "clarify" and code == "ambiguous_proposal":
        return "You have more than one refund waiting for confirmation. Tell me the amount you want to confirm."

    if action == "clarify" and code == "nothing_to_confirm":
        return "There isn't an open refund to confirm on your account."

    if action == "clarify" and code == "confirm_amount_mismatch" and amount:
        return f"That does not match the open refund of {amount}. I have not submitted a refund."

    if action == "clarify" and code == "ambiguous_quantity" and order_id and item:
        return f"How many {item} should I refund on order {order_id}? I have not submitted a refund."

    if action == "clarify" and code == "ambiguous_item" and order_id:
        return f"More than one item on order {order_id} matches that description. I have not submitted a refund."

    if action == "clarify" and code == "need_defect_reason" and order_id:
        named = item or "the item"
        return (
            f"Order {order_id} is delivered. Tell me what went wrong with {named} "
            "and I can quote the refund. I have not submitted a refund."
        )

    if action == "refund_declined" and order_id:
        return f"I have not submitted a refund for order {order_id}."
    return "I could not complete that request from the order record."


def _money(amount: str | None, currency: str | None) -> str:
    if not amount:
        return ""
    return format_money(amount, currency)
