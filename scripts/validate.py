"""Validate generated dataset against JSON Schema + custom invariants.

CLI:
    python scripts/validate.py v1/

Also exposed as a Python API:
    from validate import validate_record, validate_tree, ValidationError
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Iterable

import jsonschema
from jsonschema import Draft202012Validator

log = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
SCHEMA_DIR = REPO_ROOT / "schema"

WEIGHT_SUM_TOL = 0.005


class ValidationError(Exception):
    pass


# ---- schema loaders -----------------------------------------------------

_validators: dict[str, Draft202012Validator] = {}


def _validator(name: str) -> Draft202012Validator:
    if name not in _validators:
        path = SCHEMA_DIR / f"{name}.schema.json"
        schema = json.loads(path.read_text())
        _validators[name] = Draft202012Validator(schema)
    return _validators[name]


def _schema_name_for(record: dict) -> str:
    kind = record.get("kind")
    if kind not in {"stock", "etf"}:
        raise ValidationError(f"unknown kind: {kind!r}")
    return kind


# ---- public API ---------------------------------------------------------


def validate_record(record: dict) -> list[str]:
    """Validate a single record. Returns list of error messages (empty = OK)."""
    errors: list[str] = []
    name = _schema_name_for(record)
    v = _validator(name)
    for e in v.iter_errors(record):
        loc = "/".join(str(p) for p in e.absolute_path)
        errors.append(f"schema: {loc}: {e.message}")

    # Custom invariants
    for field in ("sector_weights", "country_weights", "asset_class_weights"):
        ws = record.get(field) or []
        if not ws:
            continue
        total = sum(w.get("weight", 0.0) for w in ws)
        if abs(total - 1.0) > WEIGHT_SUM_TOL:
            errors.append(f"weights: {field} sums to {total:.4f}, expected 1.0 ± {WEIGHT_SUM_TOL}")

    th = record.get("top_holdings") or []
    if th:
        top_total = sum(h.get("weight", 0.0) for h in th)
        if top_total > 1.0 + WEIGHT_SUM_TOL:
            errors.append(f"weights: top_holdings sums to {top_total:.4f}, must be ≤ 1.0")

    return errors


def validate_index(index: dict, root: Path) -> list[str]:
    errors: list[str] = []
    v = _validator("index")
    for e in v.iter_errors(index):
        loc = "/".join(str(p) for p in e.absolute_path)
        errors.append(f"index: {loc}: {e.message}")

    for sym, entry in index.get("symbols", {}).items():
        path = root / entry["path"]
        if not path.exists():
            errors.append(f"index: symbol {sym!r} → {entry['path']} (file missing)")

    for isin, path_str in index.get("isins", {}).items():
        path = root / path_str
        if not path.exists():
            errors.append(f"index: isin {isin} → {path_str} (file missing)")

    counts = index.get("counts", {})
    actual_stocks = sum(1 for _ in (root / "stocks").glob("*.json")) if (root / "stocks").exists() else 0
    actual_etfs = sum(1 for _ in (root / "etfs").glob("*.json")) if (root / "etfs").exists() else 0
    if counts.get("stocks") != actual_stocks:
        errors.append(f"index: counts.stocks={counts.get('stocks')} but {actual_stocks} files on disk")
    if counts.get("etfs") != actual_etfs:
        errors.append(f"index: counts.etfs={counts.get('etfs')} but {actual_etfs} files on disk")

    return errors


def validate_tree(root: Path) -> int:
    """Validate every file in `root`. Returns count of errors."""
    total_errors = 0
    files: Iterable[Path]

    files = sorted((root / "stocks").glob("*.json")) if (root / "stocks").exists() else []
    for path in files:
        try:
            record = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"FAIL {path}: invalid JSON: {e}")
            total_errors += 1
            continue
        errs = validate_record(record)
        for err in errs:
            print(f"FAIL {path}: {err}")
        total_errors += len(errs)

    files = sorted((root / "etfs").glob("*.json")) if (root / "etfs").exists() else []
    for path in files:
        try:
            record = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"FAIL {path}: invalid JSON: {e}")
            total_errors += 1
            continue
        errs = validate_record(record)
        for err in errs:
            print(f"FAIL {path}: {err}")
        total_errors += len(errs)

    index_path = root / "index.json"
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text())
        except json.JSONDecodeError as e:
            print(f"FAIL {index_path}: invalid JSON: {e}")
            return total_errors + 1
        errs = validate_index(index, root)
        for err in errs:
            print(f"FAIL {index_path}: {err}")
        total_errors += len(errs)
    else:
        print(f"FAIL: {index_path} missing")
        total_errors += 1

    return total_errors


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: validate.py <root-dir>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    errors = validate_tree(root)
    if errors:
        print(f"\n{errors} error(s)")
        return 1
    print(f"OK: {root}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main(sys.argv))
