"""Reject a reply that states a fact the tools did not return."""

from __future__ import annotations

import re

from support.slots import tokens

_AMOUNT_RE = re.compile(r"\d+\.\d{2}")
_ORDER_RE = re.compile(r"\bORD-\d+\b", re.I)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_SUBMITTED_RE = re.compile(r"i have submitted a refund", re.I)


def check_reply(reply: str, decision: dict, denylist: list[str]) -> list[str]:
    issues: list[str] = []
    allowed_amounts = set(decision.get("allowed_amounts") or [])
    for amount in _AMOUNT_RE.findall(reply):
        if amount not in allowed_amounts:
            issues.append(f"amount {amount} is not in the tool decision")

    allowed_orders = {order_id.upper() for order_id in decision.get("allowed_order_ids") or []}
    for order_id in _ORDER_RE.findall(reply):
        if order_id.upper() not in allowed_orders:
            issues.append(f"order {order_id} is not in the tool decision")

    allowed_emails = {email.lower() for email in decision.get("allowed_emails") or []}
    for email in _EMAIL_RE.findall(reply):
        if email.lower() not in allowed_emails:
            issues.append(f"email {email} is not allowed")

    for secret in denylist:
        if secret and re.search(rf"\b{re.escape(secret)}\b", reply, re.I):
            issues.append("reply contains another customer's data")
            break

    for name in decision.get("forbidden_item_names") or []:
        if name and name.lower() in reply.lower():
            issues.append(f"reply mentions {name}, which this lookup did not return")

    allowed_tokens: set[str] = set()
    for name in decision.get("allowed_item_names") or []:
        allowed_tokens |= tokens(name)
    banned_tokens: set[str] = set()
    for name in decision.get("forbidden_item_names") or []:
        for token in tokens(name):
            if len(token) > 4 and token not in allowed_tokens:
                banned_tokens.add(token)
    for token in sorted(banned_tokens):
        if re.search(rf"\b{re.escape(token)}\b", reply, re.I):
            issues.append(f"reply mentions {token}, which this lookup did not return")

    for tracking in decision.get("forbidden_tracking_numbers") or []:
        if tracking and tracking in reply:
            issues.append("reply contains a tracking number from another order")

    if decision.get("action") == "refund_executed":
        if not _SUBMITTED_RE.search(reply):
            issues.append("executed refund was not stated")
    elif _SUBMITTED_RE.search(reply):
        issues.append("reply claims a refund was submitted")
    return issues
