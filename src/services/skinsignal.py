"""skinsignal.ru ingredient page scraper (Redis-cached, polite).

skinsignal.ru is a public free Russian cosmetics analyzer. Their
robots.txt explicitly allows crawling /ingredients/* and /products/*
(only `/admin/`, `/user/`, `/users/`, `/api/` are disallowed). The site
publishes no ToS that restricts redistribution, so this client treats
the data as public.

We are still polite:
    * a global concurrency cap of 1 in-flight request,
    * a 1-second floor between requests,
    * a self-identifying User-Agent,
    * Redis-cached responses (success: 7d, 404: 30d),
    * the source is opt-in via SKINSIGNAL_ENABLED.

Useful enrichment fields:
    * Russian translation of the ingredient name ("Никотинамид"),
    * Numeric comedogenicity (0–5, complements Fulton overlay),
    * Functional roles (e.g. skin-conditioning, hair-conditioning),
    * Trait tags (e.g. "борется с акне", "успокаивает кожу").
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field

import httpx
from bs4 import BeautifulSoup
from redis import asyncio as aioredis

log = logging.getLogger(__name__)

BASE_URL = "https://skinsignal.ru"
USER_AGENT = (
    "inci-bot/0.2 (+https://github.com/; respectful scraper, "
    "obeys robots.txt; contact via repo issues)"
)
SUCCESS_TTL_SECONDS = 7 * 24 * 60 * 60
NOT_FOUND_TTL_SECONDS = 30 * 24 * 60 * 60
MIN_INTERVAL_SECONDS = 1.0


@dataclass
class SkinsignalInfo:
    slug: str
    name: str | None = None
    russian_name: str | None = None
    comedogenicity: int | None = None
    roles: list[str] = field(default_factory=list)
    traits: list[str] = field(default_factory=list)


def slug_for(name: str) -> str:
    """Turn an INCI canonical name into a skinsignal URL slug.

    Lowercase, drop diacritics not present in INCI names, replace any
    non-alphanumeric run with a single dash, trim dashes from edges.
    """
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


class SkinsignalClient:
    """Polite scraper with global rate limit and Redis cache.

    Use as an async context manager. Concurrent `fetch` calls share the
    single-slot semaphore so the source never sees more than one
    in-flight request from us.
    """

    _global_lock = asyncio.Lock()
    _last_request_at = 0.0

    def __init__(
        self,
        redis_url: str | None,
        base_url: str = BASE_URL,
        timeout: float = 8.0,
    ) -> None:
        self._redis_url = redis_url
        self._base_url = base_url
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._redis: aioredis.Redis | None = None

    async def __aenter__(self) -> "SkinsignalClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru,en;q=0.8",
            },
            follow_redirects=True,
        )
        if self._redis_url:
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._redis is not None:
            await self._redis.close()
            self._redis = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Use SkinsignalClient as async context manager")
        return self._client

    async def fetch(self, name: str) -> SkinsignalInfo | None:
        slug = slug_for(name)
        if not slug:
            return None

        cache_key = f"skinsignal:{slug}"
        if self._redis is not None:
            try:
                cached = await self._redis.get(cache_key)
            except Exception:
                cached = None
            if cached:
                if cached == "404":
                    return None
                try:
                    payload = json.loads(cached)
                    return SkinsignalInfo(**payload)
                except (json.JSONDecodeError, TypeError):
                    pass

        async with SkinsignalClient._global_lock:
            elapsed = time.monotonic() - SkinsignalClient._last_request_at
            if elapsed < MIN_INTERVAL_SECONDS:
                await asyncio.sleep(MIN_INTERVAL_SECONDS - elapsed)
            try:
                r = await self.client.get(f"/ingredients/{slug}")
            except httpx.HTTPError:
                log.exception("skinsignal fetch failed for %s", slug)
                return None
            finally:
                SkinsignalClient._last_request_at = time.monotonic()

        if r.status_code == 404:
            await self._cache_set(cache_key, "404", NOT_FOUND_TTL_SECONDS)
            return None
        if r.status_code != 200:
            log.warning("skinsignal non-200 %s for %s", r.status_code, slug)
            return None

        info = parse_ingredient_page(slug, r.text)
        if info is None:
            return None
        await self._cache_set(
            cache_key, json.dumps(asdict(info)), SUCCESS_TTL_SECONDS
        )
        return info

    async def _cache_set(self, key: str, value: str, ttl: int) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(key, value, ex=ttl)
        except Exception:
            log.exception("skinsignal cache set failed")


_LABEL_RU = {
    "переводится как": "russian_name",
    "комедогенность": "comedogenicity",
    "роль": "roles",
    "роли": "roles",
    "характеристики": "traits",
}


def parse_ingredient_page(slug: str, html: str) -> SkinsignalInfo | None:
    """Parse a /ingredients/{slug} page; tolerant to layout drift."""
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    name: str | None = None
    if h1:
        text = h1.get_text(" ", strip=True)
        name = re.sub(r"\s*в\s+косметике\s*$", "", text, flags=re.IGNORECASE).strip() or None

    info = SkinsignalInfo(slug=slug, name=name)

    ul = soup.find("ul", class_="list")
    if ul is None:
        return info if info.name else None

    for li in ul.find_all("li"):
        bold = li.find("b")
        if not bold:
            continue
        label = bold.get_text(strip=True).rstrip(":").strip().lower()
        field = _LABEL_RU.get(label)
        if field is None:
            continue

        if field == "roles":
            info.roles = [
                a.get_text(strip=True)
                for a in li.find_all("a", class_="role")
                if a.get_text(strip=True)
            ]
        elif field == "traits":
            info.traits = [
                s.get_text(strip=True)
                for s in li.find_all("span", class_="trait")
                if s.get_text(strip=True)
            ]
        elif field == "russian_name":
            bold.extract()
            text = li.get_text(" ", strip=True)
            info.russian_name = text or None
        elif field == "comedogenicity":
            bold.extract()
            for span in li.find_all("span"):
                span.extract()
            text = li.get_text(strip=True)
            m = re.search(r"-?\d+", text)
            if m:
                try:
                    info.comedogenicity = int(m.group())
                except ValueError:
                    pass

    return info
