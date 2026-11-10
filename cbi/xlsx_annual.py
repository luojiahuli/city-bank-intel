"""Import bank annual reports (2023-2025) from the W87 workbook.

Each sheet is one reporting year in wide format: one row per bank, with income
statement and balance sheet columns already denominated in 亿元. Rows are matched
to the NFRA registry by exact normalized Chinese full name; each bank keeps its
most recent year that carries any financial value.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import load_workbook

import config
from cbi import registry
from cbi.xlsx_financials import _core, _norm, build_registry_index

ENRICH_DIR = config.DATA_DIR / "enrichment"
OUT_PATH = ENRICH_DIR / "financials_annual.json"
COVERAGE_PATH = ENRICH_DIR / "financials_annual_coverage.json"

SOURCE_TITLE = "《W87 银行财务数据（2023-2025年报）》利润表 / 资产负债表（合并报表）"
YEARS = ("2023", "2024", "2025")
VALUE_FIELDS = ("asset", "revenue", "profit", "deposits", "loans")

COLUMN_MAP = {
    "revenue": "营业收入",
    "profit": "净利润",
    "asset": "资产总计",
    "deposits": "吸收存款",
    "loans": "发放贷款及垫款",
}
BANK_FIELDS = VALUE_FIELDS

RANGES = {
    "asset": (0.5, 2_000_000), "revenue": (0, 100_000), "profit": (-3000, 10_000),
    "deposits": (1, 2_000_000), "loans": (1, 2_000_000),
}


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 2) if number else None


def _pick(field: str, value, dropped: dict) -> float | None:
    if value is None:
        return None
    low, high = RANGES[field]
    if not (low <= value <= high):
        dropped[field] = dropped.get(field, 0) + 1
        return None
    return value


def load_year_rows(wb, year: str):
    ws = wb[year]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    idx = {name: i for i, name in enumerate(header) if name}
    return idx, [r for r in rows if r and r[idx.get("公司名称", -1)]]


def collect(path: Path) -> dict:
    banks = registry.load_registry()["banks"]
    by_name = build_registry_index(banks)
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        sheets = {year: load_year_rows(wb, year) for year in YEARS if year in wb.sheetnames}
    finally:
        wb.close()

    out: dict[str, dict] = {}
    dropped: dict[str, int] = {}
    field_counts: dict[str, int] = {f: 0 for f in BANK_FIELDS}
    unmatched: list[dict] = []
    excel_banks: dict[str, int] = {}
    matched_per_year: dict[str, int] = {}

    # Ascending years: a later year overwrites earlier values field by field.
    for year in sorted(sheets):
        idx, rows = sheets[year]
        excel_banks[year] = len(rows)
        matched = 0
        for row in rows:
            name = row[idx["公司名称"]]
            bank = by_name.get(_norm(name)) or by_name.get(_core(name))
            if not bank:
                if year == max(sheets):
                    unmatched.append({"name": str(name or ""), "year": year})
                continue
            matched += 1
            item = out.setdefault(bank["id"], {"bank_code": str(row[idx.get("序号", 0)] or ""),
                                               "bank_name": str(name)})
            values = {}
            for field, column in COLUMN_MAP.items():
                ci = idx.get(column)
                values[field] = _pick(field, _num(row[ci]) if ci is not None and ci < len(row)
                                      else None, dropped)
            present = [f for f, v in values.items() if v is not None]
            if not present:
                continue
            for field in present:
                item[field] = values[field]
            item["period"] = f"{year}-12-31"
            item["asset_year"] = year
            item["sources"] = [{
                "kind": "financial",
                "title": SOURCE_TITLE,
                "url": "",
                "as_of": f"{year}-12-31",
            }]
        matched_per_year[year] = matched

    for item in out.values():
        for field in BANK_FIELDS:
            if item.get(field) is not None:
                field_counts[field] += 1

    coverage = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_file": str(path),
        "excel_banks": excel_banks,
        "matched_per_year": matched_per_year,
        "matched_banks": len(out),
        "field_coverage": field_counts,
        "dropped_values": dropped,
        "unmatched": sorted(unmatched, key=lambda x: x["name"]),
    }
    return {"data": out, "coverage": coverage}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import 2023-2025 annual reports from the W87 workbook")
    parser.add_argument("--path", required=True, help="path to the .xlsx workbook")
    args = parser.parse_args()

    path = Path(args.path).expanduser()
    if not path.exists():
        raise SystemExit(f"workbook not found: {path}")
    result = collect(path)
    ENRICH_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result["data"], ensure_ascii=False, indent=1), encoding="utf-8")
    COVERAGE_PATH.write_text(
        json.dumps(result["coverage"], ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {k: v for k, v in result["coverage"].items() if k != "unmatched"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
