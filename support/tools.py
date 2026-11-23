"""Tool dispatch. The authenticated customer id is bound here, never taken from the model."""

from __future__ import annotations

from typing import Any

from commerce_mock.store import CommerceStore
from support.ledger import Ledger
from support.policy import confirm_proposal, quote_refund, read_order

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "Read one order for the authenticated customer. An order that belongs to someone else looks the same as an order that does not exist.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "quote_refund",
            "description": "Ask the refund policy about one item. The policy computes the amount from the order. execute is only a hint.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "item_query": {"type": "string"},
                    "quantity": {"type": "integer"},
                    "claimed_amount": {"type": "string"},
                    "execute": {"type": "boolean"},
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_proposal",
            "description": "Submit an open refund proposal after the customer agrees to the ledger amount.",
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string"},
                    "confirmed_amount": {"type": "string"},
                },
                "required": ["proposal_id"],
                "additionalProperties": False,
            },
        },
    },
]


def dispatch(
    store: CommerceStore,
    ledger: Ledger,
    customer_id: str,
    message: str,
    call: dict[str, Any],
) -> dict[str, Any]:
    arguments = {
        key: value
        for key, value in dict(call.get("arguments") or {}).items()
        if key != "customer_id"
    }
    name = call.get("name")
    if name == "get_order":
        result = read_order(store, ledger, customer_id, str(arguments.get("order_id") or ""))
    elif name == "quote_refund":
        result = quote_refund(store, ledger, customer_id, message, arguments)
    elif name == "confirm_proposal":
        result = confirm_proposal(store, ledger, customer_id, message, arguments)
    else:
        result = {"action": "clarify", "explanation_code": "unknown_tool"}
    return {"tool": name, "arguments": arguments, "result": result}
