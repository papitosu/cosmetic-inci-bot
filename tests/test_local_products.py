from __future__ import annotations

from src.services.local_products import LocalProductsCatalog, get_catalog


def test_local_catalog_loads_with_meaningful_size() -> None:
    catalog = get_catalog()
    assert catalog.size >= 1000
    meta = catalog.meta
    assert meta.get("license") == "MIT"
    assert "VazquezJocelyn" in (meta.get("source_url") or "")


def test_local_catalog_finds_iconic_creme_de_la_mer() -> None:
    catalog = get_catalog()
    hits = catalog.search("creme de la mer", limit=5)
    assert hits, "expected at least one hit for La Mer"
    top = hits[0]
    assert top.brand.lower() == "la mer"
    assert "crème de la mer" in top.name.lower() or "creme de la mer" in top.name.lower()
    assert "algae" in top.ingredients_text.lower()


def test_local_catalog_returns_obf_compatible_categories() -> None:
    catalog = get_catalog()
    hits = catalog.search("la mer", limit=3)
    assert hits
    top = hits[0]
    primary = top.primary_category()
    assert primary is None or primary.startswith("en:"), (
        "primary category must be OBF-shaped so analog finder can reuse it"
    )
    assert top.brand_and_name() == f"{top.brand} — {top.name}"


def test_local_catalog_rejects_garbage_query() -> None:
    catalog = get_catalog()
    assert catalog.search("xx", limit=5) == []
    assert catalog.search("", limit=5) == []


def test_local_catalog_brand_only_query_matches_multiple_products() -> None:
    catalog = LocalProductsCatalog()
    catalog.load()
    hits = catalog.search("la mer", limit=10)
    assert hits, "expected several La Mer products"
    for h in hits:
        assert "la mer" in h.brand_and_name().lower()


def test_local_catalog_does_not_match_on_stopword_only() -> None:
    """A query like 'the foo' must not return random products that
    happen to contain 'the' but nothing relevant."""
    catalog = get_catalog()
    hits = catalog.search("the qzqxzx", limit=5)
    assert hits == [], (
        f"expected empty result for non-matching multiword query, got {hits!r}"
    )
