"""Build the city-bank insight conclusions shown in the frontend modal.

The site is a static bundle: every number rendered in the UI comes from a file
under ``frontend/data``. This module reads the same public-data products the
graph is built from and emits ``frontend/data/insight.json`` so the conclusion
panel never hardcodes a statistic.

Rules of the house:
- only public disclosure or rule-computed values are used;
- a missing field stays missing and is reported as a coverage number;
- percentages are computed against the city-bank sample and stated as such.
"""
from __future__ import annotations

import collections
import glob
import json
import math
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend" / "data"

# 城商行样本 = 机构名录中 group == "city" 的法人机构。
GROUP = "city"

# 大区划分用于第 3 问「不同区域切入点」，省份 -> 大区。
REGION_OF = {
    "上海市": "华东", "江苏省": "华东", "浙江省": "华东", "安徽省": "华东",
    "江西省": "华东", "福建省": "华东", "山东省": "华东",
    "广东省": "华南", "广西壮族自治区": "华南", "海南省": "华南",
    "北京市": "华北", "天津市": "华北", "河北省": "华北", "山西省": "华北",
    "内蒙古自治区": "华北",
    "河南省": "华中", "湖北省": "华中", "湖南省": "华中",
    "重庆市": "西南", "四川省": "西南", "贵州省": "西南", "云南省": "西南",
    "西藏自治区": "西南",
    "陕西省": "西北", "甘肃省": "西北", "青海省": "西北",
    "宁夏回族自治区": "西北", "新疆维吾尔自治区": "西北",
    "辽宁省": "东北", "吉林省": "东北", "黑龙江省": "东北",
}

# 痛点阈值：取监管与行业常用口径，均可回溯到公开指标定义。
NPL_STRESS = 2.0        # 不良贷款率 ≥ 2%：资产质量承压
PROVISION_THIN = 150.0  # 拨备覆盖率 < 150%：风险缓冲不足
NIM_THIN = 1.5          # 净息差 < 1.5%：盈利空间收窄
CAPITAL_THIN = 10.5     # 资本充足率 < 10.5%：扩表受限


def percentile(values, p):
    """Linear-interpolated percentile; ignores None."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return vals[int(k)]
    return vals[lo] * (hi - k) + vals[hi] * (k - lo)


def mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def r2(value, digits=2):
    return None if value is None else round(value, digits)


def load_banks():
    banks = []
    for path in glob.glob(str(FRONTEND / "banks" / "*" / "*.json")):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("group") == GROUP:
            banks.append(data)
    return banks


def load_ratios(banks):
    """Attach disclosure-backed risk ratios from the spreadsheet import."""
    raw = json.load(open(ROOT / "data" / "enrichment" / "financials_xlsx.json",
                         encoding="utf-8"))
    for bank in banks:
        entry = raw.get(bank["id"], {})
        bank["_npl"] = entry.get("npl_ratio")
        bank["_cap"] = entry.get("capital_adequacy")
        bank["_nim"] = entry.get("nim")
        bank["_prov"] = entry.get("provision_coverage")
    return banks


def cap_short(bank):
    """capability ids in the bank's top-4 shortlist."""
    return {o["capability_id"] for o in (bank.get("opportunities") or [])}


def top_score(bank):
    scores = [o.get("score", 0) for o in (bank.get("opportunities") or [])]
    return max(scores) if scores else 0


def metric_block(banks, key, label, unit, digits=2):
    vals = [b[key] for b in banks if b.get(key) is not None]
    return {
        "label": label,
        "unit": unit,
        "n": len(vals),
        "coverage": round(len(vals) / len(banks) * 100, 1) if banks else 0,
        "median": r2(percentile(vals, 0.5), digits),
        "mean": r2(mean(vals), digits),
        "p10": r2(percentile(vals, 0.1), digits),
        "p90": r2(percentile(vals, 0.9), digits),
        "min": r2(min(vals), digits) if vals else None,
        "max": r2(max(vals), digits) if vals else None,
    }


def build_opportunity(banks, peers):
    """Q1 — business mix and pain points of city commercial banks."""
    n = len(banks)

    # 业务条线分布
    tag_count = collections.Counter()
    for bank in banks:
        for tag in bank.get("business_tags") or []:
            tag_count[tag] += 1
    business = [
        {"name": tag, "count": cnt, "share": round(cnt / n * 100, 1)}
        for tag, cnt in tag_count.most_common()
    ]

    # 区域产业结构推断分布
    ind_count = collections.Counter()
    for bank in banks:
        for tag in bank.get("region_industries") or []:
            ind_count[tag] += 1
    industries = [
        {"name": tag, "count": cnt, "share": round(cnt / n * 100, 1)}
        for tag, cnt in ind_count.most_common()
    ]

    # 规模分布
    assets = [b["asset"] for b in banks if b.get("asset")]
    tiers = collections.Counter(b.get("asset_tier") or "未披露" for b in banks)
    tier_order = ["万亿以上", "5000-10000", "1000-5000", "300-1000",
                  "300以下", "未披露"]
    size_tiers = [
        {"name": t, "count": tiers.get(t, 0),
         "share": round(tiers.get(t, 0) / n * 100, 1)}
        for t in tier_order if tiers.get(t, 0)
    ]

    # 痛点分组：每组给出家数、占比与组内平均机会分
    def cohort(pred):
        return [b for b in banks if pred(b)]

    cohorts = [
        {"key": "npl", "name": "资产质量承压（不良率 ≥ 2%）",
         "desc": "拨备计提压力直接吞噬利润，是数字化风控投入的直接触发点",
         "banks": cohort(lambda b: b["_npl"] is not None and b["_npl"] >= NPL_STRESS)},
        {"key": "prov", "name": "拨备缓冲薄（拨备覆盖率 < 150%）",
         "desc": "风险抵补余量不足，需要更高频、更精准的资产重分类与预警",
         "banks": cohort(lambda b: b["_prov"] is not None and b["_prov"] < PROVISION_THIN)},
        {"key": "nim", "name": "息差收窄（净息差 < 1.5%）",
         "desc": "利差空间压缩，倒逼低成本获客与自动化运营",
         "banks": cohort(lambda b: b["_nim"] is not None and b["_nim"] < NIM_THIN)},
        {"key": "cap", "name": "资本偏紧（资本充足率 < 10.5%）",
         "desc": "扩表受限，需在既有资本下提高单位资本产出",
         "banks": cohort(lambda b: b["_cap"] is not None and b["_cap"] < CAPITAL_THIN)},
        {"key": "loss", "name": "当期亏损",
         "desc": "盈利已破位，任何降本增效项目都必须有可量化的 ROI",
         "banks": cohort(lambda b: b.get("profit_status") == "亏损")},
        {"key": "penalty", "name": "监管处罚 ≥ 2 次",
         "desc": "合规与反洗钱能力短板暴露在外，罚单是刚性预算的理由",
         "banks": cohort(lambda b: (b.get("penalty_count") or 0) >= 2)},
    ]
    pain = []
    for c in cohorts:
        bs = c["banks"]
        c["count"] = len(bs)
        c["share"] = round(len(bs) / n * 100, 1)
        c["avg_score"] = r2(mean([top_score(b) for b in bs]), 1) if bs else None
        c["provinces"] = collections.Counter(
            b.get("province") for b in bs).most_common(4)
        del c["banks"]
        pain.append(c)

    # 与全体中小银行的横向对比
    comparison = []
    for key, label, unit in [("_npl", "不良贷款率", "%"),
                             ("_prov", "拨备覆盖率", "%"),
                             ("_nim", "净息差", "%"),
                             ("_cap", "资本充足率", "%")]:
        city_vals = [b[key] for b in banks if b.get(key) is not None]
        peer_vals = [b[key] for b in peers if b.get(key) is not None]
        cm, pm = percentile(city_vals, 0.5), percentile(peer_vals, 0.5)
        comparison.append({
            "label": label, "unit": unit,
            "city_median": r2(cm), "peer_median": r2(pm),
            "gap": r2(cm - pm) if cm is not None and pm is not None else None,
            "city_n": len(city_vals), "peer_n": len(peer_vals),
        })

    return {
        "sample": {
            "count": n,
            "asset_disclosed": len(assets),
            "asset_total": r2(sum(assets), 0),
            "asset_median": r2(percentile(assets, 0.5), 0),
            "asset_p90": r2(percentile(assets, 0.9), 0),
            "listed": sum(1 for b in banks if b.get("listed")),
            "profitable": sum(1 for b in banks if b.get("profit_status") == "盈利"),
            "loss_making": sum(1 for b in banks if b.get("profit_status") == "亏损"),
            "business_disclosed": sum(1 for b in banks if not b.get("business_inferred")),
        },
        "business": business,
        "industries": industries,
        "size_tiers": size_tiers,
        "metrics": [
            metric_block(banks, "_npl", "不良贷款率", "%"),
            metric_block(banks, "_prov", "拨备覆盖率", "%", 0),
            metric_block(banks, "_nim", "净息差", "%"),
            metric_block(banks, "_cap", "资本充足率", "%"),
        ],
        "pain_cohorts": pain,
        "comparison": comparison,
        "penalties": {
            "banks_with_penalty": sum(1 for b in banks if (b.get("penalty_count") or 0) > 0),
            "total_records": sum(b.get("penalty_count") or 0 for b in banks),
            "multi_penalty": sum(1 for b in banks if (b.get("penalty_count") or 0) >= 2),
        },
    }


def build_huawei(banks):
    """Q2 — Huawei capability opportunities for the city-bank sample."""
    n = len(banks)
    caps = {c["id"]: c for c in config.HUAWEI_CAPABILITIES}

    # 能力覆盖率 + 组内平均分
    coverage = []
    for cid, meta in caps.items():
        hit = [b for b in banks if cid in cap_short(b)]
        if not hit:
            coverage.append({
                "id": cid, "name": meta["short"], "full_name": meta["name"],
                "category": meta["category"], "entry": meta["entry"],
                "count": 0, "share": 0.0, "avg_score": None,
                "avg_score_all": None, "lift": None,
            })
            continue
        hit_scores = [top_score(b) for b in hit]
        all_scores = [top_score(b) for b in banks]
        base = mean(all_scores)
        hit_mean = mean(hit_scores)
        coverage.append({
            "id": cid, "name": meta["short"], "full_name": meta["name"],
            "category": meta["category"], "entry": meta["entry"],
            "desc": meta.get("desc", ""),
            "count": len(hit), "share": round(len(hit) / n * 100, 1),
            "avg_score": r2(hit_mean, 1),
            "avg_score_all": r2(base, 1),
            "lift": r2(hit_mean - base, 1) if base is not None else None,
        })
    coverage.sort(key=lambda c: (-c["count"], c["name"]))

    # 痛点 × 能力 命中矩阵：哪类痛点的行更可能出现某能力
    cohorts = [
        ("npl", "不良率 ≥ 2%", lambda b: b["_npl"] is not None and b["_npl"] >= NPL_STRESS),
        ("prov", "拨备 < 150%", lambda b: b["_prov"] is not None and b["_prov"] < PROVISION_THIN),
        ("nim", "息差 < 1.5%", lambda b: b["_nim"] is not None and b["_nim"] < NIM_THIN),
        ("penalty", "处罚 ≥ 2 次", lambda b: (b.get("penalty_count") or 0) >= 2),
        ("loss", "当期亏损", lambda b: b.get("profit_status") == "亏损"),
    ]
    matrix = []
    for key, label, pred in cohorts:
        bs = [b for b in banks if pred(b)]
        if not bs:
            continue
        row = {"key": key, "label": label, "count": len(bs),
               "avg_score": r2(mean([top_score(b) for b in bs]), 1), "hits": {}}
        for cid in caps:
            hit = sum(1 for b in bs if cid in cap_short(b))
            row["hits"][cid] = round(hit / len(bs) * 100, 1)
        matrix.append(row)

    # 规模 / 上市维度的机会分差：量化预算能力
    listed = [b for b in banks if b.get("listed")]
    unlisted = [b for b in banks if not b.get("listed")]
    big = [b for b in banks if (b.get("asset") or 0) >= 5000]
    small = [b for b in banks if (b.get("asset") or 0) < 5000]
    digital = collections.Counter(b.get("digital_tier") for b in banks)

    def segment(name, bs):
        scores = [top_score(b) for b in bs]
        return {
            "name": name, "count": len(bs),
            "avg_score": r2(mean(scores), 1),
            "median_score": r2(percentile(scores, 0.5), 1),
            "ge50": sum(1 for s in scores if s >= 50),
            "ge60": sum(1 for s in scores if s >= 60),
            "share_ge50": round(sum(1 for s in scores if s >= 50) / len(bs) * 100, 1) if bs else 0,
        }

    segments = [
        segment("上市城商行", listed),
        segment("非上市城商行", unlisted),
        segment("资产 ≥ 5000 亿", big),
        segment("资产 < 5000 亿", small),
        segment("数字化活跃度高", [b for b in banks if b.get("digital_tier") == "高"]),
        segment("数字化活跃度中", [b for b in banks if b.get("digital_tier") == "中"]),
        segment("数字化未披露", [b for b in banks if not b.get("digital_tier")]),
    ]

    # 机会分最高的城商行，作为验证样本
    ranked = sorted(banks, key=lambda b: -top_score(b))[:12]
    leaders = [{
        "name": b["name"], "province": b.get("province"),
        "asset": b.get("asset"), "score": top_score(b),
        "top_capability": b.get("top_capability"),
        "business": b.get("business_tags") or [],
        "listed": bool(b.get("listed")),
    } for b in ranked]

    return {
        "capabilities": coverage,
        "matrix": matrix,
        "segments": segments,
        "leaders": leaders,
        "digital_tier": {k or "未披露": v for k, v in digital.items()},
        "scoring_note": "机会分 = 规则分（五因子加权 + 产业/类型适配 + 公开证据加分），"
                        "口径见 docs/scoring-rules.md；无公开证据时标注「推断/待验证」。",
    }


def build_regions(banks):
    """Q3 — regional entry points."""
    by_region = collections.defaultdict(list)
    for bank in banks:
        by_region[REGION_OF.get(bank.get("province"), "其他")].append(bank)

    regions = []
    for region, bs in by_region.items():
        assets = [b["asset"] for b in bs if b.get("asset")]
        npl = [b["_npl"] for b in bs if b.get("_npl") is not None]
        cap = [b["_cap"] for b in bs if b.get("_cap") is not None]
        nim = [b["_nim"] for b in bs if b.get("_nim") is not None]
        prov = [b["_prov"] for b in bs if b.get("_prov") is not None]
        scores = [top_score(b) for b in bs]
        inds = collections.Counter()
        for b in bs:
            for tag in b.get("region_industries") or []:
                inds[tag] += 1
        top_caps = collections.Counter()
        for b in bs:
            if b.get("top_capability"):
                top_caps[b["top_capability"]] += 1
        regions.append({
            "name": region,
            "count": len(bs),
            "provinces": sorted({b.get("province") for b in bs if b.get("province")}),
            "asset_total": r2(sum(assets), 0),
            "asset_median": r2(percentile(assets, 0.5), 0),
            "npl_median": r2(percentile(npl, 0.5)),
            "npl_above_2": sum(1 for v in npl if v >= 2.0),
            "prov_median": r2(percentile(prov, 0.5), 0),
            "prov_below_150": sum(1 for v in prov if v < PROVISION_THIN),
            "nim_median": r2(percentile(nim, 0.5)),
            "cap_median": r2(percentile(cap, 0.5)),
            "avg_score": r2(mean(scores), 1),
            "penalty_total": sum(b.get("penalty_count") or 0 for b in bs),
            "loss": sum(1 for b in bs if b.get("profit_status") == "亏损"),
            "industries": [{"name": k, "count": v} for k, v in inds.most_common(4)],
            "top_capabilities": [{"name": k, "count": v} for k, v in top_caps.most_common(3)],
        })
    regions.sort(key=lambda r: -r["asset_total"])

    # 省份明细
    by_prov = collections.defaultdict(list)
    for bank in banks:
        by_prov[bank.get("province") or "未知"].append(bank)
    provinces = []
    for prov, bs in by_prov.items():
        assets = [b["asset"] for b in bs if b.get("asset")]
        npl = [b["_npl"] for b in bs if b.get("_npl") is not None]
        prov_cov = [b["_prov"] for b in bs if b.get("_prov") is not None]
        scores = [top_score(b) for b in bs]
        provinces.append({
            "name": prov, "count": len(bs),
            "region": REGION_OF.get(prov, "其他"),
            "asset_total": r2(sum(assets), 0),
            "npl_median": r2(percentile(npl, 0.5)),
            "prov_median": r2(percentile(prov_cov, 0.5), 0),
            "avg_score": r2(mean(scores), 1),
            "penalty_total": sum(b.get("penalty_count") or 0 for b in bs),
            "loss": sum(1 for b in bs if b.get("profit_status") == "亏损"),
        })
    provinces.sort(key=lambda p: -p["asset_total"])

    return {"regions": regions, "provinces": provinces}


def build_trends():
    """Latest public deployment cases and trends, curated under data/enrichment."""
    path = ROOT / "data" / "enrichment" / "trends.json"
    if not path.exists():
        return {"as_of": None, "trends": [], "cases": [], "playbook": [], "caveats": []}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main():
    banks = load_banks()
    if not banks:
        raise SystemExit("no city banks found under frontend/data/banks")
    load_ratios(banks)

    # 横向对比基准：全体中小银行
    peers = []
    for path in glob.glob(str(FRONTEND / "banks" / "*" / "*.json")):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("group") in ("city", "rural", "village"):
            peers.append(data)
    load_ratios(peers)

    manifest = json.load(open(FRONTEND / "manifest.json", encoding="utf-8"))
    payload = {
        "generated_at": manifest.get("generated_at"),
        "registry_as_of": manifest.get("registry_as_of"),
        "sample_note": "样本为 NFRA 法人名录中 124 家城市商业银行；"
                       "指标缺失按「未披露」计入覆盖率，不做推算。",
        "opportunity": build_opportunity(banks, peers),
        "huawei": build_huawei(banks),
        "regions": build_regions(banks),
        "trends": build_trends(),
    }
    out = FRONTEND / "insight.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
