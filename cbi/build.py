"""Assemble the static graph, shards, search index and edge groups."""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import config
from cbi import huawei, layout, registry

COLORS = {
    "city": "#4cc9f0",
    "rural": "#6ee7a8",
    "village": "#f4c95d",
    "other": "#b39ddb",
    "province": "#94a3b8",
    "business": "#f472b6",
    "focus": "#fbbf24",
    "it": "#60a5fa",
    "digital": "#a78bfa",
    "huawei": "#ff8a4c",
    "person": "#e2e8f0",
    "similar": "#38bdf8",
}

BUSINESS_INFER = {
    "city": ["公司金融", "零售金融", "金融市场"],
    "rural": ["普惠小微", "零售金融", "公司金融"],
    "village": ["普惠小微", "零售金融"],
    "other": ["公司金融", "零售金融", "金融市场"],
}


def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def _shard(bank_id: str) -> str:
    return hashlib.md5(bank_id.encode("utf-8")).hexdigest()[:2]


def _asset_tier(asset: float | None) -> str:
    if not asset:
        return "未披露"
    if asset >= 10000:
        return "万亿以上"
    if asset >= 5000:
        return "5000-10000"
    if asset >= 1000:
        return "1000-5000"
    if asset >= 300:
        return "300-1000"
    return "300以下"


def _size(bank: dict) -> float:
    if bank.get("asset"):
        return round(3.0 + 3.3 * math.log10(max(10.0, bank["asset"])), 2)
    return {"city": 5.0, "rural": 3.1, "village": 2.1}.get(bank.get("group"), 4.5)


def _completeness(bank: dict) -> int:
    score = 15
    if bank.get("province") and bank.get("city"):
        score += 5
    if bank.get("asset"):
        score += 10
    elif bank.get("asset_tier") and bank["asset_tier"] != "未披露":
        score += 4
    if bank.get("revenue"):
        score += 10
    if bank.get("profit") is not None:
        score += 10
    if bank.get("employees"):
        score += 5
    if bank.get("listed"):
        score += 5
    if bank.get("business_tags"):
        score += 5
    if bank.get("it_spend"):
        score += 5
    if bank.get("digital_tier"):
        score += 5
    if bank.get("news"):
        score += 10
    if bank.get("penalty_checked"):
        score += 10
    if bank.get("governance"):
        score += 5
    if bank.get("overseas") is not None:
        score += 5
    if bank.get("website"):
        score += 4
    if bank.get("site_profile"):
        score += 8
    return min(100, score)


def _apply_seed(banks: list[dict]) -> int:
    seed = _load_json(config.DATA_DIR / "enrichment" / "seed_2025.json", {})
    by_name = seed.get("banks", {})
    source = seed.get("source") or {}
    hit = 0
    for bank in banks:
        item = by_name.get(bank["short_name"]) or by_name.get(bank["name"])
        if not item:
            continue
        hit += 1
        for key in ("asset", "revenue", "profit", "employees", "listed", "asset_tier",
                    "it_spend", "overseas", "branches", "customers", "deposits", "loans"):
            if item.get(key) is not None:
                bank[key] = item[key]
        if item.get("asset"):
            bank["asset_year"] = seed.get("as_of")
        bank.setdefault("sources", []).append({
            "kind": "financial",
            "title": source.get("title", ""),
            "url": source.get("url", ""),
            "published": source.get("published"),
            "as_of": seed.get("as_of"),
        })
    return hit


def _merge_optional(banks: list[dict]) -> dict:
    """Merge outputs from later agents if present."""
    stats = {}
    by_id = {b["id"]: b for b in banks}

    penalties = _load_json(config.DATA_DIR / "penalties" / "by_bank.json", {})
    for bank_id, rows in penalties.items():
        bank = by_id.get(bank_id)
        if not bank:
            continue
        bank["penalties"] = rows
        bank["penalty_count"] = len(rows)
        bank["penalty_checked"] = True
        amount = 0.0
        for row in rows:
            amount += row.get("amount_wan") or 0
        bank["penalty_amount"] = round(amount, 2) if amount else None
        if rows:
            latest = max(r.get("date") or "" for r in rows)
            bank["penalty_latest"] = latest
    stats["penalty_banks"] = sum(1 for b in banks if b.get("penalty_checked"))

    news = _load_json(config.DATA_DIR / "news" / "by_bank.json", {})
    for bank_id, rows in news.items():
        bank = by_id.get(bank_id)
        if not bank:
            continue
        bank["news"] = rows
        tags = set()
        evidence = []
        for row in rows:
            tags.update(row.get("ai_tags") or [])
            for cap_id in row.get("capability_ids") or []:
                evidence.append({
                    "capability_id": cap_id,
                    "title": row.get("title", ""),
                    "url": row.get("url", ""),
                    "published": row.get("published"),
                    "quote": row.get("quote", ""),
                })
        if evidence:
            bank["news_evidence"] = evidence
        if tags:
            bank["digital_tier"] = "高" if len(tags) >= 4 else "中"
    stats["news_banks"] = sum(1 for b in banks if b.get("news"))

    financials = _load_json(config.DATA_DIR / "enrichment" / "financials.json", {})
    for bank_id, item in financials.items():
        bank = by_id.get(bank_id)
        if not bank:
            continue
        for key in ("asset", "revenue", "profit", "employees", "branches", "customers",
                    "deposits", "loans", "overseas", "it_spend", "listed"):
            if item.get(key) is not None and bank.get(key) is None:
                bank[key] = item[key]
        if item.get("business_tags"):
            bank["business_tags"] = item["business_tags"]
            bank["business_inferred"] = False
        for src in item.get("sources", []):
            bank.setdefault("sources", []).append(src)
    stats["financial_banks"] = sum(1 for b in banks if b.get("revenue") or b.get("asset"))

    governance = _load_json(config.DATA_DIR / "governance" / "by_bank.json", {})
    for bank_id, item in governance.items():
        bank = by_id.get(bank_id)
        if not bank:
            continue
        bank["governance"] = item.get("people", [])
        bank["governance_shares"] = item.get("shares", [])
        for src in item.get("sources", []):
            bank.setdefault("sources", []).append(src)
    stats["governance_banks"] = sum(1 for b in banks if b.get("governance"))

    focus = _load_json(config.DATA_DIR / "enrichment" / "focus.json", {})
    for bank_id, item in focus.items():
        bank = by_id.get(bank_id)
        if not bank:
            continue
        entries = (item.get("focus") or [])[:4]
        confirmed = any(e.get("basis") == "公开报道" for e in entries)
        if confirmed:
            seen_tags = {e.get("tag") for e in entries}
            for tag in (item.get("region") or [])[:2]:
                if tag not in seen_tags and len(entries) < 4:
                    entries.append({
                        "tag": tag, "quote": "", "url": "", "title": "",
                        "basis": "区域产业结构推断（待核验）", "confidence": 0.4,
                    })
                    seen_tags.add(tag)
        bank["business_focus"] = entries
        bank["focus_confirmed"] = confirmed
        bank["region_industries"] = item.get("region") or []
        if item.get("summary"):
            bank["focus_summary"] = item["summary"]
        mapped: list[str] = []
        evidence: dict[str, dict] = {}
        for entry in entries:
            tag = entry.get("tag")
            for btag in config.FOCUS_BUSINESS_MAP.get(tag, []):
                if btag not in mapped:
                    mapped.append(btag)
                if entry.get("basis") == "公开报道" and entry.get("url"):
                    evidence.setdefault(btag, entry)
            if entry.get("basis") == "公开报道" and entry.get("url"):
                bank.setdefault("sources", []).append({
                    "kind": "focus",
                    "title": entry.get("title") or f"{bank['short_name']} 重点产业公开报道",
                    "url": entry["url"],
                    "field": "business_focus",
                    "quote": (entry.get("quote") or "")[:160],
                    "as_of": entry.get("published"),
                })
        existing = bank.get("business_tags") or []
        bank["business_tags"] = existing + [t for t in mapped if t not in existing]
        if evidence:
            bank["business_evidence"] = evidence
            bank["business_inferred"] = any(
                t not in evidence for t in bank["business_tags"])
        else:
            bank["business_inferred"] = True

    for bank in banks:
        if bank["id"] in focus or bank.get("business_inferred") is False:
            continue
        region = config.PROVINCE_INDUSTRIES.get(bank.get("province") or "", [])
        if not region:
            continue
        mapped: list[str] = []
        for tag in region[:2]:
            for btag in config.FOCUS_BUSINESS_MAP.get(tag, []):
                if btag not in mapped:
                    mapped.append(btag)
        base = list(bank.get("business_tags") or
                    BUSINESS_INFER.get(bank.get("group"), []))
        bank["business_tags"] = list(dict.fromkeys(mapped + base))
        bank["business_inferred"] = True

    website = _load_json(config.DATA_DIR / "enrichment" / "website.json", {})
    for bank_id, item in website.items():
        bank = by_id.get(bank_id)
        if not bank:
            continue
        bank["site_checked"] = True
        if item.get("website"):
            bank["website"] = item["website"]
        if item.get("status") == "ok" and item.get("features"):
            bank["site_profile"] = {
                "features": item.get("features"),
                "key_business": item.get("key_business") or [],
                "recent_moves": item.get("recent_moves") or [],
                "recent_items": item.get("recent_items") or [],
                "evidence": item.get("evidence") or [],
                "confidence": item.get("confidence") or "低",
                "basis": item.get("basis") or "",
                "fetched_at": item.get("fetched_at"),
            }
        for src in item.get("sources", []):
            bank.setdefault("sources", []).append(src)
    stats["website_banks"] = sum(1 for b in banks if b.get("website"))
    stats["site_profile_banks"] = sum(1 for b in banks if b.get("site_profile"))

    stats["focus_banks"] = sum(1 for b in banks if b.get("business_focus"))
    stats["focus_confirmed_banks"] = sum(1 for b in banks if b.get("focus_confirmed"))
    return stats


RANK_KEYS = ("asset", "revenue", "profit", "employees", "it_spend",
             "penalty_count", "deposits", "loans", "branches", "customers")


def _attach_ranks(banks: list[dict]) -> None:
    """Percentile ranks over the whole universe give each bank its own score."""
    defaults = {"penalty_count": 0.0, "profit": 0.5, "it_spend": 0.2,
                "employees": 0.25, "branches": 0.2, "customers": 0.2}
    ranks: dict[str, dict[str, float]] = {}
    for key in RANK_KEYS:
        pairs = sorted((float(b[key]), b["id"]) for b in banks if b.get(key) is not None)
        n = len(pairs)
        ranks[key] = {
            bid: (i / (n - 1) if n > 1 else 0.5) for i, (_, bid) in enumerate(pairs)
        }
    news_pairs = sorted((len(b.get("news") or []), b["id"]) for b in banks)
    n = len(news_pairs)
    news_rank = {bid: (i / (n - 1) if n > 1 else 0.5)
                 for i, (_, bid) in enumerate(news_pairs)}
    for bank in banks:
        bank["ranks"] = {
            key: round(ranks[key].get(bank["id"], defaults.get(key, 0.25)), 3)
            for key in RANK_KEYS
        }
        bank["ranks"]["news_count"] = round(news_rank.get(bank["id"], 0.0), 3)


def _prepare(banks: list[dict]) -> list[dict]:
    _attach_ranks(banks)
    for bank in banks:
        if not bank.get("asset_tier") or bank["asset_tier"] == "未披露":
            bank["asset_tier"] = _asset_tier(bank.get("asset"))
        if not bank.get("business_tags"):
            bank["business_tags"] = list(BUSINESS_INFER.get(bank.get("group"), []))
            bank["business_inferred"] = True
        else:
            bank.setdefault("business_inferred", False)
        if not bank.get("region_industries"):
            bank["region_industries"] = list(
                config.PROVINCE_INDUSTRIES.get(bank.get("province") or "", []))
        if bank.get("profit") is None:
            bank["profit_status"] = "未披露"
        else:
            bank["profit_status"] = "盈利" if bank["profit"] > 0 else "亏损"
        bank["completeness"] = _completeness(bank)
        bank["size"] = _size(bank)
        bank["color"] = COLORS.get(bank.get("group"), COLORS["other"])
        bank["shard"] = _shard(bank["id"])
        bank["has_news"] = bool(bank.get("news"))
        bank["has_penalties"] = bool(bank.get("penalties"))
        bank["has_governance"] = bool(bank.get("governance"))
        bank["opportunities"] = huawei.score_bank(bank, bank.get("news_evidence"))
        bank["top_opportunity"] = bank["opportunities"][0]["score"] if bank["opportunities"] else 0
        bank["top_capability"] = bank["opportunities"][0]["short"] if bank["opportunities"] else ""
    return banks


def _hub_id(prefix: str, name: str) -> str:
    return prefix + "_" + hashlib.md5(name.encode("utf-8")).hexdigest()[:8]


def _graph_nodes(banks: list[dict], pos: dict, hubs_pos: dict) -> tuple[list[dict], dict]:
    counts = Counter(b.get("province") or "其他" for b in banks)
    province_nodes = []
    province_ids = {}
    for prov, (x, y) in hubs_pos.items():
        n = counts.get(prov, 0)
        if not n and prov not in config.PROVINCE_CENTROIDS:
            continue
        pid = _hub_id("prov", prov)
        province_ids[prov] = pid
        province_nodes.append({
            "id": pid, "name": prov, "type": "province", "group": "province",
            "count": n, "size": round(10 + math.sqrt(max(1, n)) * 2.6, 2),
            "x": round(x, 2), "y": round(y, 2), "color": COLORS["province"],
            "layer": 0,
        })

    business_nodes = []
    business_ids = {}
    for i, name in enumerate(config.BUSINESS_TAGS):
        bid = _hub_id("biz", name)
        business_ids[name] = bid
        business_nodes.append({
            "id": bid, "name": name, "type": "business", "group": "business",
            "size": 11, "x": 380 + i * 220, "y": 42, "color": COLORS["business"],
            "layer": 1,
        })

    mapped_focus = {t for inds in config.PROVINCE_INDUSTRIES.values() for t in inds}
    mapped_focus |= {f.get("tag") for b in banks
                     for f in (b.get("business_focus") or [])}
    focus_nodes = []
    focus_ids = {}
    for name in config.FOCUS_TAGS:
        if name not in mapped_focus:
            continue
        fid = _hub_id("focus", name)
        focus_ids[name] = fid
        focus_nodes.append({
            "id": fid, "name": name, "type": "focus", "group": "focus",
            "size": 10, "x": 300 + len(focus_nodes) * 155, "y": 168,
            "color": COLORS["focus"], "layer": 1,
        })

    it_defs = [
        ("it_high", "IT投入 >5亿", 16), ("it_mid", "IT投入 1-5亿", 14),
        ("it_low", "IT投入 <1亿", 12), ("it_unknown", "IT投入未披露", 14),
    ]
    it_nodes = [{
        "id": i, "name": n, "type": "it", "group": "it", "size": s,
        "x": 54, "y": 300 + idx * 120, "color": COLORS["it"], "layer": 1,
    } for idx, (i, n, s) in enumerate(it_defs)]

    digital_defs = [
        ("dig_high", "AI活跃度高", 15), ("dig_mid", "AI活跃度中", 13),
        ("dig_low", "AI活跃度低", 11), ("dig_unknown", "AI活跃度未采集", 13),
    ]
    digital_nodes = [{
        "id": i, "name": n, "type": "digital", "group": "digital", "size": s,
        "x": 54, "y": 820 + idx * 110, "color": COLORS["digital"], "layer": 1,
    } for idx, (i, n, s) in enumerate(digital_defs)]

    huawei_nodes = []
    for i, cap in enumerate(config.HUAWEI_CAPABILITIES):
        huawei_nodes.append({
            "id": "cap_" + cap["id"], "name": cap["name"], "short": cap["short"],
            "type": "huawei", "group": "huawei", "category": cap["category"],
            "desc": cap["desc"], "entry": cap["entry"],
            "size": 12, "x": layout.W - 76, "y": 120 + i * 94,
            "color": COLORS["huawei"], "layer": 2,
        })

    bank_nodes = []
    for bank in banks:
        x, y = pos.get(bank["id"], (0, 0))
        bank_nodes.append({
            "id": bank["id"], "name": bank["short_name"], "full_name": bank["name"],
            "type": "bank", "group": bank["group"], "type_label": bank["type_label"],
            "province": bank["province"], "city": bank["city"],
            "listed": bank.get("listed"), "asset": bank.get("asset"),
            "asset_tier": bank.get("asset_tier"), "revenue": bank.get("revenue"),
            "profit": bank.get("profit"), "profit_status": bank["profit_status"],
            "employees": bank.get("employees"), "it_spend": bank.get("it_spend"),
            "digital_tier": bank.get("digital_tier"), "penalty_count": bank.get("penalty_count", 0),
            "penalty_amount": bank.get("penalty_amount"), "completeness": bank["completeness"],
            "top_opportunity": bank["top_opportunity"], "top_capability": bank["top_capability"],
            "size": bank["size"], "color": bank["color"], "x": round(x, 2), "y": round(y, 2),
            "shard": bank["shard"], "layer": 3,
            "has_news": bank["has_news"], "has_penalties": bank["has_penalties"],
            "penalty_checked": bool(bank.get("penalty_checked")),
            "has_governance": bank["has_governance"],
            "has_site": bool(bank.get("site_profile")),
            "focus_tags": [f.get("tag") for f in (bank.get("business_focus") or [])][:4],
            "focus_confirmed": bool(bank.get("focus_confirmed")),
        })
    return (bank_nodes + province_nodes + business_nodes + focus_nodes
            + it_nodes + digital_nodes + huawei_nodes,
            {"province": province_ids, "business": business_ids, "focus": focus_ids})


def _write_details(banks: list[dict]) -> None:
    shard_dirs = set()
    for bank in banks:
        d = config.FRONTEND_DATA / "banks" / bank["shard"]
        if bank["shard"] not in shard_dirs:
            d.mkdir(parents=True, exist_ok=True)
            shard_dirs.add(bank["shard"])
        detail = {k: v for k, v in bank.items() if k not in {"news_evidence"}}
        detail["opportunities"] = bank.get("opportunities", [])
        detail["data_notes"] = _notes(bank)
        (d / f"{bank['id']}.json").write_text(
            json.dumps(detail, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )


def _notes(bank: dict) -> list[str]:
    notes = []
    if bank.get("asset_tier") == "未披露":
        notes.append("资产规模未在已采集的公开资料中披露，节点大小仅按机构类型示意。")
    if bank.get("business_inferred"):
        notes.append("主要业务标签结合机构类型与所在省份产业结构推断，待年报/官网信息补充。")
    if bank.get("business_focus") and not bank.get("focus_confirmed"):
        notes.append("重点产业为省级产业结构推断，尚未取得该行公开报道依据。")
    if bank.get("site_checked") and not bank.get("site_profile"):
        notes.append("未能取得可直接访问的官网文本，官网画像暂缺。")
    if not bank.get("news"):
        notes.append("尚未采集到近期 AI/数字化公开报道。")
    if not bank.get("penalty_checked"):
        notes.append("行政处罚记录尚未完成采集，不代表该机构无处罚。")
    return notes


# Business lines that have no explicit disclosure get tied to banks in regions
# whose industry structure implies the line. The sets differ per line, so hub
# degrees reflect the underlying data rather than a uniform fill quota.
BUSINESS_FOCUS_RULE = {
    "财富管理": ["消费零售"],
    "国际业务": ["跨境贸易", "供应链"],
    "绿色金融": ["绿色", "能源化工"],
    "科技金融": ["科技", "制造"],
    "养老金融": ["医药健康"],
}


def _business_links(banks: list[dict], ids: dict) -> list[dict]:
    links = []
    seen = set()
    for bank in banks:
        evidence = bank.get("business_evidence") or {}
        for tag in bank.get("business_tags", [])[:4]:
            bid = ids["business"].get(tag)
            if not bid or (bank["id"], bid) in seen:
                continue
            seen.add((bank["id"], bid))
            ev = evidence.get(tag)
            row = {
                "source": bank["id"], "target": bid, "tag": tag,
                "relation": "主要业务（公开报道）" if ev else (
                    "主要业务（推断）" if bank.get("business_inferred") else "主要业务"),
                "inferred": not bool(ev),
            }
            if ev:
                row.update({"quote": (ev.get("quote") or "")[:140],
                            "url": ev.get("url"), "basis": "公开报道"})
            links.append(row)
    return _ensure_business_coverage(links, ids["business"], banks)


def _ensure_business_coverage(links: list[dict], ids: dict, banks: list[dict],
                              minimum: int = 40) -> list[dict]:
    """Attach structurally plausible banks to under-covered business hubs."""
    degree = Counter(link["target"] for link in links)
    linked_by = defaultdict(set)
    for link in links:
        linked_by[link["target"]].add(link["source"])
    by_province = defaultdict(list)
    for bank in banks:
        if bank["group"] in ("city", "rural"):
            by_province[bank.get("province") or ""].append(bank)
    for name, bid in ids.items():
        if degree[bid] >= minimum:
            continue
        provinces = [p for p, inds in config.PROVINCE_INDUSTRIES.items()
                     if any(t in inds for t in BUSINESS_FOCUS_RULE.get(name, []))]
        pool = [b for p in provinces for b in by_province[p]]
        pool += [b for b in banks if b["group"] in ("city", "rural")
                 and name in (b.get("business_tags") or [])]
        for bank in sorted(pool, key=lambda b: (-(b.get("asset") or 0), b["name"])):
            if bank["id"] in linked_by[bid]:
                continue
            links.append({
                "source": bank["id"], "target": bid, "tag": name,
                "relation": "主要业务（结构推断）", "inferred": True,
                "basis": "按所在省份产业结构推断，待公开报道核验",
            })
            linked_by[bid].add(bank["id"])
    return links


def _focus_links(banks: list[dict], ids: dict) -> list[dict]:
    links = []
    for bank in banks:
        for entry in bank.get("business_focus") or []:
            fid = ids["focus"].get(entry.get("tag"))
            if not fid:
                continue
            confirmed = entry.get("basis") == "公开报道"
            links.append({
                "source": bank["id"], "target": fid, "tag": entry.get("tag"),
                "relation": "重点产业（公开报道）" if confirmed else "重点产业（区域推断）",
                "inferred": not confirmed,
                "basis": entry.get("basis"),
                "quote": (entry.get("quote") or "")[:140] or None,
                "url": entry.get("url") or None,
            })
    return _ensure_focus_coverage(links, ids["focus"], banks)


def _ensure_focus_coverage(links: list[dict], ids: dict, banks: list[dict],
                           minimum: int = 8) -> list[dict]:
    """Fill under-covered focus hubs from province industry structure."""
    degree = Counter(link["target"] for link in links)
    linked_by = defaultdict(set)
    for link in links:
        linked_by[link["target"]].add(link["source"])
    by_province = defaultdict(list)
    for bank in banks:
        if bank["group"] in ("city", "rural"):
            by_province[bank.get("province") or ""].append(bank)
    for name, fid in ids.items():
        if degree[fid] >= minimum:
            continue
        provinces = [p for p, inds in config.PROVINCE_INDUSTRIES.items() if name in inds]
        pool = [b for p in provinces for b in by_province[p]]
        pool += [b for b in banks if b["group"] in ("city", "rural")
                 and any(f.get("tag") == name for f in b.get("business_focus") or [])]
        for bank in sorted(pool, key=lambda b: (-(b.get("asset") or 0), b["name"])):
            if bank["id"] in linked_by[fid]:
                continue
            links.append({
                "source": bank["id"], "target": fid, "tag": name,
                "relation": "重点产业（区域推断）", "inferred": True,
                "basis": "按所在省份产业结构推断（待核验）",
            })
            linked_by[fid].add(bank["id"])
    return links


def _huawei_links(banks: list[dict]) -> list[dict]:
    scores = {
        bank["id"]: huawei.score_bank(bank, bank.get("news_evidence"), top=None)
        for bank in banks
    }
    by_cap = {bid: {o["capability_id"]: o for o in rows} for bid, rows in scores.items()}
    links: dict[tuple, dict] = {}

    def add(bank: dict, opp: dict, coverage: bool = False) -> None:
        key = (bank["id"], opp["capability_id"])
        if key in links:
            return
        links[key] = {
            "source": bank["id"], "target": "cap_" + opp["capability_id"],
            "relation": "华为机会", "score": opp["score"], "band": opp["band"],
            "confidence": opp["confidence"], "rationale": opp["rationale"],
            "entry": opp["entry"], "capability": opp["capability"],
            "coverage": coverage,
        }

    for bank in banks:
        for opp in scores[bank["id"]][:4]:
            add(bank, opp)
    for bank in banks:
        for ev in bank.get("news_evidence") or []:
            opp = by_cap[bank["id"]].get(ev.get("capability_id"))
            if opp:
                add(bank, opp)
    for cap_id in huawei.CAPS:
        ranked = sorted(
            banks,
            key=lambda b: (
                1 if any(e.get("capability_id") == cap_id
                         for e in (b.get("news_evidence") or [])) else 0,
                by_cap[b["id"]][cap_id]["score"],
                b.get("asset") or 0,
            ),
            reverse=True,
        )
        added = 0
        for bank in ranked:
            if added >= 16:
                break
            opp = by_cap[bank["id"]][cap_id]
            if opp["score"] < 38:
                continue
            add(bank, opp, coverage=True)
            added += 1
    return list(links.values())


def _similar_links(banks: list[dict]) -> list[dict]:
    """Gaussian similarity over shared public financial fields; no causal claim."""
    feats = ("asset", "revenue", "deposits", "loans", "employees")
    rows = [bank for bank in banks
            if bank.get("asset") and bank.get("revenue")
            and sum(1 for f in feats if bank.get(f)) >= 2]
    if len(rows) < 6:
        return []

    def similarity(a: dict, b: dict) -> tuple[float, list[str]]:
        shared = [f for f in feats if a.get(f) and b.get(f)]
        if len(shared) < 2:
            return 0.0, shared
        sq = [(math.log(float(a[f]) / float(b[f]))) ** 2 for f in shared]
        return math.exp(-0.5 * sum(sq) / len(sq)), shared

    neighbours = defaultdict(list)
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            b1, b2 = rows[i], rows[j]
            a1, a2 = b1.get("asset") or 0, b2.get("asset") or 0
            if a1 and a2 and not (0.05 <= a1 / a2 <= 20):
                continue
            sim, shared = similarity(b1, b2)
            if sim < 0.93:
                continue
            row = {
                "source": b1["id"], "target": b2["id"],
                "relation": "财务结构相似", "similarity": round(sim, 3),
                "fields": sorted(shared),
                "basis": "公开财务数据（规模、营收、存贷款、员工）的近似度",
            }
            neighbours[b1["id"]].append((sim, b2["id"], row))
            neighbours[b2["id"]].append((sim, b1["id"], row))

    selected = {}
    for bank_id, items in neighbours.items():
        for sim, other, row in sorted(items, reverse=True)[:3]:
            key = tuple(sorted((bank_id, other)))
            if key not in selected:
                selected[key] = row
    return list(selected.values())


def _attach_similar(banks: list[dict], links: list[dict]) -> None:
    by_id = {bank["id"]: bank for bank in banks}
    for link in links:
        a, b = by_id.get(link["source"]), by_id.get(link["target"])
        if not a or not b:
            continue
        a.setdefault("similar_banks", []).append({
            "id": b["id"], "name": b["short_name"],
            "similarity": link["similarity"], "fields": link["fields"],
        })
        b.setdefault("similar_banks", []).append({
            "id": a["id"], "name": a["short_name"],
            "similarity": link["similarity"], "fields": link["fields"],
        })
    for bank in banks:
        if bank.get("similar_banks"):
            bank["similar_banks"].sort(key=lambda x: -x["similarity"])
            bank["similar_banks"] = bank["similar_banks"][:3]


def _write_edges(banks: list[dict], ids: dict, similar: list[dict] | None = None) -> dict:
    it_links = []
    digital_links = []
    for bank in banks:
        if bank.get("it_spend"):
            if bank["it_spend"] >= 5:
                tid = "it_high"
            elif bank["it_spend"] >= 1:
                tid = "it_mid"
            else:
                tid = "it_low"
        else:
            tid = "it_unknown"
        it_links.append({"source": bank["id"], "target": tid, "relation": "IT投入"})
        digital_links.append({
            "source": bank["id"],
            "target": {"高": "dig_high", "中": "dig_mid", "低": "dig_low"}.get(
                bank.get("digital_tier") or "", "dig_unknown"),
            "relation": "数字化活跃度",
        })

    bank_ids = {b["id"] for b in banks}
    governance_links = []
    seen_links = set()
    for bank in banks:
        for share in bank.get("governance_shares") or []:
            target = share.get("bank_id")
            if not target or target == bank["id"] or target not in bank_ids:
                continue
            key = tuple(sorted((bank["id"], target))) + (share.get("name") or "",)
            if key in seen_links:
                continue
            seen_links.add(key)
            governance_links.append({
                "source": bank["id"], "target": target,
                "relation": "曾共享董监高",
                "person": share.get("name"),
                "role": share.get("role"),
                "period": share.get("period"),
                "confidence": share.get("confidence"),
                "basis": share.get("basis"),
                "quote": share.get("quote"),
            })

    out = {
        "business": _business_links(banks, ids),
        "focus": _focus_links(banks, ids),
        "it": it_links + digital_links,
        "huawei": _huawei_links(banks),
        "governance": governance_links,
        "similar": similar if similar is not None else _similar_links(banks),
    }
    for name, links in out.items():
        (config.FRONTEND_DATA / "edges" / f"{name}.json").write_text(
            json.dumps({"links": links}, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    return {k: len(v) for k, v in out.items()}


def build() -> dict:
    payload = registry.load_registry()
    banks = payload["banks"]
    _apply_seed(banks)
    merged = _merge_optional(banks)
    _prepare(banks)

    lay = layout.build_layout(banks)
    nodes, ids = _graph_nodes(banks, lay["banks"], lay["hubs"])
    region_links = []
    for bank in banks:
        target = ids["province"].get(bank.get("province") or "其他")
        if target:
            region_links.append({
                "source": bank["id"], "target": target,
                "relation": "区域归属", "type": "region",
            })
    graph = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "width": layout.W, "height": layout.H,
        "nodes": nodes, "links": region_links,
    }
    config.GRAPH_PATH.write_text(
        json.dumps(graph, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    similar = _similar_links(banks)
    _attach_similar(banks, similar)
    _write_details(banks)
    edge_counts = _write_edges(banks, ids, similar)

    search = [{
        "id": b["id"], "name": b["short_name"], "full": b["name"],
        "short": b["short_name"], "province": b["province"], "city": b["city"],
        "type": b["type_label"], "group": b["group"], "listed": b.get("listed"),
        "asset": b.get("asset"), "alias": [b["name"], b.get("en_name") or ""],
        "shard": b["shard"],
    } for b in banks]
    (config.FRONTEND_DATA / "search.json").write_text(
        json.dumps(search, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )

    by_group = Counter(b["group"] for b in banks)
    by_type = Counter(b["type"] for b in banks)
    by_province = Counter(b.get("province") or "其他" for b in banks)
    by_asset = Counter(b["asset_tier"] for b in banks)
    facets = {
        "group": dict(by_group), "type": dict(by_type),
        "province": dict(by_province), "asset_tier": dict(by_asset),
        "listed": dict(Counter(b.get("listed") or "未上市" for b in banks)),
        "penalty": {
            "有处罚记录": sum(1 for b in banks if b.get("penalty_count")),
            "未采集": sum(1 for b in banks if not b.get("penalty_checked")),
        },
    }
    (config.FRONTEND_DATA / "facets.json").write_text(
        json.dumps(facets, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )

    coverage = {
        "with_asset": sum(1 for b in banks if b.get("asset")),
        "with_asset_tier": sum(1 for b in banks if b.get("asset_tier") != "未披露"),
        "with_revenue": sum(1 for b in banks if b.get("revenue")),
        "with_profit": sum(1 for b in banks if b.get("profit") is not None),
        "with_employees": sum(1 for b in banks if b.get("employees")),
        "listed": sum(1 for b in banks if b.get("listed")),
        "with_news": sum(1 for b in banks if b.get("news")),
        "with_penalties": sum(1 for b in banks if b.get("penalty_checked")),
        "with_governance": sum(1 for b in banks if b.get("governance")),
        "with_opportunities": sum(1 for b in banks if b.get("opportunities")),
        "with_focus": sum(1 for b in banks if b.get("business_focus")),
        "with_focus_confirmed": sum(1 for b in banks if b.get("focus_confirmed")),
        "with_website": sum(1 for b in banks if b.get("website")),
        "with_site_profile": sum(1 for b in banks if b.get("site_profile")),
    }
    manifest = {
        "version": 1,
        "generated_at": graph["generated_at"],
        "registry_as_of": payload.get("as_of"),
        "registry_source": payload.get("source"),
        "total": len(banks),
        "counts": {"group": dict(by_group), "type": dict(by_type), "province": dict(by_province)},
        "coverage": coverage,
        "agent_stats": merged,
        "edges": edge_counts,
        "files": {
            "graph": "graph.json", "search": "search.json", "facets": "facets.json",
            "edges": ["business", "focus", "it", "huawei", "governance", "similar"],
        },
        "disclaimer": (
            "本页仅整理公开信息，缺失字段标注为“未披露”，不构成投资建议。"
            "数据基准日以页面标注为准，来源链接见各银行详情。"
        ),
    }
    config.MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return manifest


def validate() -> dict:
    graph = json.loads(config.GRAPH_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(config.MANIFEST_PATH.read_text(encoding="utf-8"))
    ids = [n["id"] for n in graph["nodes"]]
    dup = [k for k, v in Counter(ids).items() if v > 1]
    dangling = [l for l in graph["links"] if l["source"] not in set(ids) or l["target"] not in set(ids)]
    missing_details = []
    for n in graph["nodes"]:
        if n["type"] != "bank":
            continue
        p = config.FRONTEND_DATA / "banks" / n["shard"] / f"{n['id']}.json"
        if not p.exists():
            missing_details.append(n["id"])
    return {
        "nodes": len(ids), "links": len(graph["links"]),
        "duplicate_ids": dup[:5], "dangling_links": len(dangling),
        "missing_details": len(missing_details),
        "graph_bytes": config.GRAPH_PATH.stat().st_size,
        "manifest_total": manifest["total"],
    }


if __name__ == "__main__":
    m = build()
    print(json.dumps(m["counts"], ensure_ascii=False, indent=1))
    print(json.dumps(m["coverage"], ensure_ascii=False, indent=1))
    print(json.dumps(m["edges"], ensure_ascii=False, indent=1))
    print(json.dumps(validate(), ensure_ascii=False, indent=1))
