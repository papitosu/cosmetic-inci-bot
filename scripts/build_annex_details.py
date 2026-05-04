"""Build data/annex_details.json from the openfoodfacts mirror of EU CosIng Annexes II/III/IV/V/VI.

Source: ``openfoodfacts/openbeautyfacts/cosing/COSING_Annex.*_v2.csv``.
These files are EU public documents republished by Open Beauty Facts;
re-use is allowed under EU Decision 2011/833/EU.

Why we want this on top of the inventory snapshot:

* ``data/regulatory.json`` (built from the inventory) only has Annex
  membership and CMR category. It tells you "Salicylic Acid is in
  Annex III", not "max 0.5% in leave-on products, not for kids under 3".
* The Annex CSVs add three high-value fields per substance:
  ``Product Type, body parts`` (e.g. ``a) Rinse-off products b) Leave-on``),
  ``Maximum concentration in ready for use preparation`` (e.g. ``0.5%``),
  ``Wording of conditions of use and warnings`` (e.g. ``Not to be used
  for children under 3 years of age``).
* That information is what users actually want to see — "this is not
  illegal, but it has a hard cap" — and it scales well for the
  classical "max 1% in leave-on" framing the user asked for.

Output schema:

    {
        "_meta": {...},
        "ingredients": {
            "salicylic acid": [
                {
                    "annex": "III",
                    "ref": "III/3",
                    "product_type": "...",
                    "max_conc": "0.5% (acid)",
                    "warning": "Not to be used for children under 3..."
                },
                ...
            ],
            ...
        }
    }

A single INCI may appear in multiple annexes (Salicylic Acid is in
both III and V) — we keep one entry per (annex, ref) pair.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SOURCE_DIR = DATA / "annexes"
TARGET = DATA / "annex_details.json"

ANNEXES = ("II", "III", "IV", "V", "VI")
# EU CSVs mix two label styles: ``a) foo b) bar`` and ``(a) foo (b) bar``.
# Both forms must round-trip through the same parser, so the open paren
# is optional and we anchor on the closing ``)`` plus letter.
ITEM_LABEL_RE = re.compile(r"^\(?([a-z])\)\s*", re.IGNORECASE)
SUBLABEL_RE = re.compile(r"(?<=\s)\(?([a-z])\)\s*", re.IGNORECASE)


_PROSE_PREFIX = (
    "inorganic ", "salts of ", "esters of ", "moved or ",
    "their salts", "the substance", "any of ", "compounds with ",
)


def _strip_inci_noise(name: str) -> str:
    """Drop CSV idiosyncrasies that creep into name fields:
    leading ``(*)`` (EU CMR marker), trailing footnote markers like ``(10)``,
    and stray surrounding whitespace.
    """
    s = re.sub(r"^\s*\(\*\)\s*", "", name)
    s = re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", s)
    return s.strip().lower()


def _looks_like_inci(name: str) -> bool:
    """Filter chemical descriptions / regulation prose that aren't real INCI.

    Real INCI names start with a letter/digit, never with a paren, comma or
    quote, and don't read like a sentence ('any of the following...').
    """
    if not name or len(name) < 3:
        return False
    if not (name[0].isalpha() or name[0].isdigit()):
        return False
    if name.startswith(_PROSE_PREFIX):
        return False
    return True


def _split_inci(field: str) -> list[str]:
    """Pull INCI names out of the semicolon-separated `Identified INGREDIENTS`
    field. Names are uppercase in the source; we lowercase for our index.
    """
    if not field:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for chunk in field.split(";"):
        name = _strip_inci_noise(chunk)
        if not _looks_like_inci(name) or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _split_glossary(field: str) -> list[str]:
    """Glossary field uses slash-separated names like
    ``BENZOIC ACID; SODIUM BENZOATE`` or ``SALICYLIC ACID / CALCIUM SALICYLATE``.
    Both separators show up in the wild — handle both.
    """
    if not field:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[;/]", field):
        name = _strip_inci_noise(token)
        if not _looks_like_inci(name) or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _ref_from(reference: str, annex: str) -> str:
    """Annex-relative ref string: ``III/3``, ``V/12 bis``, etc."""
    ref = (reference or "").strip()
    if not ref:
        return annex
    return f"{annex}/{ref}"


def _split_labelled(text: str) -> list[tuple[str, str]]:
    """Parse ``a) foo b) bar`` into ``[('a', 'foo'), ('b', 'bar')]``.

    Returns ``[('-', text)]`` if no ``a)`` / ``b)`` markers exist.
    """
    s = (text or "").strip()
    if not s:
        return []
    if not ITEM_LABEL_RE.match(s):
        return [("-", s)]
    parts: list[tuple[str, str]] = []
    cursor = s
    while cursor:
        m = ITEM_LABEL_RE.match(cursor)
        if not m:
            if parts:
                # trailing text without label — fold into previous chunk
                label, prev = parts[-1]
                parts[-1] = (label, f"{prev} {cursor.strip()}".strip())
            else:
                parts.append(("-", cursor.strip()))
            break
        label = m.group(1).lower()
        rest = cursor[m.end():]
        nxt = SUBLABEL_RE.search(rest)
        if nxt:
            parts.append((label, rest[: nxt.start()].strip()))
            # Re-feed the next label (``b)`` or ``(b)``) into the loop so the
            # outer ``ITEM_LABEL_RE.match`` picks it up cleanly.
            cursor = rest[nxt.start():]
        else:
            parts.append((label, rest.strip()))
            cursor = ""
    return parts


def _join_pair(product_type: str, max_conc: str, warning: str) -> list[dict[str, str]]:
    """Pair up a/b/c labels across product_type / max_conc / warning.

    The CSV authors line them up by letter. Unlabelled text (label ``-``)
    represents content that applies to every labelled substep, so we
    fold it into each labelled row instead of leaving it as a dangling
    entry without context.
    """
    pt = _split_labelled(product_type)
    mc = _split_labelled(max_conc)
    wn = _split_labelled(warning)

    pt_map = {label: text for label, text in pt}
    mc_map = {label: text for label, text in mc}
    wn_map = {label: text for label, text in wn}

    labels = []
    seen_labels: set[str] = set()
    for src in (pt, mc, wn):
        for label, _ in src:
            if label not in seen_labels:
                seen_labels.add(label)
                labels.append(label)
    has_letters = any(lab != "-" for lab in labels)
    if has_letters:
        # Drop the unlabelled bucket from the iteration — its content
        # gets folded into every labelled row below.
        labels = [lab for lab in labels if lab != "-"]

    rows: list[dict[str, str]] = []
    for label in labels:
        row: dict[str, str] = {}
        pt_val = pt_map.get(label) or (pt_map.get("-") if has_letters else None)
        mc_val = mc_map.get(label) or (mc_map.get("-") if has_letters else None)
        wn_val = wn_map.get(label) or (wn_map.get("-") if has_letters else None)
        if pt_val:
            row["product_type"] = pt_val
        if mc_val:
            row["max_conc"] = mc_val
        if wn_val:
            row["warning"] = wn_val
        if row:
            rows.append(row)
    return rows


def _parse_row(row: dict[str, str], annex: str) -> tuple[list[str], list[dict[str, str]]]:
    """Turn one CSV row into (inci_names, [restriction_entries])."""
    if (row.get("Chemical name / INN") or row.get("Chemical name") or "").strip().lower() in (
        "moved or deleted",
        "",
    ):
        return [], []

    names: list[str] = []
    names.extend(_split_inci(row.get("Identified INGREDIENTS or substances e.g.", "")))
    names.extend(_split_glossary(row.get("Name of Common Ingredients Glossary", "")))
    names.extend(_split_glossary(
        row.get("Colour index Number / Name of Common Ingredients Glossary", "")
    ))
    seen: set[str] = set()
    deduped: list[str] = []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        deduped.append(n)
    if not deduped:
        return [], []

    ref = _ref_from(row.get("Reference Number") or row.get("Reference number") or "", annex)

    # Annex II is simply a banned list; everything else has restrictions.
    if annex == "II":
        return deduped, [{"annex": annex, "ref": ref, "status": "prohibited"}]

    pairs = _join_pair(
        row.get("Product Type, body parts", ""),
        row.get("Maximum concentration in ready for use preparation", ""),
        row.get("Wording of conditions of use and warnings", ""),
    )
    out: list[dict[str, str]] = []
    if pairs:
        for p in pairs:
            entry: dict[str, str] = {"annex": annex, "ref": ref}
            entry.update(p)
            out.append(entry)
    else:
        out.append({"annex": annex, "ref": ref})
    return deduped, out


def build(source_dir: Path = SOURCE_DIR, target: Path = TARGET) -> int:
    if not source_dir.exists():
        raise SystemExit(f"{source_dir} not found.")

    ingredients: dict[str, list[dict[str, str]]] = {}
    skipped = 0
    for annex in ANNEXES:
        path = source_dir / f"annex_{annex}.csv"
        if not path.exists():
            print(f"skip annex {annex}: {path} missing")
            continue
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                names, entries = _parse_row(row, annex)
                if not names:
                    skipped += 1
                    continue
                for name in names:
                    bucket = ingredients.setdefault(name, [])
                    for entry in entries:
                        # Multiple substeps share a ref (e.g. III/98 a + III/98 b),
                        # so dedupe on the full content tuple — not on ref alone.
                        sig = tuple(sorted(entry.items()))
                        if any(tuple(sorted(b.items())) == sig for b in bucket):
                            continue
                        bucket.append(entry)

    payload = {
        "_meta": {
            "source": "EU CosIng Annexes II–VI (Regulation 1223/2009)",
            "source_repo": "openfoodfacts/openbeautyfacts (github)",
            "source_url": "https://github.com/openfoodfacts/openbeautyfacts/tree/develop/cosing",
            "license": "EU re-use under Decision 2011/833/EU",
            "build_date": date.today().isoformat(),
            "shape": (
                "{ <inci>: [ {annex, ref, product_type?, max_conc?, warning?, status?}, ...] }"
            ),
            "annexes_loaded": list(ANNEXES),
            "skipped_rows": skipped,
        },
        "ingredients": dict(sorted(ingredients.items())),
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(ingredients):,} INCI entries to {target} (skipped {skipped} rows)")
    return len(ingredients)


if __name__ == "__main__":
    build()
