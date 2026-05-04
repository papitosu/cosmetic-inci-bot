"""Tesseract-based OCR with light preprocessing.

Reads bytes -> preprocessed image -> tesseract eng+rus -> post-processed text.
Designed for cosmetic ingredient labels: a single block of text with
relatively clean fonts.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re

import cv2
import numpy as np
import pytesseract
from PIL import Image

from src.core.config import get_settings

log = logging.getLogger(__name__)


_PREFIX_RE = re.compile(
    r"(ingredient[s]?|ingredient[ie]nti|ingr[eé]dient[s]?|состав|ингредиенты|inci)\s*[:\-–]?\s*",
    re.IGNORECASE,
)
_HYPHEN_NEWLINE_RE = re.compile(r"-\s*\n\s*")


def _configure_tesseract() -> None:
    settings = get_settings()
    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd


def _preprocess(image_bytes: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    arr = np.array(img)
    if arr.ndim == 3:
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    else:
        gray = arr

    h, w = gray.shape[:2]
    scale = 1.0
    if max(h, w) < 1200:
        scale = 1200 / max(h, w)
    if scale != 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    gray = cv2.bilateralFilter(gray, d=7, sigmaColor=75, sigmaSpace=75)
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, blockSize=31, C=10,
    )
    kernel = np.ones((1, 1), np.uint8)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
    return thresh


def _postprocess(text: str) -> str:
    if not text:
        return ""
    s = _HYPHEN_NEWLINE_RE.sub("", text)
    s = s.replace("\u00a0", " ")
    s = _PREFIX_RE.sub("", s)
    s = re.sub(r"[|]+", "", s)
    s = re.sub(r"\s*\n+\s*", ", ", s)
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" ,.;:")
    return s


def _ocr_sync(image_bytes: bytes, langs: str) -> str:
    _configure_tesseract()
    img = _preprocess(image_bytes)
    try:
        text = pytesseract.image_to_string(img, lang=langs, config="--oem 1 --psm 6")
    except pytesseract.TesseractError as e:
        log.warning("Tesseract error with langs=%s: %s; retrying with eng only", langs, e)
        text = pytesseract.image_to_string(img, lang="eng", config="--oem 1 --psm 6")
    return _postprocess(text)


async def extract_ingredients_from_image(image_bytes: bytes) -> str:
    settings = get_settings()
    return await asyncio.to_thread(_ocr_sync, image_bytes, settings.tesseract_langs)
