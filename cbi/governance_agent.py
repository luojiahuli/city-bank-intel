"""Collect public 董监高 information for city commercial banks.

The agent extracts only names and roles explicitly present in public sources.
Bank-to-bank links are created only when a source states that a person served
at another bank; same-name overlaps are written to a QA file, not to the graph.
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
from cbi.nfra_penalty import BankResolver

GOV_DIR = config.DATA_DIR / "governance"
CACHE_DIR = config.CACHE_DIR / "governance"
ROTATION_PATH = GOV_DIR / "rotation.json"
OUT_PATH = GOV_DIR / "by_bank.json"
OVERLAP_PATH = GOV_DIR / "name_overlap.json"
COVERAGE_PATH = GOV_DIR / "coverage.json"
for d in (GOV_DIR, CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)

ROLE_HINTS = ("董事长", "副董事长", "董事", "监事长", "监事", "行长", "副行长",
              "董事会秘书", "党委书记", "纪委书记", "首席", "独立董事")
ROLE_EXCLUDE = ("分行", "支行", "部门", "经理", "处长", "科长", "主任", "职员")


def _senior_role(role: str) -> bool:
    role = role.strip()
    if not role or len(role) > 24:
        return False
    if any(x in role for x in ROLE_EXCLUDE):
        return False
    return any(h in role for h in ROLE_HINTS)


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
            out.append({"title": (row.get("title") or "").strip(),
                        "url": url, "content": content[:1800]})
    _save_json(cpath, out)
    return out


def _tokens(bank: dict) -> list[str]:
    name = bank.get("name") or ""
    base = re.sub(r"(股份有限公司|有限责任公司|有限公司)$", "", name)
    short = bank.get("short_name") or ""
    tokens = {name, base, short}
    if short.endswith("农商行"):
        tokens.add(short.replace("农商行", "农村商业银行"))
    return [t for t in tokens if len(t) >= 4]


def extract(bank: dict, results: list[dict]) -> dict:
    key = ENV.get("DEEPSEEK_API_KEY")
    if not key or not results:
        return {}
    rows = [{"title": r["title"], "url": r["url"], "content": r["content"][:1400]}
            for r in results]
    system = (
        "你是银行公开信息抽取助手。只依据给定网页摘要，禁止根据常识补充人名或职务。输出 JSON："
        "{\"people\":[{\"name\":\"姓名\",\"role\":\"董监高职务\",\"since\":\"任职起始\",\"until\":\"离任时间\"}],"
        "\"cross_bank\":[{\"name\":\"姓名\",\"other_bank\":\"曾任职或现任职的另一家银行全称/简称\","
        "\"role\":\"在该行的职务\",\"period\":\"时间\",\"quote\":\"原文片段\"}],"
        "\"period\":\"名单对应期间\",\"sources\":[{\"url\":\"来源链接\",\"field\":\"people/cross_bank\","
        "\"quote\":\"包含该信息的原文片段\"}]}。"
        "只收录董事、监事、行长、副行长、董秘、党委书记、纪委书记、首席信息官等高层；不收部门经理。"
        "cross_bank 仅在原文明确写出该人在另一家银行任职时填写；严禁根据同名推断。"
        "没有把握的字段留空；不要使用未提供的链接。"
    )
    user = (
        f"银行：{bank.get('name')}（{bank.get('province')}，{bank.get('type_label')}）。"
        f"网页摘要：{json.dumps(rows, ensure_ascii=False)}"
    )
    ckey = _key("governance-v1", bank["id"], json.dumps(rows, ensure_ascii=False))
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


def fetch_bank(bank: dict, use_cache: bool = True) -> dict | None:
    query = (
        f"{bank.get('short_name')} {bank.get('name')} 董事 监事 高级管理人员 名单 "
        "2025 年报 简历"
    )
    results = _tavily(query, use_cache=use_cache)
    tokens = _tokens(bank)
    relevant = [
        r for r in results
        if any(tok in f"{r['title']} {r['content']}" for tok in tokens)
    ]
    if not relevant:
        return None
    parsed = extract(bank, relevant)
    if not parsed:
        return None
    people = []
    seen = set()
    for p in parsed.get("people") or []:
        name = (p.get("name") or "").strip()
        role = (p.get("role") or "").strip()
        if not name or not role or name in seen:
            continue
        if len(name) < 2 or len(name) > 6:
            continue
        if not any(h in role for h in ROLE_HINTS):
            continue
        seen.add(name)
        people.append({
            "name": name, "role": role,
            "since": (p.get("since") or "").strip()[:50],
            "until": (p.get("until") or "").strip()[:50],
        })
    if not people:
        return None
    by_url = {r["url"]: r for r in relevant}
    sources = []
    used_urls = set()
    for src in parsed.get("sources") or []:
        url = src.get("url")
        if url not in by_url:
            continue
        used_urls.add(url)
        sources.append({
            "kind": "governance",
            "title": by_url[url]["title"],
            "url": url,
            "field": src.get("field"),
            "quote": (src.get("quote") or "")[:180],
            "as_of": parsed.get("period"),
        })
    for url in by_url:
        if url in used_urls:
            continue
        sources.append({
            "kind": "governance", "title": by_url[url]["title"], "url": url,
            "as_of": parsed.get("period"),
        })
    return {
        "people": people[:40],
        "cross_bank": parsed.get("cross_bank") or [],
        "period": (parsed.get("period") or "").strip()[:40],
        "sources": sources[:6],
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def build_links(data: dict, banks: list[dict]) -> tuple[dict, list[dict]]:
    resolver = BankResolver(banks)
    by_id = {b["id"]: b for b in banks}
    overlap_index: dict[str, list[tuple[str, str]]] = {}
    for bank_id, item in data.items():
        for person in item.get("people") or []:
            overlap_index.setdefault(person["name"], []).append((bank_id, person["role"]))

    for bank_id, item in data.items():
        shares = []
        explicit = set()
        for row in item.get("cross_bank") or []:
            name = (row.get("name") or "").strip()
            other = (row.get("other_bank") or "").strip()
            role = (row.get("role") or "").strip()
            if not name or not other:
                continue
            # Only board / senior-management moves count as governance links.
            if not _senior_role(role):
                continue
            hit = resolver.resolve(other)
            if not hit or hit["bank"]["id"] == bank_id:
                continue
            other_id = hit["bank"]["id"]
            explicit.add((name, other_id))
            shares.append({
                "name": name,
                "bank_id": other_id,
                "bank": by_id[other_id]["name"],
                "role": (row.get("role") or "").strip(),
                "period": (row.get("period") or "").strip()[:30],
                "quote": (row.get("quote") or "").strip()[:140],
                "confidence": 0.9,
                "basis": "公开简历/报道明确记载",
            })
        item["shares"] = shares
        item["explicit_names"] = sorted({name for name, _ in explicit})

    overlaps = []
    for name, entries in overlap_index.items():
        bank_ids = {b for b, _ in entries}
        if len(name) >= 3 and len(bank_ids) >= 2:
            overlaps.append({
                "name": name,
                "banks": [
                    {"bank_id": b, "bank": by_id[b]["name"], "role": role}
                    for b, role in entries
                ],
            })
    overlaps.sort(key=lambda x: -len(x["banks"]))
    return data, overlaps


def select_banks(limit: int, force: bool = False) -> list[dict]:
    banks = [b for b in registry.load_registry()["banks"] if b["group"] == "city"]
    banks.sort(key=lambda b: b["name"])
    rotation = _load_json(ROTATION_PATH, {"cursor": 0, "batch": []})
    if force and rotation.get("batch"):
        by_id = {b["id"]: b for b in banks}
        picked = [by_id[i] for i in rotation["batch"] if i in by_id]
        if picked:
            return picked
    cursor = int(rotation.get("cursor") or 0) % max(1, len(banks))
    picked = [banks[(cursor + i) % len(banks)] for i in range(min(limit, len(banks)))]
    rotation.update({
        "cursor": (cursor + len(picked)) % max(1, len(banks)),
        "batch": [b["id"] for b in picked],
        "last_run": date.today().isoformat(),
    })
    _save_json(ROTATION_PATH, rotation)
    return picked


def collect(limit: int = 10, force: bool = False, bank_ids: list[str] | None = None,
            verbose: bool = True) -> dict:
    banks = registry.load_registry()["banks"]
    by_id = {b["id"]: b for b in banks}
    picked = ([by_id[i] for i in bank_ids if i in by_id] if bank_ids
              else select_banks(limit, force=force))
    data = _load_json(OUT_PATH, {})
    hits = 0
    for i, bank in enumerate(picked, 1):
        try:
            item = fetch_bank(bank, use_cache=not force)
        except Exception as exc:
            if verbose:
                print(f"[{i}/{len(picked)}] {bank['short_name']} failed: {type(exc).__name__}")
            continue
        if item:
            data[bank["id"]] = item
            hits += 1
        if verbose:
            n = len((data.get(bank["id"]) or {}).get("people") or [])
            print(f"[{i}/{len(picked)}] {bank['short_name']}: {n} 人")
        if i % 5 == 0:
            _save_json(OUT_PATH, data)
    data, overlaps = build_links(data, banks)
    _save_json(OUT_PATH, data)
    _save_json(OVERLAP_PATH, {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "candidates": overlaps[:200],
        "note": "同名董监高仅作人工核验线索，未写入关系图。",
    })
    coverage = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "banks_checked": len(picked),
        "banks_with_people": hits,
        "total_banks_with_people": sum(1 for v in data.values() if v.get("people")),
        "people_total": sum(len(v.get("people") or []) for v in data.values()),
        "explicit_links": sum(len(v.get("shares") or []) for v in data.values()),
        "same_name_overlap_groups": len(overlaps),
    }
    _save_json(COVERAGE_PATH, coverage)
    return coverage


def main() -> None:
    parser = argparse.ArgumentParser(description="Public bank governance agent")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--bank-id", action="append", default=[])
    args = parser.parse_args()
    result = collect(limit=args.limit, force=args.force,
                     bank_ids=args.bank_id or None)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
