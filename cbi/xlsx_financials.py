"""Import bank profiles from the 2007-2024 bank dataset workbook.

The workbook ships balance sheet, income statement, financial and risk-indicator
sheets at year-end granularity. Rows are matched to the NFRA registry by exact
normalized Chinese full name; unmatched banks are reported, never fuzzy-matched.
Amounts are converted from yuan to 亿元 and stored with their report period.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import load_workbook

import config
from cbi import registry

ENRICH_DIR = config.DATA_DIR / "enrichment"
OUT_PATH = ENRICH_DIR / "financials_xlsx.json"
COVERAGE_PATH = ENRICH_DIR / "financials_xlsx_coverage.json"

SOURCE_TITLE = (
    "《2007-2024年最新银行数据》基本信息表 / 资产负债表 / 利润表 / "
    "财务指标 / 贷款分析指标 / 信用风险指标"
)

BANK_FIELDS = (
    "asset", "revenue", "profit", "employees", "branches", "deposits", "loans",
    "npl_ratio", "provision_coverage", "capital_adequacy", "tier1_ratio",
    "core_tier1_ratio", "roe", "roaa", "nim",
)

RANGES = {
    "asset": (0.5, 2_000_000), "revenue": (0, 100_000), "profit": (-3000, 10_000),
    "employees": (10, 2_000_000), "branches": (1, 50_000),
    "deposits": (1, 2_000_000), "loans": (1, 2_000_000),
    "npl_ratio": (0, 1000), "provision_coverage": (0, 5000),
    "capital_adequacy": (0, 1000), "tier1_ratio": (0, 1000),
    "core_tier1_ratio": (0, 1000), "roe": (-100, 1000),
    "roaa": (-100, 1000), "nim": (-100, 1000),
}


def _norm(name) -> str:
    text = str(name or "").strip().replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", text)


def _core(name) -> str:
    text = _norm(name)
    for suffix in ("股份有限公司", "有限责任公司", "有限公司", "股份公司"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _yi(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number / 1e8, 2) if number else None


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 2)


def _pick(field: str, value, dropped: dict) -> float | None:
    if value is None:
        return None
    low, high = RANGES[field]
    if not (low <= value <= high):
        dropped[field] = dropped.get(field, 0) + 1
        return None
    return value


def _sheet_rows(ws):
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    next(rows)  # Chinese header
    next(rows)  # unit row
    return header, rows


def _col(header: dict, name: str) -> int | None:
    return header.get(name)


def _at(row, index: int | None):
    return row[index] if index is not None and index < len(row) else None


def _latest_by_code(ws, date_col: str, code_col: str = "Bankcd",
                    typreport: str | None = None, consolidated: str = "A"):
    header, rows = _sheet_rows(ws)
    idx = {name: i for i, name in enumerate(header) if name}
    ci = idx.get(code_col)
    di = idx.get(date_col)
    if ci is None or di is None:
        return header, {}
    ti = idx.get(typreport) if typreport else None
    latest: dict[str, tuple] = {}
    fallback: dict[str, tuple] = {}
    for row in rows:
        width = max(ci, di, ti or 0) + 1
        if len(row) < width:
            continue
        code = row[ci]
        if not code:
            continue
        code = str(code)
        date = str(row[di] or "")
        bucket = latest if ti is None or str(row[ti]) == consolidated else fallback
        current = bucket.get(code)
        if current is None or date > current[0]:
            bucket[code] = (date, row)
    for code, item in fallback.items():
        latest.setdefault(code, item)
    return header, latest


def _value(header: list, row, name: str):
    i = _col({h: j for j, h in enumerate(header) if h}, name)
    return row[i] if i is not None and i < len(row) else None


def load_workbook_sheets(path: Path) -> dict:
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        basic_header, basic = _latest_by_code(wb["基本信息表"], "Clsdt")
        bs_header, balance = _latest_by_code(wb["资产负债表"], "Accper", typreport="Typrep")
        is_header, income = _latest_by_code(wb["利润表"], "Accper", typreport="Typrep")
        fi_header, indicators = _latest_by_code(wb["财务指标"], "Reptdt")
        ln_header, loan_metrics = _latest_by_code(wb["贷款分析指标"], "Reptdt")
        cr_header, credit = _latest_by_code(
            wb["信用风险指标"], "EndDate", code_col="BankCode")
    finally:
        wb.close()
    return {
        "basic": (basic_header, basic),
        "balance": (bs_header, balance),
        "income": (is_header, income),
        "indicators": (fi_header, indicators),
        "loan_metrics": (ln_header, loan_metrics),
        "credit": (cr_header, credit),
    }


def build_registry_index(banks: list[dict]) -> dict:
    index = {}
    for bank in banks:
        index.setdefault(_norm(bank["name"]), bank)
        index.setdefault(_core(bank["name"]), bank)
    return index


def collect(path: Path) -> dict:
    banks = registry.load_registry()["banks"]
    by_name = build_registry_index(banks)
    sheets = load_workbook_sheets(path)
    basic_header, basic = sheets["basic"]

    name_col = basic_header.index("Bnm_Cn")
    code_col = basic_header.index("Bankcd")
    code_to_bank: dict[str, dict] = {}
    unmatched: list[dict] = []
    short_col = basic_header.index("Shortbnm")
    date_col = basic_header.index("Clsdt")
    for code, (_, row) in basic.items():
        name = _at(row, name_col)
        bank = by_name.get(_norm(name)) or by_name.get(_core(name))
        if bank:
            code_to_bank[str(code)] = bank
        else:
            unmatched.append({"name": str(name or ""), "short": str(_at(row, short_col) or ""),
                              "latest": str(_at(row, date_col) or "")[:10]})

    out: dict[str, dict] = {}
    dropped: dict[str, int] = {}
    field_counts: dict[str, int] = {f: 0 for f in BANK_FIELDS}

    def lookup(sheet_name: str, code: str):
        header, rows = sheets[sheet_name]
        hit = rows.get(str(code))
        return header, (hit[1] if hit else None)

    for code, bank in code_to_bank.items():
        item: dict = {"bank_code": code}
        basic_h, basic_row = lookup("basic", code)
        bs_h, bs_row = lookup("balance", code)
        is_h, is_row = lookup("income", code)
        fi_h, fi_row = lookup("indicators", code)
        ln_h, ln_row = lookup("loan_metrics", code)
        cr_h, cr_row = lookup("credit", code)

        if bs_row:
            item["asset"] = _pick("asset", _yi(_value(bs_h, bs_row, "A001000000")), dropped)
            item["_asset_date"] = str(bs_row[bs_h.index("Accper")] or "")[:10]
        if is_row:
            item["revenue"] = _pick("revenue", _yi(_value(is_h, is_row, "B001101000")), dropped)
            item["profit"] = _pick("profit", _yi(_value(is_h, is_row, "B002000000")), dropped)
            item["_income_date"] = str(is_row[is_h.index("Accper")] or "")[:10]
        if basic_row:
            employees = _pick(
                "employees", _num(_value(basic_h, basic_row, "PersonsEngaged")), dropped)
            branches = (_value(basic_h, basic_row, "AgencyNumber")
                        or _value(basic_h, basic_row, "BranchNumber"))
            branches = _pick("branches", _num(branches), dropped)
            item["employees"] = int(employees) if employees is not None else None
            item["branches"] = int(branches) if branches is not None else None
            item["_basic_date"] = str(basic_row[basic_h.index("Clsdt")] or "")[:10]
            stock = str(_value(basic_h, basic_row, "Stkcd") or "").strip()
            if stock[:1] in ("6", "0"):
                item["listed"] = "A"
        if fi_row:
            deposits = _yi(_value(fi_h, fi_row, "Ttdeps"))
            if deposits is None and bs_row:
                deposits = _yi(_value(bs_h, bs_row, "A0B2103201"))
            item["deposits"] = _pick("deposits", deposits, dropped)
            item["loans"] = _pick("loans", _yi(_value(fi_h, fi_row, "Ttloan")), dropped)
            item["roe"] = _pick("roe", _num(_value(fi_h, fi_row, "ROE")), dropped)
            item["roaa"] = _pick("roaa", _num(_value(fi_h, fi_row, "ROAA")), dropped)
            item["nim"] = _pick("nim", _num(_value(fi_h, fi_row, "NIM")), dropped)
            item["_indicator_date"] = str(fi_row[fi_h.index("Reptdt")] or "")[:10]
        if ln_row:
            item["npl_ratio"] = _pick("npl_ratio", _num(_value(ln_h, ln_row, "Nplra")), dropped)
            item["provision_coverage"] = _pick(
                "provision_coverage", _num(_value(ln_h, ln_row, "Pvcra")), dropped)
            item["_loan_date"] = str(ln_row[ln_h.index("Reptdt")] or "")[:10]
        if cr_row:
            item["capital_adequacy"] = _pick(
                "capital_adequacy", _num(_value(cr_h, cr_row, "CapitalAdequacyRatio")), dropped)
            item["tier1_ratio"] = _pick(
                "tier1_ratio", _num(_value(cr_h, cr_row, "Tier1CapitalRatio")), dropped)
            item["core_tier1_ratio"] = _pick(
                "core_tier1_ratio", _num(_value(cr_h, cr_row, "CorCapitalAdequacyRatio")), dropped)
            item["_credit_date"] = str(cr_row[cr_h.index("EndDate")] or "")[:10]

        dates = [v for k, v in item.items() if k.endswith("_date") and v]
        if not dates:
            continue
        period = max(dates)
        item["period"] = period
        if item.get("_asset_date"):
            item["asset_year"] = item["_asset_date"][:4] or None
        item["sources"] = [{
            "kind": "financial",
            "title": SOURCE_TITLE,
            "url": "",
            "as_of": period,
        }]
        for key in [k for k in item if k.startswith("_")]:
            item.pop(key)

        present = [f for f in BANK_FIELDS if item.get(f) is not None]
        if not present:
            continue
        for f in present:
            field_counts[f] += 1
        out[bank["id"]] = item

    coverage = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_file": str(path),
        "excel_banks": len(basic),
        "matched_banks": len(out),
        "unmatched_banks": len(unmatched),
        "field_coverage": field_counts,
        "dropped_values": dropped,
        "unmatched": sorted(unmatched, key=lambda x: x["name"]),
    }
    return {"data": out, "coverage": coverage}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import bank profiles from the bank dataset workbook")
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
