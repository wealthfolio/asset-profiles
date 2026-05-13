# manual_overrides/

Per-record JSON patches that survive weekly rebuilds.

## How it works

After the build pipeline normalizes a record from upstream sources, it
**deep-merges** any matching file in this directory over the
auto-generated record. The merged result is then validated against the
JSON Schema and written to `v1/`.

This lets contributors fix individual records (typos, sector
mis-classifications, missing ISINs) without forking the upstream
source.

## Filenames

The filename **must** match the record's `shard_key`:

- For stocks/ETFs with an ISIN: `{ISIN}.json` (e.g. `US0378331005.json`)
- Otherwise: `{primary_symbol}.json` (e.g. `AAPL.json`)

If you're unsure which key applies, check the path that
`v1/index.json` resolves to.

## Patch format

The file contains a partial record. Fields you specify replace the
auto-generated values; fields you omit are left alone.

Example — fixing an industry label:

```json
{
  "industry": "Internet Retail"
}
```

Example — adding a missing ISIN:

```json
{
  "identifiers": {
    "isin": "US12345X6789"
  }
}
```

Lists (e.g. `top_holdings`, `listings`) are **replaced wholesale**, not
merged element-by-element. If you need to add to a list, copy the
generated list into the override and edit it.

## Synthetic records (commodity / non-equity ETFs)

For ETFs whose underlying isn't a security — physical commodity funds
(GLD, SLV), currency trusts, etc. — the upstream sources have nothing
useful to return. Set a top-level `_synthetic: true` flag and the
build will **skip the fetch entirely**, treating the override file as
the sole source of weights and holdings.

The marker is stripped from the written record (like `_note`); it
exists only as a signal to `scripts/build.py`.

```json
{
  "_note": "Physical gold, not securities — see issue #...",
  "_synthetic": true,
  "isin": "US78463V1070",
  "name": "SPDR Gold Shares",
  "asset_class_weights": [{ "asset_class": "Commodity", "weight": 1.0 }]
}
```

## Provenance

Overridden fields don't get a special marker, but the build emits a
log line per override applied. If you'd like to record *why* an
override exists, add a top-level `_note` field — it's stripped before
writing to `v1/` but preserved here for future contributors:

```json
{
  "_note": "Upstream lists wrong country for ADR; corrected per prospectus 2026-04",
  "country": "Netherlands",
  "country_code": "NL"
}
```

## Pruning

If upstream data improves and an override becomes redundant, please
remove it. The build emits a warning when an override sets a field to
exactly the auto-generated value (no-op override).
