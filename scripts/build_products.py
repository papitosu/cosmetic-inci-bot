"""Build data/products.json from the Sephora products CSV.

Source dataset: Vazquez/Cosmetic-Price-Analysis (cosmetics.csv, MIT license),
which is in turn derived from "Comparing Cosmetics by Ingredients"
(public Sephora scrape). 1 472 products across 5 categories with full
INCI lists already in the right order.

We deliberately drop:

* ``Price`` — the dataset is from 2018, prices are stale and would mislead
  users.
* ``Rank`` — same staleness reason.
* ``Combination/Dry/Normal/Oily/Sensitive`` — the dataset's authors mark
  skin-type compatibility manually with no documented method; we run our
  own analysis on the ingredient list.

We keep:

* brand, product name, category (``Moisturizer``, ``Cleanser``,
  ``Face Mask``, ``Treatment``, ``Eye cream``);
* the raw INCI string, exactly as scraped from Sephora's product page.

That is enough to (a) match the user's free-text query against the local
catalogue before falling back to Open Beauty Facts, and (b) reuse the
existing ``parse(...)`` + ``analyze_full(...)`` pipeline unchanged.

The output is a flat array of dicts so it can be loaded once at startup
and kept in memory next to the INCI overlays.
"""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SOURCE = DATA / "sephora_products.csv"
TARGET = DATA / "products.json"

VALID_LABELS = {"Moisturizer", "Cleanser", "Face Mask", "Treatment", "Eye cream"}


def build(source: Path = SOURCE, target: Path = TARGET) -> int:
    if not source.exists():
        raise SystemExit(f"{source} not found.")

    with source.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    products: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    skipped = 0
    for row in rows:
        label = (row.get("Label") or "").strip()
        brand = (row.get("Brand") or "").strip()
        name = (row.get("Name") or "").strip()
        ingredients = (row.get("Ingredients") or "").strip()
        if not (brand and name and ingredients) or label not in VALID_LABELS:
            skipped += 1
            continue
        # Some Sephora rows place placeholders like "#NAME?" or "Visit the ..."
        # in ingredients; require at least one comma to look like a real list.
        if "," not in ingredients:
            skipped += 1
            continue
        key = (brand.lower(), name.lower())
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        products.append(
            {
                "brand": brand,
                "name": name,
                "category": label,
                "ingredients_text": ingredients,
            }
        )

    products.sort(key=lambda p: (p["brand"].lower(), p["name"].lower()))

    payload = {
        "_meta": {
            "source": "Vazquez/Cosmetic-Price-Analysis cosmetics.csv (Sephora)",
            "source_url": "https://github.com/VazquezJocelyn/Cosmetic-Price-Analysis",
            "license": "MIT",
            "license_holder": "Jocelyn Vazquez",
            "build_date": date.today().isoformat(),
            "kept_fields": ["brand", "name", "category", "ingredients_text"],
            "dropped_fields_reason": (
                "Price/Rank are 2018 vintage and would mislead; "
                "skin-type flags are unsourced subjective labels — "
                "we run our own analysis on the ingredient list."
            ),
            "skipped_rows": skipped,
        },
        "products": products,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(products):,} products to {target} (skipped {skipped} rows)")
    return len(products)


if __name__ == "__main__":
    build()
