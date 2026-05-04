from __future__ import annotations

import pytest

from src.core.enums import SkinType
from src.services.analyzer import analyze
from src.services.ingredients_db import get_db


@pytest.fixture(scope="module", autouse=True)
def _ensure_db_loaded() -> None:
    get_db().load()


def test_analyzer_finds_comedogenic_coconut_oil() -> None:
    result = analyze(["Water", "Glycerin", "Coconut Oil", "Niacinamide"])
    canonicals = {i.canonical for i in result.ingredients}
    assert "cocos nucifera oil" in canonicals
    assert any(c.canonical == "cocos nucifera oil" for c in result.comedogenic)


def test_analyzer_flags_parfum_as_irritant_and_allergen() -> None:
    result = analyze(["Water", "Glycerin", "Parfum"])
    irritant_canonicals = {i.canonical for i in result.irritants}
    allergen_canonicals = {i.canonical for i in result.allergens}
    assert "fragrance" in irritant_canonicals
    assert "fragrance" in allergen_canonicals


def test_analyzer_finds_niacinamide_as_beneficial() -> None:
    result = analyze(["Water", "Niacinamide"])
    beneficial_canonicals = {i.canonical for i in result.beneficial}
    assert "niacinamide" in beneficial_canonicals


def test_analyzer_acne_prone_amplifies_comedogenic_score() -> None:
    formula = ["Water", "Coconut Oil", "Isopropyl Myristate", "Niacinamide"]
    base = analyze(formula, skin_type=SkinType.NORMAL)
    acne = analyze(formula, skin_type=SkinType.ACNE_PRONE)
    assert acne.risk_score > base.risk_score


def test_analyzer_low_risk_simple_humectants() -> None:
    result = analyze(["Water", "Glycerin", "Hyaluronic Acid", "Niacinamide", "Panthenol"])
    assert result.verdict in ("low", "medium")
    assert result.risk_score < 30.0


def test_analyzer_handles_synonyms() -> None:
    result = analyze(["Water", "Vitamin C", "Vitamin E"])
    canonicals = {i.canonical for i in result.ingredients}
    assert "ascorbic acid" in canonicals
    assert "tocopherol" in canonicals


def test_analyzer_handles_russian_synonym() -> None:
    result = analyze(["Вода", "Ниацинамид", "Глицерин"])
    canonicals = {i.canonical for i in result.ingredients}
    assert "water" in canonicals
    assert "niacinamide" in canonicals
    assert "glycerin" in canonicals


def test_analyzer_to_dict_serialisation() -> None:
    result = analyze(["Water", "Coconut Oil"])
    payload = result.to_dict()
    assert "risk_score" in payload
    assert "ingredients" in payload
    assert isinstance(payload["ingredients"], list)


def test_analyzer_unknown_ingredients_are_collected() -> None:
    result = analyze(["XyzNotARealIngredient12345"])
    assert len(result.unknown) == 1


def test_analyzer_position_weight() -> None:
    """Coconut oil at position 1 (high) should be riskier than at position 20."""
    formula_top = ["Coconut Oil"] + [f"Water"] * 19
    formula_bottom = [f"Water"] * 19 + ["Coconut Oil"]
    top = analyze(formula_top)
    bottom = analyze(formula_bottom)
    assert top.risk_score > bottom.risk_score


def test_analyzer_flags_eu_prohibited_substance() -> None:
    """Hydroquinone is in Annex II (banned in cosmetics under 1223/2009)."""
    result = analyze(["Water", "Glycerin", "Hydroquinone"])
    prohibited_canonicals = {i.canonical for i in result.prohibited}
    assert "hydroquinone" in prohibited_canonicals
    item = next(i for i in result.prohibited if i.canonical == "hydroquinone")
    reg = item.flags.get("regulatory") or {}
    assert "II" in reg.get("annexes", [])


def test_analyzer_prohibited_substance_raises_risk_score() -> None:
    """Annex II substance must visibly bump the risk score over the same formula
    without it (we expect at least +30 points of pure regulatory penalty)."""
    clean = analyze(["Water", "Glycerin", "Niacinamide"])
    tainted = analyze(["Water", "Glycerin", "Niacinamide", "Hydroquinone"])
    assert tainted.risk_score >= clean.risk_score + 25.0
    assert tainted.verdict == "high"


def test_analyzer_summary_mentions_prohibited() -> None:
    result = analyze(["Water", "Hydroquinone"])
    assert "Annex II" in result.summary or "запрещ" in result.summary.lower()


def test_analyzer_offline_functions_attached_to_glycerin() -> None:
    """CosIng inventory ships per-INCI function tags. Glycerin is in there."""
    result = analyze(["Water", "Glycerin"])
    glycerin = next(i for i in result.ingredients if i.canonical == "glycerin")
    funcs = glycerin.flags.get("functions") or []
    assert funcs, "expected offline functions for glycerin"
    joined = " ".join(funcs).lower()
    assert "humectant" in joined or "skin" in joined


def test_analyzer_offline_functions_cover_top_of_loi() -> None:
    """We expect 99% inventory coverage, so a typical formula must have
    function metadata on the top of the LOI without any network call."""
    result = analyze([
        "Water",
        "Glycerin",
        "Niacinamide",
        "Caprylic/Capric Triglyceride",
        "Cetearyl Alcohol",
    ])
    with_funcs = [i for i in result.ingredients if i.flags.get("functions")]
    assert len(with_funcs) >= 4


def test_enrich_with_cosing_skips_when_offline_complete(monkeypatch) -> None:
    """If every targeted item already has both functions and regulatory
    data offline, we must not even open a CosIng client."""
    import asyncio

    from src.services import analyzer as az

    result = analyze(["Water", "Glycerin"])
    for item in result.ingredients:
        item.flags.setdefault("functions", ["humectant"])
        item.flags.setdefault("regulatory", {"annexes": [], "refs": [], "cmr": []})

    called = {"count": 0}

    class _Boom:
        async def __aenter__(self):
            called["count"] += 1
            raise AssertionError("CosingClient must not be entered when offline is complete")

        async def __aexit__(self, *args):
            return False

    def _fake_factory(*_args, **_kwargs):
        return _Boom()

    monkeypatch.setattr("src.services.cosing.CosingClient", _fake_factory)
    asyncio.run(az.enrich_with_cosing(result, redis_url=None))
    assert called["count"] == 0


def test_annex_details_salicylic_acid_carries_max_conc_per_product_type() -> None:
    """Salicylic Acid is in both Annex III (3.0% rinse-off / 2.0% other) and
    Annex V (0.5% as preservative). Our overlay must keep both records and
    expose the per-product-type max concentration."""
    result = analyze(["Salicylic Acid"])
    item = result.ingredients[0]
    reg = item.flags.get("regulatory") or {}
    details = reg.get("details") or []
    assert details, "expected Annex details on Salicylic Acid"
    annexes = {d.get("annex") for d in details}
    assert {"III", "V"}.issubset(annexes)
    annex_v = next(d for d in details if d.get("annex") == "V")
    assert "0.5%" in (annex_v.get("max_conc") or "")
    annex_iii_rinse = next(
        d for d in details
        if d.get("annex") == "III" and "rinse" in (d.get("product_type") or "").lower()
    )
    assert "3.0%" in (annex_iii_rinse.get("max_conc") or "")


def test_annex_details_benzoic_acid_three_substeps() -> None:
    """Benzoic Acid V/1 has three substeps (rinse-off / oral / leave-on)
    with three different max concentrations. Don't collapse them."""
    result = analyze(["Benzoic Acid"])
    item = result.ingredients[0]
    details = (item.flags.get("regulatory") or {}).get("details") or []
    pts = {d.get("product_type", "").lower() for d in details if d.get("annex") == "V"}
    assert any("rinse" in p for p in pts)
    assert any("leave-on" in p for p in pts)
    assert any("oral" in p for p in pts)


def test_annex_details_hydroquinone_keeps_prohibited_status() -> None:
    """Hydroquinone is II/1339 (banned) plus III/14 (artificial nail systems
    only, professional use). Verdict must stay 'high' on Annex II grounds,
    but the Annex III restriction should still appear in the details list."""
    result = analyze(["Water", "Hydroquinone"])
    item = next(i for i in result.ingredients if i.canonical == "hydroquinone")
    details = (item.flags.get("regulatory") or {}).get("details") or []
    annexes = {d.get("annex") for d in details}
    assert "III" in annexes  # Annex III/14 row
    assert result.verdict == "high"


def test_annex_details_format_lines_emit_max_conc() -> None:
    """The formatter must surface 'макс. 0.5%' for salicylic acid leave-on."""
    from src.bot.formatting import _detail_lines

    result = analyze(["Salicylic Acid"])
    item = result.ingredients[0]
    lines = _detail_lines(item, annex="V")
    assert lines, "expected at least one Annex V detail line"
    assert any("0.5%" in line for line in lines)


def test_annex_details_formaldehyde_carries_all_three_annexes() -> None:
    """Formaldehyde is in II (banned), III (5% nail hardener), V (0.1% oral).
    Verdict must stay 'high' on Annex II grounds, but membership across the
    three annexes must round-trip into flags."""
    result = analyze(["Water", "Formaldehyde"])
    item = next(i for i in result.ingredients if i.canonical == "formaldehyde")
    annexes = (item.flags.get("regulatory") or {}).get("annexes") or []
    assert {"II", "III", "V"}.issubset(set(annexes))
    assert result.verdict == "high"


def test_annex_details_prohibited_block_shows_exceptions() -> None:
    """Hydroquinone is banned (II) but Annex III/14 carves out artificial
    nail systems for professional use. The 'prohibited' block must surface
    that carve-out, otherwise the user only sees a bare ref."""
    from src.bot.formatting import format_analysis

    result = analyze(["Water", "Hydroquinone"])
    text = format_analysis(result)
    assert "Запрещены в ЕС" in text
    assert "Artificial nail systems" in text or "искусственн" in text.lower()


def test_annex_details_format_translates_common_warnings() -> None:
    """Russian phrases for the most frequent EU warnings should appear
    instead of raw English in the output."""
    from src.bot.formatting import _detail_lines

    result = analyze(["Salicylic Acid"])
    item = result.ingredients[0]
    lines = _detail_lines(item, annex="III")
    joined = " | ".join(lines).lower()
    assert "не использовать у детей до 3 лет" in joined
    assert "смываемые" in joined


def test_annex_details_restricted_block_shows_non_ii_substeps() -> None:
    """Salicylic Acid is in both Annex III and V. Routing it into the
    'restricted' block must not hide the Annex V leave-on cap (0.5%)."""
    from src.bot.formatting import format_analysis

    result = analyze(["Water", "Salicylic Acid"])
    text = format_analysis(result)
    assert "Ограничены в ЕС" in text
    assert "3.0%" in text  # Annex III rinse-off
    assert "0.5%" in text  # Annex V leave-on cap, easy to lose otherwise


def test_annex_details_truncate_respects_word_boundary() -> None:
    """Long detail lines are trimmed on a space, not in the middle of a word."""
    from src.bot.formatting import _truncate_words

    s = "макс. 0,4% (as acid) for single ester 0,8% (as acid) for mixtures of esters"
    out = _truncate_words(s, 40)
    assert out.endswith("…")
    body = out[:-1]
    assert s.startswith(body), "truncated body must be a prefix of the source"
    assert s[len(body)] == " ", "cut must happen at a space, not mid-word"
    assert len(out) <= 40


def test_build_annex_details_filters_pseudo_inci(tmp_path) -> None:
    """The (*) CMR marker in EU CSVs must not become an INCI key."""
    import json
    from pathlib import Path

    payload = json.loads(
        Path("data/annex_details.json").read_text(encoding="utf-8")
    )
    keys = list(payload.get("ingredients", {}).keys())
    assert not any(k.startswith("(*)") or k.startswith("(") for k in keys)
    # And we should still have substantive data after the filter.
    assert len(keys) > 2_500


def test_enrich_with_cosing_merges_live_into_flags(monkeypatch) -> None:
    """When live API returns functions and the offline overlay had none,
    the formatter should still see them via flags['functions']."""
    import asyncio

    from src.services import analyzer as az
    from src.services.cosing import CosingInfo

    result = analyze(["Hydroquinone"])
    target = result.ingredients[0]
    target.cosing_id = "ZZZ"
    target.flags.pop("functions", None)
    target.flags.pop("regulatory", None)

    class _StubClient:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def fetch(self, _cid):
            return CosingInfo(
                cosing_id="ZZZ",
                name="hydroquinone",
                inn_name=None,
                iupac_name=None,
                functions=["BLEACHING", "ANTIOXIDANT"],
                annexes=["II"],
                cmr=False,
                restrictions=None,
                raw={},
            )

    monkeypatch.setattr("src.services.cosing.CosingClient", _StubClient)
    asyncio.run(az.enrich_with_cosing(result, redis_url=None))
    assert target.flags.get("functions") == ["bleaching", "antioxidant"]
    assert target.cosing and target.cosing["annexes"] == ["II"]
