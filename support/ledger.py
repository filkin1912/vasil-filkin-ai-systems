"""Refund, proposal, case, and review records.

The commerce mock only stores dicts. This module owns the record shape,
ids, and the idempotency key that stops a second payment.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from commerce_mock.store import CommerceStore

PROPOSALS = "proposals"
REFUNDS = "refunds"
CASES = "cases"
REVIEWS = "reviews"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Ledger:
    def __init__(self, store: CommerceStore) -> None:
        self.store = store
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._counts = {}

    def next_id(self, prefix: str) -> str:
        with self._lock:
            self._counts[prefix] = self._counts.get(prefix, 0) + 1
            number = self._counts[prefix]
        return f"{prefix}-{number:04d}"

    def open_proposals(self, customer_id: str) -> list[dict[str, Any]]:
        return [
            row
            for row in self.store.runtime_records(PROPOSALS)
            if row.get("customer_id") == customer_id and row.get("status") == "open"
        ]

    def prompt_view(self, customer_id: str) -> list[dict[str, Any]]:
        view = []
        for row in self.open_proposals(customer_id):
            view.append(
                {
                    "proposal_id": row["proposal_id"],
                    "order_id": row["order_id"],
                    "amount": row["amount"],
                    "currency": row["currency"],
                    "item_name": row["lines"][0]["name"],
                }
            )
        return view

    def executed_refunds(self, customer_id: str | None = None) -> list[dict[str, Any]]:
        rows = [
            row
            for row in self.store.runtime_records(REFUNDS)
            if row.get("status") == "executed"
        ]
        if customer_id is None:
            return rows
        return [row for row in rows if row.get("customer_id") == customer_id]

    def refunded_quantity(self, order_id: str, item_id: str) -> int:
        total = 0
        for refund in self.executed_refunds():
            if refund.get("order_id") != order_id:
                continue
            for line in refund.get("lines", []):
                if line.get("item_id") == item_id:
                    total += int(line["quantity"])
        return total

    def find_executed_by_key(self, key: str) -> dict[str, Any] | None:
        for refund in self.executed_refunds():
            if refund.get("idempotency_key") == key:
                return refund
        return None

    def find_executed_by_amount(self, customer_id: str, amount: str) -> dict[str, Any] | None:
        matches = [
            refund
            for refund in self.executed_refunds(customer_id)
            if refund.get("amount") == amount
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def find_open_by_key(self, key: str) -> dict[str, Any] | None:
        for row in self.store.runtime_records(PROPOSALS):
            if row.get("idempotency_key") == key and row.get("status") == "open":
                return row
        return None

    def save_proposal(self, record: dict[str, Any]) -> dict[str, Any]:
        existing = self.find_open_by_key(record["idempotency_key"])
        if existing:
            copy = dict(existing)
            copy["duplicate"] = True
            return copy
        item_id = record["lines"][0]["item_id"]
        self.store.runtime_update(
            PROPOSALS,
            lambda row, item_id=item_id, record=record: (
                row.get("status") == "open"
                and row.get("customer_id") == record["customer_id"]
                and row.get("order_id") == record["order_id"]
                and row.get("lines", [{}])[0].get("item_id") == item_id
                and row.get("idempotency_key") != record["idempotency_key"]
            ),
            {"status": "superseded"},
        )
        proposal = {
            "proposal_id": self.next_id("PROP"),
            "status": "open",
            "created_at": now(),
            **record,
        }
        return self.store.runtime_append(PROPOSALS, proposal)

    def save_executed(self, record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        existing = self.find_executed_by_key(record["idempotency_key"])
        if existing:
            copy = dict(existing)
            copy["duplicate"] = True
            return copy, True
        refund = {
            "refund_id": self.next_id("REF"),
            "status": "executed",
            "created_at": now(),
            **record,
        }
        stored = self.store.runtime_append(REFUNDS, refund)
        key = record["idempotency_key"]
        self.store.runtime_update(
            PROPOSALS,
            lambda row, key=key: row.get("idempotency_key") == key and row.get("status") == "open",
            {"status": "confirmed", "refund_id": stored["refund_id"]},
        )
        return stored, False

    def record_case(self, record: dict[str, Any]) -> dict[str, Any]:
        case = {"case_id": self.next_id("CASE"), "created_at": now(), **record}
        return self.store.runtime_append(CASES, case)

    def enqueue_review(self, record: dict[str, Any]) -> dict[str, Any]:
        item = {"review_id": self.next_id("REV"), "created_at": now(), **record}
        return self.store.runtime_append(REVIEWS, item)
