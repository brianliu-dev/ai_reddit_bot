"""Raw-pull retention. Keeps each week's JSON for later trend-over-time analysis."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import config


def save_raw(run: dict, when: dt.date | None = None) -> Path:
    when = when or dt.date.today()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.DATA_DIR / f"{when.isoformat()}.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(run, fh, ensure_ascii=False, indent=2)
    return path


def load_raw(when: dt.date) -> dict:
    path = config.DATA_DIR / f"{when.isoformat()}.json"
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
