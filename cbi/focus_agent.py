"""Classify each bank's key industries from public reports and region.

Confirmed focus tags require a quote from a retrieved public article. When no
article supports a tag, the province's industry structure is attached with an
explicit "区域推断" basis so the map never implies a disclosed fact.
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
from cbi.news_agent import ENV

ENRICH_DIR = config.DATA_DIR / "enrichment"
CACHE_DIR = config.CACHE_DIR / "focus"
ROTATION_PATH = ENRICH_DIR / "focus_rotation.json"
OUT_PATH = ENRICH_DIR / "focus.json"
COVERAGE_PATH = ENRICH_DIR / "focus_coverage.json"
for d in (ENRICH_DIR, CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)

TAG_NAMES = list(config.FOCUS_TAGS)
REGION_BASIS = "区域产业结构推断（待核验）"


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


def _tokens(bank: dict) -> list[str]:
    name = bank.get("name") or ""
    base = re.sub(r"(股份有限公司|有限责任公司|有限公司)$", "", name)
    short = bank.get("short_name") or ""
    tokens = {name, base, short}
    if short.endswith("农商行"):
        tokens.add(short.replace("农商行", "农村商业银行"))
    return [t for t in tokens if len(t) >= 4]


def _tavily(query: str, use_cache: bool = True) -> list[dict]:
    key = ENV.get("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("TAVILY_API_KEY is not configured")
    cpath = CACHE_DIR / (_key("search", query, date.today().isoformat()) + ".json")
    if use_cache and cpath.exists():
        return _load_json(cpath, [])
    resp = requests.post(
        config.TAVILY_URL,
        json={
            "api_key": key, "query": query, "topic": "general",
            "search_depth": "advanced", "max_results": 6,
            "include_answer": False, "include_raw_content": False,
        },
        timeout=60,
    )
    resp.raise_for_status()
    out = []
    for row in resp.json().get("results") or []:
        url = (row.get("url") or "").strip()
        content = re.sub(r"\s+", " ", row.get("content") or "").strip()
        if url and len(content) >= 60:
            out.append({"title": (row.get("title") or "").strip(), "url": url,
                        "content": content[:1800]})
    _save_json(cpath, out)
    return out


def _news_rows(bank_id: str) -> list[dict]:
    news = _load_json(config.NEWS_DIR / "by_bank.json", {}).get(bank_id) or []
    rows = []
    for item in news:
        content = re.sub(r"\s+", " ", " ".join(
            [item.get("title") or "", item.get("summary") or "", item.get("quote") or ""]
        )).strip()
        if item.get("url") and len(content) >= 40:
            rows.append({"title": item.get("title") or "", "url": item["url"],
                         "content": content[:1500]})
    return rows


def _keyword_focus(rows: list[dict]) -> list[dict]:
    hits = []
    seen = set()
    for row in rows:
        text = f"{row['title']} {row['content']}"
        for tag, words in config.FOCUS_TAGS.items():
            if tag in seen:
                continue
            for word in words:
                idx = text.find(word)
                if idx < 0:
                    continue
                start = max(0, idx - 25)
                hits.append({"tag": tag, "quote": text[start:start + 90].strip(),
                             "url": row["url"], "title": row["title"],
                             "basis": "公开报道", "confidence": 0.7})
                seen.add(tag)
                break
    return hits


def _extract_llm(bank: dict, rows: list[dict]) -> dict:
    key = ENV.get("DEEPSEEK_API_KEY")
    if not key or not rows:
        return {}
    prompt_rows = [{"title": r["title"], "url": r["url"], "content": r["content"][:1200]}
                   for r in rows]
    system = (
        "你是银行公开报道梳理助手。只能依据给定网页摘要，不能根据常识补充。"
        "输出 JSON：{\"focus\":[{\"tag\":\"产业标签\",\"quote\":\"包含该判断的原文片段\","
        "\"url\":\"来源链接\"}],\"summary\":\"不超过50字的重点业务概括\"}。"
        f"tag 只能从以下选择：{'、'.join(TAG_NAMES)}。"
        "每个 tag 必须有原文直接依据；没有把握就返回空数组；quote 必须逐字出现在摘要中；"
        "不得使用未提供的链接。"
    )
    user = (
        f"银行：{bank.get('name')}（{bank.get('province')}，{bank.get('type_label')}）。"
        f"网页摘要：{json.dumps(prompt_rows, ensure_ascii=False)}"
    )
    ckey = _key("focus-v1", bank["id"], json.dumps(prompt_rows, ensure_ascii=False))
    cpath = CACHE_DIR / (ckey + ".json")
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


def fetch_bank(bank: dict, use_cache: bool = True, use_llm: bool = True) -> dict:
    tokens = _tokens(bank)
    rows = _news_rows(bank["id"])
    if not rows:
        query = (
            f"{bank.get('short_name')} {bank.get('name')} 重点支持 产业 贷款 "
            "科技 农业 制造 小微 2025"
        )
        results = _tavily(query, use_cache=use_cache)
        rows = [r for r in results
                if any(tok in f"{r['title']} {r['content']}" for tok in tokens)]

    confirmed = []
    summary = ""
    if rows and use_llm:
        parsed = _extract_llm(bank, rows)
        by_url = {r["url"]: r for r in rows}
        used = set()
        for item in parsed.get("focus") or []:
            tag = (item.get("tag") or "").strip()
            url = (item.get("url") or "").strip()
            quote = re.sub(r"\s+", " ", item.get("quote") or "").strip()
            if tag not in config.FOCUS_TAGS or tag in used or url not in by_url:
                continue
            if len(quote) < 6 or quote not in by_url[url]["content"]:
                continue
            used.add(tag)
            confirmed.append({
                "tag": tag, "quote": quote[:120], "url": url,
                "title": by_url[url]["title"], "basis": "公开报道", "confidence": 0.8,
            })
        summary = (parsed.get("summary") or "").strip()[:60]
    if not confirmed and rows:
        confirmed = _keyword_focus(rows)

    region = list(config.PROVINCE_INDUSTRIES.get(bank.get("province") or "", []))
    focus = confirmed[:4]
    if not focus:
        focus = [{
            "tag": tag, "quote": "", "url": "", "title": "",
            "basis": REGION_BASIS, "confidence": 0.4,
        } for tag in region[:3]]
    return {
        "focus": focus,
        "region": region,
        "summary": summary,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _candidates(group: str) -> list[dict]:
    banks = registry.load_registry()["banks"]
    picked = [b for b in banks if group == "all" or b["group"] == group]
    picked.sort(key=lambda b: b["name"])
    return picked


def select_banks(limit: int, group: str = "city", force: bool = False) -> list[dict]:
    candidates = _candidates(group)
    rotation = _load_json(ROTATION_PATH, {"cursor": 0, "batch": [], "group": group})
    if force and rotation.get("batch"):
        by_id = {b["id"]: b for b in candidates}
        picked = [by_id[i] for i in rotation["batch"] if i in by_id]
        if picked:
            return picked
    cursor = int(rotation.get("cursor") or 0) % max(1, len(candidates))
    picked = [candidates[(cursor + i) % len(candidates)]
              for i in range(min(limit, len(candidates)))]
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
    confirmed_count = inferred_count = 0
    for i, bank in enumerate(banks, 1):
        try:
            item = fetch_bank(bank, use_cache=not force, use_llm=use_llm)
        except Exception as exc:
            if verbose:
                print(f"[{i}/{len(banks)}] {bank['short_name']} failed: {type(exc).__name__}")
            continue
        data[bank["id"]] = item
        if item["focus"] and item["focus"][0]["basis"] == "公开报道":
            confirmed_count += 1
        else:
            inferred_count += 1
        if verbose:
            tags = ",".join(f["tag"] for f in item["focus"]) or "无"
            print(f"[{i}/{len(banks)}] {bank['short_name']}: {tags}")
        if i % 5 == 0:
            _save_json(OUT_PATH, data)
    _save_json(OUT_PATH, data)
    coverage = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "group": group,
        "banks_checked": len(banks),
        "banks_with_confirmed_focus": confirmed_count,
        "banks_with_inferred_focus": inferred_count,
        "total_banks_with_focus": len(data),
    }
    _save_json(COVERAGE_PATH, coverage)
    return coverage


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank business-focus classifier")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--group", default="city",
                        choices=["city", "rural", "village", "all"])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--bank-id", action="append", default=[])
    args = parser.parse_args()
    result = collect(limit=args.limit, group=args.group, force=args.force,
                     use_llm=not args.no_llm, bank_ids=args.bank_id or None)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
