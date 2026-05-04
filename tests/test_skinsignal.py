from __future__ import annotations

from pathlib import Path

import pytest

from src.services.skinsignal import parse_ingredient_page, slug_for

SAMPLE_HTML = Path(__file__).parent / "fixtures" / "skinsignal_niacinamide.html"


def test_slug_for_basic_lowercase() -> None:
    assert slug_for("Niacinamide") == "niacinamide"


def test_slug_for_multiword_replaces_spaces() -> None:
    assert slug_for("Sodium Hyaluronate") == "sodium-hyaluronate"


def test_slug_for_punctuation_collapsed() -> None:
    assert slug_for("Vitamin C (Ascorbic Acid)") == "vitamin-c-ascorbic-acid"


def test_slug_for_trims_dashes() -> None:
    assert slug_for("--weird--") == "weird"


def test_slug_for_empty_string() -> None:
    assert slug_for("") == ""


def test_parse_returns_none_on_unrelated_page() -> None:
    assert parse_ingredient_page("x", "<html><body><p>Nothing here</p></body></html>") is None


@pytest.mark.skipif(not SAMPLE_HTML.exists(), reason="HTML fixture not present")
def test_parse_real_niacinamide_fixture() -> None:
    html = SAMPLE_HTML.read_text(encoding="utf-8")
    info = parse_ingredient_page("niacinamide", html)
    assert info is not None
    assert info.name == "Niacinamide"
    assert info.russian_name == "Никотинамид"
    assert info.comedogenicity == 0
    assert len(info.roles) >= 1
    assert len(info.traits) >= 1


def test_parse_minimal_synthetic_page() -> None:
    html = """
    <html><body>
      <h1>TestStuff в Косметике</h1>
      <ul class="list">
        <li><b>Переводится как:</b> Тестовое</li>
        <li><b>Роль:</b> <a class="role">главная роль</a></li>
        <li><b>Характеристики:</b> <span class="trait">первый</span><span class="trait">второй</span></li>
        <li><b>Комедогенность:</b> 3 <span class="pointer">?</span></li>
      </ul>
    </body></html>
    """
    info = parse_ingredient_page("test-stuff", html)
    assert info is not None
    assert info.name == "TestStuff"
    assert info.russian_name == "Тестовое"
    assert info.comedogenicity == 3
    assert info.roles == ["главная роль"]
    assert info.traits == ["первый", "второй"]


def test_parse_missing_ul_returns_name_only() -> None:
    html = "<html><body><h1>Foo в Косметике</h1></body></html>"
    info = parse_ingredient_page("foo", html)
    assert info is not None
    assert info.name == "Foo"
    assert info.comedogenicity is None
    assert info.roles == []


def test_parse_handles_non_numeric_comedogenicity() -> None:
    html = """
    <html><body>
      <h1>X в Косметике</h1>
      <ul class="list">
        <li><b>Комедогенность:</b> неизвестно</li>
      </ul>
    </body></html>
    """
    info = parse_ingredient_page("x", html)
    assert info is not None
    assert info.comedogenicity is None
