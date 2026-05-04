"""Format AnalysisResult into Telegram-ready text."""
from __future__ import annotations

from src.core.enums import SkinType
from src.services.analyzer import AnalysisResult, IngredientAnalysis

VERDICT_EMOJI = {"low": "🟢", "medium": "🟡", "high": "🔴"}
VERDICT_RU = {"low": "Низкий риск", "medium": "Средний риск", "high": "Высокий риск"}

SKIN_TYPE_LABELS = {
    SkinType.NORMAL: "Нормальная",
    SkinType.DRY: "Сухая",
    SkinType.OILY: "Жирная",
    SkinType.COMBINATION: "Комбинированная",
    SkinType.SENSITIVE: "Чувствительная",
    SkinType.ACNE_PRONE: "С акне",
    SkinType.UNKNOWN: "Не указана",
}


def _name(item: IngredientAnalysis) -> str:
    return (item.canonical or item.raw).title()


def _short_list(items: list[IngredientAnalysis], max_items: int = 8) -> list[IngredientAnalysis]:
    return items[:max_items]


def format_analysis(result: AnalysisResult, product_title: str | None = None) -> str:
    lines: list[str] = []
    header = "🔍 <b>Анализ состава завершён</b>"
    if product_title:
        header += f"\nПродукт: <i>{product_title}</i>"
    lines.append(header)
    lines.append("")

    if result.comedogenic:
        lines.append("⚠️ <b>Комедогенные:</b>")
        for it in _short_list(result.comedogenic):
            rating = it.flags.get("comedogenic_rating", "?")
            lines.append(f"• {_name(it)} ({rating}/5)")
        if len(result.comedogenic) > 8:
            lines.append(f"  …и ещё {len(result.comedogenic) - 8}")
        lines.append("")

    if result.irritants:
        lines.append("⚠️ <b>Раздражители:</b>")
        for it in _short_list(result.irritants):
            ir = it.flags.get("irritant", {})
            sev = ir.get("severity")
            sev_part = f" ({sev})" if sev else ""
            lines.append(f"• {_name(it)}{sev_part}")
        if len(result.irritants) > 8:
            lines.append(f"  …и ещё {len(result.irritants) - 8}")
        lines.append("")

    if result.allergens:
        lines.append("⚠️ <b>Потенциальные аллергены:</b>")
        for it in _short_list(result.allergens):
            al = it.flags.get("allergen", {})
            sev = al.get("severity")
            sev_part = f" ({sev})" if sev else ""
            lines.append(f"• {_name(it)}{sev_part}")
        if len(result.allergens) > 8:
            lines.append(f"  …и ещё {len(result.allergens) - 8}")
        lines.append("")

    if result.beneficial:
        lines.append("✅ <b>Полезные:</b>")
        for it in _short_list(result.beneficial):
            cat = it.flags.get("beneficial", {}).get("category", "")
            cat_part = f" — {_pretty_category(cat)}" if cat else ""
            lines.append(f"• {_name(it)}{cat_part}")
        if len(result.beneficial) > 8:
            lines.append(f"  …и ещё {len(result.beneficial) - 8}")
        lines.append("")

    if result.unknown:
        lines.append(f"❔ <b>Неопознано:</b> {len(result.unknown)} (нет в базе INCI)")
        lines.append("")

    if result.prohibited:
        lines.append("🚫 <b>Запрещены в ЕС</b> (Регламент 1223/2009, Annex II):")
        for it in result.prohibited[:8]:
            ref = _first_ref(it, "II")
            ref_part = f" — {ref}" if ref else ""
            lines.append(f"• {_name(it)}{ref_part}")
            # Hydroquinone-style entries are banned (Annex II) but still have a
            # narrow professional carve-out in Annex III/IV. Surface those
            # conditions so the user sees both the ban and the exception.
            for detail_line in _detail_lines(it, annex=None, exclude_annexes=("II",)):
                lines.append(f"   ↳ {detail_line}")
        if len(result.prohibited) > 8:
            lines.append(f"  …и ещё {len(result.prohibited) - 8}")
        lines.append("")

    if result.restricted:
        lines.append("⚠️ <b>Ограничены в ЕС</b> (Annex III — условия / макс. концентрация):")
        for it in result.restricted[:8]:
            ref = _first_ref(it, "III")
            ref_part = f" — {ref}" if ref else ""
            lines.append(f"• {_name(it)}{ref_part}")
            # Annex III is the membership trigger for this block, but salicylic
            # acid–style INCI also have V/VI substeps that matter (preservative
            # caps, UV-filter conditions). Show every non-II detail so users
            # don't lose Annex V / VI rows just because we routed them here.
            for detail_line in _detail_lines(it, annex=None, exclude_annexes=("II",)):
                lines.append(f"   ↳ {detail_line}")
        if len(result.restricted) > 8:
            lines.append(f"  …и ещё {len(result.restricted) - 8}")
        lines.append("")

    regulated_status = [
        i for i in result.ingredients
        if (i.flags.get("regulatory") or {}).get("annexes")
        and not _is_in_group(i, result.prohibited)
        and not _is_in_group(i, result.restricted)
    ]
    if regulated_status:
        lines.append("🇪🇺 <b>Регуляторный статус ЕС:</b>")
        for it in regulated_status[:6]:
            anns = (it.flags.get("regulatory") or {}).get("annexes") or []
            label = ", ".join(_annex_label(a) for a in anns)
            lines.append(f"• {_name(it)} — {label}")
            # No annex filter here — show every restriction line we have for
            # this INCI; substep ordering is preserved from the source.
            for detail_line in _detail_lines(it, annex=None):
                lines.append(f"   ↳ {detail_line}")
        if len(regulated_status) > 6:
            lines.append(f"  …и ещё {len(regulated_status) - 6}")
        lines.append("")

    cmr_items = [
        i for i in result.ingredients
        if (i.flags.get("regulatory") or {}).get("cmr") or (i.cosing and i.cosing.get("cmr"))
    ]
    if cmr_items:
        lines.append("☣️ <b>CMR-маркеры (канцероген/мутаген/репротокс):</b>")
        for it in cmr_items[:5]:
            grades = (it.flags.get("regulatory") or {}).get("cmr") or []
            grade = grades[0] if grades else "CMR"
            lines.append(f"• {_name(it)} — {grade}")
        if len(cmr_items) > 5:
            lines.append(f"  …и ещё {len(cmr_items) - 5}")
        lines.append("")

    penetrating = [
        i for i in result.ingredients
        if i.pubchem and i.pubchem.get("xlogp") is not None and float(i.pubchem["xlogp"]) >= 4.0
    ]
    if penetrating:
        lines.append("🧪 <b>Высокая липофильность (склонны к проникновению):</b>")
        for it in penetrating[:5]:
            xlogp = it.pubchem["xlogp"]
            lines.append(f"• {_name(it)} (XLogP {xlogp:.1f})")
        if len(penetrating) > 5:
            lines.append(f"  …и ещё {len(penetrating) - 5}")
        lines.append("")

    function_items = [
        i for i in result.ingredients[:6]
        if i.flags.get("functions")
    ]
    if function_items:
        lines.append("🧬 <b>Функции (CosIng):</b>")
        for it in function_items:
            funcs = it.flags.get("functions") or []
            head = ", ".join(_pretty_function(f) for f in funcs[:3])
            lines.append(f"• {_name(it)} — {head}")
        lines.append("")

    skin_extra = [
        i for i in result.ingredients
        if i.skinsignal and (i.skinsignal.get("russian_name") or i.skinsignal.get("traits"))
    ]
    if skin_extra:
        lines.append("🇷🇺 <b>Дополнительные характеристики:</b>")
        for it in skin_extra[:5]:
            ss = it.skinsignal or {}
            ru = ss.get("russian_name")
            traits = (ss.get("traits") or [])[:3]
            head = f"• {_name(it)}"
            if ru:
                head += f" — {ru}"
            if traits:
                head += f" — {', '.join(traits).lower()}"
            lines.append(head)
        if len(skin_extra) > 5:
            lines.append(f"  …и ещё {len(skin_extra) - 5}")
        lines.append("")

    emoji = VERDICT_EMOJI.get(result.verdict, "")
    verdict_ru = VERDICT_RU.get(result.verdict, "")
    lines.append(f"{emoji} <b>Итог:</b> {verdict_ru}")
    lines.append(f"Риск-скор: <b>{result.risk_score:.1f}</b>/100")
    skin_label = SKIN_TYPE_LABELS.get(result.skin_type, result.skin_type.value)
    lines.append(f"Тип кожи: {skin_label}")
    if result.summary:
        lines.append("")
        lines.append(f"<i>{result.summary}</i>")

    return "\n".join(lines)


_FUNCTION_RU = {
    "skin conditioning": "уход за кожей",
    "skin conditioning - emollient": "эмолент",
    "skin conditioning - humectant": "гумектант",
    "skin conditioning - occlusive": "окклюзив",
    "skin conditioning - miscellaneous": "уход за кожей",
    "humectant": "гумектант",
    "emollient": "эмолент",
    "antioxidant": "антиоксидант",
    "antimicrobial": "антимикробное",
    "preservative": "консервант",
    "skin protecting": "защита кожи",
    "surfactant": "ПАВ",
    "surfactant - cleansing": "ПАВ — очищающее",
    "surfactant - emulsifying": "ПАВ — эмульгатор",
    "surfactant - foam boosting": "усилитель пены",
    "surfactant - solubilizing": "солюбилизатор",
    "viscosity controlling": "регулятор вязкости",
    "film forming": "плёнкообразующее",
    "antistatic": "антистатик",
    "fragrance": "отдушка",
    "perfuming": "отдушка",
    "uv absorber": "UV-абсорбер",
    "uv filter": "UV-фильтр",
    "buffering": "стабилизатор pH",
    "chelating": "хелатирующее",
    "denaturant": "денатурат",
    "solvent": "растворитель",
    "binding": "связующее",
    "bulking": "наполнитель",
    "opacifying": "опалесцент",
    "abrasive": "абразив",
    "absorbent": "абсорбент",
    "anticaking": "против слёживания",
    "astringent": "вяжущее",
    "soothing": "успокаивающее",
    "tonic": "тонизирующее",
    "exfoliant": "эксфолиант",
    "anti-acne": "против акне",
    "anti-seborrheic": "против себореи",
    "anti-dandruff agent": "против перхоти",
    "anticorrosive": "антикоррозийное",
    "antifoaming": "пеногаситель",
    "bleaching": "осветляющее",
    "depilatory": "депиляция",
    "deodorant": "дезодорирующее",
    "emulsifying": "эмульгатор",
    "emulsion stabilising": "стабилизатор эмульсии",
    "foaming": "пенообразующее",
    "gel forming": "гелеобразующее",
    "hair conditioning": "кондиционер для волос",
    "hair waving or straightening": "укладка волос",
    "hair dyeing": "краситель для волос",
    "hair fixing": "фиксатор",
    "keratolytic": "кератолитик",
    "lytic": "лизирующее",
    "masking": "маскирующее",
    "moisturising": "увлажняющее",
    "nail conditioning": "кондиционер ногтей",
    "oral care": "уход за полостью рта",
    "oxidising": "окислитель",
    "plasticiser": "пластификатор",
    "propellant": "пропеллент",
    "reducing": "восстановитель",
    "refreshing": "освежающее",
    "smoothing": "разглаживающее",
    "stabilising": "стабилизатор",
    "tanning": "автозагар",
    "viscosity decreasing": "снижает вязкость",
    "viscosity increasing": "повышает вязкость",
}


def _pretty_function(fn: str) -> str:
    return _FUNCTION_RU.get(fn.lower(), fn.lower())


def _pretty_category(cat: str) -> str:
    return {
        "humectant": "увлажнитель",
        "emollient": "эмолент",
        "barrier": "восстанавливает барьер",
        "antioxidant": "антиоксидант",
        "anti_inflammatory": "противовоспалительный",
        "active_brightener": "активный осветлитель",
        "active_anti_aging": "анти-эйдж активный",
        "active_exfoliant": "эксфолиант",
        "active_acne": "против акне",
        "spf_filter": "UV-фильтр",
    }.get(cat, cat.replace("_", " "))


_ANNEX_LABEL_RU = {
    "II": "запрещён",
    "III": "ограничен",
    "IV": "разрешённый краситель",
    "V": "разрешённый консервант",
    "VI": "разрешённый UV-фильтр",
}


def _annex_label(annex: str) -> str:
    return _ANNEX_LABEL_RU.get(annex, f"Annex {annex}")


def _first_ref(item: IngredientAnalysis, annex: str) -> str | None:
    refs = (item.flags.get("regulatory") or {}).get("refs") or []
    for ref in refs:
        if ref.startswith(f"{annex}/"):
            return ref
    return refs[0] if refs else None


_MAX_DETAIL_TEXT = 140


def _detail_lines(
    item: IngredientAnalysis,
    annex: str | None,
    *,
    max_rows: int = 3,
    exclude_annexes: tuple[str, ...] = (),
) -> list[str]:
    """Render Annex restriction rows ('макс. 0.5%, leave-on, не для детей <3').

    - ``annex``: pin to a specific annex (Annex III block shows III, not V).
    - ``exclude_annexes``: hide rows from given annex letters (used under the
      'prohibited' block — the Annex II ban is already in the header, so we
      only want to show the III/IV/V exceptions if any).
    - ``max_rows``: keep messages compact.
    """
    details = (item.flags.get("regulatory") or {}).get("details") or []
    if not details:
        return []
    if annex:
        scoped = [d for d in details if d.get("annex") == annex]
        rows = scoped or details
    else:
        rows = details
    if exclude_annexes:
        rows = [d for d in rows if d.get("annex") not in exclude_annexes]
    out: list[str] = []
    for d in rows[:max_rows]:
        parts: list[str] = []
        if d.get("max_conc"):
            parts.append(f"макс. {_clean_text(d['max_conc'])}")
        if d.get("product_type"):
            parts.append(_translate_product_type(_clean_text(d["product_type"])))
        if d.get("warning"):
            parts.append(_translate_warning(_clean_text(d["warning"])))
        if not parts and d.get("status") == "prohibited":
            parts.append("запрещено в косметике (ЕС)")
        if parts:
            line = "; ".join(parts)
            if len(line) > _MAX_DETAIL_TEXT:
                line = _truncate_words(line, _MAX_DETAIL_TEXT)
            out.append(line)
    return out


def _truncate_words(text: str, limit: int) -> str:
    """Trim ``text`` to ``limit`` chars on a word boundary, ellipsis-suffixed.
    Beats raw slicing because it never produces ``... fragra…`` mid-word."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    head, sep, _ = cut.rpartition(" ")
    return (head if sep else cut).rstrip(" ,;:-") + "…"


def _clean_text(text: str) -> str:
    """Squash whitespace, fix detached hyphens (``Rinse -off``) and strip
    footnote markers like ``(11)`` that point back to the regulation —
    all noise for the end user."""
    import re

    s = re.sub(r"\s+", " ", text).strip()
    s = re.sub(r"\s+-\s*", "-", s)
    s = re.sub(r"\s*\(\s*\d+\s*\)$", "", s)
    return s


# Top-N most common product_type / warning patterns from the EU CSVs,
# translated to Russian. Source text is matched case-insensitively against
# the cleaned input. Anything that doesn't match falls through unchanged —
# better English than mistranslated Russian.
_PRODUCT_TYPE_RU = {
    "rinse-off products": "смываемые продукты",
    "rinse-off hair products": "смываемые средства для волос",
    "leave-on products": "несмываемые продукты",
    "leave-on hair products": "несмываемые средства для волос",
    "oral products": "продукты для полости рта",
    "oral care products": "уход за полостью рта",
    "nail hardening products": "укрепители для ногтей",
    "artificial nail systems": "системы искусственных ногтей",
    "other products": "прочие продукты",
    "all products": "все продукты",
}

_WARNING_PATTERNS_RU: list[tuple[str, str]] = [
    ("not to be used for children under three years of age",
     "не использовать у детей до 3 лет"),
    ("not to be used for children under 3 years of age",
     "не использовать у детей до 3 лет"),
    ("not to be used in products for children under 3 years of age, except for shampoos",
     "не использовать у детей до 3 лет (кроме шампуней)"),
    ("for professional use only",
     "только для профессионального использования"),
    ("avoid skin contact",
     "избегать контакта с кожей"),
    ("read directions for use carefully",
     "внимательно читать инструкцию"),
    ("contains formaldehyde",
     "содержит формальдегид"),
    ("protect cuticles with grease or oil",
     "защищать кутикулы жиром или маслом"),
    ("not to be used in spray products",
     "не использовать в спреях"),
]


def _translate_product_type(text: str) -> str:
    key = text.lower()
    return _PRODUCT_TYPE_RU.get(key, text)


def _translate_warning(text: str) -> str:
    """Translate the most common EU warning phrases. Falls back to English
    when nothing matches — silent mistranslation would be worse."""
    norm = text.lower()
    for needle, replacement in _WARNING_PATTERNS_RU:
        if needle in norm:
            return replacement
    return text


def _is_in_group(item: IngredientAnalysis, group: list[IngredientAnalysis]) -> bool:
    return any(g.position == item.position for g in group)
