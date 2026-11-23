"""Run the supplied requests, then show the confirmation gate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commerce_mock import default_seed_dir
from commerce_mock.store import CommerceStore
from support.service import SupportService


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    store = CommerceStore(default_seed_dir())
    service = SupportService(store)
    requests = json.loads((ROOT / "scenarios" / "requests.json").read_text(encoding="utf-8"))

    print("Official requests")
    print("-----------------")
    for item in requests:
        result = service.handle(item["authenticated_customer_id"], item["message"])
        _print_turn(item["scenario_id"], item["authenticated_customer_id"], item["message"], result)

    executed = _executed(store)
    if executed:
        print("FAIL: an official request submitted a refund. The policy should only quote those cases.")
        return 1

    print("Follow-up turns (not part of the supplied set)")
    print("----------------------------------------------")
    bare = service.handle("CUST-001", "yes")
    _print_turn("follow-up-yes", "CUST-001", "yes", bare)
    if bare["action"] != "clarify" or _executed(store):
        print("FAIL: a bare yes must not choose between two open refunds.")
        return 1

    confirm = service.handle("CUST-001", "yes, confirm €29.90")
    _print_turn("follow-up-confirm", "CUST-001", "yes, confirm €29.90", confirm)
    executed = _executed(store)
    if confirm["action"] != "refund_executed" or len(executed) != 1 or executed[0]["amount"] != "29.90":
        print("FAIL: confirming €29.90 should submit one Travel Mug refund for that amount.")
        return 1

    repeat = service.handle("CUST-001", "yes, confirm €29.90")
    _print_turn("follow-up-repeat", "CUST-001", "yes, confirm €29.90", repeat)
    executed = _executed(store)
    if len(executed) != 1:
        print("FAIL: repeating the confirmation created a second refund.")
        return 1

    print("Ledger")
    print("------")
    print(f"Executed refunds: {len(executed)}")
    for refund in executed:
        line = refund["lines"][0]
        print(f"  {refund['refund_id']}  {refund['order_id']}  {line['quantity']} x {line['name']}  EUR {refund['amount']}")
    still_open = [
        row for row in store.runtime_records("proposals") if row.get("status") == "open"
    ]
    print(f"Open proposals left: {len(still_open)}")
    for proposal in still_open:
        line = proposal["lines"][0]
        print(f"  {proposal['proposal_id']}  {proposal['order_id']}  {line['name']}  EUR {proposal['amount']}")
    return 0


def _print_turn(label: str, customer_id: str, message: str, result: dict) -> None:
    print()
    print(f"{label}  {customer_id}")
    print(f"  message: {message}")
    print(f"  action:  {result['action']} ({result['explanation_code']})")
    if result.get("amount"):
        print(f"  amount:  EUR {result['amount']}")
    print(f"  reply:   {result['reply']}")


def _executed(store: CommerceStore) -> list[dict]:
    return [row for row in store.runtime_records("refunds") if row.get("status") == "executed"]


if __name__ == "__main__":
    raise SystemExit(main())
