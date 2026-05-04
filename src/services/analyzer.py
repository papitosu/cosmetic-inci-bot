"""Analyze a list of ingredients and produce a structured AnalysisResult.

The score is computed with positional weighting (LOI rule) and
skin-type-aware multipliers. After scoring, results can be enriched
with EU CosIng (regulatory) and PubChem (chemistry) data.
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import asdict, dataclass, field
from typing import Any

from src.core.enums import SkinType
from src.services.ingredients_db import (
    IngredientMatch,
    IngredientsDB,
    get_db,
)


@dataclass
class IngredientAnalysis:
    position: int
    raw: str
    canonical: str | None
    matched_via: str
    confidence: float
    cosing_id: str | None = None
    pubchem_cid: str | None = None
    flags: dict[str, Any] = field(default_factory=dict)
    cosing: dict[str, Any] | None = None
    pubchem: dict[str, Any] | None = None
    skinsignal: dict[str, Any] | None = None

    @classmethod
    def from_match(cls, position: int, m: IngredientMatch) -> "IngredientAnalysis":
        flags_dict: dict[str, Any] = {}
        f = m.flags
        if f.comedogenic_rating is not None:
            flags_dict["comedogenic_rating"] = f.comedogenic_rating
        if f.is_allergen:
            flags_dict["allergen"] = {
                "severity": f.allergen_severity,
                "note": f.allergen_note,
            }
        if f.is_irritant:
            flags_dict["irritant"] = {
                "severity": f.irritant_severity,
                "category": f.irritant_category,
                "note": f.irritant_note,
            }
        if f.benefit_category:
            flags_dict["beneficial"] = {
                "category": f.benefit_category,
                "tags": f.benefit_tags,
                "note": f.benefit_note,
            }
        if f.regulatory_annexes or f.regulatory_cmr or f.regulatory_details:
            flags_dict["regulatory"] = {
                "annexes": list(f.regulatory_annexes),
                "refs": list(f.regulatory_refs),
                "cmr": list(f.regulatory_cmr),
                "details": [dict(d) for d in f.regulatory_details],
            }
        if f.functions:
            flags_dict["functions"] = list(f.functions)
        return cls(
            position=position,
            raw=m.raw,
            canonical=m.canonical,
            matched_via=m.matched_via,
            confidence=round(m.confidence, 1),
            cosing_id=m.record.cosing_id if m.record else None,
            pubchem_cid=m.record.pubchem_cid if m.record else None,
            flags=flags_dict,
        )


@dataclass
class AnalysisResult:
    ingredients: list[IngredientAnalysis]
    comedogenic: list[IngredientAnalysis]
    allergens: list[IngredientAnalysis]
    irritants: list[IngredientAnalysis]
    beneficial: list[IngredientAnalysis]
    unknown: list[IngredientAnalysis]
    prohibited: list[IngredientAnalysis]
    restricted: list[IngredientAnalysis]
    risk_score: float
    verdict: str
    skin_type: SkinType
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ingredients": [asdict(i) for i in self.ingredients],
            "comedogenic": [asdict(i) for i in self.comedogenic],
            "allergens": [asdict(i) for i in self.allergens],
            "irritants": [asdict(i) for i in self.irritants],
            "beneficial": [asdict(i) for i in self.beneficial],
            "unknown": [asdict(i) for i in self.unknown],
            "prohibited": [asdict(i) for i in self.prohibited],
            "restricted": [asdict(i) for i in self.restricted],
            "risk_score": self.risk_score,
            "verdict": self.verdict,
            "skin_type": self.skin_type.value,
            "summary": self.summary,
        }


VERDICTS_RU = {
    "low": "Низкий риск",
    "medium": "Средний риск",
    "high": "Высокий риск",
}


def position_weight(index: int) -> float:
    """Exponential decay — top of the LOI matters most. weight(0) ~= 1.0, weight(8) ~= 0.37."""
    return math.exp(-index / 8)


def severity_score(severity: str | None) -> float:
    return {"low": 1.5, "medium": 3.0, "high": 4.5}.get(severity or "", 0.0)


def skin_multipliers(skin_type: SkinType) -> dict[str, float]:
    """Returns multipliers applied to per-category contributions."""
    base = {"comedogenic": 1.0, "allergen": 1.0, "irritant": 1.0}
    if skin_type in (SkinType.ACNE_PRONE, SkinType.OILY):
        base["comedogenic"] = 2.0
    if skin_type == SkinType.SENSITIVE:
        base["allergen"] = 1.5
        base["irritant"] = 1.5
    if skin_type == SkinType.DRY:
        base["irritant"] = 1.3
    return base


def _is_prohibited(flags: dict[str, Any]) -> bool:
    reg = flags.get("regulatory") or {}
    return "II" in (reg.get("annexes") or [])


def _is_restricted(flags: dict[str, Any]) -> bool:
    reg = flags.get("regulatory") or {}
    return "III" in (reg.get("annexes") or [])


def analyze_matches(
    matches: list[IngredientMatch],
    skin_type: SkinType = SkinType.UNKNOWN,
) -> AnalysisResult:
    items = [IngredientAnalysis.from_match(i, m) for i, m in enumerate(matches)]

    comedogenic = [i for i in items if "comedogenic_rating" in i.flags and i.flags["comedogenic_rating"] >= 2]
    allergens = [i for i in items if "allergen" in i.flags]
    irritants = [i for i in items if "irritant" in i.flags]
    beneficial = [i for i in items if "beneficial" in i.flags]
    unknown = [i for i in items if i.canonical is None]
    prohibited = [i for i in items if _is_prohibited(i.flags)]
    restricted = [i for i in items if _is_restricted(i.flags) and not _is_prohibited(i.flags)]

    mults = skin_multipliers(skin_type)
    weighted_risk = 0.0
    weight_sum = 0.0
    benefit_sum = 0.0
    regulatory_penalty = 0.0
    for i in items:
        w = position_weight(i.position)
        weight_sum += w

        com = i.flags.get("comedogenic_rating")
        if com is not None:
            weighted_risk += w * (com * mults["comedogenic"])

        al = i.flags.get("allergen")
        if al is not None:
            weighted_risk += w * (severity_score(al.get("severity")) * mults["allergen"])

        ir = i.flags.get("irritant")
        if ir is not None:
            weighted_risk += w * (severity_score(ir.get("severity")) * mults["irritant"])

        if i.flags.get("beneficial"):
            benefit_sum += w * 1.5

        # Regulatory penalty (EU 1223/2009). Applied as flat additive points
        # that are independent of weight_sum normalization, because finding a
        # prohibited substance is a categorical red flag — its severity
        # should not be diluted by the rest of the LOI.
        reg = i.flags.get("regulatory") or {}
        annexes = reg.get("annexes") or []
        cmr = reg.get("cmr") or []
        if "II" in annexes:
            regulatory_penalty += 35.0
        elif "III" in annexes:
            regulatory_penalty += 8.0
        if cmr and "II" not in annexes:
            # CMR1A/1B/2 substance not yet in Annex II is still a serious flag
            grade = cmr[0].upper()
            regulatory_penalty += 18.0 if grade in ("1A", "1B") else 8.0

    if weight_sum > 0:
        normalized_risk = (weighted_risk / weight_sum) * 14.0
        normalized_risk = max(0.0, normalized_risk - benefit_sum / max(weight_sum, 1.0) * 5.0)
    else:
        normalized_risk = 0.0

    normalized_risk += regulatory_penalty
    risk_score = round(min(100.0, normalized_risk), 1)

    # Categorical override: presence of an EU-banned substance (Annex II) or
    # CMR1A/1B is a red flag on its own — it should never come back as "low".
    has_severe_cmr = any(
        any(g in ("1A", "1B") for g in (i.flags.get("regulatory") or {}).get("cmr") or [])
        for i in items
    )
    if prohibited or has_severe_cmr:
        verdict = "high"
    elif risk_score < 25:
        verdict = "low"
    elif risk_score < 55:
        verdict = "medium"
    else:
        verdict = "high"

    summary = _build_summary(
        verdict, skin_type, comedogenic, allergens, irritants, beneficial, prohibited, restricted
    )

    return AnalysisResult(
        ingredients=items,
        comedogenic=comedogenic,
        allergens=allergens,
        irritants=irritants,
        beneficial=beneficial,
        unknown=unknown,
        prohibited=prohibited,
        restricted=restricted,
        risk_score=risk_score,
        verdict=verdict,
        skin_type=skin_type,
        summary=summary,
    )


def _build_summary(
    verdict: str,
    skin_type: SkinType,
    comedogenic: list[IngredientAnalysis],
    allergens: list[IngredientAnalysis],
    irritants: list[IngredientAnalysis],
    beneficial: list[IngredientAnalysis],
    prohibited: list[IngredientAnalysis] | None = None,
    restricted: list[IngredientAnalysis] | None = None,
) -> str:
    base = VERDICTS_RU[verdict]
    # Regulatory red flags trump skin-type framing — surface them first.
    if prohibited:
        names = ", ".join(filter(None, (i.canonical or i.raw for i in prohibited[:2])))
        return f"{base} — найдены вещества из списка запрещённых ЕС (Annex II): {names}."
    if restricted and verdict != "low":
        names = ", ".join(filter(None, (i.canonical or i.raw for i in restricted[:2])))
        return f"{base} — есть ингредиенты с регуляторными ограничениями ЕС (Annex III): {names}."
    if skin_type == SkinType.ACNE_PRONE:
        if comedogenic:
            return f"{base} для кожи с акне — найдены комедогенные компоненты."
        return f"{base} для кожи с акне — комедогенов не обнаружено."
    if skin_type == SkinType.SENSITIVE:
        if allergens or irritants:
            return f"{base} для чувствительной кожи — есть потенциальные раздражители/аллергены."
        return f"{base} для чувствительной кожи — раздражителей не обнаружено."
    if skin_type == SkinType.OILY:
        if comedogenic:
            return f"{base} для жирной кожи — есть комедогены."
        return f"{base} для жирной кожи — комедогенов не обнаружено."
    if skin_type == SkinType.COMBINATION:
        if comedogenic and (allergens or irritants):
            return f"{base} для комбинированной кожи — есть и комедогены, и раздражители."
        if comedogenic:
            return f"{base} для комбинированной кожи — есть комедогены."
        return base
    if skin_type == SkinType.DRY and irritants:
        return f"{base} для сухой кожи — есть подсушивающие компоненты."
    return base


def analyze(
    raw_ingredients: list[str],
    skin_type: SkinType = SkinType.UNKNOWN,
    db: IngredientsDB | None = None,
) -> AnalysisResult:
    db = db or get_db()
    matches = [db.lookup(raw) for raw in raw_ingredients]
    return analyze_matches(matches, skin_type=skin_type)


async def analyze_full(
    raw_ingredients: list[str],
    skin_type: SkinType,
    *,
    redis_url: str | None = None,
    db: IngredientsDB | None = None,
) -> AnalysisResult:
    """Full analysis pipeline (free for all users).

    1) CPU-bound `analyze` runs in a worker thread (parser + fuzzy lookup
       over 28K names).
    2) Top items are enriched with EU CosIng (CMR / annexes) and PubChem
       (XLogP / molecular weight) in parallel. Both sources are public,
       free, and Redis-cached for 7 days.

    Both enrichments are best-effort: any network failure is swallowed
    so the user still sees a usable analysis.
    """
    result = await asyncio.to_thread(analyze, raw_ingredients, skin_type, db)
    if not result.ingredients:
        return result

    tasks = [
        asyncio.create_task(_safe(enrich_with_cosing, result, redis_url=redis_url)),
        asyncio.create_task(_safe(enrich_with_pubchem, result, redis_url=redis_url)),
    ]
    if _skinsignal_enabled():
        tasks.append(
            asyncio.create_task(_safe(enrich_with_skinsignal, result, redis_url=redis_url))
        )
    await asyncio.gather(*tasks)
    return result


def _skinsignal_enabled() -> bool:
    try:
        from src.core.config import get_settings

        return bool(get_settings().skinsignal_enabled)
    except Exception:
        return False


async def _safe(fn, *args, **kwargs) -> None:
    try:
        await fn(*args, **kwargs)
    except Exception:
        pass


async def enrich_with_cosing(
    result: AnalysisResult,
    *,
    redis_url: str | None,
    max_lookups: int = 6,
) -> AnalysisResult:
    """Augment top-N items with live CosIng data.

    The offline overlays cover most of what CosIng exposes (functions
    via the inventory snapshot, regulatory annexes / CMR flags via the
    same source), so the live API is only useful when the offline
    picture is incomplete — either functions or regulatory missing.
    That makes the API a safety-net for ingredients added to CosIng
    after our snapshot, not a hot path on every analysis.

    Anything the live API returns is merged back into ``item.flags``
    so the formatter has a single source of truth for both functions
    and regulatory data — independent of where they came from.
    """
    from src.services.cosing import CosingClient

    candidates = [
        item
        for item in result.ingredients[: max(max_lookups * 4, 24)]
        if item.cosing_id
        and not (item.flags.get("functions") and item.flags.get("regulatory"))
    ]
    targets = candidates[:max_lookups]
    if not targets:
        return result

    async with CosingClient(redis_url=redis_url) as client:
        for item in targets:
            try:
                info = await client.fetch(item.cosing_id)  # type: ignore[arg-type]
            except Exception:
                continue
            if info is None:
                continue
            item.cosing = {
                "functions": info.functions[:5],
                "annexes": info.annexes[:5],
                "cmr": info.cmr,
                "restrictions": info.restrictions,
            }
            if info.functions and not item.flags.get("functions"):
                item.flags["functions"] = [str(f).lower() for f in info.functions[:5]]
            if (info.annexes or info.cmr) and not item.flags.get("regulatory"):
                item.flags["regulatory"] = {
                    "annexes": list(info.annexes or []),
                    "refs": [],
                    "cmr": (["CMR"] if info.cmr else []),
                }
    return result


async def enrich_with_skinsignal(
    result: AnalysisResult,
    *,
    redis_url: str | None,
    max_lookups: int | None = None,
) -> AnalysisResult:
    """Pull translation / comedogenicity / traits from skinsignal.ru.

    Only canonical (matched) names are queried — unknown raw inputs
    would produce 404 storms. The source is rate-limited to 1 req/s
    globally, so we keep `max_lookups` small (default 6, configurable
    via SKINSIGNAL_MAX_LOOKUPS).
    """
    from src.core.config import get_settings
    from src.services.skinsignal import SkinsignalClient

    if max_lookups is None:
        max_lookups = get_settings().skinsignal_max_lookups
    targets = [
        item
        for item in result.ingredients[: max(max_lookups * 2, 12)]
        if item.canonical
    ][:max_lookups]
    if not targets:
        return result

    async with SkinsignalClient(redis_url=redis_url) as client:
        for item in targets:
            try:
                info = await client.fetch(item.canonical)  # type: ignore[arg-type]
            except Exception:
                continue
            if info is None:
                continue
            payload: dict[str, Any] = {}
            if info.russian_name:
                payload["russian_name"] = info.russian_name
            if info.comedogenicity is not None:
                payload["comedogenicity"] = info.comedogenicity
            if info.roles:
                payload["roles"] = info.roles[:5]
            if info.traits:
                payload["traits"] = info.traits[:8]
            if payload:
                item.skinsignal = payload
    return result


async def enrich_with_pubchem(
    result: AnalysisResult,
    *,
    redis_url: str | None,
    max_lookups: int = 12,
) -> AnalysisResult:
    """Augment top-N items with PubChem chemistry properties (XLogP, MW, IUPAC)."""
    from src.services.pubchem import PubChemClient

    targets = [
        item
        for item in result.ingredients[: max(max_lookups * 2, 16)]
        if item.pubchem_cid
    ][:max_lookups]
    if not targets:
        return result

    async with PubChemClient(redis_url=redis_url) as client:
        for item in targets:
            try:
                info = await client.fetch(item.pubchem_cid)  # type: ignore[arg-type]
            except Exception:
                continue
            if info is None:
                continue
            item.pubchem = {
                "iupac_name": info.iupac_name,
                "molecular_weight": info.molecular_weight,
                "xlogp": info.xlogp,
                "h_bond_donor_count": info.h_bond_donor_count,
                "h_bond_acceptor_count": info.h_bond_acceptor_count,
            }
    return result
