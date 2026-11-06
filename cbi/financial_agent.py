"""Collect public financial data for banks.

Listed A-share banks use Sina/Eastmoney statements through akshare. Other banks
use Tavily discovery plus strict LLM extraction; a field is stored only when an
explicit number and a source URL are returned.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import requests

import config
from cbi import registry
from cbi.news_agent import ENV  # reuse environment loading

ENRICH_DIR = config.DATA_DIR / "enrichment"
CACHE_DIR = config.CACHE_DIR / "financial"
ROTATION_PATH = ENRICH_DIR / "financial_rotation.json"
OUT_PATH = ENRICH_DIR / "financials.json"
COVERAGE_PATH = ENRICH_DIR / "financials_coverage.json"
for d in (ENRICH_DIR, CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)

A_CODES = {
    "北京银行": "601169", "上海银行": "601229", "江苏银行": "600919",
    "南京银行": "601009", "宁波银行": "002142", "杭州银行": "600926",
    "成都银行": "601838", "长沙银行": "601577", "贵阳银行": "601997",
    "郑州银行": "002936", "青岛银行": "002948", "苏州银行": "002966",
    "西安银行": "600928", "厦门银行": "601187", "重庆银行": "601963",
    "兰州银行": "001227", "齐鲁银行": "601665", "青岛农商行": "002958",
    "渝农商行": "601077", "重庆农商行": "601077", "常熟银行": "601128",
    "紫金银行": "601860", "江苏紫金农商行": "601860", "无锡银行": "600908",
    "无锡农商行": "600908", "张家港行": "002839", "张家港农商行": "002839",
    "苏农银行": "603323", "苏州农商行": "603323", "江阴银行": "002807",
    "江阴农商行": "002807", "沪农商行": "601825", "上海农商行": "601825",
    "瑞丰银行": "601528", "浙江绍兴瑞丰农商行": "601528",
}

NUMERIC_FIELDS = {
    "asset": (1, 200000), "revenue": (0.1, 50000), "profit": (-5000, 5000),
    "employees": (10, 2_000_000), "branches": (1, 50_000),
    "customers": (100, 2_000_000_000), "deposits": (1, 200000),
    "loans": (1, 200000), "it_spend": (0.01, 5000),
}


def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def _save_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]


def _to_yi(value, divisor: float = 1e8) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not number:
        return None
    return round(number / divisor, 2)


# ---------------------------------------------------------------------------
# listed banks via akshare / Sina statements


def fetch_listed(bank: dict) -> dict | None:
    code = A_CODES.get(bank.get("short_name") or "") or A_CODES.get(bank.get("name") or "")
    if not code:
        return None
    import akshare as ak

    prefix = "sh" if code.startswith("6") else "sz"
    stock = prefix + code
    out: dict = {"listed": bank.get("listed") or "A", "sources": []}
    period = None
    try:
        balance = ak.stock_financial_report_sina(stock=stock, symbol="资产负债表")
        col = next((c for c in balance.columns if "资产总计" in str(c)), None)
        if col is not None:
            for _, row in balance.iterrows():
                val = _to_yi(row.get(col))
                if val:
                    out["asset"] = val
                    period = str(row.get("报告日") or "")
                    break
    except Exception:
        pass
    try:
        income = ak.stock_financial_report_sina(stock=stock, symbol="利润表")
        rev_col = next((c for c in income.columns if str(c) in ("营业收入", "营业总收入")), None)
        profit_col = next((c for c in income.columns if str(c) in ("净利润", "归属于母公司所有者的净利润")), None)
        if rev_col or profit_col:
            row = income.iloc[0]
            if rev_col:
                out["revenue"] = _to_yi(row.get(rev_col))
            if profit_col:
                out["profit"] = _to_yi(row.get(profit_col))
            period = period or str(row.get("报告日") or "")
    except Exception:
        pass
    if not any(out.get(k) for k in ("asset", "revenue", "profit")):
        return None
    if period:
        out["period"] = f"{period[:4]}-{period[4:6]}-{period[6:8]}" if len(period) == 8 else period
        out["asset_year"] = period[:4]
    out["sources"].append({
        "kind": "financial",
        "title": f"{bank['short_name']} 定期报告财务数据（新浪财经 / 东方财富）",
        "url": f"https://finance.sina.com.cn/realstock/company/{stock}/nc.shtml",
        "as_of": out.get("period"),
    })
    return out


# ---------------------------------------------------------------------------
# non-listed banks via Tavily + LLM extraction


def tavily_company_search(query: str, max_results: int = 6, use_cache: bool = True) -> list[dict]:
    key = ENV.get("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("TAVILY_API_KEY is not configured")
    cpath = CACHE_DIR / (_key(query, str(max_results), date.today().isoformat()) + ".json")
    if use_cache and cpath.exists():
        return _load_json(cpath, [])
    resp = requests.post(
        config.TAVILY_URL,
        json={
            "api_key": key,
            "query": query,
            "topic": "general",
            "search_depth": "advanced",
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
        },
        timeout=60,
    )
    resp.raise_for_status()
    items = []
    for row in resp.json().get("results") or []:
        url = (row.get("url") or "").strip()
        if not url:
            continue
        items.append({
            "title": (row.get("title") or "").strip(),
            "url": url,
            "content": re.sub(r"\s+", " ", row.get("content") or "").strip()[:1600],
        })
    _save_json(cpath, items)
    return items


def _bank_tokens(bank: dict) -> list[str]:
    name = bank.get("name") or ""
    base = re.sub(r"(股份有限公司|有限责任公司|有限公司)$", "", name)
    short = bank.get("short_name") or ""
    tokens = {name, base, short}
    if short.endswith("农商行"):
        tokens.add(short.replace("农商行", "农村商业银行"))
    return [t for t in tokens if len(t) >= 4]


def extract_with_llm(bank: dict, results: list[dict]) -> dict:
    key = ENV.get("DEEPSEEK_API_KEY")
    if not key or not results:
        return {}
    rows = [{"title": r["title"], "url": r["url"], "content": r["content"][:1200]}
            for r in results]
    system = (
        "你是银行公开财务数据抽取助手。只能从给定网页摘要中抄录明确出现的数字，禁止推算、估算或补全。"
        "金额统一换算为人民币亿元（若原文为万元则除以10000，元则除以1e8），员工人数为整数。"
        "输出 JSON：{\"fields\":{\"asset\":数字或null,\"revenue\":数字或null,\"profit\":数字或null,"
        "\"employees\":数字或null,\"branches\":数字或null,\"customers\":数字或null,"
        "\"deposits\":数字或null,\"loans\":数字或null,\"it_spend\":数字或null,\"overseas\":true/false/null},"
        "\"period\":\"数据期间，如2025年报/2025年末\",\"sources\":[{\"url\":\"来源链接\","
        "\"field\":\"asset\",\"quote\":\"包含该数字的原文片段\"}]}。"
        "employees 必须是原文明确写出的员工/在职员工人数，不得用参保人数、社保人数或估算值代替。"
        "找不到明确数字时返回 null；不要在 sources 中使用未提供或未使用的链接。"
    )
    user = (
        f"银行：{bank.get('name')}（{bank.get('province')}，{bank.get('type_label')}）。"
        f"网页摘要：{json.dumps(rows, ensure_ascii=False)}"
    )
    ckey = _key("v2", bank["id"], json.dumps(rows, ensure_ascii=False))
    cpath = CACHE_DIR / (ckey + "_llm.json")
    if cpath.exists():
        return _load_json(cpath, {})
    try:
        resp = requests.post(
            config.LLM_BASE_URL.rstrip("/") + "/chat/completions",
            json={
                "model": config.LLM_MODEL,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=90,
        )
        resp.raise_for_status()
        parsed = json.loads(resp.json()["choices"][0]["message"]["content"])
    except Exception:
        return {}
    _save_json(cpath, parsed)
    return parsed


def fetch_unlisted(bank: dict, use_cache: bool = True, use_llm: bool = True) -> dict | None:
    tokens = _bank_tokens(bank)
    query = (
        f"{bank.get('short_name') or bank.get('name')} {bank.get('name')} "
        "2025 年报 总资产 营业收入 净利润 员工人数 分支机构 存款 贷款"
    )
    results = tavily_company_search(query, max_results=6, use_cache=use_cache)
    relevant = [
        r for r in results
        if any(tok in f"{r['title']} {r['content']}" for tok in tokens)
    ]
    if not relevant:
        return None
    out: dict = {"sources": []}
    if use_llm:
        parsed = extract_with_llm(bank, relevant)
        fields = parsed.get("fields") or {}
        for field, (lo, hi) in NUMERIC_FIELDS.items():
            value = fields.get(field)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if lo <= number <= hi:
                out[field] = round(number, 2) if field not in {"employees", "branches", "customers"} else int(number)
        if isinstance(fields.get("overseas"), bool):
            out["overseas"] = fields["overseas"]
        if parsed.get("period"):
            out["period"] = str(parsed["period"])[:40]
            m = re.search(r"(20\d{2})", out["period"])
            if m:
                out["asset_year"] = m.group(1)
        by_url = {r["url"] for r in relevant}
        for src in parsed.get("sources") or []:
            url = src.get("url")
            if url in by_url:
                out["sources"].append({
                    "kind": "financial",
                    "title": f"{bank['short_name']} 公开财务数据",
                    "url": url,
                    "field": src.get("field"),
                    "quote": (src.get("quote") or "")[:160],
                    "as_of": out.get("period"),
                })
    if not any(out.get(k) for k in NUMERIC_FIELDS):
        return None
    if not out["sources"]:
        for r in relevant[:2]:
            out["sources"].append({
                "kind": "financial", "title": r["title"], "url": r["url"],
                "as_of": out.get("period"),
            })
    return out


# ---------------------------------------------------------------------------
# rotation and persistence


def _rotation_candidates(group: str) -> list[dict]:
    banks = registry.load_registry()["banks"]
    if group == "all":
        picked = [b for b in banks if b["group"] in ("city", "rural")]
    elif group == "city":
        picked = [b for b in banks if b["group"] == "city"]
    else:
        picked = [b for b in banks if b["group"] == group]
    picked.sort(key=lambda b: b["name"])
    return picked


def select_banks(limit: int, group: str = "city", force: bool = False) -> list[dict]:
    candidates = _rotation_candidates(group)
    rotation = _load_json(ROTATION_PATH, {"cursor": 0, "batch": [], "group": group})
    if force and rotation.get("batch"):
        by_id = {b["id"]: b for b in candidates}
        picked = [by_id[i] for i in rotation["batch"] if i in by_id]
        if picked:
            return picked
    cursor = int(rotation.get("cursor") or 0) % max(1, len(candidates))
    picked = [candidates[(cursor + i) % len(candidates)] for i in range(min(limit, len(candidates)))]
    rotation.update({
        "cursor": (cursor + len(picked)) % max(1, len(candidates)),
        "batch": [b["id"] for b in picked],
        "group": group,
        "last_run": date.today().isoformat(),
    })
    _save_json(ROTATION_PATH, rotation)
    return picked


def collect(limit: int = 20, group: str = "city", force: bool = False,
            use_llm: bool = True, bank_ids: list[str] | None = None,
            verbose: bool = True) -> dict:
    by_id = {b["id"]: b for b in registry.load_registry()["banks"]}
    banks = ([by_id[i] for i in bank_ids if i in by_id] if bank_ids
             else select_banks(limit, group=group, force=force))
    data = _load_json(OUT_PATH, {})
    hits = 0
    fields_count = 0
    for i, bank in enumerate(banks, 1):
        result = None
        if A_CODES.get(bank.get("short_name") or "") or A_CODES.get(bank.get("name") or ""):
            try:
                result = fetch_listed(bank)
            except Exception:
                result = None
        if not result:
            try:
                result = fetch_unlisted(bank, use_cache=not force, use_llm=use_llm)
            except Exception as exc:
                if verbose:
                    print(f"[{i}/{len(banks)}] {bank['short_name']} failed: {type(exc).__name__}")
                continue
        if result:
            previous = data.get(bank["id"]) or {}
            merged = result if force else {
                **previous, **{k: v for k, v in result.items() if v is not None}
            }
            data[bank["id"]] = merged
            hits += 1
            fields_count += sum(1 for k in NUMERIC_FIELDS if merged.get(k) is not None)
        if verbose:
            got = [k for k in NUMERIC_FIELDS if (data.get(bank["id"]) or {}).get(k) is not None]
            print(f"[{i}/{len(banks)}] {bank['short_name']}: {','.join(got) if got else '无公开财务数字'}")
        if i % 5 == 0:
            _save_json(OUT_PATH, data)
    _save_json(OUT_PATH, data)
    coverage = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "group": group,
        "banks_checked": len(banks),
        "banks_with_data": hits,
        "field_values": fields_count,
        "total_banks_with_data": len(data),
    }
    _save_json(COVERAGE_PATH, coverage)
    return coverage


def main() -> None:
    parser = argparse.ArgumentParser(description="Public bank financial data agent")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--group", default="city", choices=["city", "rural", "village", "all"])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--bank-id", action="append", default=[])
    args = parser.parse_args()
    result = collect(limit=args.limit, group=args.group, force=args.force,
                     use_llm=not args.no_llm, bank_ids=args.bank_id or None)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
