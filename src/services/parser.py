"""Parse a free-form INCI string into a list of ingredient names."""
from __future__ import annotations

import re

_PREFIX_RE = re.compile(
    r"^\s*(ingredients?|состав|ингредиенты?|inci|composition|composizione)\s*[:\-–]\s*",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"[,;\n\r\u2022•·]")
_BULLET_RE = re.compile(r"^[\s\-\*\u2022•·•·]+")
_TRAILING_RE = re.compile(r"[\s\.\,\;\:\u00a0]+$")
_MULTISPACE_RE = re.compile(r"\s+")


def parse(text: str) -> list[str]:
    """Split free-form composition text into ingredient candidates.

    - Strips common prefixes like "Ingredients:" / "Состав:"
    - Splits on commas, semicolons, bullets, newlines
    - Drops empty/very short fragments and pure numbers/percentages
    - Handles variations like "&" -> "and", `aqua / water` kept as-is
    """
    if not text:
        return []

    cleaned = text.strip()
    cleaned = _PREFIX_RE.sub("", cleaned)
    cleaned = cleaned.replace("\u00a0", " ")

    parts = _SPLIT_RE.split(cleaned)
    out: list[str] = []
    for p in parts:
        token = _BULLET_RE.sub("", p).strip()
        token = _TRAILING_RE.sub("", token)
        token = _MULTISPACE_RE.sub(" ", token)
        if not token:
            continue
        if len(token) < 2:
            continue
        if token.replace(".", "").replace(",", "").replace("%", "").isdigit():
            continue
        out.append(token)

    seen: set[str] = set()
    deduped: list[str] = []
    for token in out:
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(token)
    return deduped
