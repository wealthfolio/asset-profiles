# Asset Profiles — Design & Implementation Spec

**Repo:** `wealthfolio/asset-profiles`
**Consumers:** Wealthfolio desktop/web/mobile clients (this repo)
**Status:** Proposed
**Owner:** TBD
**Last updated:** 2026-05-09

---

## 1. Overview

A public, openly-licensed reference dataset of stock and ETF profile data
(sector, industry, country, ETF holdings, ETF sector/country weights),
published as static JSON over a CDN, refreshed weekly by GitHub Actions.

Wealthfolio clients fetch profile records on demand and cache them locally
to enrich the user's portfolio with allocations, sector breakdowns, and
geographic exposure — without depending on a per-user API key (Finnhub) or a
flaky third-party API (Yahoo).

---

## 2. Goals & Non-goals

### Goals
- **Reliable** sector/industry/country data for stocks across global exchanges.
- **Reliable** sector/country weight + top holdings for the most-held ETFs.
- **Free at the edge** — no API keys, no rate limits for clients.
- **Open & auditable** — every record traceable to its source, dataset under
  CC-BY-NC-SA, code under MIT.
- **Operationally trivial** — one weekly cron, no servers, no secrets to
  rotate.
- **Extensible** — new fields, new asset classes, new sources can be added
  without breaking shipped clients.

### Non-goals
- ❌ Real-time or daily quotes / OHLCV (out of scope; licensing minefield).
- ❌ Fundamentals (earnings, P/E, dividends history).
- ❌ News, ratings, analyst targets.
- ❌ Per-user data of any kind.
- ❌ Coverage of every obscure micro-cap globally; **target the top
  ~80% of holdings by frequency in real Wealthfolio portfolios.**

---

## 3. Architecture

```
                       ┌─────────────────────────────────────┐
                       │  GitHub Actions (cron weekly Sun)   │
                       │  in wealthfolio/asset-profiles      │
                       │                                     │
                       │  scripts/build.py                   │
                       │  ┌───────────────────────────────┐  │
   FinanceDatabase ───▶│  │ pull equities.csv, etfs.csv  │  │
   (MIT, weekly)       │  └───────────────────────────────┘  │
                       │  ┌───────────────────────────────┐  │
   SEC EDGAR    ──────▶│  │ fetch N-PORT for US ETFs     │  │
   (public domain)     │  └───────────────────────────────┘  │
                       │  ┌───────────────────────────────┐  │
   etf-scraper  ──────▶│  │ scrape non-US issuer pages   │  │
   (issuer CSVs)       │  │ (only when EDGAR insufficient)│  │
                       │  └───────────────────────────────┘  │
                       │  ┌───────────────────────────────┐  │
                       │  │ normalize → JSON shards      │  │
                       │  │ build index.json             │  │
                       │  └───────────────────────────────┘  │
                       │            ↓                        │
                       │  git commit + push                  │
                       └────────────────┬────────────────────┘
                                        │
                                        ▼
                       ┌─────────────────────────────────────┐
                       │  GitHub raw / jsDelivr CDN          │
                       │  cdn.jsdelivr.net/gh/...            │
                       │  (later: profiles.wealthfolio.app)  │
                       └────────────────┬────────────────────┘
                                        │ HTTPS GET
                                        ▼
                       ┌─────────────────────────────────────┐
                       │  Wealthfolio client                 │
                       │  ProfileService (Rust, in core)     │
                       │  ├─ fetch index.json on startup     │
                       │  ├─ lazy-fetch per-ticker shards    │
                       │  └─ cache in SQLite with TTL        │
                       └─────────────────────────────────────┘
```

---

## 4. Data Sources

| # | Source                                  | License/Status         | Used for                           | Priority |
| - | --------------------------------------- | ---------------------- | ---------------------------------- | -------- |
| 1 | `JerBouma/FinanceDatabase`              | MIT, weekly auto       | Stock sector/industry/country/ISIN | Primary  |
| 2 | SEC EDGAR (N-PORT, N-CSR)               | US public domain       | US ETF holdings + breakdowns       | Primary  |
| 3 | `etf-scraper` (iShares, Vanguard, SSGA, Invesco issuer CSVs) | Issuer ToS (gray, mitigated) | Non-US ETF holdings, EDGAR gaps    | Fallback |
| 4 | Yahoo Finance                           | ToS forbids redistribution | **NOT used** here. Client-side only. | N/A      |

**Provenance is stamped per record** (`source`, `fetched_at`, `source_url`).
This is a hard requirement — supports takedowns and audit.

---

## 5. JSON Schema

### 5.1 `index.json` (root)

```json
{
  "schema_version": "1.0.0",
  "generated_at": "2026-05-09T06:00:00Z",
  "next_refresh_at": "2026-05-16T06:00:00Z",
  "counts": { "stocks": 12456, "etfs": 312 },
  "symbols": {
    "AAPL":     { "kind": "stock", "path": "stocks/AAPL.json", "isin": "US0378331005" },
    "SHOP":     { "kind": "stock", "path": "stocks/CA82509L1076.json", "isin": "CA82509L1076" },
    "SHOP.TO":  { "kind": "stock", "path": "stocks/CA82509L1076.json", "isin": "CA82509L1076" },
    "SPY":      { "kind": "etf",   "path": "etfs/SPY.json",   "isin": "US78462F1030" }
  },
  "isins": {
    "US0378331005": "stocks/AAPL.json",
    "CA82509L1076": "stocks/CA82509L1076.json"
  }
}
```

**Notes**
- Multiple symbol variants can point to the same path (cross-listings).
- Filenames prefer ISIN when known, fall back to symbol-only (US-listed
  primary) — the index abstracts this from clients.
- Both `symbols` and `isins` indexes — clients can resolve from either.

### 5.2 `stocks/{key}.json`

```json
{
  "schema_version": "1.0.0",
  "kind": "stock",
  "isin": "CA82509L1076",
  "primary_symbol": "SHOP",
  "listings": [
    { "symbol": "SHOP",    "exchange_mic": "XNYS", "currency": "USD" },
    { "symbol": "SHOP.TO", "exchange_mic": "XTSE", "currency": "CAD" }
  ],
  "name": "Shopify Inc.",
  "sector": "Technology",
  "industry_group": "Software & Services",
  "industry": "IT Services",
  "country": "Canada",
  "country_code": "CA",
  "website": "https://www.shopify.com",
  "summary": "Shopify Inc., together with its subsidiaries...",
  "market_cap_band": "Large Cap",
  "identifiers": {
    "isin": "CA82509L1076",
    "cusip": "82509L107",
    "figi": "BBG004DW5JB6",
    "composite_figi": "BBG004DW5JB7"
  },
  "provenance": {
    "source": "FinanceDatabase",
    "source_url": "https://github.com/JerBouma/FinanceDatabase",
    "fetched_at": "2026-05-09T06:00:00Z",
    "license": "MIT"
  }
}
```

### 5.3 `etfs/{key}.json`

```json
{
  "schema_version": "1.0.0",
  "kind": "etf",
  "isin": "US78462F1030",
  "primary_symbol": "SPY",
  "listings": [
    { "symbol": "SPY", "exchange_mic": "ARCX", "currency": "USD" }
  ],
  "name": "SPDR S&P 500 ETF Trust",
  "issuer": "State Street",
  "category_group": "Equity",
  "category": "Large Cap Blend",
  "expense_ratio": 0.0945,
  "aum_usd": 530000000000,
  "inception_date": "1993-01-22",
  "as_of_date": "2026-04-30",
  "sector_weights": [
    { "sector": "Technology", "weight": 0.317 },
    { "sector": "Financials", "weight": 0.142 }
  ],
  "country_weights": [
    { "country": "United States", "country_code": "US", "weight": 0.998 },
    { "country": "Other",          "country_code": null, "weight": 0.002 }
  ],
  "asset_class_weights": [
    { "asset_class": "Equity", "weight": 0.998 },
    { "asset_class": "Cash",   "weight": 0.002 }
  ],
  "top_holdings": [
    { "symbol": "AAPL", "isin": "US0378331005", "name": "Apple Inc.", "weight": 0.071 },
    { "symbol": "MSFT", "isin": "US5949181045", "name": "Microsoft Corp.", "weight": 0.063 }
  ],
  "holdings_count": 503,
  "identifiers": {
    "isin": "US78462F1030",
    "cusip": "78462F103"
  },
  "provenance": {
    "source": "SEC EDGAR N-PORT",
    "source_url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000884394",
    "fetched_at": "2026-05-09T06:00:00Z",
    "license": "public domain"
  }
}
```

### 5.4 Schema rules

- All weights are decimal fractions, not percentages. `0.317` not `31.7`.
- All weights in a list MUST sum to within `1.0 ± 0.005` (tolerance for
  rounding). Reject and log if outside.
- Missing fields are omitted, not `null`. Clients treat missing == unknown.
- `schema_version` follows semver. Breaking changes bump major and live
  under a new path prefix (`/v2/`).
- Dates are ISO 8601 UTC.
- `country_code` is ISO 3166-1 alpha-2. `currency` is ISO 4217.
- `exchange_mic` is ISO 10383 MIC.

---

## 6. Repository Layout

```
asset-profiles/
├── README.md                  # Public-facing intro, usage examples, takedown contact
├── LICENSE                    # MIT (for code in scripts/)
├── LICENSE-DATA               # CC-BY-NC-SA 4.0 (for /v1/**)
├── DISCLAIMER.md              # Not investment advice, no warranty
├── CONTRIBUTING.md            # How to add a new ticker / fix a record
├── schema/
│   ├── stock.schema.json      # JSON Schema for stocks/*.json
│   ├── etf.schema.json        # JSON Schema for etfs/*.json
│   └── index.schema.json
├── scripts/
│   ├── build.py               # Main pipeline entrypoint
│   ├── sources/
│   │   ├── finance_database.py
│   │   ├── edgar.py
│   │   └── issuer_scraper.py
│   ├── normalize.py           # Source records → schema-compliant JSON
│   ├── validate.py            # Run JSON Schema validation on outputs
│   └── requirements.txt
├── config/
│   ├── etf_universe.yml       # List of ~300 ETF tickers to scrape
│   ├── exchange_mic.yml       # Yahoo suffix → MIC mapping (mirror of client)
│   └── sector_taxonomy.yml    # Source-label → normalized-label map
├── v1/
│   ├── index.json
│   ├── stocks/                # one file per stock (keyed by ISIN or symbol)
│   └── etfs/                  # one file per ETF
└── .github/
    └── workflows/
        ├── refresh.yml        # Weekly cron
        └── validate-pr.yml    # Validate JSON Schema on PRs
```

---

## 7. Symbol Resolution & Disambiguation

### Problem
- `SHOP` (NYSE) and `SHOP.TO` (TSX) = same company, same ISIN.
- `BLT` on LSE ≠ `BLT` on US exchange (potentially different securities).
- `BRK.A` ≠ `BRK.B` (different share classes, different ISINs).
- ADRs (`BABA` NYSE) vs underlying (`9988.HK`) — different ISINs but
  same underlying business.

### Rules
1. **Canonical key** for a profile record = ISIN if present, else
   `{primary_symbol}` (US listing assumed primary).
2. **Filename** = `{key}.json`. Multiple symbols → one file via index.
3. **Cross-listings of the same share class** share one file (same ISIN).
4. **Different share classes** = different files (different ISINs).
5. **ADRs and underlying** = different files (different ISINs), but
   `composite_figi` lets clients group later if desired.

### Client lookup ladder (in `ProfileService::resolve`)
```
1. exact symbol in index.symbols → return path
2. asset.isin in index.isins → return path
3. parse_symbol_with_exchange_suffix(symbol) → try base@MIC variants
4. try base symbol alone (assume US primary)
5. miss → return None, fall back to Yahoo enrichment in client
```

---

## 8. Build Pipeline (`scripts/build.py`)

### Inputs
- `config/etf_universe.yml` — list of tickers to fetch ETF data for.
- Network: FinanceDatabase, SEC EDGAR, issuer pages.

### Steps

```python
def main():
    # 1. Stocks: pull and normalize FinanceDatabase
    fd_equities = fetch_finance_database_csv("equities.csv")
    stocks = [normalize_stock(row) for row in fd_equities]

    # 2. ETF metadata: pull FinanceDatabase etfs.csv (categories, ISINs)
    fd_etfs = fetch_finance_database_csv("etfs.csv")
    etf_meta = {row.symbol: normalize_etf_meta(row) for row in fd_etfs}

    # 3. ETF holdings: try EDGAR first, fall back to issuer scraper
    universe = load_yaml("config/etf_universe.yml")
    etfs = []
    for ticker in universe:
        meta = etf_meta.get(ticker, {})
        try:
            holdings = fetch_edgar_nport(ticker)
        except (NotFound, NotUSDomiciled):
            holdings = fetch_issuer_csv(ticker)
        etfs.append(merge_etf(meta, holdings))

    # 4. Validate every record against JSON Schema
    for record in stocks + etfs:
        validate(record, schema_for(record["kind"]))

    # 5. Write shards
    for s in stocks:
        write_shard(f"v1/stocks/{shard_key(s)}.json", s)
    for e in etfs:
        write_shard(f"v1/etfs/{shard_key(e)}.json", e)

    # 6. Build & write index.json
    write_shard("v1/index.json", build_index(stocks, etfs))

    # 7. Print diff summary for commit message
    print_diff_summary()
```

### Required HTTP UA for SEC
```
User-Agent: Wealthfolio asset-profiles bot opensource@wealthfolio.app
```

### Polite scraping
- 1 req/sec max to any single host.
- Honor `robots.txt`.
- Cache HTTP responses to disk during a build to allow re-runs without
  re-hitting issuers.

### Idempotency / change detection
- Compute SHA256 of each output file.
- If unchanged from last commit, don't rewrite (keeps git history clean).
- `index.json` always updates `generated_at` and `next_refresh_at`.

---

## 9. GitHub Actions

### `.github/workflows/refresh.yml`

```yaml
name: Refresh asset profiles

on:
  schedule:
    - cron: '0 6 * * 0'   # Sunday 06:00 UTC
  workflow_dispatch:

permissions:
  contents: write

jobs:
  refresh:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'
      - run: pip install -r scripts/requirements.txt
      - name: Build
        env:
          SEC_USER_AGENT: "Wealthfolio asset-profiles opensource@wealthfolio.app"
        run: python scripts/build.py
      - name: Validate
        run: python scripts/validate.py v1/
      - uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: "chore: refresh profiles ($(date -u +%Y-%m-%d))"
          file_pattern: "v1/**"
```

### `.github/workflows/validate-pr.yml`

```yaml
name: Validate PR

on: pull_request

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -r scripts/requirements.txt
      - run: python scripts/validate.py v1/
```

---

## 10. Publishing & CDN

### Phase 1 (launch)
- Direct fetch from jsDelivr:
  ```
  https://cdn.jsdelivr.net/gh/wealthfolio/asset-profiles@main/v1/index.json
  https://cdn.jsdelivr.net/gh/wealthfolio/asset-profiles@main/v1/stocks/AAPL.json
  ```
- jsDelivr cache TTL ~12h on `@main`. For a weekly refresh, that's fine.
- Pinned versions via tags: `@v1.4.0` for clients that want immutable URLs.

### Phase 2 (when warranted)
- CNAME `profiles.wealthfolio.app` → jsDelivr (or self-hosted).
- Same paths, just stable branded URL. Clients use the alias from day 1
  via a config constant so the swap is invisible.

### Cache headers (if/when self-hosted)
```
Cache-Control: public, max-age=86400, stale-while-revalidate=604800
```

---

## 11. Versioning Policy

- `schema_version` field in every JSON file (semver).
- Path prefix `/v1/`, `/v2/` for breaking changes. Both versions live
  side-by-side during deprecation windows (≥6 months).
- Additive changes (new optional fields) bump minor, no path change.
- Field renames or type changes bump major and require a new path.
- Tagged releases (`git tag v1.x.y`) for pinned client URLs.

---

## 12. Wealthfolio Client Integration

### Where it lives
- New module: `crates/core/src/profiles/`
- New service: `ProfileService` with trait `AssetProfileSource`.
- Integration point: `assets_service::enrich_asset_profile` calls
  `ProfileService::get(asset)` *before* falling back to Yahoo.

### Caching
- SQLite table: `profile_cache`
  ```sql
  CREATE TABLE profile_cache (
      cache_key       TEXT PRIMARY KEY,   -- ISIN or symbol@MIC
      kind            TEXT NOT NULL,
      json            TEXT NOT NULL,
      schema_version  TEXT NOT NULL,
      fetched_at      TEXT NOT NULL,
      expires_at      TEXT NOT NULL
  );
  ```
- Default TTL: 7 days for individual records, 1 day for `index.json`.
- Stale-while-revalidate: serve expired cache immediately, refresh in
  background.

### Resolution flow (in client)
```rust
async fn resolve(&self, asset: &Asset) -> Option<Profile> {
    let index = self.get_index().await.ok()?;
    let path = self.lookup_in_index(&index, asset)?;     // §7 ladder
    self.fetch_or_cached(&path).await.ok()
}
```

### Failure modes
- Network failure → use cached version even if expired.
- 404 → mark "unknown ticker" with short negative-cache TTL (1h) to
  avoid hammering for non-covered symbols.
- Schema mismatch → log and skip; never crash.

### Configuration
- Base URL configurable via env var / settings:
  `ASSET_PROFILES_BASE_URL=https://cdn.jsdelivr.net/gh/wealthfolio/asset-profiles@main`
- Default points at jsDelivr `@main`. Self-hosters can point at their own.

---

## 13. Licensing & Legal Posture

### Licenses
- **Code** (`scripts/`, schemas, workflows) → **MIT**
- **Dataset** (`v1/**`) → **CC-BY-NC-SA 4.0**
  - Forces attribution
  - Forces share-alike
  - Discourages commercial reuse without permission
  - Compatible with the open-source ethos
- **Per-record provenance** preserves source attribution explicitly.

### README must include
- Disclaimer: not investment advice, no warranty of accuracy.
- Source acknowledgments: FinanceDatabase, SEC EDGAR, issuer names.
- **Takedown contact**: `opensource@wealthfolio.app` (or similar) with
  commitment to act within 7 days on legitimate requests.
- Statement of non-commercial intent.

### Sourcing rules (enforced in code)
- ✅ EDGAR first for US ETFs (public domain, no contract).
- ✅ Issuer CSVs only as fallback, with attribution and `as_of_date`.
- ❌ Never include data derived from Yahoo Finance.
- ❌ Never include real-time quotes, OHLCV, or anything from exchange feeds.
- ⚠️ Don't use proprietary sector taxonomy names (don't say "GICS").

### Scope discipline
- Target ~300 most-held ETFs (covers >80% of typical portfolios).
- Don't chase comprehensive global ETF coverage — diminishing returns
  + higher visibility risk.

---

## 14. Phased Implementation

### Phase 0 — Repo bootstrap (1–2 hrs)
- [ ] Create `wealthfolio/asset-profiles` repo (done by user)
- [ ] Add `README.md`, `LICENSE`, `LICENSE-DATA`, `DISCLAIMER.md`
- [ ] Add JSON Schema files
- [ ] Add empty `v1/index.json` skeleton

### Phase 1 — Stocks pipeline (1 day)
- [ ] `scripts/sources/finance_database.py` — pull & parse equities.csv
- [ ] `scripts/normalize.py` — row → stock JSON
- [ ] `scripts/build.py` — wire stocks path end-to-end
- [ ] `scripts/validate.py` — JSON Schema enforcement
- [ ] `.github/workflows/refresh.yml` — weekly cron, runs only stocks
- [ ] First green run; verify output on jsDelivr

### Phase 2 — Client consumption (1–2 days, in `wealthfolio/wealthfolio`)
- [ ] `crates/core/src/profiles/` module
- [ ] `ProfileService` trait + HTTP impl
- [ ] SQLite cache table + migration
- [ ] Wire into `assets_service::enrich_asset_profile`
- [ ] Tauri command exposure if needed by frontend
- [ ] Integration tests with fixtures
- [ ] Frontend: surface sector/country in asset detail page

### Phase 3 — ETFs via EDGAR (2–3 days)
- [ ] `scripts/sources/edgar.py` — CIK lookup, N-PORT fetch, XML parse
- [ ] `config/etf_universe.yml` — top ~300 US ETFs (start with top 50)
- [ ] Sector/country aggregation from holdings
- [ ] Wire into build pipeline

### Phase 4 — ETFs via issuer scrapers (2 days)
- [ ] `scripts/sources/issuer_scraper.py` — wraps `etf-scraper` PyPI lib
- [ ] EDGAR-first, issuer-fallback logic in `build.py`
- [ ] Add ~50 non-US ETFs (UCITS) to universe

### Phase 5 — Polish & launch (1 day)
- [ ] README with usage examples & contributor guide
- [ ] CONTRIBUTING.md
- [ ] Announcement / docs in main Wealthfolio repo
- [ ] Tag `v1.0.0`

---

## 15. Open Questions

1. **Branded domain timing.** Set up `profiles.wealthfolio.app` from
   day 1, or wait until usage proves it out?
2. **ETF universe selection.** Start with a hand-curated top-50 list, or
   derive from Wealthfolio user data? (Privacy implications if the latter.)
3. **Non-US ETF coverage priority.** UCITS first (large EU user base) or
   Canadian-listed ETFs first (TSX has many cross-listings of US funds)?
4. **Frontend surface.** Sector/country pie chart in asset detail page is
   table stakes — anything more ambitious for v1 (portfolio-level
   aggregation)?
5. **Contributor flow.** Allow PRs that hand-edit a single ticker's JSON?
   (Yes for fixes, but auto-rebuild would overwrite — need a
   `manual_overrides/` directory or similar.)
6. **Versioning of CDN URL in client.** Pin to a tag (`@v1.0.0`) or always
   track `@main`? Tag pinning is safer but requires client updates to get
   new fields.
7. **Error telemetry.** Should the client (with user opt-in) report
   missing-ticker resolutions back so we can prioritize coverage?

---

## Appendix A — Estimated dataset size

| Set                     | Records  | Avg size | Total      |
| ----------------------- | -------- | -------- | ---------- |
| Stocks (FinanceDatabase)| ~120,000 | ~1.5 KB  | ~180 MB    |
| ETFs (top 300)          | 300      | ~50 KB   | ~15 MB     |
| `index.json`            | 1        | ~10 MB   | ~10 MB     |
| **Total uncompressed**  |          |          | **~205 MB**|
| **Total gzipped**       |          |          | **~50 MB** |

Well within GitHub's per-file (100 MB) and total-repo (5 GB) limits, and
within jsDelivr's per-file (50 MB) limit. Per-ticker shards keep
individual file sizes <100 KB.

If `index.json` approaches 50 MB, shard it into
`index/by-symbol/{a}.json`, `{b}.json`, ... at that point.

---

## Appendix B — Comparable open-source datasets

- [JerBouma/FinanceDatabase](https://github.com/JerBouma/FinanceDatabase) — direct upstream for stocks
- [nikulpatel3141/ETF-Scraper](https://github.com/nikulpatel3141/ETF-Scraper) — Python lib for issuer scraping
- [edgebips/baskets](https://github.com/edgebips/baskets) — comparable ETF holdings tracker
- [SEC EDGAR full-text search](https://efts.sec.gov/LATEST/search-index?forms=NPORT-P) — EDGAR fund filings
