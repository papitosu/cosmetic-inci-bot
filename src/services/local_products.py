"""Local Sephora-product catalogue.

Source data: ``data/products.json`` built from
``data/sephora_products.csv`` (Vazquez/Cosmetic-Price-Analysis, MIT).
1 100+ products across 5 categories, with the original INCI list
preserved verbatim.

The catalogue is meant to give an *instant* answer for popular query
shapes ("la roche-posay toleriane", "ordinary niacinamide", "creme de
la mer") before falling back to the Open Beauty Facts network search.

Indexing is intentionally simple but fast at our scale (~1 K rows):

* normalize brand / name / category once at load time into a flat
  ``haystack`` string per product;
* split each haystack into tokens; the first 4 letters of the token
  is the bigram-ish bucket key;
* a query is normalized and split the same way; we union the buckets
  for every query-token's prefix and only score that subset.

The scorer is the actual filter for noisy queries: rather than keeping
an explicit stop-word list, it requires either a full-query substring
match, two matched tokens, or one solid (>= 5-char) match. That keeps
queries like ``"the qzqxzx"`` from latching onto every product that
happens to contain ``"the"``.

The result type imitates ``OpenBeautyFactsProduct``'s public surface
(``brand_and_name``, ``primary_category``, ``ingredients_text``, …) so
the existing handlers and analog finder don't need to special-case
the source.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from src.core.config import DATA_DIR

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_PREFIX_LEN = 4

CATEGORY_TO_OBF = {
    "Moisturizer": "en:moisturizers",
    "Cleanser": "en:cleansers",
    "Face Mask": "en:facial-masks",
    "Treatment": "en:facial-treatments",
    "Eye cream": "en:eye-creams",
}


@dataclass
class LocalProduct:
    """A Sephora product entry from the local catalogue.

    Field shape is intentionally compatible with
    :class:`src.services.product_search.OpenBeautyFactsProduct` so that
    handlers can store either source in the same FSM state cache.
    """

    brand: str
    name: str
    category: str
    ingredients_text: str
    code: str | None = None
    image_url: str | None = None
    categories_tags: list[str] = field(default_factory=list)

    @property
    def product_name(self) -> str:
        return self.name

    @property
    def brands(self) -> str:
        return self.brand

    def brand_and_name(self) -> str:
        return f"{self.brand} — {self.name}"

    def primary_category(self) -> str | None:
        """Most specific OBF-shaped category tag.

        Mirrors :meth:`OpenBeautyFactsProduct.primary_category` so the
        analog finder treats local and OBF sources identically: prefer
        the last ``en:`` tag, fall back to the last tag, then ``None``.
        Today our local entries carry exactly one tag — but the fallback
        keeps the contract correct if we ever index sub-categories.
        """
        for tag in reversed(self.categories_tags):
            if tag.startswith("en:") and len(tag) > 3:
                return tag
        return self.categories_tags[-1] if self.categories_tags else None


def _normalize(text: str) -> str:
    return text.lower().strip()


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(_normalize(text))


def _key(token: str) -> str:
    return token[:_PREFIX_LEN]


class LocalProductsCatalog:
    def __init__(self) -> None:
        self._products: list[LocalProduct] = []
        self._haystacks: list[str] = []
        self._tokens: list[set[str]] = []
        self._index: dict[str, list[int]] = {}
        self._loaded = False
        self._lock = threading.Lock()
        self._meta: dict = {}

    def load(self, data_dir: Path | None = None) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            base = data_dir or DATA_DIR
            path = base / "products.json"
            if not path.exists():
                log.warning("LocalProductsCatalog: %s not found", path)
                self._loaded = True
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            self._meta = data.get("_meta") or {}
            for entry in data.get("products") or []:
                brand = entry.get("brand") or ""
                name = entry.get("name") or ""
                category = entry.get("category") or ""
                ingredients_text = entry.get("ingredients_text") or ""
                if not (brand and name and ingredients_text):
                    continue
                obf_tag = CATEGORY_TO_OBF.get(category)
                product = LocalProduct(
                    brand=brand,
                    name=name,
                    category=category,
                    ingredients_text=ingredients_text,
                    categories_tags=[obf_tag] if obf_tag else [],
                )
                idx = len(self._products)
                self._products.append(product)
                hay = f"{brand} {name} {category}"
                hay_norm = _normalize(hay)
                self._haystacks.append(hay_norm)
                tokens = set(_tokens(hay))
                self._tokens.append(tokens)
                for tok in tokens:
                    self._index.setdefault(_key(tok), []).append(idx)
            self._loaded = True
            log.info("LocalProductsCatalog: loaded %d products", len(self._products))

    @property
    def size(self) -> int:
        return len(self._products)

    @property
    def meta(self) -> dict:
        return dict(self._meta)

    def search(self, query: str, limit: int = 8) -> list[LocalProduct]:
        if not self._loaded:
            self.load()
        if not self._products:
            return []
        q_norm = _normalize(query)
        if len(q_norm) < 3:
            return []
        q_tokens = _tokens(q_norm)
        if not q_tokens:
            return []

        candidate_ids: set[int] = set()
        for tok in q_tokens:
            bucket = self._index.get(_key(tok))
            if bucket:
                candidate_ids.update(bucket)
        if not candidate_ids:
            return []

        scored: list[tuple[int, int]] = []
        for idx in candidate_ids:
            score = self._score(q_norm, q_tokens, idx)
            if score <= 0:
                continue
            scored.append((idx, score))

        scored.sort(key=lambda x: (-x[1], self._haystacks[x[0]]))
        return [self._products[idx] for idx, _ in scored[:limit]]

    def _score(self, q_norm: str, q_tokens: list[str], idx: int) -> int:
        haystack = self._haystacks[idx]
        product_tokens = self._tokens[idx]

        # Substring on the full haystack is the strongest signal — handles
        # queries like "creme de la mer" against "la mer crème de la mer".
        full_substring = q_norm in haystack
        score = 100 if full_substring else 0

        matched_tokens: list[str] = []
        for q in q_tokens:
            if q in product_tokens:
                matched_tokens.append(q)
                score += 10
                continue
            # Allow prefix match (e.g. "ordinary" hits "ordinaries"
            # — rare in this dataset but cheap to support).
            if len(q) >= 3:
                for tok in product_tokens:
                    if tok.startswith(q):
                        matched_tokens.append(q)
                        score += 4
                        break

        meaningful = [t for t in q_tokens if len(t) >= 3]
        if not matched_tokens:
            return 0
        # With multi-word queries, a single short token like "the" or "la"
        # is not enough — require either the full query as a substring,
        # at least two matched tokens, or one solid (>= 5-char) match.
        if len(meaningful) >= 2 and not full_substring:
            solid_match = any(len(t) >= 5 for t in matched_tokens)
            if len(matched_tokens) < 2 and not solid_match:
                return 0
        return score


_catalog_singleton: LocalProductsCatalog | None = None


def get_catalog() -> LocalProductsCatalog:
    global _catalog_singleton
    if _catalog_singleton is None:
        _catalog_singleton = LocalProductsCatalog()
        _catalog_singleton.load()
    return _catalog_singleton
