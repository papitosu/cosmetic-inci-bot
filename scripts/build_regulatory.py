"""Build data/regulatory.json from the official EU CosIng inventory CSV.

Source: https://ec.europa.eu/growth/tools-databases/cosing/
File:   data/cosing_inventory.csv (snapshot 15/12/2020, public re-use under
        Decision 2011/833/EU on the re-use of Commission documents).

Output schema (consumed by IngredientsDB):

    {
        "_meta": { ... }          # human-readable docs
        "ingredients": {
            "<lowercased INCI>": {
                "annexes": ["II", "III"],    # one or more annex codes
                "refs":    ["II/665"],       # original ref strings
                "cmr":     ["1B"]            # optional CMR category tags
            },
            ...
        }
    }

Annex meaning under Regulation (EC) No 1223/2009:
    II  – PROHIBITED in cosmetic products
    III – RESTRICTED (allowed only with conditions / max concentrations)
    IV  – ALLOWED COLORANTS
    V   – ALLOWED PRESERVATIVES
    VI  – ALLOWED UV FILTERS
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SOURCE = DATA / "cosing_inventory.csv"
TARGET = DATA / "regulatory.json"

ANNEX_RE = re.compile(r"\b(II|III|IV|V|VI)/(\d+)")
CMR_RE = re.compile(r"CMR\s*([12][AB]?)", re.IGNORECASE)


def _read_rows(path: Path) -> list[dict[str, str]]:
    """The official CSV starts with a few human-readable banner lines and a
    `sep=,` directive. Skip everything until the real header row."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        text = f.read()
    lines = text.splitlines()
    try:
        header_idx = next(i for i, l in enumerate(lines) if l.startswith("COSING Ref No"))
    except StopIteration as exc:
        raise SystemExit(f"Could not locate header row in {path}") from exc
    body = "\n".join(lines[header_idx:])
    return list(csv.DictReader(io.StringIO(body)))


def _parse_restriction(text: str) -> dict[str, list[str]] | None:
    if not text or not text.strip():
        return None
    refs = [f"{a}/{n}" for a, n in ANNEX_RE.findall(text)]
    cmr = [m.upper() for m in CMR_RE.findall(text)]
    if not refs and not cmr:
        return None
    annexes: list[str] = []
    for r in refs:
        a = r.split("/", 1)[0]
        if a not in annexes:
            annexes.append(a)
    out: dict[str, list[str]] = {}
    if annexes:
        out["annexes"] = annexes
    if refs:
        out["refs"] = refs
    if cmr:
        out["cmr"] = cmr
    return out


def build(source: Path = SOURCE, target: Path = TARGET) -> int:
    if not source.exists():
        raise SystemExit(
            f"{source} not found. Download the latest CosIng inventory from "
            f"https://ec.europa.eu/growth/tools-databases/cosing/ and place it there."
        )
    rows = _read_rows(source)

    ingredients: dict[str, dict[str, list[str]]] = {}
    for row in rows:
        name = (row.get("INCI name") or "").strip().lower()
        if not name:
            continue
        parsed = _parse_restriction(row.get("Restriction", ""))
        if parsed is None:
            continue
        existing = ingredients.get(name)
        if existing:
            for k, vs in parsed.items():
                merged = existing.get(k, [])
                for v in vs:
                    if v not in merged:
                        merged.append(v)
                existing[k] = merged
        else:
            ingredients[name] = parsed

    payload = {
        "_meta": {
            "source": "EU CosIng — Inventory of Ingredients",
            "source_url": "https://ec.europa.eu/growth/tools-databases/cosing/",
            "snapshot_csv": "data/cosing_inventory.csv",
            "regulation": "Regulation (EC) No 1223/2009 — Cosmetic Products",
            "annex_meaning": {
                "II": "PROHIBITED — must not be present in cosmetic products",
                "III": "RESTRICTED — allowed only with conditions / max concentrations",
                "IV": "ALLOWED COLORANTS — list of permitted colorants",
                "V": "ALLOWED PRESERVATIVES — list of permitted preservatives",
                "VI": "ALLOWED UV FILTERS — list of permitted UV filters",
            },
            "cmr_meaning": {
                "1A": "Carcinogenic / Mutagenic / Reprotoxic — known (highest concern)",
                "1B": "Carcinogenic / Mutagenic / Reprotoxic — presumed",
                "2": "Carcinogenic / Mutagenic / Reprotoxic — suspected",
            },
            "license": (
                "EU re-use under Decision 2011/833/EU. Aggregated overlay published "
                "as part of this project; original CosIng inventory remains EU-owned."
            ),
            "build_date": date.today().isoformat(),
            "key": "lowercased INCI name as in cosing_inventory.csv",
        },
        "ingredients": dict(sorted(ingredients.items())),
    }

    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Wrote {len(ingredients):,} regulatory entries to {target}")
    return len(ingredients)


if __name__ == "__main__":
    build()
