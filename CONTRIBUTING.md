# Contributing

Thanks for your interest in improving the dataset. This repository is
an open reference dataset of stock and ETF profiles, refreshed weekly
from upstream sources by GitHub Actions.

## Ways to contribute

### 1. Fix a single record

If a stock or ETF record is wrong (sector mismatch, missing ISIN,
outdated name), open a PR that adds or edits a JSON file under
[`manual_overrides/`](manual_overrides/). The pipeline deep-merges
overrides over the auto-generated record on every refresh, so your
fix survives weekly rebuilds.

Example: to fix Shopify's industry, create
`manual_overrides/CA82509L1076.json`:

```json
{
  "industry": "Internet Retail"
}
```

The filename **must** match the auto-generated `shard_key` (ISIN if
present, else primary symbol). See [`manual_overrides/README.md`](manual_overrides/README.md)
for details.

### 2. Add an ETF to the universe

The list of ETFs we cover lives in
[`config/etf_universe.yml`](config/etf_universe.yml). To request that
a new ETF be tracked, open a PR adding an entry. Provide:

- `ticker` — primary symbol
- `cik` — SEC filer CIK if US-domiciled (10-digit string), else omit
- `name` — fund display name
- `issuer` — iShares, Vanguard, SSGA, Invesco, etc. (used to pick the
  scraper if EDGAR has nothing)

We aim to cover the **top ~300 ETFs by holdings frequency in real
portfolios**, not every ETF in existence. PRs for obscure funds with
no demonstrated user demand may be deferred.

### 3. Improve the pipeline

The build code lives in `scripts/`. Run locally with:

```bash
uv pip install -r scripts/requirements.txt
SEC_USER_AGENT="dev-name dev@example.com" python scripts/build.py
python scripts/validate.py v1/
```

PRs welcome for:

- New normalization rules (e.g. better sector mapping)
- New issuer scrapers (only as fallback for non-US funds)
- Schema additions (must be backwards compatible — additive only on
  minor; breaking changes require `/v2/` path)

### 4. Don't

- ❌ **Don't** import data from Yahoo Finance — their ToS forbids
  redistribution.
- ❌ **Don't** import real-time quotes, OHLCV, fundamentals, or
  analyst ratings — out of scope.
- ❌ **Don't** use proprietary sector taxonomy names (e.g. don't say
  "GICS"). Use the normalized labels in
  [`config/sector_taxonomy.yml`](config/sector_taxonomy.yml).

## PR validation

`.github/workflows/validate-pr.yml` runs `python scripts/validate.py v1/`
on every PR. It checks JSON Schema conformance, weight-sum invariants,
and index consistency. Make sure it passes locally before pushing.

## License

By contributing code, you agree it is licensed under MIT (see
[`LICENSE`](LICENSE)). By contributing data, you agree it is licensed
under CC-BY-NC-SA 4.0 (see [`LICENSE-DATA`](LICENSE-DATA)).

## Takedown / disputes

Email `opensource@wealthfolio.app`. See [`DISCLAIMER.md`](DISCLAIMER.md).
