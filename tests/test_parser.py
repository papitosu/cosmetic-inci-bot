from __future__ import annotations

from src.services.parser import parse


def test_parse_basic_text() -> None:
    text = "Water, Glycerin, Coconut Oil, Niacinamide"
    result = parse(text)
    assert result == ["Water", "Glycerin", "Coconut Oil", "Niacinamide"]


def test_parse_strips_prefix() -> None:
    text = "Ingredients: Water, Glycerin, Tocopherol."
    assert parse(text) == ["Water", "Glycerin", "Tocopherol"]


def test_parse_strips_russian_prefix() -> None:
    text = "Состав: Water, Glycerin"
    assert parse(text) == ["Water", "Glycerin"]


def test_parse_handles_bullets_and_newlines() -> None:
    text = "• Water\n• Glycerin\n• Niacinamide"
    assert parse(text) == ["Water", "Glycerin", "Niacinamide"]


def test_parse_drops_empty_and_short_tokens() -> None:
    assert parse("Water, , a, , Glycerin") == ["Water", "Glycerin"]


def test_parse_dedupes_case_insensitive() -> None:
    assert parse("Water, water, GLYCERIN, Glycerin") == ["Water", "GLYCERIN"]


def test_parse_handles_semicolons() -> None:
    assert parse("Water; Glycerin; Niacinamide") == ["Water", "Glycerin", "Niacinamide"]


def test_parse_keeps_aqua_water_pair() -> None:
    text = "Aqua / Water, Glycerin"
    assert parse(text) == ["Aqua / Water", "Glycerin"]


def test_parse_empty() -> None:
    assert parse("") == []
    assert parse("   ") == []
