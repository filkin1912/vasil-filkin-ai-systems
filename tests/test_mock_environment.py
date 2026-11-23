"""Infrastructure checks only. No refund outcomes."""

from pathlib import Path

from commerce_mock import default_seed_dir
from commerce_mock.store import CommerceStore


def test_seed_loads() -> None:
    store = CommerceStore(default_seed_dir())
    assert {row["customer_id"] for row in store.list_customers()} == {"CUST-001", "CUST-002"}
    assert {row["order_id"] for row in store.list_orders()} == {"ORD-100", "ORD-200", "ORD-204", "ORD-300"}


def test_runtime_can_be_stored_read_and_cleared(seed_bytes: dict[str, bytes]) -> None:
    store = CommerceStore(default_seed_dir())
    store.orders.append({"order_id": "ORD-HACK"})
    stored = store.runtime_append("notes", {"text": "temp"})
    assert store.runtime_records("notes") == [stored]

    store.clear_runtime()

    assert store.runtime_records("notes") == []
    assert all(row["order_id"] != "ORD-HACK" for row in store.orders)
    assert {row["order_id"] for row in store.list_orders()} == {"ORD-100", "ORD-200", "ORD-204", "ORD-300"}
    on_disk = {path.name: path.read_bytes() for path in Path(default_seed_dir()).iterdir() if path.is_file()}
    assert on_disk == seed_bytes


def test_a_new_process_does_not_keep_runtime_records() -> None:
    first = CommerceStore(default_seed_dir())
    first.runtime_append("notes", {"text": "temp"})
    second = CommerceStore(default_seed_dir())
    assert second.runtime_records("notes") == []
