"""Build data/functions.json from the official EU CosIng inventory CSV.

The inventory's `Function` column gives the official ingredient role(s)
(e.g. ``HUMECTANT, SKIN CONDITIONING``). 28 494 of 28 712 entries (99 %)
are populated, which makes it a very good local replacement for the live
``CosingClient.fetch`` call when all we need is "what does this ingredient
do" — no network, no rate limits.

Output schema:

    {
        "_meta": {...},
        "ingredients": {
            "<lowercased INCI>": ["humectant", "skin conditioning"],
            ...
        }
    }

Function tags are normalized to lowercase, kept in their CosIng order
(producers list the most prominent function first), and ``UNIQUE`` is
preserved per row (no global dedup of synonyms — keeps fidelity).
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SOURCE = DATA / "cosing_inventory.csv"
TARGET = DATA / "functions.json"


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        text = f.read()
    lines = text.splitlines()
    header_idx = next(i for i, l in enumerate(lines) if l.startswith("COSING Ref No"))
    body = "\n".join(lines[header_idx:])
    return list(csv.DictReader(io.StringIO(body)))


def _split_functions(raw: str) -> list[str]:
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for tag in raw.split(","):
        norm = tag.strip().lower()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def build(source: Path = SOURCE, target: Path = TARGET) -> int:
    if not source.exists():
        raise SystemExit(f"{source} not found.")
    rows = _read_rows(source)

    ingredients: dict[str, list[str]] = {}
    for row in rows:
        name = (row.get("INCI name") or "").strip().lower()
        if not name:
            continue
        funcs = _split_functions(row.get("Function") or "")
        if not funcs:
            continue
        existing = ingredients.get(name)
        if existing:
            for f in funcs:
                if f not in existing:
                    existing.append(f)
        else:
            ingredients[name] = funcs

    payload = {
        "_meta": {
            "source": "EU CosIng — Inventory of Ingredients (Function column)",
            "source_url": "https://ec.europa.eu/growth/tools-databases/cosing/",
            "snapshot_csv": "data/cosing_inventory.csv",
            "license": "EU re-use under Decision 2011/833/EU",
            "build_date": date.today().isoformat(),
            "key": "lowercased INCI name as in cosing_inventory.csv",
            "values": "list of canonical CosIng function tags, lowercased",
            "duplicate_inci_handling": (
                "If the same INCI appears on multiple inventory rows, "
                "their function tags are merged in first-seen order, "
                "preserving uniqueness."
            ),
        },
        "ingredients": dict(sorted(ingredients.items())),
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(ingredients):,} INCI -> function entries to {target}")
    return len(ingredients)


if __name__ == "__main__":
    build()
