# asset-profiles

Open reference dataset of stock and ETF profile data — sector, industry,
country, ETF holdings, ETF sector/country weights — published as static
JSON over a CDN, refreshed weekly by GitHub Actions.

Built for the [Wealthfolio](https://github.com/wealthfolio) clients to
enrich portfolios with allocations, sector breakdowns, and geographic
exposure without per-user API keys or fragile third-party APIs.

- **Schema version:** `1.0.0`
- **Refresh cadence:** weekly (Sunday 06:00 UTC)
- **License (code):** MIT — see [`LICENSE`](LICENSE)
- **License (data):** CC-BY-NC-SA 4.0 — see [`LICENSE-DATA`](LICENSE-DATA)
- **Disclaimer:** [`DISCLAIMER.md`](DISCLAIMER.md) — not investment advice
- **Spec:** [`docs/asset-profiles-spec.md`](docs/asset-profiles-spec.md)

## CDN URLs

The dataset is served free at the edge by
[jsDelivr](https://www.jsdelivr.com/) directly from the GitHub repo:

```
https://cdn.jsdelivr.net/gh/wealthfolio/asset-profiles@main/v1/index.json
https://cdn.jsdelivr.net/gh/wealthfolio/asset-profiles@main/v1/stocks/AAPL.json
https://cdn.jsdelivr.net/gh/wealthfolio/asset-profiles@main/v1/etfs/SPY.json
```

For pinned, immutable URLs, use a tag instead of `@main`:

```
https://cdn.jsdelivr.net/gh/wealthfolio/asset-profiles@v1.0.0/v1/index.json
```

jsDelivr caches `@main` for ~12 hours. For a weekly refresh, that's
fine; for production clients, pin to a tag.

## Usage examples

### curl

```bash
curl -s https://cdn.jsdelivr.net/gh/wealthfolio/asset-profiles@main/v1/index.json \
  | jq '.symbols.AAPL'
# → { "kind": "stock", "path": "stocks/AAPL.json", "isin": "US0378331005" }

curl -s https://cdn.jsdelivr.net/gh/wealthfolio/asset-profiles@main/v1/stocks/AAPL.json \
  | jq '{ name, sector, country }'
```

### JavaScript / TypeScript

```ts
const BASE = "https://cdn.jsdelivr.net/gh/wealthfolio/asset-profiles@main/v1";

const index = await fetch(`${BASE}/index.json`).then(r => r.json());

async function profileFor(symbol: string) {
  const entry = index.symbols[symbol];
  if (!entry) return null;
  return fetch(`${BASE}/${entry.path}`).then(r => r.json());
}

console.log(await profileFor("AAPL"));
```

### Python

```python
import requests

BASE = "https://cdn.jsdelivr.net/gh/wealthfolio/asset-profiles@main/v1"

index = requests.get(f"{BASE}/index.json").json()

def profile_for(symbol: str):
    entry = index["symbols"].get(symbol)
    if entry is None:
        return None
    return requests.get(f"{BASE}/{entry['path']}").json()

print(profile_for("SPY"))
```

## What's in the dataset

| Set                 | Source                         | Records | License |
| ------------------- | ------------------------------ | ------- | ------- |
| Stocks              | [JerBouma/FinanceDatabase][fd] | ~120k   | MIT (upstream) |
| ETFs (US-domiciled) | [SEC EDGAR N-PORT][edgar]      | ~250    | US public domain |
| ETFs (non-US)       | Issuer holdings (fallback)     | ~50     | Issuer ToS, attributed |

[fd]: https://github.com/JerBouma/FinanceDatabase
[edgar]: https://www.sec.gov/edgar

Each record carries a `provenance` block identifying its upstream
source, fetch timestamp, and license.

## Schema

JSON Schemas live under [`schema/`](schema/):

- [`stock.schema.json`](schema/stock.schema.json)
- [`etf.schema.json`](schema/etf.schema.json)
- [`index.schema.json`](schema/index.schema.json)

Full reference is in [`docs/asset-profiles-spec.md`](docs/asset-profiles-spec.md).

Key rules:

- **Weights are decimal fractions** (`0.317`, not `31.7`). Lists must
  sum to `1.0 ± 0.005` or the validator rejects them.
- **Missing fields are omitted, not `null`.** Treat missing as
  unknown.
- **`country_code`** is ISO 3166-1 alpha-2, **`currency`** is ISO 4217,
  **`exchange_mic`** is ISO 10383 MIC.
- Breaking schema changes bump major and live under `/v2/`. Both
  versions stay live ≥6 months during deprecation.

## Build pipeline

The dataset is regenerated each week by
[`.github/workflows/refresh.yml`](.github/workflows/refresh.yml):

1. Pull `equities.csv` and `etfs.csv` from FinanceDatabase.
2. Normalize stock rows; group cross-listings by ISIN.
3. For each ETF in [`config/etf_universe.yml`](config/etf_universe.yml):
   - Try SEC EDGAR N-PORT first.
   - Fall back to issuer scraper (iShares / Vanguard / SSGA / Invesco)
     if no N-PORT (non-US ETFs).
4. Apply [`manual_overrides/`](manual_overrides/) patches.
5. Validate every record against its JSON Schema.
6. Write per-ticker JSON shards + `index.json`.
7. Commit and push if anything changed.

Run locally:

```bash
uv pip install -r scripts/requirements.txt
SEC_USER_AGENT="your-name your@email" python scripts/build.py
python scripts/validate.py v1/
```

## Design decisions

A few open questions from the spec have been resolved for v1:

- **Domain.** No custom domain; jsDelivr serves directly from
  GitHub. We may revisit `profiles.wealthfolio.app` once usage
  warrants it.
- **ETF universe.** Hand-curated in
  [`config/etf_universe.yml`](config/etf_universe.yml). Adds happen
  via PR — we don't auto-derive from user data (privacy).
- **Versioning.** Schema is `1.0.0`; the dataset lives under `/v1/`.
  Breaking changes will live side-by-side under `/v2/` ≥6 months.
- **Manual fixes.** Per-record patches in
  [`manual_overrides/`](manual_overrides/) survive weekly rebuilds.
- **Telemetry.** None in this repo. Clients can opt-in upstream to
  report missing-ticker resolutions.

See [`docs/asset-profiles-spec.md`](docs/asset-profiles-spec.md) §15
for the full set of open questions.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) — fixing a record, proposing
a new ETF, improving the pipeline.

## Takedown

Rights-holders: email `opensource@wealthfolio.app`. We commit to act
on legitimate requests within 7 days. See [`DISCLAIMER.md`](DISCLAIMER.md).
