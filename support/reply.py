"""Deterministic customer reply used when the model draft fails its checks."""

from __future__ import annotations

from support.slots import format_money

_SAFE = "I couldn't safely answer from the order record. A specialist will review the case."


def render_template(decision: dict) -> str:
    action = decision.get("action")
    code = decision.get("explanation_code")
    order_id = decision.get("order_id")
    item = decision.get("item_name")
    amount = _money(decision.get("amount"), decision.get("currency"))
    claimed = _money(decision.get("claimed_amount"), decision.get("currency"))

    if action == "order_status" and order_id and decision.get("order_status"):
        status = str(decision["order_status"]).replace("_", " ")
        text = f"Order {order_id} is {status}."
        if decision.get("tracking_number"):
            carrier = decision.get("carrier") or "the carrier"
            text += f" It is with {carrier}. Tracking number: {decision['tracking_number']}."
        if decision.get("items_summary"):
            text += f" It includes {decision['items_summary']}."
        return text

    if action == "not_found" and order_id:
        return f"I can't find order {order_id} on your account."

    if action == "refund_proposed" and order_id and item and amount and decision.get("quantity"):
        lead = ""
        if decision.get("claimed_amount") and decision.get("claimed_amount") != decision.get("amount"):
            lead = f"You mentioned {claimed}, which does not match this order. "
        received = ""
        if decision.get("reason") == "return_received":
            received = "The return is already received. "
        return (
            f"{lead}{received}The refund I can offer is {amount} for {decision['quantity']} × {item} "
            f"on order {order_id}. I have not submitted a refund. Reply yes, confirm {amount}."
        )

    if action == "refund_executed" and order_id and item and amount and decision.get("refund_id"):
        return (
            f"I have submitted a refund of {amount} for {decision['quantity']} × {item} "
            f"on order {order_id}. Reference {decision['refund_id']}."
        )

    if action == "already_refunded" and order_id and item and amount:
        reference = f" as {decision['refund_id']}" if decision.get("refund_id") else ""
        quantity = decision.get("quantity")
        unit = f"{quantity} × " if quantity else ""
        return (
            f"A refund of {amount} for {unit}{item} on order {order_id} was already submitted{reference}. "
            "I have not submitted another one."
        )

    if action == "refund_declined" and code == "not_eligible_in_transit" and order_id:
        tracking = ""
        if decision.get("tracking_number"):
            tracking = f" Tracking number: {decision['tracking_number']}."
        return f"Order {order_id} is still in transit, so I can't refund it yet.{tracking} I have not submitted a refund."

    if action == "refund_declined" and code == "return_not_received" and order_id:
        named = item or "that item"
        return f"The return for {named} on order {order_id} is not received yet. I have not submitted a refund."

    if action == "refund_declined" and code == "item_not_on_order" and order_id:
        return f"I can't match that item on order {order_id}. I have not submitted a refund."

    if action == "refund_declined" and code == "quantity_not_refundable" and order_id:
        return f"That quantity can't be refunded on order {order_id}. I have not submitted a refund."

    if action == "clarify" and code == "missing_order_reference":
        return "Which order should I look at? Please send the order id."

    if action == "clarify" and code == "multiple_orders":
        return "Please send one order id so I can look at the right order."

    if action == "clarify" and code == "ambiguous_proposal":
        return "More than one refund is waiting for confirmation. Tell me the amount you want to confirm."

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

    return _SAFE


def _money(amount: str | None, currency: str | None) -> str:
    if not amount:
        return ""
    return format_money(amount, currency)
