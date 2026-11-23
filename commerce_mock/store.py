"""In-memory commerce data.

Seed files are read once and never written. clear_runtime() drops scratch
records and restores the in-memory customers and orders from that snapshot.
A new process does the same, because nothing is persisted.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any


class CommerceStore:
    def __init__(self, seed_dir: Path) -> None:
        self.seed_dir = Path(seed_dir)
        self._customer_seed = self._read_seed("customers.json")
        self._order_seed = self._read_seed("orders.json")
        self._lock = threading.Lock()
        self.customers: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self.runtime: dict[str, list[dict[str, Any]]] = {}
        self.clear_runtime()

    def _read_seed(self, name: str) -> Any:
        path = self.seed_dir / name
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle, parse_float=Decimal)

    def clear_runtime(self) -> None:
        with self._lock:
            self.customers = deepcopy(self._customer_seed)
            self.orders = deepcopy(self._order_seed)
            self.runtime = {}

    def list_customers(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self.customers)

    def list_orders(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self.orders)

    def runtime_records(self, collection: str) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self.runtime.get(collection, []))

    def runtime_append(self, collection: str, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            stored = deepcopy(record)
            self.runtime.setdefault(collection, []).append(stored)
            return deepcopy(stored)

    def runtime_update(
        self,
        collection: str,
        predicate: Callable[[dict[str, Any]], bool],
        changes: dict[str, Any],
    ) -> int:
        with self._lock:
            updated = 0
            for row in self.runtime.get(collection, []):
                if predicate(row):
                    row.update(deepcopy(changes))
                    updated += 1
            return updated
