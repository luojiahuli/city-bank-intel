"""Explainable Huawei capability matching.

Rules produce a score; public evidence can raise the confidence and the cap.
The LLM never invents a score or a fact here.
"""
from __future__ import annotations

import config

BASE = {
    "city": {
        "hw_core": 52, "hw_data": 54, "hw_agent": 50, "hw_token": 46,
        "hw_agentic_data": 46, "hw_aicc": 52, "hw_credit": 52, "hw_fraud": 55,
        "hw_cloud": 50, "hw_ascend": 44, "hw_network": 40, "hw_xinchuang": 50,
        "hw_mobile": 48, "hw_green": 34,
    },
    "rural": {
        "hw_core": 48, "hw_data": 52, "hw_agent": 44, "hw_token": 34,
        "hw_agentic_data": 38, "hw_aicc": 46, "hw_credit": 56, "hw_fraud": 60,
        "hw_cloud": 54, "hw_ascend": 34, "hw_network": 34, "hw_xinchuang": 46,
        "hw_mobile": 42, "hw_green": 28,
    },
    "village": {
        "hw_core": 28, "hw_data": 40, "hw_agent": 30, "hw_token": 24,
        "hw_agentic_data": 28, "hw_aicc": 34, "hw_credit": 46, "hw_fraud": 50,
        "hw_cloud": 44, "hw_ascend": 24, "hw_network": 24, "hw_xinchuang": 34,
        "hw_mobile": 34, "hw_green": 22,
    },
    "other": {
        "hw_core": 55, "hw_data": 56, "hw_agent": 52, "hw_token": 48,
        "hw_agentic_data": 48, "hw_aicc": 52, "hw_credit": 52, "hw_fraud": 54,
        "hw_cloud": 52, "hw_ascend": 46, "hw_network": 44, "hw_xinchuang": 52,
        "hw_mobile": 50, "hw_green": 36,
    },
}

CAPS = {c["id"]: c for c in config.HUAWEI_CAPABILITIES}


def _asset_bonus(bank: dict) -> tuple[int, str | None]:
    asset = bank.get("asset")
    if asset:
        if asset >= 10000:
            return 8, "资产规模超万亿"
        if asset >= 5000:
            return 6, "资产规模 5000 亿以上"
        if asset >= 1000:
            return 4, "资产规模 1000 亿以上"
        if asset >= 300:
            return 2, "资产规模 300 亿以上"
        return 0, None
    tier = bank.get("asset_tier")
    if tier == "5000-10000":
        return 4, "资产规模 5000 亿以上"
    if tier == "1000-5000":
        return 2, "资产规模 1000 亿以上"
    return 0, None


def score_bank(bank: dict, evidence: list[dict] | None = None) -> list[dict]:
    group = bank.get("group") or "other"
    base = BASE.get(group, BASE["other"])
    asset_bonus, asset_note = _asset_bonus(bank)
    listed_bonus = 4 if bank.get("listed") else 0
    digital = bank.get("digital_tier")
    digital_bonus = {"高": 8, "中": 4}.get(digital or "", 0)
    penalty = bank.get("penalty_count") or 0
    tags = set(bank.get("business_tags") or [])
    evidence = evidence or []

    out = []
    for cap_id, cap in CAPS.items():
        factors: list[str] = []
        score = base.get(cap_id, 40)
        if asset_note:
            score += asset_bonus
            factors.append(asset_note)
        if listed_bonus:
            score += listed_bonus
            factors.append("上市银行，预算与披露更透明")
        if digital_bonus:
            score += digital_bonus
            factors.append(f"数字化活跃度 {digital}")
        if penalty and cap_id in {"hw_fraud", "hw_credit"}:
            add = min(10, int(penalty * 1.5))
            score += add
            factors.append(f"近五年处罚 {penalty} 条，合规风控压力")
        if penalty and cap_id in {"hw_data", "hw_agentic_data"}:
            score += min(6, penalty)
            factors.append("监管数据报送/治理压力")
        if "国际业务" in tags and cap_id in {"hw_fraud", "hw_cloud"}:
            score += 4
            factors.append("有国际业务/跨境需求")
        if "零售金融" in tags and cap_id in {"hw_aicc", "hw_mobile"}:
            score += 3
            factors.append("零售客群与全渠道服务")
        if "财富管理" in tags and cap_id in {"hw_data", "hw_agent"}:
            score += 2
            factors.append("财富管理数据与智能投顾")
        if "普惠小微" in tags and cap_id in {"hw_credit"}:
            score += 3
            factors.append("普惠小微信贷场景")

        ev = [e for e in evidence if e.get("capability_id") == cap_id]
        if ev:
            score += min(15, 8 + len(ev) * 2)
            factors.append(f"公开报道证据 {len(ev)} 条")
            confidence = "公开证据"
        else:
            score = min(score, 55)
            confidence = "推断/待验证"

        score = max(0, min(100, round(score)))
        band = "高" if score >= 65 else "中" if score >= 50 else "观察"
        rationale = "；".join(factors) if factors else "基于银行类型与规模的通用匹配"
        out.append({
            "capability_id": cap_id,
            "capability": cap["name"],
            "short": cap["short"],
            "category": cap["category"],
            "entry": cap["entry"],
            "score": score,
            "band": band,
            "rationale": rationale,
            "confidence": confidence,
            "evidence": ev[:3],
        })

    out.sort(key=lambda x: (-x["score"], x["capability_id"]))
    return out[:4]


def top_score(opportunities: list[dict]) -> int:
    return opportunities[0]["score"] if opportunities else 0
