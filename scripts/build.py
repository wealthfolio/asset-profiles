"""Asset Profiles build pipeline.

Produces:
  v1/index.json
  v1/stocks/{ISIN-or-symbol}.json
  v1/etfs/{ISIN-or-symbol}.json

Idempotency: only rewrites a shard file if its SHA256 changed; index.json
is always rewritten because timestamps tick.

Usage:
  SEC_USER_AGENT="name email" python scripts/build.py [--no-etfs] [--limit N]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import sys
import traceback
from pathlib import Path
from typing import Iterable

import yaml

# Allow `from sources.foo import bar` when run as `python scripts/build.py`.
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from sources import finance_database  # noqa: E402
from sources import edgar  # noqa: E402
from sources import issuer_scraper  # noqa: E402

import normalize  # noqa: E402
import validate as validate_mod  # noqa: E402

log = logging.getLogger("build")

REPO_ROOT = SCRIPTS_DIR.parent
CONFIG_DIR = REPO_ROOT / "config"
OVERRIDES_DIR = REPO_ROOT / "manual_overrides"
OUT_DIR = REPO_ROOT / "v1"
SCHEMA_VERSION = "1.0.0"


# ---- helpers ------------------------------------------------------------


def utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def add_days(iso: str, days: int) -> str:
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (t + dt.timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_mappings() -> dict:
    return {
        "exchange_mic": load_yaml(CONFIG_DIR / "exchange_mic.yml"),
        **load_yaml(CONFIG_DIR / "sector_taxonomy.yml"),
    }


def write_if_changed(path: Path, payload: dict, *, summary: dict) -> None:
    """Write `path` only if its serialized form differs from current contents."""
    body = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    new_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if path.exists():
        old_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if old_hash == new_hash:
            summary["unchanged"] += 1
            return
        summary["changed"] += 1
    else:
        summary["added"] += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def reap_removed(directory: Path, current_keys: set[str], summary: dict) -> None:
    if not directory.exists():
        return
    for path in directory.glob("*.json"):
        if path.stem not in current_keys:
            path.unlink()
            summary["removed"] += 1


# ---- stocks pass --------------------------------------------------------


def build_stocks(mappings: dict, fetched_at: str, limit: int | None = None) -> list[dict]:
    iterator = finance_database.fetch_equities()
    normalized = []
    seen = 0
    for row in iterator:
        seen += 1
        rec = normalize.normalize_stock(row, fetched_at=fetched_at, mappings=mappings)
        if rec is not None:
            normalized.append(rec)
            if limit and len(normalized) >= limit:
                break
    log.info("normalized %d stock records (from %d input rows)", len(normalized), seen)
    grouped = normalize.group_cross_listings(normalized)
    log.info("after cross-listing merge: %d records", len(grouped))
    return grouped


# ---- ETFs pass ----------------------------------------------------------


def _index_stocks(stocks: Iterable[dict]) -> tuple[dict, dict]:
    by_isin: dict[str, dict] = {}
    by_symbol: dict[str, dict] = {}
    for s in stocks:
        if s.get("isin"):
            by_isin[s["isin"]] = s
        for lst in s.get("listings", []):
            by_symbol[lst["symbol"]] = s
    return by_isin, by_symbol


def _synthetic_override(ticker: str) -> bool:
    """True if `manual_overrides/{ticker}.json` declares `_synthetic: true`.

    Synthetic records bypass the EDGAR/issuer-scraper fetch entirely; the
    override file alone supplies weights, holdings, and identifiers. Used
    for commodity ETFs (GLD, SLV) whose underlying isn't a security and
    whose upstream filings are degenerate.
    """
    path = OVERRIDES_DIR / f"{ticker}.json"
    if not path.exists():
        return False
    try:
        return bool(json.loads(path.read_text(encoding="utf-8")).get("_synthetic"))
    except (json.JSONDecodeError, OSError):
        return False


def build_etfs(
    universe: list[dict],
    fd_etfs_meta: dict[str, dict],
    stocks_by_isin: dict[str, dict],
    stocks_by_symbol: dict[str, dict],
    fetched_at: str,
    mappings: dict,
) -> tuple[list[dict], list[tuple[str, str]]]:
    """Returns (etf_records, errors). One failed ETF doesn't abort the build."""
    records: list[dict] = []
    errors: list[tuple[str, str]] = []

    for entry in universe:
        ticker = entry.get("ticker")
        if not ticker:
            continue
        meta_extra = dict(fd_etfs_meta.get(ticker, {}))
        meta_extra.update(entry)  # universe-level overrides win
        meta_extra.setdefault("symbol", ticker)

        cik = entry.get("cik")
        source_label = source_url = license_label = None
        holdings = None

        if _synthetic_override(ticker):
            log.info("synthetic override for %s; skipping fetch", ticker)
            holdings = {"as_of_date": None, "total_value": None, "holdings": []}
            source_label = "manual override"
            source_url = (
                "https://github.com/wealthfolio/asset-profiles/blob/main/"
                f"manual_overrides/{ticker}.json"
            )
            license_label = "manual"
        else:
            try:
                if cik:
                    holdings = edgar.fetch_latest_nport(cik)
                    source_label = "SEC EDGAR N-PORT"
                    source_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
                    license_label = "public domain"
            except (edgar.NotFoundError, edgar.NotUSDomiciledError) as e:
                log.info("EDGAR miss for %s: %s; falling back to issuer scraper", ticker, e)
                holdings = None
            except Exception as e:
                log.warning("EDGAR error for %s: %s", ticker, e)

            if holdings is None:
                try:
                    issuer = entry.get("issuer") or meta_extra.get("issuer")
                    holdings = issuer_scraper.fetch_issuer_holdings(ticker, issuer)
                    source_label = f"Issuer holdings ({issuer or 'unknown'})"
                    source_url = issuer_scraper.source_url_for(ticker, issuer)
                    license_label = "issuer ToS (attributed, non-commercial)"
                except Exception as e:
                    msg = f"{type(e).__name__}: {e}"
                    log.error("ETF %s failed: %s", ticker, msg)
                    errors.append((ticker, msg))
                    continue

        try:
            record = normalize.normalize_etf(
                meta_extra,
                holdings,
                fetched_at=fetched_at,
                stocks_by_isin=stocks_by_isin,
                stocks_by_symbol=stocks_by_symbol,
                source_label=source_label,
                source_url=source_url,
                license_label=license_label,
            )
            records.append(record)
        except Exception as e:
            msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            log.error("normalize failed for %s: %s", ticker, e)
            errors.append((ticker, msg))

    return records, errors


# ---- index --------------------------------------------------------------


def build_index(stocks: list[dict], etfs: list[dict], generated_at: str) -> dict:
    symbols: dict[str, dict] = {}
    isins: dict[str, str] = {}

    for record in stocks:
        path = f"stocks/{normalize.shard_key(record)}.json"
        for lst in record.get("listings", []):
            symbols[lst["symbol"]] = _strip_none({
                "kind": "stock",
                "path": path,
                "isin": record.get("isin"),
            })
        if record.get("isin"):
            isins[record["isin"]] = path

    for record in etfs:
        path = f"etfs/{normalize.shard_key(record)}.json"
        for lst in record.get("listings", []):
            symbols[lst["symbol"]] = _strip_none({
                "kind": "etf",
                "path": path,
                "isin": record.get("isin"),
            })
        if record.get("isin"):
            isins[record["isin"]] = path

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "next_refresh_at": add_days(generated_at, 7),
        "counts": {
            "stocks": len(stocks),
            "etfs": len(etfs),
        },
        "symbols": symbols,
        "isins": isins,
    }


def _strip_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


# ---- main ---------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-etfs", action="store_true", help="skip ETF pass (stocks only)")
    parser.add_argument("--no-stocks", action="store_true", help="skip stocks pass (ETFs only)")
    parser.add_argument("--limit", type=int, default=None, help="cap number of stocks (debug)")
    parser.add_argument("--out", default=str(OUT_DIR), help="output root (default: v1/)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "stocks").mkdir(exist_ok=True)
    (out_dir / "etfs").mkdir(exist_ok=True)

    fetched_at = utcnow_iso()
    mappings = load_mappings()
    summary = {"added": 0, "changed": 0, "unchanged": 0, "removed": 0}

    # ---- stocks ----
    stocks: list[dict] = []
    if not args.no_stocks:
        stocks = build_stocks(mappings, fetched_at, limit=args.limit)

        stock_errors = 0
        stock_keys: set[str] = set()
        for rec in stocks:
            rec = normalize.apply_overrides(rec, OVERRIDES_DIR)
            errs = validate_mod.validate_record(rec)
            if errs:
                stock_errors += 1
                for e in errs[:3]:
                    log.warning("stock %s: %s", normalize.shard_key(rec), e)
                continue
            key = normalize.shard_key(rec)
            stock_keys.add(key)
            write_if_changed(out_dir / "stocks" / f"{key}.json", rec, summary=summary)
        reap_removed(out_dir / "stocks", stock_keys, summary)
        log.info("stocks: %d valid, %d invalid", len(stock_keys), stock_errors)

    # ---- ETFs ----
    etfs: list[dict] = []
    etf_errors: list[tuple[str, str]] = []
    if not args.no_etfs:
        universe_path = CONFIG_DIR / "etf_universe.yml"
        if universe_path.exists():
            universe_doc = load_yaml(universe_path)
            universe = universe_doc.get("etfs", []) if isinstance(universe_doc, dict) else universe_doc
            log.info("ETF universe: %d entries", len(universe))

            fd_etf_rows = list(finance_database.fetch_etfs_meta())
            fd_etfs_meta = {r["symbol"]: r for r in fd_etf_rows if r.get("symbol")}

            by_isin, by_symbol = _index_stocks(stocks)
            etfs, etf_errors = build_etfs(
                universe, fd_etfs_meta, by_isin, by_symbol, fetched_at, mappings
            )

            etf_keys: set[str] = set()
            invalid = 0
            for rec in etfs:
                rec = normalize.apply_overrides(rec, OVERRIDES_DIR)
                errs = validate_mod.validate_record(rec)
                if errs:
                    invalid += 1
                    for e in errs[:3]:
                        log.warning("etf %s: %s", normalize.shard_key(rec), e)
                    continue
                key = normalize.shard_key(rec)
                etf_keys.add(key)
                write_if_changed(out_dir / "etfs" / f"{key}.json", rec, summary=summary)
            reap_removed(out_dir / "etfs", etf_keys, summary)
            etfs = [r for r in etfs if normalize.shard_key(r) in etf_keys]
            log.info("etfs: %d valid, %d invalid, %d errors", len(etf_keys), invalid, len(etf_errors))
        else:
            log.info("no etf_universe.yml; skipping ETF pass")

    # ---- index ----
    # Re-load successful records from disk to ensure index reflects post-validation truth.
    valid_stocks: list[dict] = []
    if (out_dir / "stocks").exists():
        for path in (out_dir / "stocks").glob("*.json"):
            valid_stocks.append(json.loads(path.read_text()))
    valid_etfs: list[dict] = []
    if (out_dir / "etfs").exists():
        for path in (out_dir / "etfs").glob("*.json"):
            valid_etfs.append(json.loads(path.read_text()))

    index = build_index(valid_stocks, valid_etfs, generated_at=fetched_at)
    (out_dir / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    log.info(
        "index: %d symbols, %d ISINs (%d stocks, %d ETFs)",
        len(index["symbols"]),
        len(index["isins"]),
        index["counts"]["stocks"],
        index["counts"]["etfs"],
    )

    # ---- summary ----
    log.info(
        "diff: +%d / ~%d / -%d (unchanged %d)",
        summary["added"], summary["changed"], summary["removed"], summary["unchanged"],
    )
    if etf_errors:
        log.warning("ETF errors:")
        for ticker, msg in etf_errors:
            log.warning("  %s: %s", ticker, msg.splitlines()[0])

    return 0


if __name__ == "__main__":
    sys.exit(main())
