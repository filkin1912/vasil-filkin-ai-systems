"""Refund decisions.

Amounts come from the order line. The customer message can ask for a refund
and can name an item, but it cannot set the payable amount. Model arguments
cannot widen that decision.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from commerce_mock.store import CommerceStore
from support.ledger import Ledger
from support.slots import (
    extract_amount,
    extract_order_ids,
    implied_quantity,
    is_affirmation,
    message_has_refund_intent,
    message_mentions_defect,
    message_requests_execution,
    money,
    quantity_all,
    resolve_item_query,
    tokens,
    choose_proposal,
)

FACT_KEYS = (
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
    "duplicate",
    "reason",
)


def eligibility(status: str, return_status: str | None, defect: bool) -> str | None:
    """Return an explanation code when the line cannot be refunded yet."""
    if status == "in_transit":
        return "not_eligible_in_transit"
    if status == "returned":
        if return_status != "received":
            return "return_not_received"
        return None
    if status == "delivered":
        if not defect:
            return "need_defect_reason"
        return None
    return "status_not_refundable"


def match_item(items: list[dict[str, Any]], query: str | None) -> tuple[dict[str, Any] | None, str | None]:
    if not query:
        if len(items) == 1:
            return items[0], None
        return None, "ambiguous_item"
    query_tokens = tokens(query)
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in items:
        item_tokens = tokens(str(item["name"]))
        if not query_tokens or not item_tokens:
            continue
        overlap = query_tokens & item_tokens
        if not overlap:
            continue
        scored.append((len(overlap) / len(query_tokens), item))
    if not scored:
        return None, "item_not_on_order"
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None, "ambiguous_item"
    if scored[0][0] < 0.5:
        return None, "item_not_on_order"
    return scored[0][1], None


def _order_for_customer(store: CommerceStore, customer_id: str, order_id: str) -> dict[str, Any] | None:
    for order in store.list_orders():
        if order["order_id"] == order_id and order["customer_id"] == customer_id:
            return order
    return None


def _line(item: dict[str, Any], quantity: int) -> dict[str, Any]:
    unit = Decimal(item["unit_price"])
    amount = money(unit * quantity)
    return {
        "item_id": item["item_id"],
        "name": item["name"],
        "quantity": quantity,
        "unit_price": money(unit),
        "amount": amount,
    }


def _idempotency_key(customer_id: str, order_id: str, item_id: str, quantity: int, reason: str, amount: str) -> str:
    return f"{customer_id}|{order_id}|{item_id}|{quantity}|{reason}|{amount}"


def read_order(store: CommerceStore, ledger: Ledger, customer_id: str, order_id: str) -> dict[str, Any]:
    order = _order_for_customer(store, customer_id, order_id)
    if order is None:
        return {"accessible": False, "order_id": order_id}
    items = []
    for item in order["items"]:
        presented = {
            "item_id": item["item_id"],
            "name": item["name"],
            "quantity": int(item["quantity"]),
            "unit_price": money(item["unit_price"]),
            "refunded_quantity": ledger.refunded_quantity(order["order_id"], item["item_id"]),
        }
        if item.get("return_status"):
            presented["return_status"] = item["return_status"]
        items.append(presented)
    result: dict[str, Any] = {
        "accessible": True,
        "order_id": order["order_id"],
        "status": order["status"],
        "currency": order["currency"],
        "total": money(order["total"]),
        "items": items,
        "shipping": None,
    }
    shipping = order.get("shipping")
    if shipping:
        result["shipping"] = {
            "carrier": shipping.get("carrier"),
            "tracking_number": shipping.get("tracking_number"),
        }
    return result


def quote_refund(
    store: CommerceStore,
    ledger: Ledger,
    customer_id: str,
    message: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Quote or submit one line. `arguments` are the model's hint and are not granted."""
    model_execute = bool(arguments.get("execute"))
    order_ids = extract_order_ids(message)
    if len(order_ids) != 1 or not message_has_refund_intent(message):
        return {
            "action": "ignored",
            "explanation_code": "not_a_refund_request",
            "model_execute": model_execute,
        }
    order_id = order_ids[0]
    order = _order_for_customer(store, customer_id, order_id)
    if order is None:
        return {
            "action": "not_found",
            "explanation_code": "order_not_on_account",
            "order_id": order_id,
            "model_execute": model_execute,
        }

    query = resolve_item_query(arguments.get("item_query"), message)
    item, match_code = match_item(order["items"], query)
    if item is None:
        action = "clarify" if match_code == "ambiguous_item" else "refund_declined"
        return {
            "action": action,
            "explanation_code": match_code,
            "order_id": order_id,
            "order_status": order["status"],
            "model_execute": model_execute,
        }

    purchased = int(item["quantity"])
    already = ledger.refunded_quantity(order_id, item["item_id"])
    remaining = purchased - already
    if remaining <= 0:
        return _already_refunded(ledger, customer_id, order, item, purchased)

    if quantity_all(message):
        quantity = remaining
    else:
        quantity = implied_quantity(message, str(item["name"]), purchased)
    if quantity is None:
        return {
            "action": "clarify",
            "explanation_code": "ambiguous_quantity",
            "order_id": order_id,
            "order_status": order["status"],
            "item_name": item["name"],
            "model_execute": model_execute,
        }
    if quantity < 1 or quantity > remaining:
        return {
            "action": "refund_declined",
            "explanation_code": "quantity_not_refundable",
            "order_id": order_id,
            "order_status": order["status"],
            "item_name": item["name"],
            "quantity": quantity,
            "model_execute": model_execute,
        }

    blocked = eligibility(order["status"], item.get("return_status"), message_mentions_defect(message))
    if blocked:
        result = {
            "action": "clarify" if blocked == "need_defect_reason" else "refund_declined",
            "explanation_code": blocked,
            "order_id": order_id,
            "order_status": order["status"],
            "item_name": item["name"],
            "model_execute": model_execute,
        }
        _copy_shipping(result, order)
        return result

    reason = "return_received" if order["status"] == "returned" else "damaged"
    line = _line(item, quantity)
    claimed = extract_amount(message)
    execute = message_requests_execution(message)
    key = _idempotency_key(customer_id, order_id, item["item_id"], quantity, reason, line["amount"])
    base = {
        "order_id": order_id,
        "order_status": order["status"],
        "item_name": item["name"],
        "quantity": quantity,
        "amount": line["amount"],
        "claimed_amount": claimed,
        "currency": order["currency"],
        "reason": reason,
        "model_execute": model_execute,
        "policy_execute": execute,
    }
    mismatch = claimed is not None and claimed != line["amount"]
    if (not execute) or mismatch:
        proposal = ledger.save_proposal(
            {
                "customer_id": customer_id,
                "order_id": order_id,
                "currency": order["currency"],
                "amount": line["amount"],
                "reason": reason,
                "lines": [line],
                "claimed_amount": claimed,
                "idempotency_key": key,
                "explanation_code": "claim_does_not_match_ledger" if mismatch else "awaiting_confirmation",
            }
        )
        return {
            **base,
            "action": "refund_proposed",
            "explanation_code": proposal["explanation_code"],
            "proposal_id": proposal["proposal_id"],
            "duplicate": bool(proposal.get("duplicate")),
        }

    refund, duplicate = ledger.save_executed(
        {
            "customer_id": customer_id,
            "order_id": order_id,
            "currency": order["currency"],
            "amount": line["amount"],
            "reason": reason,
            "lines": [line],
            "idempotency_key": key,
        }
    )
    if duplicate:
        return _already_from_refund(refund, item["name"])
    return {
        **base,
        "action": "refund_executed",
        "explanation_code": "refund_executed",
        "refund_id": refund["refund_id"],
        "duplicate": False,
    }


def confirm_proposal(
    store: CommerceStore,
    ledger: Ledger,
    customer_id: str,
    message: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Submit an open proposal. The model may name an id; the message decides which one."""
    del arguments
    if not is_affirmation(message):
        return {"action": "ignored", "explanation_code": "not_a_confirmation"}
    amount = extract_amount(message)
    existing = ledger.find_executed_by_amount(customer_id, amount) if amount else None
    proposal, code = choose_proposal(message, ledger.open_proposals(customer_id))
    if existing and (proposal is None or proposal.get("amount") != amount):
        return _already_from_refund(existing, existing["lines"][0]["name"])
    if code == "no_open_match":
        amount = extract_amount(message)
        existing = ledger.find_executed_by_amount(customer_id, amount) if amount else None
        if existing:
            return _already_from_refund(existing, existing["lines"][0]["name"])
        return {"action": "clarify", "explanation_code": "nothing_to_confirm"}
    if proposal is None:
        return {"action": "clarify", "explanation_code": code}
    if code == "mismatch":
        return {
            "action": "clarify",
            "explanation_code": "confirm_amount_mismatch",
            "order_id": proposal["order_id"],
            "item_name": proposal["lines"][0]["name"],
            "quantity": proposal["lines"][0]["quantity"],
            "amount": proposal["amount"],
            "claimed_amount": extract_amount(message),
            "currency": proposal["currency"],
            "proposal_id": proposal["proposal_id"],
        }

    blocked = _proposal_exceeds_remaining(store, ledger, customer_id, proposal)
    if blocked:
        return blocked

    refund, duplicate = ledger.save_executed(
        {
            "customer_id": customer_id,
            "order_id": proposal["order_id"],
            "currency": proposal["currency"],
            "amount": proposal["amount"],
            "reason": proposal["reason"],
            "lines": proposal["lines"],
            "idempotency_key": proposal["idempotency_key"],
        }
    )
    if duplicate:
        return _already_from_refund(refund, proposal["lines"][0]["name"])
    return {
        "action": "refund_executed",
        "explanation_code": "refund_executed",
        "order_id": proposal["order_id"],
        "item_name": proposal["lines"][0]["name"],
        "quantity": proposal["lines"][0]["quantity"],
        "amount": proposal["amount"],
        "currency": proposal["currency"],
        "reason": proposal["reason"],
        "refund_id": refund["refund_id"],
        "duplicate": False,
    }


def _proposal_exceeds_remaining(
    store: CommerceStore,
    ledger: Ledger,
    customer_id: str,
    proposal: dict[str, Any],
) -> dict[str, Any] | None:
    order = _order_for_customer(store, customer_id, proposal["order_id"])
    line = proposal["lines"][0]
    if order is None:
        return {
            "action": "not_found",
            "explanation_code": "order_not_on_account",
            "order_id": proposal["order_id"],
        }
    purchased = 0
    for item in order["items"]:
        if item["item_id"] == line["item_id"]:
            purchased = int(item["quantity"])
    remaining = purchased - ledger.refunded_quantity(proposal["order_id"], line["item_id"])
    if int(line["quantity"]) <= remaining:
        return None
    existing = ledger.find_executed_by_key(proposal["idempotency_key"])
    if existing:
        return _already_from_refund(existing, line["name"])
    return {
        "action": "refund_declined",
        "explanation_code": "quantity_not_refundable",
        "order_id": proposal["order_id"],
        "item_name": line["name"],
        "quantity": line["quantity"],
    }


def _already_refunded(
    ledger: Ledger,
    customer_id: str,
    order: dict[str, Any],
    item: dict[str, Any],
    purchased: int,
) -> dict[str, Any]:
    existing = next(
        (
            refund
            for refund in ledger.executed_refunds(customer_id)
            if refund.get("order_id") == order["order_id"]
            and any(line.get("item_id") == item["item_id"] for line in refund.get("lines", []))
        ),
        None,
    )
    amount = existing["amount"] if existing else money(Decimal(item["unit_price"]) * purchased)
    return _already_from_refund(existing, item["name"]) if existing else {
        "action": "already_refunded",
        "explanation_code": "already_refunded",
        "order_id": order["order_id"],
        "item_name": item["name"],
        "quantity": purchased,
        "amount": amount,
        "currency": order["currency"],
    }


def _already_from_refund(refund: dict[str, Any], item_name: str) -> dict[str, Any]:
    line = refund["lines"][0]
    return {
        "action": "already_refunded",
        "explanation_code": "already_refunded",
        "order_id": refund["order_id"],
        "item_name": item_name,
        "quantity": line["quantity"],
        "amount": refund["amount"],
        "currency": refund["currency"],
        "refund_id": refund["refund_id"],
        "duplicate": True,
    }


def _copy_shipping(result: dict[str, Any], order: dict[str, Any]) -> None:
    shipping = order.get("shipping") or {}
    if shipping.get("tracking_number"):
        result["carrier"] = shipping.get("carrier")
        result["tracking_number"] = shipping["tracking_number"]


def assemble_decision(
    store: CommerceStore,
    customer_id: str,
    hint: str,
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    results = {obs["tool"]: obs["result"] for obs in observations}
    confirm = results.get("confirm_proposal")
    quote = results.get("quote_refund")
    order = results.get("get_order")

    if confirm and confirm.get("action") != "ignored":
        decision = _pick_facts(confirm)
    elif quote and quote.get("action") != "ignored":
        decision = _pick_facts(quote)
    elif order and order.get("accessible"):
        decision = _status_from_order(order)
    elif order and order.get("accessible") is False:
        decision = {
            "action": "not_found",
            "explanation_code": "order_not_on_account",
            "order_id": order.get("order_id"),
        }
    else:
        code = hint if hint and hint != "none" else "missing_order_reference"
        decision = {"action": "clarify", "explanation_code": code}

    if order and order.get("accessible") and decision.get("action") in {"refund_declined", "clarify"}:
        shipping = order.get("shipping") or {}
        if shipping.get("tracking_number") and not decision.get("tracking_number"):
            decision["carrier"] = shipping.get("carrier")
            decision["tracking_number"] = shipping["tracking_number"]
    return _with_allow_lists(decision, store, customer_id)


def _pick_facts(result: dict[str, Any]) -> dict[str, Any]:
    return {key: result[key] for key in FACT_KEYS if result.get(key) not in (None, False)}


def _status_from_order(order: dict[str, Any]) -> dict[str, Any]:
    decision: dict[str, Any] = {
        "action": "order_status",
        "explanation_code": "order_status",
        "order_id": order["order_id"],
        "order_status": order["status"],
        "items_summary": ", ".join(item["name"] for item in order["items"]),
        "currency": order.get("currency"),
    }
    shipping = order.get("shipping") or {}
    if shipping.get("tracking_number"):
        decision["carrier"] = shipping.get("carrier")
        decision["tracking_number"] = shipping["tracking_number"]
    return decision


def _with_allow_lists(decision: dict[str, Any], store: CommerceStore, customer_id: str) -> dict[str, Any]:
    del customer_id
    amounts = [decision[key] for key in ("amount", "claimed_amount") if decision.get(key)]
    item_names: list[str] = []
    if decision.get("item_name"):
        item_names.append(decision["item_name"])
    if decision.get("action") == "order_status" and decision.get("items_summary"):
        item_names.extend(part.strip() for part in str(decision["items_summary"]).split(",") if part.strip())
    tracking = [decision["tracking_number"]] if decision.get("tracking_number") else []
    catalog: list[str] = []
    all_tracking: list[str] = []
    for order in store.list_orders():
        shipping = order.get("shipping") or {}
        if shipping.get("tracking_number"):
            all_tracking.append(shipping["tracking_number"])
        for item in order["items"]:
            catalog.append(item["name"])
    allowed_items = list(dict.fromkeys(item_names))
    decision["allowed_amounts"] = list(dict.fromkeys(amounts))
    decision["allowed_order_ids"] = [decision["order_id"]] if decision.get("order_id") else []
    decision["allowed_item_names"] = allowed_items
    decision["allowed_tracking_numbers"] = tracking
    decision["allowed_emails"] = []
    decision["forbidden_item_names"] = [name for name in dict.fromkeys(catalog) if name not in allowed_items]
    decision["forbidden_tracking_numbers"] = [number for number in all_tracking if number not in tracking]
    return decision
