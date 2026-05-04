"""Open Beauty Facts client.

Two endpoints, used for different jobs:

* ``/cgi/search.pl`` — legacy but reliable full-text and tag search.
  Used for the "search by name" feature; the v2 ``/api/v2/search``
  endpoint silently ignores ``search_terms`` in production and returns
  the same popularity-sorted slice for every query. Documented as
  beta/unreliable in OBF's own ref docs.

* ``/api/v2`` — used for ``categories_tags_en`` (analog finder) and
  ``/product/{barcode}`` lookups, which both work fine on v2.

Data licensed under the Open Database License (ODbL) — free to use,
share, and modify with attribution.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

BASE_HOST = "https://world.openbeautyfacts.org"
USER_AGENT = "inci-bot/0.1 (telegram bot)"
PRODUCT_FIELDS = (
    "code,product_name,brands,ingredients_text,image_front_url,image_url,"
    "categories_tags"
)


@dataclass
class OpenBeautyFactsProduct:
    code: str | None
    product_name: str | None
    brands: str | None
    ingredients_text: str | None
    image_url: str | None
    categories_tags: list[str] = field(default_factory=list)

    def brand_and_name(self) -> str:
        """Display label, guaranteed non-empty after stripping.

        OBF rows can carry whitespace-only or comma-only ``brands`` /
        ``product_name`` values. We want the join to skip those instead
        of producing a bare ``" — "`` that later renders as an empty
        button. The dash fallback stays as a last resort so callers
        can detect 'no useful label' via ``label == "—"``.
        """
        parts: list[str] = []
        if self.brands:
            head = self.brands.split(",", 1)[0].strip()
            if head:
                parts.append(head)
        if self.product_name:
            tail = self.product_name.strip()
            if tail and tail not in parts:
                parts.append(tail)
        if parts:
            return " — ".join(parts)
        return "—"

    def primary_category(self) -> str | None:
        """Pick the most specific (last) category tag with `en:` prefix.

        OBF orders categories from generic to specific, so the last entry
        is usually the most discriminating (e.g. `en:face-creams`).
        """
        for tag in reversed(self.categories_tags):
            if tag.startswith("en:") and len(tag) > 3:
                return tag
        return self.categories_tags[-1] if self.categories_tags else None


class OpenBeautyFactsClient:
    def __init__(self, base_host: str = BASE_HOST, timeout: float = 8.0) -> None:
        self._base_host = base_host
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "OpenBeautyFactsClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_host,
            timeout=self._timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Use OpenBeautyFactsClient as an async context manager")
        return self._client

    async def search(self, query: str, page_size: int = 10) -> list[OpenBeautyFactsProduct]:
        """Free-text search via the legacy CGI endpoint.

        The newer ``/api/v2/search`` ignores ``search_terms`` in
        practice (returns the same popularity slice for every query),
        so we use ``/cgi/search.pl`` which honours the parameter and is
        what the OBF web UI itself calls.
        """
        if not query.strip():
            return []
        params = {
            "search_terms": query,
            "search_simple": 1,
            "action": "process",
            "json": 1,
            "page_size": page_size,
            "fields": PRODUCT_FIELDS,
            "sort_by": "popularity",
        }
        try:
            r = await self.client.get("/cgi/search.pl", params=params)
        except httpx.HTTPError:
            log.exception("OpenBeautyFacts search failed")
            return []
        if r.status_code != 200:
            log.warning("OBF cgi/search non-200: %s", r.status_code)
            return []
        try:
            data = r.json()
        except ValueError:
            log.warning("OBF cgi/search returned non-JSON")
            return []
        products = data.get("products") or []
        return [self._parse(p) for p in products]

    async def search_by_category(
        self, category_tag: str, page_size: int = 30
    ) -> list[OpenBeautyFactsProduct]:
        """Pull popular products from the same OBF category (e.g. `en:face-creams`).

        Uses the v2 endpoint — category filtering works fine there and
        returns structured data, which is what the analog finder wants."""
        if not category_tag:
            return []
        params = {
            "categories_tags_en": category_tag.removeprefix("en:"),
            "page_size": page_size,
            "fields": PRODUCT_FIELDS,
            "sort_by": "popularity",
        }
        return await self._v2_search(params)

    async def get_by_barcode(self, barcode: str) -> OpenBeautyFactsProduct | None:
        params = {"fields": PRODUCT_FIELDS}
        try:
            r = await self.client.get(f"/api/v2/product/{barcode}", params=params)
        except httpx.HTTPError:
            log.exception("OpenBeautyFacts get_by_barcode failed")
            return None
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("status") != 1 or not data.get("product"):
            return None
        return self._parse(data["product"])

    async def _v2_search(self, params: dict) -> list[OpenBeautyFactsProduct]:
        try:
            r = await self.client.get("/api/v2/search", params=params)
        except httpx.HTTPError:
            log.exception("OpenBeautyFacts v2 search failed")
            return []
        if r.status_code != 200:
            log.warning("OBF v2 non-200: %s", r.status_code)
            return []
        try:
            data = r.json()
        except ValueError:
            log.warning("OBF v2 returned non-JSON")
            return []
        products = data.get("products") or []
        return [self._parse(p) for p in products]

    @staticmethod
    def _parse(p: dict) -> OpenBeautyFactsProduct:
        cats = p.get("categories_tags") or []
        if not isinstance(cats, list):
            cats = []
        return OpenBeautyFactsProduct(
            code=str(p.get("code") or "") or None,
            product_name=p.get("product_name") or None,
            brands=p.get("brands") or None,
            ingredients_text=p.get("ingredients_text") or None,
            image_url=p.get("image_front_url") or p.get("image_url") or None,
            categories_tags=[str(t) for t in cats],
        )
