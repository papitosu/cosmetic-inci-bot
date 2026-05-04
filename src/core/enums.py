from __future__ import annotations

import enum


class SkinType(str, enum.Enum):
    UNKNOWN = "unknown"
    DRY = "dry"
    OILY = "oily"
    COMBINATION = "combination"
    SENSITIVE = "sensitive"
    NORMAL = "normal"
    ACNE_PRONE = "acne_prone"


class AnalysisSource(str, enum.Enum):
    TEXT = "text"
    PHOTO = "photo"
    PRODUCT = "product"
