"""Source records → schema-compliant JSON.

Two main entry points:

  - normalize_stock(row, *, fetched_at, mappings) → stock dict
  - normalize_etf(meta, holdings, *, fetched_at, stocks_by_isin, source) → etf dict

Plus helpers:

  - shard_key(record): ISIN if present, else primary_symbol
  - group_cross_listings(stocks): merge same-ISIN stock rows into one
  - apply_overrides(record, overrides_dir): deep-merge per-record patch
"""

from __future__ import annotations

import copy
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional

import pycountry

SCHEMA_VERSION = "1.0.0"

log = logging.getLogger(__name__)


# ---- mappings -----------------------------------------------------------


def country_to_alpha2(country_name: str | None) -> Optional[str]:
    if not country_name:
        return None
    name = country_name.strip()
    if not name:
        return None
    # Common upstream variants
    aliases = {
        "United States": "US",
        "USA": "US",
        "U.S.A.": "US",
        "U.S.": "US",
        "United Kingdom": "GB",
        "UK": "GB",
        "South Korea": "KR",
        "Korea, South": "KR",
        "North Korea": "KP",
        "Russia": "RU",
        "Vietnam": "VN",
        "Taiwan": "TW",
        "Iran": "IR",
        "Czech Republic": "CZ",
        "Slovakia": "SK",
    }
    if name in aliases:
        return aliases[name]
    try:
        c = pycountry.countries.lookup(name)
        return c.alpha_2
    except LookupError:
        return None


def alpha2_to_country_name(code: str | None) -> Optional[str]:
    if not code:
        return None
    try:
        return pycountry.countries.get(alpha_2=code.upper()).name  # type: ignore[union-attr]
    except (KeyError, AttributeError):
        return None


# ---- core ---------------------------------------------------------------


def _strip_empty(d: dict) -> dict:
    """Remove keys whose value is None / empty string / empty container.

    Spec rule: "Missing fields are omitted, not null."
    """
    out: dict = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, str) and v == "":
            continue
        if isinstance(v, dict):
            v = _strip_empty(v)
            if not v:
                continue
        if isinstance(v, list) and not v:
            continue
        out[k] = v
    return out


def _resolve_mic_for_symbol(symbol: str, mic_map: dict) -> Optional[str]:
    # exact suffix (e.g. ".TO")
    for suffix, mic in mic_map.items():
        if suffix and symbol.endswith(suffix):
            return mic
    # bare symbol → default ("" key)
    return mic_map.get("")


def _yahoo_currency_for_mic(mic: str | None) -> Optional[str]:
    """Best-effort: pick a currency from the exchange MIC."""
    if not mic:
        return None
    return {
        "XNYS": "USD", "XNAS": "USD", "ARCX": "USD", "BATS": "USD",
        "XTSE": "CAD", "XTSX": "CAD", "XCNQ": "CAD", "NEOE": "CAD",
        "XLON": "GBP", "XAMS": "EUR", "XPAR": "EUR", "XBRU": "EUR",
        "XLIS": "EUR", "XMIL": "EUR", "XMAD": "EUR", "XETR": "EUR",
        "XFRA": "EUR", "XSTU": "EUR", "XWBO": "EUR", "XSWX": "CHF",
        "XOSL": "NOK", "XSTO": "SEK", "XHEL": "EUR", "XCSE": "DKK",
        "XIST": "TRY", "XWAR": "PLN", "XPRA": "CZK", "XATH": "EUR",
        "XDUB": "EUR", "XHKG": "HKD", "XTKS": "JPY", "XKRX": "KRW",
        "XKOS": "KRW", "XSHG": "CNY", "XSHE": "CNY", "XTAI": "TWD",
        "XSES": "SGD", "XKLS": "MYR", "XBKK": "THB", "XIDX": "IDR",
        "XNSE": "INR", "XBOM": "INR", "XASX": "AUD", "XNZE": "NZD",
        "XSAU": "SAR", "DSMD": "QAR", "XADS": "AED", "DIFX": "AED",
        "XCAI": "EGP", "XJSE": "ZAR", "BVMF": "BRL", "XSGO": "CLP",
        "XBUE": "ARS", "XMEX": "MXN",
    }.get(mic)


# ---- stocks -------------------------------------------------------------


def normalize_stock(
    row: dict,
    *,
    fetched_at: str,
    mappings: dict,
) -> Optional[dict]:
    """FinanceDatabase equities.csv row → stock dict.

    Returns None if the row is unusable (no symbol, no name).
    """
    # FinanceDatabase columns vary slightly across releases. Probe each.
    symbol = (row.get("symbol") or row.get("Symbol") or "").strip()
    name = (row.get("name") or row.get("long_name") or row.get("Name") or "").strip()
    if not symbol or not name:
        return None

    sector_map = mappings.get("finance_database", {}).get("sectors", {})
    raw_sector = row.get("sector") or row.get("Sector")
    sector = sector_map.get(raw_sector, raw_sector) if raw_sector else None

    industry_group = row.get("industry_group") or row.get("Industry Group")
    industry = row.get("industry") or row.get("Industry")

    country_name = row.get("country") or row.get("Country")
    country_code = country_to_alpha2(country_name)
    # Re-canonicalize country name from alpha-2 for consistency.
    if country_code:
        canonical = alpha2_to_country_name(country_code) or country_name
        country_name = canonical

    isin = (row.get("isin") or row.get("ISIN") or "").strip() or None
    cusip = (row.get("cusip") or row.get("CUSIP") or "").strip() or None
    figi = (row.get("figi") or row.get("FIGI") or "").strip() or None
    composite_figi = (row.get("composite_figi") or "").strip() or None

    market_cap_band = row.get("market_cap")
    market_cap_band = market_cap_band if market_cap_band in {
        "Nano Cap", "Micro Cap", "Small Cap", "Mid Cap", "Large Cap", "Mega Cap"
    } else None

    summary = row.get("summary") or row.get("Description")
    website = row.get("website") or row.get("Website")
    if website and not website.startswith(("http://", "https://")):
        website = "https://" + website

    mic = _resolve_mic_for_symbol(symbol, mappings.get("exchange_mic", {}))
    currency = _yahoo_currency_for_mic(mic)
    listing = _strip_empty({
        "symbol": symbol,
        "exchange_mic": mic,
        "currency": currency,
    })

    record = {
        "schema_version": SCHEMA_VERSION,
        "kind": "stock",
        "isin": isin,
        "primary_symbol": symbol,
        "listings": [listing],
        "name": name,
        "sector": sector,
        "industry_group": industry_group,
        "industry": industry,
        "country": country_name,
        "country_code": country_code,
        "website": website,
        "summary": summary,
        "market_cap_band": market_cap_band,
        "identifiers": _strip_empty({
            "isin": isin,
            "cusip": cusip,
            "figi": figi,
            "composite_figi": composite_figi,
        }),
        "provenance": {
            "source": "FinanceDatabase",
            "source_url": "https://github.com/JerBouma/FinanceDatabase",
            "fetched_at": fetched_at,
            "license": "MIT",
        },
    }
    return _strip_empty(record)


def shard_key(record: dict) -> str:
    """Filename stem: ISIN if known, else primary symbol."""
    return record.get("isin") or record["primary_symbol"]


def group_cross_listings(stocks: Iterable[dict]) -> list[dict]:
    """Merge multiple stock records sharing the same ISIN into one record.

    Cross-listings keep distinct `listings[]` entries but a single
    canonical record per ISIN. Records without an ISIN pass through
    untouched (keyed by symbol).
    """
    by_isin: dict[str, list[dict]] = defaultdict(list)
    no_isin: list[dict] = []
    for s in stocks:
        if s.get("isin"):
            by_isin[s["isin"]].append(s)
        else:
            no_isin.append(s)

    merged: list[dict] = list(no_isin)
    for isin, group in by_isin.items():
        if len(group) == 1:
            merged.append(group[0])
            continue
        # Pick US-listing as primary if available; else first.
        def is_us(rec: dict) -> bool:
            for lst in rec.get("listings", []):
                if lst.get("exchange_mic") in {"XNYS", "XNAS", "ARCX", "BATS"}:
                    return True
            return False

        group_sorted = sorted(group, key=lambda r: (not is_us(r),))
        primary = copy.deepcopy(group_sorted[0])
        seen = {(l["symbol"], l.get("exchange_mic")) for l in primary["listings"]}
        for other in group_sorted[1:]:
            for lst in other.get("listings", []):
                key = (lst["symbol"], lst.get("exchange_mic"))
                if key not in seen:
                    primary["listings"].append(lst)
                    seen.add(key)
            # Fill in missing scalar fields from secondary records.
            for k, v in other.items():
                if k in {"listings", "primary_symbol"}:
                    continue
                if k not in primary or not primary.get(k):
                    primary[k] = v
        merged.append(primary)
    return merged


# ---- ETFs ---------------------------------------------------------------


def normalize_etf(
    meta: dict,
    holdings: dict,
    *,
    fetched_at: str,
    stocks_by_isin: dict[str, dict],
    stocks_by_symbol: dict[str, dict],
    source_label: str,
    source_url: str,
    license_label: str,
) -> dict:
    """Build an ETF record from FinanceDatabase metadata + parsed holdings.

    `holdings` is the structured object returned by the EDGAR / issuer
    scraper modules (see edgar.py:fetch_latest_nport for shape):

        {
            "as_of_date": "2026-04-30",
            "total_value": 530_000_000_000.0,   # optional
            "holdings": [
                {
                    "name": "Apple Inc.",
                    "isin": "US0378331005",
                    "cusip": "037833100",
                    "ticker": "AAPL",
                    "weight": 0.071,
                    "country": "United States",
                    "country_code": "US",
                    "asset_class": "Equity",
                    "value": 37_000_000_000.0,
                },
                ...
            ],
        }

    `stocks_by_isin` / `stocks_by_symbol` come from the stocks pass and
    are used to look up sector / country for holdings that didn't carry
    that info in the filing.
    """
    primary_symbol = (meta.get("symbol") or meta.get("ticker") or "").strip()
    if not primary_symbol:
        raise ValueError("ETF metadata missing symbol/ticker")

    name = (meta.get("name") or meta.get("long_name") or primary_symbol).strip()

    listings = [_strip_empty({
        "symbol": primary_symbol,
        "exchange_mic": meta.get("exchange_mic"),
        "currency": meta.get("currency") or _yahoo_currency_for_mic(meta.get("exchange_mic")),
    })]

    enriched = [_enrich_holding(h, stocks_by_isin, stocks_by_symbol) for h in holdings.get("holdings", [])]

    sector_weights = _aggregate_weights(enriched, "sector")
    country_weights = _aggregate_country_weights(enriched)
    asset_class_weights = _aggregate_weights(enriched, "asset_class")

    top_holdings = sorted(enriched, key=lambda h: h.get("weight") or 0.0, reverse=True)[:10]
    top_holdings = [_strip_empty({
        "symbol": h.get("ticker"),
        "isin":   h.get("isin"),
        "cusip":  h.get("cusip"),
        "name":   h.get("name"),
        "weight": h.get("weight"),
    }) for h in top_holdings]

    record = {
        "schema_version": SCHEMA_VERSION,
        "kind": "etf",
        "isin": meta.get("isin"),
        "primary_symbol": primary_symbol,
        "listings": listings,
        "name": name,
        "issuer": meta.get("issuer"),
        "category_group": meta.get("category_group"),
        "category": meta.get("category"),
        "expense_ratio": _to_float(meta.get("expense_ratio")),
        "aum_usd": _to_float(meta.get("aum_usd")) or _to_float(holdings.get("total_value")),
        "inception_date": meta.get("inception_date"),
        "as_of_date": holdings.get("as_of_date"),
        "sector_weights": sector_weights,
        "country_weights": country_weights,
        "asset_class_weights": asset_class_weights,
        "top_holdings": top_holdings,
        "holdings_count": len(enriched) if enriched else None,
        "identifiers": _strip_empty({
            "isin":  meta.get("isin"),
            "cusip": meta.get("cusip"),
        }),
        "provenance": {
            "source": source_label,
            "source_url": source_url,
            "fetched_at": fetched_at,
            "license": license_label,
        },
    }
    return _strip_empty(record)


def _to_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _enrich_holding(
    h: dict,
    stocks_by_isin: dict[str, dict],
    stocks_by_symbol: dict[str, dict],
) -> dict:
    """Fill in sector / country / asset_class from the stock dataset when missing."""
    enriched = dict(h)
    stock = None
    if h.get("isin"):
        stock = stocks_by_isin.get(h["isin"])
    if stock is None and h.get("ticker"):
        stock = stocks_by_symbol.get(h["ticker"])
    if stock is not None:
        if not enriched.get("sector"):
            enriched["sector"] = stock.get("sector")
        if not enriched.get("country"):
            enriched["country"] = stock.get("country")
        if not enriched.get("country_code"):
            enriched["country_code"] = stock.get("country_code")
    # Guarantee a value for every field we aggregate on, so weights sum cleanly.
    if not enriched.get("sector"):
        enriched["sector"] = "Unknown"
    if not enriched.get("country"):
        enriched["country"] = "Unknown"
        enriched.setdefault("country_code", None)
    if not enriched.get("asset_class"):
        enriched["asset_class"] = "Other"
    return enriched


def _aggregate_weights(holdings: list[dict], key: str) -> list[dict]:
    """Sum `weight` per `key` value, then renormalize to sum to 1.0.

    Renormalization absorbs float drift and the difference between
    `total_value` (net assets) and the sum of holding values (gross),
    so the sum-to-1 invariant is met by construction for long-only ETFs.
    """
    totals: dict[str, float] = defaultdict(float)
    for h in holdings:
        val = h.get(key)
        w = h.get("weight")
        if val and w is not None:
            totals[val] += float(w)
    return _renormalized(totals, key)


def _aggregate_country_weights(holdings: list[dict]) -> list[dict]:
    """Country weights need both `country` (display) and `country_code`."""
    totals: dict[tuple[str, Optional[str]], float] = defaultdict(float)
    for h in holdings:
        country = h.get("country")
        code = h.get("country_code")
        w = h.get("weight")
        if country and w is not None:
            totals[(country, code)] += float(w)
    total = sum(totals.values())
    if total <= 0:
        return []
    out = [
        _strip_empty({
            "country": c,
            "country_code": code,
            "weight": round(w / total, 6),
        })
        for (c, code), w in totals.items()
    ]
    out.sort(key=lambda d: d["weight"], reverse=True)
    return out


def _renormalized(totals: dict[str, float], key: str) -> list[dict]:
    total = sum(totals.values())
    if total <= 0:
        return []
    out = [{key: k, "weight": round(v / total, 6)} for k, v in totals.items()]
    out.sort(key=lambda d: d["weight"], reverse=True)
    return out


# ---- manual overrides ---------------------------------------------------


def apply_overrides(record: dict, overrides_dir: Path) -> dict:
    """Deep-merge `overrides_dir/{shard_key}.json` over `record`."""
    key = shard_key(record)
    path = overrides_dir / f"{key}.json"
    if not path.exists():
        return record
    try:
        patch = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        log.warning("invalid override %s: %s", path, e)
        return record
    patch.pop("_note", None)  # documentation, not data
    merged = _deep_merge(record, patch)
    log.info("applied override %s.json", key)
    return merged


def _deep_merge(base: dict, patch: dict) -> dict:
    """Deep-merge dict-of-dicts; non-dict values (incl. lists) replace wholesale."""
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out
