"""Seed-backed commerce data for local exercise runs.

Scratch records are untyped. The support package decides what they mean.
"""

from pathlib import Path

from commerce_mock.store import CommerceStore


def default_seed_dir() -> Path:
    return Path(__file__).resolve().parent / "seed"


__all__ = ["CommerceStore", "default_seed_dir"]
