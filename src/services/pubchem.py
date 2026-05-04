"""PubChem REST API client (Redis-cached).

PubChem is the NIH public chemistry database. The PUG REST endpoint is
free, requires no API key, and is rate-limited at ~5 requests/second
for public clients. We cache responses in Redis for 7 days.

Useful for cosmetics analysis:
    * XLogP — predicts how easily an ingredient penetrates the lipid skin
      barrier. High XLogP (>3) often correlates with comedogenicity.
    * Molecular weight — large molecules (>500 Da) generally cannot
      penetrate intact stratum corneum (the "500 Dalton rule").
    * IUPAC name — chemically unambiguous identifier for cross-referencing.

Docs: https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx
from redis import asyncio as aioredis

log = logging.getLogger(__name__)

BASE_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
USER_AGENT = "inci-bot/0.1 (+https://github.com/) (educational use)"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

PROPERTIES = (
    "MolecularWeight,IUPACName,XLogP,HBondDonorCount,HBondAcceptorCount"
)


@dataclass
class PubChemInfo:
    cid: str
    iupac_name: str | None
    molecular_weight: float | None
    xlogp: float | None
    h_bond_donor_count: int | None
    h_bond_acceptor_count: int | None
    raw: dict


class PubChemClient:
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

    async def __aenter__(self) -> "PubChemClient":
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
            raise RuntimeError("Use PubChemClient as async context manager")
        return self._client

    async def fetch(self, cid: str) -> PubChemInfo | None:
        if not cid:
            return None
        cache_key = f"pubchem:{cid}"
        if self._redis is not None:
            try:
                cached = await self._redis.get(cache_key)
            except Exception:
                cached = None
            if cached:
                try:
                    data = json.loads(cached)
                    return self._parse(cid, data)
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass

        url = f"/compound/cid/{cid}/property/{PROPERTIES}/JSON"
        try:
            r = await self.client.get(url)
        except httpx.HTTPError:
            log.exception("PubChem fetch failed for cid=%s", cid)
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
                log.exception("PubChem cache set failed")

        return self._parse(cid, data)

    @staticmethod
    def _parse(cid: str, raw: dict) -> PubChemInfo | None:
        try:
            props = raw["PropertyTable"]["Properties"][0]
        except (KeyError, IndexError, TypeError):
            return None

        def _f(key: str) -> float | None:
            v = props.get(key)
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        def _i(key: str) -> int | None:
            v = props.get(key)
            try:
                return int(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        return PubChemInfo(
            cid=cid,
            iupac_name=props.get("IUPACName"),
            molecular_weight=_f("MolecularWeight"),
            xlogp=_f("XLogP"),
            h_bond_donor_count=_i("HBondDonorCount"),
            h_bond_acceptor_count=_i("HBondAcceptorCount"),
            raw=props,
        )
