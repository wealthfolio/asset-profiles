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
