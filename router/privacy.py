"""Data classification, masking and keyed hashing (FR-4, FR-6, FR-12; ADR-4).

Classification fails CLOSED: if detection raises for any reason, the request is treated as
sensitive. A false "sensitive" only costs an escalation; a false "non-sensitive" leaks data.
"""

from __future__ import annotations

import hashlib
import hmac
import re

NON_SENSITIVE = "non_sensitive"
SENSITIVE = "sensitive"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_EMIRATES_ID = re.compile(r"\b784[-\s]?\d{4}[-\s]?\d{7}[-\s]?\d\b")
_UAE_PHONE = re.compile(
    r"(?:\+|00)971[\s-]?\d{1,2}[\s-]?\d{3}[\s-]?\d{4}\b|\b05\d[\s-]?\d{3}[\s-]?\d{4}\b"
)
_INTL_PHONE = re.compile(r"\+\d[\d\s-]{8,16}\d")
_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _cards(text: str) -> list[re.Match]:
    out = []
    for m in _CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            out.append(m)
    return out


def contains_pii(text: str) -> bool:
    return bool(
        _EMAIL.search(text)
        or _EMIRATES_ID.search(text)
        or _UAE_PHONE.search(text)
        or _INTL_PHONE.search(text)
        or _cards(text)
    )


def classify(text: str, flagged_sensitive: bool) -> str:
    """Return SENSITIVE if the caller flagged it OR the detector fires. Fails closed."""
    if flagged_sensitive:
        return SENSITIVE
    try:
        return SENSITIVE if contains_pii(text) else NON_SENSITIVE
    except Exception:  # noqa: BLE001 - any detector failure must fail closed
        return SENSITIVE


def mask(text: str) -> str:
    """Replace detectable personal data with placeholders before anything is stored."""
    for m in sorted(_cards(text), key=lambda m: m.start(), reverse=True):
        text = text[: m.start()] + "[CARD]" + text[m.end() :]
    text = _EMAIL.sub("[EMAIL]", text)
    text = _EMIRATES_ID.sub("[EMIRATES_ID]", text)
    text = _UAE_PHONE.sub("[PHONE]", text)
    text = _INTL_PHONE.sub("[PHONE]", text)
    return text


def keyed_hash(text: str, key: str) -> str:
    """HMAC-SHA256: pseudonymous, and short prompts cannot be guessed without the key."""
    return hmac.new(key.encode(), text.encode(), hashlib.sha256).hexdigest()
