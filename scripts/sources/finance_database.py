"""FinanceDatabase loader — pulls equities.csv / etfs.csv from
JerBouma/FinanceDatabase (MIT) and yields normalized rows.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Iterator

import pandas as pd

from http_cache import default as default_http

log = logging.getLogger(__name__)

# Raw GitHub URLs. We pin to `main` for the weekly cadence — if upstream
# breaks the schema this is the place we'll notice first.
EQUITIES_CSV_URL = (
    "https://raw.githubusercontent.com/JerBouma/FinanceDatabase/main/database/equities.csv"
)
ETFS_CSV_URL = (
    "https://raw.githubusercontent.com/JerBouma/FinanceDatabase/main/database/etfs.csv"
)
SOURCE_REPO_URL = "https://github.com/JerBouma/FinanceDatabase"


@dataclass(frozen=True)
class FinanceDatabaseSource:
    name: str = "FinanceDatabase"
    license: str = "MIT"
    source_url: str = SOURCE_REPO_URL


def _fetch_csv(url: str) -> pd.DataFrame:
    log.info("fetching %s", url)
    body = default_http().get(url, accept="text/csv")
    df = pd.read_csv(io.BytesIO(body), dtype=str, keep_default_na=False, na_values=[""])
    log.info("loaded %d rows from %s", len(df), url.rsplit("/", 1)[-1])
    return df


def _clean_row(record: dict) -> dict:
    out = {}
    for k, v in record.items():
        if v is None:
            continue
        if isinstance(v, float):
            # pandas may emit NaN as float despite dtype=str
            continue
        s = str(v).strip()
        if not s or s.lower() in ("nan", "none"):
            continue
        out[k] = s
    return out


def fetch_equities() -> Iterator[dict]:
    """Yield equities rows as plain dicts. Empty cells are excluded."""
    df = _fetch_csv(EQUITIES_CSV_URL)
    for record in df.to_dict(orient="records"):
        cleaned = _clean_row(record)
        if cleaned:
            yield cleaned


def fetch_etfs_meta() -> Iterator[dict]:
    """Yield ETF metadata rows (categories, ISINs)."""
    df = _fetch_csv(ETFS_CSV_URL)
    for record in df.to_dict(orient="records"):
        cleaned = _clean_row(record)
        if cleaned:
            yield cleaned
