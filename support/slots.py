"""Read slots from the customer message.

The mock parser and the refund policy both use this module. The policy
trusts the message, not a model flag: a tool argument cannot add an item,
an amount, or a payment the customer did not ask for.
"""

from __future__ import annotations

import re
from decimal import Decimal

ORDER_RE = re.compile(r"\bORD-\d+\b", re.I)
AMOUNT_RE = re.compile(
    r"(?:€|eur\s*)(\d+(?:[.,]\d{1,2})?)|(\d+(?:[.,]\d{1,2})?)\s*(?:€|eur\b)",
    re.I,
)
REFUND_RE = re.compile(r"\b(refunds?|broken|damaged|damage|returns?|returned)\b", re.I)
DEFECT_RE = re.compile(r"\b(broken|damaged|damage|cracked|defect|smashed)\b", re.I)
PROCESS_RE = re.compile(r"\b(process|go ahead|submit|issue)\b", re.I)
YES_RE = re.compile(
    r"^(yes|yes please|confirm|confirmed|ok|okay|go ahead|please do|proceed)[.!]?\s*$",
    re.I,
)
ONE_RE = re.compile(r"\bone of\b|\ba single\b|\bjust one\b|\bone\b", re.I)
ALL_RE = re.compile(r"\ball of them\b|\ball\b|\bboth\b", re.I)

_STOP = {"the", "a", "an", "of", "my", "order", "item", "for", "it"}


def money(value: Decimal | int | str) -> str:
    return format(Decimal(str(value)).quantize(Decimal("0.01")), "f")


def format_money(amount: str, currency: str | None) -> str:
    if currency == "EUR":
        return f"€{amount}"
    if currency:
        return f"{amount} {currency}"
    return amount


def extract_order_ids(message: str) -> list[str]:
    return [match.group(0).upper() for match in ORDER_RE.finditer(message)]


def extract_amount(message: str) -> str | None:
    match = AMOUNT_RE.search(message)
    if not match:
        return None
    raw = next(group for group in match.groups() if group)
    return money(raw.replace(",", "."))


def message_has_refund_intent(message: str) -> bool:
    return bool(REFUND_RE.search(message))


def message_mentions_defect(message: str) -> bool:
    return bool(DEFECT_RE.search(message))


def message_requests_execution(message: str) -> bool:
    return bool(PROCESS_RE.search(message))


def is_affirmation(message: str) -> bool:
    if ORDER_RE.search(message):
        return False
    text = message.strip()
    if YES_RE.match(text):
        return True
    return bool(re.search(r"\b(yes|confirm|proceed)\b", text, re.I))


def extract_quantity(message: str) -> int | None:
    cleaned = AMOUNT_RE.sub(" ", message)
    cleaned = ORDER_RE.sub(" ", cleaned)
    if ONE_RE.search(cleaned):
        return 1
    if ALL_RE.search(cleaned):
        return None
    numbers = [int(value) for value in re.findall(r"\b(\d+)\b", cleaned)]
    if len(numbers) == 1:
        return numbers[0]
    return None


def implied_quantity(message: str, item_name: str, purchased: int) -> int | None:
    """Quantity the customer actually named. Singular wording means one unit."""
    explicit = extract_quantity(message)
    if explicit is not None:
        return explicit
    if purchased == 1:
        return 1
    name_tokens = tokens(item_name)
    singular = False
    plural = False
    for word in re.findall(r"[a-z]+", message.lower()):
        if word in name_tokens:
            singular = True
        stem = _plural_stem(word)
        if stem and stem in name_tokens and word not in name_tokens:
            plural = True
    if singular and not plural:
        return 1
    return None


def _plural_stem(word: str) -> str | None:
    if len(word) > 4 and word.endswith("es"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return None


def quantity_all(message: str) -> bool:
    cleaned = AMOUNT_RE.sub(" ", message)
    cleaned = ORDER_RE.sub(" ", cleaned)
    return bool(ALL_RE.search(cleaned))


def tokens(text: str) -> set[str]:
    found: set[str] = set()
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if word in _STOP:
            continue
        found.add(word)
        if len(word) > 4 and word.endswith("es"):
            found.add(word[:-2])
        elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            found.add(word[:-1])
    return found


def tokens_covered(query: str, message: str) -> bool:
    query_tokens = tokens(query)
    return bool(query_tokens) and query_tokens <= tokens(message)


def clean_phrase(phrase: str) -> str:
    phrase = re.sub(r"\s+", " ", phrase).strip(" .,:;?!")
    return re.sub(r"^(a|an|my)\s+", "", phrase, flags=re.I)


def extract_item_query(message: str) -> str | None:
    text = AMOUNT_RE.sub(" ", ORDER_RE.sub(" ", message))
    patterns = [
        r"one of the\s+(.+?)\s+in order",
        r"one of the\s+(.+?)\s+(?:arrived|was|is|broke)",
        r"(?:returned|refund(?:\s+for)?|broken|damaged)\s+the\s+(.+?)\s+from order",
        r"the\s+(.+?)\s+from order",
        r"the\s+(.+?)\s+in order",
        r"the\s+(.+?)\s+(?:arrived|was broken|broke)",
        r"(?:broken|damaged)\s+(.+?)\s+on order",
        r"refund for (?:the |my )?(.+?)(?: on | in | from |[.?!]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        phrase = clean_phrase(match.group(1))
        if phrase and phrase.lower() not in {"it", "them", "this", "that", "one"}:
            return phrase
    return None


def resolve_item_query(model_query: str | None, message: str) -> str | None:
    if model_query and tokens_covered(model_query, message):
        return clean_phrase(model_query)
    return extract_item_query(message)


def choose_proposal(message: str, proposals: list[dict]) -> tuple[dict | None, str]:
    if not proposals:
        return None, "nothing_to_confirm"
    amount = extract_amount(message)
    by_id = [row for row in proposals if row["proposal_id"].lower() in message.lower()]
    if len(by_id) == 1:
        return _amount_check(by_id[0], amount)
    by_order = [row for row in proposals if row["order_id"].lower() in message.lower()]
    if len(by_order) == 1:
        return _amount_check(by_order[0], amount)
    if amount:
        by_amount = [row for row in proposals if row["amount"] == amount]
        if len(by_amount) == 1:
            return by_amount[0], "ok"
        if len(by_amount) > 1:
            return None, "ambiguous_proposal"
        if len(proposals) == 1:
            return proposals[0], "mismatch"
        return None, "no_open_match"
    if len(proposals) == 1:
        return proposals[0], "ok"
    return None, "ambiguous_proposal"


def _amount_check(proposal: dict, amount: str | None) -> tuple[dict, str]:
    if amount and amount != proposal["amount"]:
        return proposal, "mismatch"
    return proposal, "ok"
