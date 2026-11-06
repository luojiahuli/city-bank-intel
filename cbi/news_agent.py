"""Public-news collection agent for city commercial banks.

Tavily discovers public articles; DeepSeek only summarises and tags the
retrieved snippets. Every item keeps its URL and publication date, and the LLM
is instructed not to add facts beyond the supplied text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

import config
from cbi import registry

NEWS_DIR = config.NEWS_DIR
CACHE_DIR = config.CACHE_DIR / "news"
for d in (NEWS_DIR, CACHE_DIR, CACHE_DIR / "search", CACHE_DIR / "llm"):
    d.mkdir(parents=True, exist_ok=True)

BY_BANK_PATH = NEWS_DIR / "by_bank.json"
INDUSTRY_PATH = NEWS_DIR / "industry.json"
ROTATION_PATH = NEWS_DIR / "rotation.json"
COVERAGE_PATH = NEWS_DIR / "coverage.json"

TAGS = {
    "大模型": ["大模型", "LLM", "千亿参数", "模型训练"],
    "智能体": ["智能体", "Agent", "AI助手", "数字员工"],
    "AI应用": ["人工智能", "AI", "机器学习", "智能风控", "智能投顾"],
    "数据中台": ["数据中台", "数据治理", "数据平台", "湖仓", "数据仓库", "监管报送"],
    "核心系统": ["核心系统", "新核心", "分布式核心", "主机下移"],
    "信创": ["信创", "国产化", "鲲鹏", "欧拉", "高斯", "自主可控"],
    "智能客服": ["智能客服", "远程银行", "呼叫中心", "坐席"],
    "智慧信贷": ["智慧信贷", "信贷系统", "智能风控", "贷后", "尽调"],
    "移动金融": ["手机银行", "鸿蒙", "移动金融", "全渠道"],
    "数字人民币": ["数字人民币", "数币"],
    "科技投入": ["科技投入", "IT投入", "信息科技", "金融科技", "研发投入"],
    "海外业务": ["海外", "跨境", "国际业务", "全球化"],
    "财报": ["年报", "季报", "营收", "净利润", "总资产", "不良率"],
}

CAP_KEYWORDS = {
    c["id"]: c["keywords"] for c in config.HUAWEI_CAPABILITIES
}
CAP_NAMES = {c["id"]: c["short"] for c in config.HUAWEI_CAPABILITIES}


def _load_env() -> dict:
    env = {}
    for path in (config.ROOT / ".env", Path.home() / ".hermes" / ".env"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    for key in ("TAVILY_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY",
                "QWEN_API_KEY", "OPENAI_API_KEY"):
        if os.getenv(key):
            env[key] = os.environ[key]
    return env


ENV = _load_env()


def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _cache_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]


def _norm_date(raw: str) -> str:
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except Exception:
        m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", raw)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return raw[:10]


def _usable(row: dict) -> bool:
    url = (row.get("url") or "").strip()
    return bool(
        url
        and "tavily.com/knowledge" not in url
        and (row.get("published") or "").strip()
        and len((row.get("content") or "").strip()) >= 60
    )


def tavily_search(query: str, days: int = 180, max_results: int = 6,
                  use_cache: bool = True) -> list[dict]:
    key = ENV.get("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("TAVILY_API_KEY is not configured")
    cpath = CACHE_DIR / "search" / (
        _cache_key(query, str(days), str(max_results), date.today().isoformat()) + ".json"
    )
    if use_cache and cpath.exists():
        return [r for r in _load_json(cpath, []) if _usable(r)]
    resp = requests.post(
        config.TAVILY_URL,
        json={
            "api_key": key,
            "query": query,
            "topic": "news",
            "search_depth": "basic",
            "max_results": max_results,
            "days": days,
            "include_answer": False,
            "include_raw_content": False,
        },
        timeout=45,
    )
    resp.raise_for_status()
    data = resp.json()
    items = []
    for row in data.get("results") or []:
        url = (row.get("url") or "").strip()
        if not url or "tavily.com/knowledge" in url:
            continue
        published = _norm_date(row.get("published_date") or "")
        content = re.sub(r"\s+", " ", row.get("content") or "").strip()
        # News without a date or with only navigation text is not usable.
        if not published or len(content) < 60:
            continue
        items.append({
            "title": re.sub(r"\s+", " ", row.get("title") or "").strip(),
            "url": url,
            "published": published,
            "content": content,
            "score": row.get("score"),
        })
    _save_json(cpath, items)
    return items


def classify(text: str) -> tuple[list[str], list[str]]:
    low = (text or "").lower()
    tags = [tag for tag, words in TAGS.items() if any(w.lower() in low for w in words)]
    caps = []
    for cap_id, words in CAP_KEYWORDS.items():
        if any(w.lower() in low for w in words):
            caps.append(cap_id)
    return tags, caps[:4]


def _bank_tokens(bank: dict) -> list[str]:
    name = bank.get("name") or ""
    base = re.sub(r"(股份有限公司|有限责任公司|有限公司)$", "", name)
    short = bank.get("short_name") or ""
    tokens = {name, base, short}
    if short.endswith("农商行"):
        tokens.add(short.replace("农商行", "农村商业银行"))
    return [t for t in tokens if len(t) >= 4]


def relevant_results(bank: dict, results: list[dict]) -> list[dict]:
    tokens = _bank_tokens(bank)
    if not tokens:
        return results
    out = []
    for row in results:
        text = f"{row.get('title','')} {row.get('content','')}"
        if any(tok in text for tok in tokens):
            out.append(row)
    return out


def _fallback_items(results: list[dict], bank: dict | None = None) -> list[dict]:
    tokens = _bank_tokens(bank) if bank else []
    out = []
    for row in results:
        text = row["content"]
        tags, caps = classify(f"{row['title']} {text}")
        idx = next((text.find(tok) for tok in tokens if tok in text), -1)
        if idx >= 0:
            start = max(0, idx - 20)
            excerpt = text[start:start + 130]
        else:
            excerpt = text[:130]
        summary = excerpt.strip() + ("…" if len(excerpt) >= 130 else "")
        out.append({
            "title": row["title"], "url": row["url"], "published": row["published"],
            "summary": summary, "ai_tags": tags, "capability_ids": caps,
            "quote": excerpt[:70].strip(), "analysis": "关键词抽取（未调用大模型）",
        })
    return out


def _llm_summaries(bank: dict, results: list[dict], model: str = "") -> list[dict]:
    key = ENV.get("DEEPSEEK_API_KEY")
    if not key or not results:
        return []
    caps = [f"{c['id']}={c['name']}" for c in config.HUAWEI_CAPABILITIES]
    prompt_rows = [
        {"title": r["title"], "url": r["url"], "published": r["published"],
         "content": r["content"][:600]}
        for r in results
    ]
    system = (
        "你是银行公开新闻抽取助手。只能依据用户提供的搜索结果写作，禁止补充或推断未出现的事实。"
        "输出 JSON 对象，格式：{\"items\":[{\"url\":\"原链接\",\"summary\":\"不超过80字的中文摘要\","
        "\"ai_tags\":[\"标签\"],\"capability_ids\":[\"能力ID\"],\"quote\":\"不超过60字的原文摘录\","
        "\"analysis\":\"一句话说明与华为能力的关系\"}]}。"
        f"标签从以下选择：{'、'.join(TAGS)}。能力ID从以下选择：{'；'.join(caps)}。"
        "保留所有与数字化、AI、IT投入、财报、业务战略相关的条目；无关条目可丢弃。不要编造 URL。"
    )
    user = (
        f"银行：{bank.get('name')}（{bank.get('province')}，{bank.get('type_label')}）。"
        f"搜索结果：{json.dumps(prompt_rows, ensure_ascii=False)}"
    )
    ckey = _cache_key(bank["id"], json.dumps(prompt_rows, ensure_ascii=False))
    cpath = CACHE_DIR / "llm" / f"{ckey}.json"
    if cpath.exists():
        return _load_json(cpath, [])
    url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    payload = {
        "model": model or config.LLM_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    try:
        resp = requests.post(
            url, json=payload, timeout=90,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        items = parsed.get("items") or []
    except Exception as exc:
        print(f"  LLM failed for {bank['short_name']}: {type(exc).__name__}")
        return []
    by_url = {r["url"]: r for r in results}
    out = []
    for item in items:
        url = (item.get("url") or "").strip()
        source = by_url.get(url)
        if not source:
            continue
        tags = [t for t in (item.get("ai_tags") or []) if t in TAGS]
        caps = [c for c in (item.get("capability_ids") or []) if c in CAP_KEYWORDS]
        if not caps:
            _, caps = classify(f"{source['title']} {source['content']}")
        out.append({
            "title": source["title"],
            "url": url,
            "published": source["published"],
            "summary": (item.get("summary") or "").strip()[:180],
            "ai_tags": tags,
            "capability_ids": caps[:4],
            "quote": (item.get("quote") or "").strip()[:100],
            "analysis": (item.get("analysis") or "").strip()[:160],
        })
    if out:
        _save_json(cpath, out)
    return out


def fetch_bank_news(bank: dict, days: int = 180, use_cache: bool = True,
                    use_llm: bool = True) -> list[dict]:
    query = f"{bank['short_name']} 银行 人工智能 大模型 数字化 信息科技"
    results = tavily_search(query, days=days, max_results=8, use_cache=use_cache)
    results = relevant_results(bank, results)
    if not results:
        return []
    items = _llm_summaries(bank, results) if use_llm else []
    if not items:
        items = _fallback_items(results, bank)
    # De-duplicate by URL and drop stale/unusable items.
    dedup = {}
    for item in items:
        if item["url"] not in dedup:
            dedup[item["url"]] = item
    out = sorted(dedup.values(), key=lambda x: x.get("published") or "", reverse=True)
    for item in out:
        item["source"] = "Tavily · 公开新闻"
        item["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tags, caps = classify(f"{item['title']} {item.get('summary','')} {item.get('quote','')}")
        item["ai_tags"] = sorted(set(item.get("ai_tags") or []) | set(tags))
        item["capability_ids"] = sorted(set(item.get("capability_ids") or []) | set(caps))[:4]
    return out[:6]


def select_banks(limit: int, force: bool = False) -> list[dict]:
    data = registry.load_registry()
    city = [b for b in data["banks"] if b["group"] == "city"]
    city.sort(key=lambda b: b["name"])
    rotation = _load_json(ROTATION_PATH, {"cursor": 0, "last_run": None, "batch": []})
    if force and rotation.get("batch"):
        ids = rotation["batch"]
        by_id = {b["id"]: b for b in city}
        picked = [by_id[i] for i in ids if i in by_id]
        if picked:
            return picked
    cursor = int(rotation.get("cursor") or 0) % max(1, len(city))
    picked = [city[(cursor + i) % len(city)] for i in range(min(limit, len(city)))]
    rotation.update({
        "cursor": (cursor + len(picked)) % max(1, len(city)),
        "last_run": date.today().isoformat(),
        "batch": [b["id"] for b in picked],
    })
    _save_json(ROTATION_PATH, rotation)
    return picked


def collect(limit: int = 20, days: int = 180, force: bool = False,
            use_llm: bool = True, bank_ids: list[str] | None = None,
            verbose: bool = True) -> dict:
    data = registry.load_registry()
    by_id = {b["id"]: b for b in data["banks"]}
    if bank_ids:
        banks = [by_id[i] for i in bank_ids if i in by_id]
    else:
        banks = select_banks(limit, force=force)
    by_bank = _load_json(BY_BANK_PATH, {})
    total_items = 0
    no_news = []
    for i, bank in enumerate(banks, 1):
        try:
            items = fetch_bank_news(bank, days=days, use_cache=not force, use_llm=use_llm)
        except Exception as exc:
            if verbose:
                print(f"[{i}/{len(banks)}] {bank['short_name']} failed: {type(exc).__name__}")
            continue
        if items:
            by_bank[bank["id"]] = items
            total_items += len(items)
        elif bank["id"] not in by_bank:
            by_bank[bank["id"]] = []
            no_news.append(bank["short_name"])
        if verbose:
            print(f"[{i}/{len(banks)}] {bank['short_name']}: {len(items)} 条")
        if i % 5 == 0:
            _save_json(BY_BANK_PATH, by_bank)
    _save_json(BY_BANK_PATH, by_bank)
    coverage = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "banks_checked": len(banks),
        "banks_with_news": sum(1 for b in banks if by_bank.get(b["id"])),
        "news_items": total_items,
        "no_news": no_news,
        "days": days,
        "llm": bool(use_llm and ENV.get("DEEPSEEK_API_KEY")),
    }
    _save_json(COVERAGE_PATH, coverage)
    return coverage


def collect_industry(limit: int = 8, use_cache: bool = True) -> dict:
    query = "中国 中小银行 数字金融 人工智能 大模型 监管 科技投入"
    try:
        results = tavily_search(query, days=30, max_results=limit, use_cache=use_cache)
    except Exception as exc:
        return {"error": type(exc).__name__}
    items = _fallback_items(results)
    for item in items:
        item["source"] = "Tavily · 行业新闻"
        item["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": items,
    }
    _save_json(INDUSTRY_PATH, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="City-bank public news agent")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--force", action="store_true", help="bypass cache and reuse current batch")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--all-city", action="store_true")
    parser.add_argument("--bank-id", action="append", default=[])
    parser.add_argument("--industry", action="store_true")
    args = parser.parse_args()
    limit = 124 if args.all_city else args.limit
    result = collect(limit=limit, days=args.days, force=args.force,
                     use_llm=not args.no_llm, bank_ids=args.bank_id or None)
    if args.industry:
        result["industry"] = {
            k: v for k, v in collect_industry().items() if k != "items"
        }
        result["industry"]["items"] = len(_load_json(INDUSTRY_PATH, {}).get("items") or [])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
