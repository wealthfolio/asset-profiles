"""Issuer holdings scraper — fallback for non-US ETFs.

Wraps `etf-scraper` (PyPI: `etf-scraper`, GitHub:
nikulpatel3141/ETF-Scraper) which exposes scrapers for iShares,
Vanguard, SSGA SPDR, and Invesco issuer-published holdings files.

Returns the same shape as `edgar.fetch_latest_nport`:

    {
      "as_of_date": "2026-04-30",
      "total_value": <None or float>,
      "holdings": [
        { "name", "isin", "cusip", "ticker", "weight", "country",
          "country_code", "asset_class" },
        ...
      ],
    }

Polite by construction — etf-scraper uses `requests` under the hood,
and we'd ideally route through `http_cache.default()`. The library
doesn't currently expose a session hook, so we accept its native HTTP
behavior but throttle by limiting how many ETFs we scrape per refresh.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from normalize import alpha2_to_country_name, country_to_alpha2

log = logging.getLogger(__name__)


# ---- supported issuers --------------------------------------------------

# Map issuer hint → (canonical key in etf-scraper, source URL template).
_ISSUER_KEYS = {
    "ishares":   "iShares",
    "vanguard":  "Vanguard",
    "ssga":      "SPDR",
    "spdr":      "SPDR",
    "invesco":   "Invesco",
}

# Per-issuer landing pages used for `provenance.source_url`.
_SOURCE_URLS = {
    "iShares":  "https://www.ishares.com",
    "Vanguard": "https://www.vanguard.com",
    "SPDR":     "https://www.ssga.com/spdrs",
    "Invesco":  "https://www.invesco.com",
}


def source_url_for(ticker: str, issuer_hint: Optional[str]) -> str:
    canonical = _canonical_issuer(issuer_hint)
    return _SOURCE_URLS.get(canonical, "https://github.com/nikulpatel3141/ETF-Scraper")


def _canonical_issuer(hint: Optional[str]) -> Optional[str]:
    if not hint:
        return None
    return _ISSUER_KEYS.get(hint.lower())


# ---- main entry ---------------------------------------------------------


def fetch_issuer_holdings(ticker: str, issuer_hint: Optional[str]) -> dict:
    """Try to fetch holdings via etf-scraper.

    Raises if no scraper supports the issuer or the fetch fails.
    """
    canonical = _canonical_issuer(issuer_hint)
    if canonical is None:
        raise NotImplementedError(
            f"no issuer scraper for {ticker} (issuer={issuer_hint!r})"
        )

    try:
        from etf_scraper import ETFScraper  # type: ignore[import-untyped]
    except ImportError as e:
        raise RuntimeError("etf-scraper not installed; pip install etf-scraper") from e

    scraper = ETFScraper()
    log.info("issuer scrape: ticker=%s issuer=%s", ticker, canonical)
    try:
        df = scraper.query_holdings(ticker, holdings_date=None)
    except Exception as e:
        raise RuntimeError(f"etf-scraper query_holdings({ticker}) failed: {e}") from e

    if df is None or len(df) == 0:
        raise RuntimeError(f"no holdings returned for {ticker}")

    return _df_to_holdings(df, ticker=ticker, issuer=canonical)


def _df_to_holdings(df, *, ticker: str, issuer: str) -> dict:
    """Normalize an etf-scraper DataFrame into our holdings dict."""
    cols = {c.lower(): c for c in df.columns}

    def col(*candidates: str) -> Optional[str]:
        for c in candidates:
            if c in cols:
                return cols[c]
        return None

    name_col   = col("security_name", "name", "holding")
    ticker_col = col("ticker", "symbol", "security_ticker")
    isin_col   = col("isin")
    cusip_col  = col("cusip")
    sedol_col  = col("sedol")
    country_col = col("country", "location", "geography")
    weight_col = col("weight", "% of net assets", "weighting", "percent_of_fund")
    value_col  = col("market_value", "market value", "value")
    asset_col  = col("asset_class", "asset class", "asset_type")
    date_col   = col("as_of_date", "as of date", "fund_holdings_as_of")

    # Decide once per file whether the weight column is in percent (0-100)
    # or fractions (0-1) — per-row heuristics mis-classify small holdings.
    raw_weights = [_to_float(_get(row, weight_col)) for _, row in df.iterrows()] if weight_col else []
    nonneg = [w for w in raw_weights if w is not None]
    weight_divisor = 100.0 if nonneg and max(nonneg) > 1.5 else 1.0

    holdings: list[dict] = []
    for i, (_, row) in enumerate(df.iterrows()):
        raw_w = raw_weights[i] if i < len(raw_weights) else None
        weight = (raw_w / weight_divisor) if raw_w is not None else None

        country_raw = _get(row, country_col)
        country_code = country_to_alpha2(country_raw) if country_raw else None
        country_name = alpha2_to_country_name(country_code) if country_code else country_raw

        h = {
            "name": _get(row, name_col),
            "ticker": _get(row, ticker_col),
            "isin": _get(row, isin_col),
            "cusip": _get(row, cusip_col),
            "sedol": _get(row, sedol_col),
            "weight": weight,
            "value": _to_float(_get(row, value_col)),
            "country": country_name,
            "country_code": country_code,
            "asset_class": _get(row, asset_col) or "Equity",
        }
        holdings.append({k: v for k, v in h.items() if v not in (None, "")})

    as_of = None
    if date_col is not None and len(df) > 0:
        as_of = _get(df.iloc[0], date_col)
    as_of = _normalize_date(as_of)

    return {
        "as_of_date": as_of,
        "total_value": None,
        "holdings": holdings,
    }


def _get(row, col_name):
    if col_name is None:
        return None
    try:
        v = row[col_name]
    except (KeyError, IndexError):
        return None
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "nan", "NaN", "None", "-"):
        return None
    return s


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("$", "").replace("%", ""))
    except (ValueError, TypeError):
        return None


def _normalize_date(s) -> Optional[str]:
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y"):
        try:
            return dt.datetime.strptime(s[: len(fmt) + 4], fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return dt.date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return None
