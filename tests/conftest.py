from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://inci:inci@localhost:5432/inci"
)
