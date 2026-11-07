"""Explainable Huawei capability matching.

Rules produce a score; public evidence can raise the confidence and the cap.
The LLM never invents a score or a fact here.
"""
from __future__ import annotations

import config

CAPS = {c["id"]: c for c in config.HUAWEI_CAPABILITIES}

FOCUS_BONUS = {
    "绿色": {"hw_green": 9, "hw_data": 2},
    "科技": {"hw_agent": 5, "hw_token": 4, "hw_ascend": 5, "hw_data": 3},
    "制造": {"hw_credit": 3, "hw_core": 3},
    "农业": {"hw_credit": 5, "hw_mobile": 2},
    "跨境贸易": {"hw_cloud": 3, "hw_fraud": 3, "hw_mobile": 2},
    "小微普惠": {"hw_credit": 4},
    "消费零售": {"hw_aicc": 4, "hw_mobile": 4, "hw_data": 2},
    "医药健康": {"hw_data": 2},
    "文旅": {"hw_aicc": 2, "hw_mobile": 2},
    "能源化工": {"hw_green": 4, "hw_core": 2},
    "供应链": {"hw_data": 3, "hw_credit": 2},
    "建筑地产": {"hw_credit": 2},
}

DIGITAL_VALUE = {"高": 0.9, "中": 0.55, "低": 0.2}
GROUP_OFFSET = {"city": 0.04, "rural": 0.0, "village": -0.08, "other": 0.05}

# Institutions without a disclosed metric still differ by licence type, so the
# fallback percentile is group-specific instead of one flat number.
GROUP_RANK_DEFAULT = {
    "asset": {"city": 0.55, "rural": 0.28, "village": 0.12, "other": 0.42},
    "revenue": {"city": 0.55, "rural": 0.28, "village": 0.12, "other": 0.42},
    "deposits": {"city": 0.5, "rural": 0.3, "village": 0.14, "other": 0.42},
    "employees": {"city": 0.5, "rural": 0.3, "village": 0.15, "other": 0.4},
    "it_spend": {"city": 0.35, "rural": 0.2, "village": 0.1, "other": 0.3},
}

# Capability suitability by licence type turns the ranking into a real shortlist
# (rural → credit/mobile, city → core/data/AI) instead of one universal answer.
GROUP_AFFINITY = {
    "hw_core": {"city": 4, "rural": 1, "other": 2},
    "hw_data": {"city": 3, "rural": 1, "other": 1},
    "hw_agent": {"city": 3, "rural": 0, "other": 1},
    "hw_token": {"city": 2, "other": 1},
    "hw_agentic_data": {"city": 1, "other": 1},
    "hw_aicc": {"city": 1, "rural": 1, "village": 0},
    "hw_credit": {"city": 2, "rural": 5, "village": 4, "other": 2},
    "hw_fraud": {"city": 2, "rural": 1},
    "hw_cloud": {"city": 2, "other": 1},
    "hw_ascend": {"city": 2},
    "hw_network": {"city": 2, "rural": 0},
    "hw_xinchuang": {"city": 2, "rural": 1},
    "hw_mobile": {"city": 1, "rural": 3, "village": 3, "other": 1},
    "hw_green": {"rural": 1},
}

# Per-capability factor weights. They let two banks with the same asset size
# receive different scores based on disclosure, digital activity and business mix.
CAP_WEIGHTS = {
    "hw_core": {"scale": .35, "budget": .25, "digital": .15, "risk": .05, "fit": .20},
    "hw_data": {"scale": .20, "budget": .15, "digital": .30, "risk": .15, "fit": .20},
    "hw_agent": {"scale": .15, "budget": .20, "digital": .35, "risk": .05, "fit": .25},
    "hw_token": {"scale": .25, "budget": .30, "digital": .30, "risk": 0, "fit": .15},
    "hw_agentic_data": {"scale": .15, "budget": .15, "digital": .30, "risk": .10, "fit": .30},
    "hw_aicc": {"scale": .10, "budget": .10, "digital": .25, "risk": .05, "fit": .50},
    "hw_credit": {"scale": .15, "budget": .10, "digital": .15, "risk": .20, "fit": .40},
    "hw_fraud": {"scale": .20, "budget": .15, "digital": .20, "risk": .30, "fit": .15},
    "hw_cloud": {"scale": .30, "budget": .25, "digital": .15, "risk": .05, "fit": .25},
    "hw_ascend": {"scale": .25, "budget": .30, "digital": .35, "risk": 0, "fit": .10},
    "hw_network": {"scale": .35, "budget": .30, "digital": .15, "risk": .05, "fit": .15},
    "hw_xinchuang": {"scale": .30, "budget": .30, "digital": .20, "risk": .05, "fit": .15},
    "hw_mobile": {"scale": .10, "budget": .10, "digital": .25, "risk": .05, "fit": .50},
    "hw_green": {"scale": .20, "budget": .20, "digital": .10, "risk": 0, "fit": .50},
}

CAP_FIT = {
    "hw_core": {"business": ["公司金融", "科技金融"], "focus": ["制造", "科技"]},
    "hw_data": {"business": ["公司金融", "零售金融", "财富管理"],
                "focus": ["科技", "供应链"]},
    "hw_agent": {"business": ["财富管理", "零售金融", "科技金融"],
                 "focus": ["科技", "消费零售"]},
    "hw_token": {"business": ["零售金融", "财富管理"], "focus": ["科技"]},
    "hw_agentic_data": {"business": ["财富管理", "公司金融"],
                        "focus": ["科技", "医药健康"]},
    "hw_aicc": {"business": ["零售金融", "财富管理"],
                "focus": ["消费零售", "文旅"]},
    "hw_credit": {"business": ["普惠小微"],
                  "focus": ["农业", "制造", "小微普惠"]},
    "hw_fraud": {"business": ["零售金融", "国际业务"],
                 "focus": ["跨境贸易", "消费零售"]},
    "hw_cloud": {"business": ["国际业务", "公司金融"], "focus": ["科技", "跨境贸易"]},
    "hw_ascend": {"business": ["科技金融"], "focus": ["科技"]},
    "hw_network": {"business": ["公司金融", "金融市场"], "focus": ["制造", "能源化工"]},
    "hw_xinchuang": {"business": ["公司金融", "科技金融"], "focus": ["制造", "科技"]},
    "hw_mobile": {"business": ["零售金融", "财富管理"], "focus": ["消费零售", "文旅"]},
    "hw_green": {"business": ["绿色金融"], "focus": ["绿色", "能源化工"]},
}


def score_bank(bank: dict, evidence: list[dict] | None = None,
               top: int | None = 4) -> list[dict]:
    group = bank.get("group") or "other"
    ranks = bank.get("ranks") or {}
    tags = set(bank.get("business_tags") or [])
    evidence = evidence or []
    focus_tags = {f.get("tag") for f in bank.get("business_focus") or []
                  if f.get("basis") == "公开报道"}
    region_tags = set(bank.get("region_industries") or [])
    profit = bank.get("profit")

    def rank(key: str, default: float | None = None) -> float:
        if default is None:
            default = GROUP_RANK_DEFAULT.get(key, {}).get(group, 0.25)
        return float(ranks.get(key, default))

    scale = (0.50 * rank("asset") + 0.25 * rank("revenue")
             + 0.15 * rank("deposits") + 0.10 * rank("employees"))
    budget = 0.60 * rank("it_spend", 0.2) + 0.40 * (1.0 if bank.get("listed") else 0.0)
    digital = (0.65 * DIGITAL_VALUE.get(bank.get("digital_tier") or "", 0.15)
               + 0.35 * rank("news_count", 0.0))
    risk = (0.75 * rank("penalty_count", 0.0)
            + 0.25 * (1.0 if profit is not None and profit < 0 else 0.0))

    out = []
    for cap_id, cap in CAPS.items():
        factors: list[str] = []
        weights = CAP_WEIGHTS[cap_id]
        fit_def = CAP_FIT.get(cap_id, {})
        biz_hit = any(t in tags for t in fit_def.get("business", []))
        focus_hit = any(t in focus_tags for t in fit_def.get("focus", []))
        region_matches = sum(1 for t in fit_def.get("focus", []) if t in region_tags)
        region_hit = region_matches > 0
        fit = min(1.0, (0.45 if biz_hit else 0.0) + (0.6 if focus_hit else 0.0)
                  + min(0.5, 0.25 * region_matches))
        weighted = (weights["scale"] * scale + weights["budget"] * budget
                    + weights["digital"] * digital + weights["risk"] * risk
                    + weights["fit"] * fit)

        focus_add = 0
        for tag in focus_tags:
            focus_add += FOCUS_BONUS.get(tag, {}).get(cap_id, 0)
        affinity = GROUP_AFFINITY.get(cap_id, {}).get(group, 0)
        score = (18.0 + 44.0 * weighted + 6.0 * GROUP_OFFSET.get(group, 0.0)
                 + min(12, focus_add) * 0.35 + affinity)

        if scale >= 0.75:
            factors.append(f"规模因子处于样本前{max(5, round((1 - scale) * 100))}%")
        elif scale >= 0.45:
            factors.append(f"规模因子处于样本中位以上（{round(scale * 100)}分位）")
        if budget >= 0.55:
            factors.append("科技投入/上市披露更充分")
        if digital >= 0.55:
            factors.append(f"数字化信号 {round(digital * 100)}分")
        if weights["risk"] and risk >= 0.6:
            factors.append("处罚与合规压力较高")
        if focus_hit:
            factors.append(f"业务场景匹配（{'、'.join(sorted(focus_tags))}）")
        elif region_hit:
            factors.append("区域产业匹配（省级结构推断）")
        elif biz_hit and fit >= 0.6:
            factors.append("业务结构匹配")
        if affinity >= 3:
            factors.append("机构类型适配度高")

        ev = [e for e in evidence if e.get("capability_id") == cap_id]
        if ev:
            score += min(14, 6 + len(ev) * 2)
            factors.append(f"公开报道证据 {len(ev)} 条")
            confidence = "公开证据"
        else:
            confidence = "推断/待验证"

        score = max(0, min(95, round(score)))
        band = "高" if score >= 65 else "中" if score >= 50 else "观察"
        rationale = "；".join(factors) or "基于公开披露与业务结构的综合匹配"
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
    return out[:top] if top else out


def top_score(opportunities: list[dict]) -> int:
    return opportunities[0]["score"] if opportunities else 0
