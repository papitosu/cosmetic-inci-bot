"""In-memory ingredients database.

Structure:
    - canonical INCI dict (~28K) loaded from data/inci_dict.csv
    - overlay JSONs (comedogenic, allergens, beneficial, irritants)
    - synonyms map for trivial-name fallback

At load time, overlay keys are passed through the synonyms map so they end up
keyed by the dataset-canonical form whenever such a form exists. Overlay-only
entries (e.g. "parfum", "alcohol denat") remain under their original key and
are surfaced as canonicals via an explicit cascade step in `lookup`.
"""
from __future__ import annotations

import csv
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

from src.core.config import DATA_DIR

_NORMALIZE_RE = re.compile(r"\s+")
_PAREN_RE = re.compile(r"\([^)]*\)")
_PERCENT_RE = re.compile(r"\b\d+([.,]\d+)?\s*%")


def normalize(text: str) -> str:
    if not text:
        return ""
    s = text.strip().lower()
    s = _PERCENT_RE.sub("", s)
    s = _PAREN_RE.sub("", s)
    s = s.replace("&", " and ")
    s = s.replace("\u00a0", " ")
    s = _NORMALIZE_RE.sub(" ", s).strip(" ,.;:-*•·")
    return s


@dataclass(frozen=True)
class IngredientRecord:
    name: str
    cosing_id: str | None
    cas_no: str | None
    ec_no: str | None
    pubchem_cid: str | None
    pubchem_url: str | None


@dataclass
class IngredientFlags:
    comedogenic_rating: int | None = None
    is_allergen: bool = False
    allergen_severity: str | None = None
    allergen_note: str | None = None
    is_irritant: bool = False
    irritant_severity: str | None = None
    irritant_category: str | None = None
    irritant_note: str | None = None
    benefit_category: str | None = None
    benefit_tags: list[str] = field(default_factory=list)
    benefit_note: str | None = None
    regulatory_annexes: list[str] = field(default_factory=list)
    regulatory_refs: list[str] = field(default_factory=list)
    regulatory_cmr: list[str] = field(default_factory=list)
    # Per-annex restriction rows (max_conc, product_type, warning) sourced from
    # data/annex_details.json (mirror of EU Annex II–VI tables).
    regulatory_details: list[dict[str, Any]] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)


@dataclass
class IngredientMatch:
    canonical: str | None
    record: IngredientRecord | None
    flags: IngredientFlags
    raw: str
    matched_via: str
    confidence: float


class IngredientsDB:
    def __init__(self) -> None:
        self._dict: dict[str, IngredientRecord] = {}
        self._names: list[str] = []
        # Bucket names by first character to keep fuzzy lookups O(N/26) per call.
        self._names_by_initial: dict[str, list[str]] = {}
        self._comedogenic: dict[str, int] = {}
        self._allergens: dict[str, dict[str, Any]] = {}
        self._irritants: dict[str, dict[str, Any]] = {}
        self._beneficial: dict[str, dict[str, Any]] = {}
        self._regulatory: dict[str, dict[str, Any]] = {}
        self._annex_details: dict[str, list[dict[str, Any]]] = {}
        self._functions: dict[str, list[str]] = {}
        self._synonyms: dict[str, str] = {}
        self._loaded = False
        self._lock = threading.Lock()

    def load(self, data_dir: Path | None = None) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            base = data_dir or DATA_DIR
            self._load_dict(base / "inci_dict.csv")
            self._load_synonyms(base / "synonyms.json")

            self._comedogenic = self._load_overlay_int(base / "comedogenic.json", "ingredients")
            self._allergens = self._load_overlay_dict(base / "allergens.json", "allergens")
            self._irritants = self._load_overlay_dict(base / "irritants.json", "ingredients")
            self._beneficial = self._load_overlay_dict(base / "beneficial.json", "ingredients")
            self._regulatory = self._load_overlay_dict(base / "regulatory.json", "ingredients")
            self._annex_details = self._load_overlay_listdict(
                base / "annex_details.json", "ingredients"
            )
            self._functions = self._load_overlay_list(base / "functions.json", "ingredients")
            self._loaded = True

    def _load_dict(self, path: Path) -> None:
        if not path.exists():
            return
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("name") or "").strip().lower()
                if not name:
                    continue
                rec = IngredientRecord(
                    name=name,
                    cosing_id=(row.get("substanceId") or "").strip() or None,
                    cas_no=(row.get("casNo") or "").strip() or None,
                    ec_no=(row.get("ecNo") or "").strip() or None,
                    pubchem_cid=(row.get("pubchem_cid") or "").strip() or None,
                    pubchem_url=(row.get("pubchem") or "").strip() or None,
                )
                self._dict[name] = rec
        self._names = list(self._dict.keys())
        for name in self._names:
            initial = name[:1]
            self._names_by_initial.setdefault(initial, []).append(name)

    def _load_synonyms(self, path: Path) -> None:
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        for src, dst in (data.get("synonyms") or {}).items():
            self._synonyms[normalize(src)] = normalize(dst)

    def _load_overlay_int(self, path: Path, root_key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        if not path.exists():
            return out
        data = json.loads(path.read_text(encoding="utf-8"))
        for raw_name, value in (data.get(root_key) or {}).items():
            try:
                v = int(value)
            except (TypeError, ValueError):
                continue
            for k in self._overlay_keys(raw_name):
                out[k] = v
        return out

    def _load_overlay_dict(self, path: Path, root_key: str) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        if not path.exists():
            return out
        data = json.loads(path.read_text(encoding="utf-8"))
        for raw_name, value in (data.get(root_key) or {}).items():
            if not isinstance(value, dict):
                continue
            for k in self._overlay_keys(raw_name):
                out[k] = value
        return out

    def _load_overlay_list(self, path: Path, root_key: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        if not path.exists():
            return out
        data = json.loads(path.read_text(encoding="utf-8"))
        for raw_name, value in (data.get(root_key) or {}).items():
            if not isinstance(value, list):
                continue
            cleaned = [str(v).strip() for v in value if str(v).strip()]
            if not cleaned:
                continue
            for k in self._overlay_keys(raw_name):
                out[k] = cleaned
        return out

    def _load_overlay_listdict(
        self, path: Path, root_key: str
    ) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        if not path.exists():
            return out
        data = json.loads(path.read_text(encoding="utf-8"))
        for raw_name, value in (data.get(root_key) or {}).items():
            if not isinstance(value, list):
                continue
            cleaned = [v for v in value if isinstance(v, dict) and v]
            if not cleaned:
                continue
            for k in self._overlay_keys(raw_name):
                out[k] = cleaned
        return out

    def _overlay_keys(self, raw_name: str) -> list[str]:
        """Return the list of keys an overlay entry should be indexed under.

        Always include the normalized original (so overlay-only entries like
        "parfum" stay reachable). Additionally include the synonym target so
        the entry is reachable via dataset-canonical names too.
        """
        n = normalize(raw_name)
        keys = [n]
        target = self._synonyms.get(n)
        if target and target != n:
            keys.append(target)
        return keys

    @property
    def size(self) -> int:
        return len(self._dict)

    def get(self, name: str) -> IngredientRecord | None:
        return self._dict.get(name)

    def has_overlay(self, name: str) -> bool:
        return (
            name in self._comedogenic
            or name in self._allergens
            or name in self._irritants
            or name in self._beneficial
            or name in self._regulatory
            or name in self._annex_details
            or name in self._functions
        )

    def lookup(self, raw: str, fuzzy_threshold: float = 92.0) -> IngredientMatch:
        normalized = normalize(raw)
        if not normalized:
            return IngredientMatch(
                canonical=None,
                record=None,
                flags=IngredientFlags(),
                raw=raw,
                matched_via="empty",
                confidence=0.0,
            )

        if normalized in self._synonyms:
            canonical = self._synonyms[normalized]
            rec = self._dict.get(canonical)
            return IngredientMatch(
                canonical=canonical,
                record=rec,
                flags=self._build_flags(canonical),
                raw=raw,
                matched_via="synonym",
                confidence=100.0,
            )

        if normalized in self._dict:
            return IngredientMatch(
                canonical=normalized,
                record=self._dict[normalized],
                flags=self._build_flags(normalized),
                raw=raw,
                matched_via="exact",
                confidence=100.0,
            )

        if self.has_overlay(normalized):
            return IngredientMatch(
                canonical=normalized,
                record=None,
                flags=self._build_flags(normalized),
                raw=raw,
                matched_via="overlay",
                confidence=100.0,
            )

        if not self._names:
            return IngredientMatch(
                canonical=None,
                record=None,
                flags=IngredientFlags(),
                raw=raw,
                matched_via="unknown",
                confidence=0.0,
            )

        candidates = self._names_by_initial.get(normalized[:1]) or self._names
        match = process.extractOne(
            normalized,
            candidates,
            scorer=fuzz.token_set_ratio,
            score_cutoff=fuzzy_threshold,
        )
        if match is not None:
            best_name, score, _ = match
            return IngredientMatch(
                canonical=best_name,
                record=self._dict[best_name],
                flags=self._build_flags(best_name),
                raw=raw,
                matched_via="fuzzy",
                confidence=float(score),
            )

        return IngredientMatch(
            canonical=None,
            record=None,
            flags=IngredientFlags(),
            raw=raw,
            matched_via="unknown",
            confidence=0.0,
        )

    def _build_flags(self, canonical: str) -> IngredientFlags:
        flags = IngredientFlags()
        if canonical in self._comedogenic:
            flags.comedogenic_rating = int(self._comedogenic[canonical])
        if canonical in self._allergens:
            entry = self._allergens[canonical] or {}
            flags.is_allergen = True
            flags.allergen_severity = entry.get("severity")
            flags.allergen_note = entry.get("note")
        if canonical in self._irritants:
            entry = self._irritants[canonical] or {}
            flags.is_irritant = True
            flags.irritant_severity = entry.get("severity")
            flags.irritant_category = entry.get("category")
            flags.irritant_note = entry.get("note")
        if canonical in self._beneficial:
            entry = self._beneficial[canonical] or {}
            flags.benefit_category = entry.get("category")
            flags.benefit_tags = list(entry.get("tags") or [])
            flags.benefit_note = entry.get("note")
        annexes: list[str] = []
        refs: list[str] = []
        cmr: list[str] = []
        if canonical in self._regulatory:
            entry = self._regulatory[canonical] or {}
            annexes.extend(entry.get("annexes") or [])
            refs.extend(entry.get("refs") or [])
            cmr.extend(entry.get("cmr") or [])
        # Merge in detailed restriction rows so annex membership stays consistent
        # whether we sourced it from the inventory snapshot or the Annex CSVs.
        details: list[dict[str, Any]] = []
        if canonical in self._annex_details:
            for entry in self._annex_details[canonical]:
                details.append(entry)
                ann = entry.get("annex")
                if ann and ann not in annexes:
                    annexes.append(ann)
                ref = entry.get("ref")
                if ref and ref not in refs:
                    refs.append(ref)
        flags.regulatory_annexes = annexes
        flags.regulatory_refs = refs
        flags.regulatory_cmr = cmr
        flags.regulatory_details = details
        if canonical in self._functions:
            flags.functions = list(self._functions[canonical])
        return flags


_db_singleton: IngredientsDB | None = None


def get_db() -> IngredientsDB:
    global _db_singleton
    if _db_singleton is None:
        _db_singleton = IngredientsDB()
        _db_singleton.load()
    return _db_singleton
