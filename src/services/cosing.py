"""CosIng Checker API client (Redis-cached).

CosIng Checker (cosingchecker.com/api/v1/) is an open mirror of the EU CosIng
database. We hit it only for Premium/Pro tiers, since data is enriched and
cached for a week per ingredient.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx
from redis import asyncio as aioredis

log = logging.getLogger(__name__)

BASE_URL = "https://cosingchecker.com/api/v1"
USER_AGENT = "inci-bot/0.1"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


@dataclass
class CosingInfo:
    cosing_id: str
    name: str | None
    inn_name: str | None
    iupac_name: str | None
    functions: list[str]
    annexes: list[str]
    cmr: bool
    restrictions: dict | None
    raw: dict


class CosingClient:
    def __init__(self, redis_url: str | None, base_url: str = BASE_URL, timeout: float = 8.0) -> None:
        self._redis_url = redis_url
        self._base_url = base_url
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._redis: aioredis.Redis | None = None

    async def __aenter__(self) -> "CosingClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
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
            raise RuntimeError("Use CosingClient as async context manager")
        return self._client

    async def fetch(self, cosing_id: str) -> CosingInfo | None:
        if not cosing_id:
            return None
        cache_key = f"cosing:{cosing_id}"
        if self._redis is not None:
            try:
                cached = await self._redis.get(cache_key)
            except Exception:
                cached = None
            if cached:
                try:
                    raw = json.loads(cached)
                    return self._parse(cosing_id, raw)
                except (json.JSONDecodeError, KeyError):
                    pass

        try:
            r = await self.client.get(f"/ingredient/{cosing_id}")
        except httpx.HTTPError:
            log.exception("CosIng fetch failed for %s", cosing_id)
            return None
        if r.status_code != 200:
            return None
        try:
            data = r.json()
        except ValueError:
            return None

        if self._redis is not None:
            try:
                await self._redis.set(cache_key, json.dumps(data), ex=CACHE_TTL_SECONDS)
            except Exception:
                log.exception("Cosing cache set failed")

        return self._parse(cosing_id, data)

    @staticmethod
    def _parse(cosing_id: str, raw: dict) -> CosingInfo:
        functions = raw.get("functions") or raw.get("function") or []
        if isinstance(functions, str):
            functions = [functions]
        annexes = raw.get("annexes") or raw.get("annex") or []
        if isinstance(annexes, str):
            annexes = [annexes]
        cmr = bool(raw.get("cmr") or raw.get("CMR"))
        restrictions = raw.get("restrictions") or raw.get("restriction")
        return CosingInfo(
            cosing_id=cosing_id,
            name=raw.get("inci_name") or raw.get("name"),
            inn_name=raw.get("inn_name"),
            iupac_name=raw.get("iupac_name") or raw.get("chem_name"),
            functions=list(functions),
            annexes=list(annexes),
            cmr=cmr,
            restrictions=restrictions if isinstance(restrictions, dict) else None,
            raw=raw,
        )
