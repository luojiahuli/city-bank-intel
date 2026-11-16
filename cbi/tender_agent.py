"""Public IT tender/bid collector.

Searches public web results for each bank's IT procurement announcements
(招标公告 / 中标公告 / 结果公示), keeps only entries that name the bank and an
IT category, and stores the title, date, source link, snippet and disclosed
amount. Missing values stay empty; no amount is inferred.

Search source is 360 web search; when it blocks the host with an antispider
page, the collector falls back to Toutiao search, which serves the same public
pages with title, target URL and publish date. Results are cached per day and
requests are throttled.
"""
from __future__ import annotations

import argparse
import html
import json
import random
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import requests

import config
from cbi import registry
from cbi.news_agent import _bank_tokens, _cache_key, _load_json, _save_json

OUT_DIR = config.DATA_DIR / "tenders"
BY_BANK_PATH = OUT_DIR / "by_bank.json"
COVERAGE_PATH = OUT_DIR / "coverage.json"
CACHE_DIR = config.CACHE_DIR / "tender"
for _d in (OUT_DIR, CACHE_DIR / "search", CACHE_DIR / "toutiao",
           CACHE_DIR / "link", CACHE_DIR / "html"):
    _d.mkdir(parents=True, exist_ok=True)

# Once 360 answers with its antispider page the block persists for the whole
# batch, so stop spending a request per query on it.
_SO_BLOCKED = False

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

QUERIES = (
    "{short} 中标 信息科技 采购",
    "{short} 中标公告 系统 信创",
)
EXTRA_QUERY = "{short} 采购项目 中标金额 万元"

IT_CATEGORIES = (
    ("核心系统", ("核心系统", "新核心", "分布式核心", "主机下移", "核心账务")),
    ("人工智能", ("大模型", "人工智能", "智能体", "算力", "GPU", "机器学习",
                  "模型训练", "智能客服", "远程银行", "AI")),
    ("数据平台", ("数据中台", "数据仓库", "数据湖", "湖仓", "数据治理",
                  "监管报送", "大数据平台", "数据平台")),
    ("信创与硬件", ("信创", "应用创新", "国产化", "鲲鹏", "海光", "飞腾", "达梦",
                    "高斯", "麒麟", "服务器", "存储", "数据库", "操作系统",
                    "虚拟化", "终端", "PC设备", "扫描仪", "网络设备", "交换机",
                    "云平台")),
    ("网络安全", ("网络安全", "信息安全", "数据安全", "防火墙", "加密", "密评",
                  "态势感知", "堡垒机", "漏洞")),
    ("信贷风控", ("信贷系统", "风控", "反欺诈", "征信", "贷后", "尽调")),
    ("运维服务", ("维保", "运维", "技术支持", "驻场", "第三方服务")),
    ("其他 IT", ("系统", "软件", "平台", "数据库", "信息化")),
)

STAGE_WORDS = ("中标", "成交", "结果公示", "结果公告", "候选人",
               "招标", "采购公告", "磋商", "询价", "比选")
RESULT_WORDS = ("中标", "成交", "结果公示", "结果公告", "候选人")
BLOCK_TITLES = ("短视频大全", "360图片", "图片", "视频", "百科", "贴吧",
                "问答", "文库", "地图", "最新招标采购公告", "招标采购公告_第",
                "采购公告_第", "招标信息_第", "最新招标公告", "采购动态")
NON_IT_WORDS = ("物业", "装修", "食堂", "保洁", "安保", "押运", "审计", "评估",
                "保险", "存款", "开户", "账户", "基金", "托管", "现金管理",
                "绿植", "车辆", "印刷", "广告", "家具", "空调")
STRONG_IT_WORDS = ("系统", "软件", "数据库", "服务器", "平台", "网络", "大模型",
                   "算力", "信创改造", "维保", "终端", "存储", "云", "安全")

AMOUNT_RE = re.compile(r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(亿元|万元|万|元)")
DATE_RE = re.compile(r"(20\d{2})[年\-/.](\d{1,2})[月\-/.](\d{1,2})")
CAPTCHA_MARKERS = ("antispider", "验证码", "wappass", "安全验证")


def _clean(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _parse_date(text: str) -> str | None:
    m = DATE_RE.search(text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def _to_wan(value: float, unit: str) -> float:
    if unit == "亿元":
        return value * 10000.0
    if unit == "万元":
        return value
    if unit == "万":
        return value
    return value / 10000.0


def _amount(text: str) -> float | None:
    best: tuple[int, float, int] | None = None
    for m in AMOUNT_RE.finditer(text or ""):
        try:
            value = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        wan = _to_wan(value, m.group(2))
        if not 1.0 <= wan <= 5_000_000.0:
            continue
        ctx = (text or "")[max(0, m.start() - 24):m.start()]
        score = 2 if re.search(
            r"(中标金额|成交金额|中标价|中标总价|预算金额|采购预算|项目金额|总金额)", ctx
        ) else 1
        if best is None or score > best[0]:
            best = (score, round(wan, 2), m.start())
    return best[1] if best else None


def _category(text: str) -> str | None:
    low = (text or "").lower()
    for label, words in IT_CATEGORIES:
        for word in words:
            if word == "AI":
                if re.search(r"\bai\b", low):
                    return label
            elif word.lower() in low:
                return label
    return None


def _stage(title: str, body: str) -> str:
    if any(w in title for w in RESULT_WORDS):
        return "中标/结果"
    if any(w in title for w in ("招标", "采购", "磋商", "询价", "比选")):
        return "招标/采购"
    text = f"{title} {body}"
    return "中标/结果" if any(w in text for w in RESULT_WORDS) else "招标/采购"


def _non_it(text: str) -> bool:
    if not any(w in text for w in NON_IT_WORDS):
        return False
    return not any(w in text for w in STRONG_IT_WORDS)


def _is_official(domain: str, bank: dict) -> bool:
    if not domain:
        return False
    if domain.endswith("gov.cn") or "ggzy" in domain or "ccgp" in domain:
        return True
    site = (bank.get("website") or "").lower()
    return bool(site and urlparse(site).netloc.replace("www.", "") in domain)


def _recent_cache(query: str, subdir: str, days: int = 3) -> dict | None:
    """A batch that crosses midnight or hits a captcha can reuse yesterday's hits."""
    for offset in range(1, days + 1):
        day = (date.today() - timedelta(days=offset)).isoformat()
        path = CACHE_DIR / subdir / f"{_cache_key(query, day)}.json"
        if not path.exists():
            continue
        data = _load_json(path, {})
        if data.get("results"):
            return data
    return None


def _search(query: str, refresh: bool = False) -> dict:
    cache = CACHE_DIR / "search" / f"{_cache_key(query, date.today().isoformat())}.json"
    if not refresh and cache.exists():
        return _load_json(cache, {"results": []})
    url = "https://www.so.com/s?q=" + quote(query) + "&pn=1"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
    except requests.RequestException as exc:
        return {"error": type(exc).__name__, "results": []}
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}", "results": []}
    text = resp.text
    if any(marker in text for marker in CAPTCHA_MARKERS):
        stale = _recent_cache(query, "search")
        return {"error": "captcha", "results": (stale or {}).get("results", [])}
    results = []
    for block in re.findall(r'<li class="res-list.*?</li>', text, re.S):
        m = re.search(r'<h3[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        title = _clean(m.group(2))
        href = html.unescape(m.group(1))
        body = _clean(block)
        if not title or not href:
            continue
        results.append({"title": title, "href": href, "text": body})
    data = {"error": None, "results": results}
    _save_json(cache, data)
    return data


def _parse_toutiao(text: str) -> list[dict]:
    starts = [m.start() for m in re.finditer(r'class="result-content"', text)]
    results = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else min(start + 25000, len(text))
        seg = text[start:end]
        params = re.search(r'cr-params="([^"]+)"', seg)
        if not params:
            continue
        try:
            meta = json.loads(html.unescape(params.group(1)))
        except (ValueError, TypeError):
            continue
        title = re.sub(r"\s+", "", meta.get("title") or "")
        href = meta.get("url") or ""
        target = parse_qs(urlparse(href).query).get("h5_url")
        if target:
            href = target[0]
        visible = re.sub(r'(cr-params|data-log-extra)="[^"]*"', "", seg)
        visible = re.sub(r"<(script|style).*?</\1>", " ", visible, flags=re.S)
        body = _clean(visible)
        if not title or not href or not body:
            continue
        results.append({"title": title, "href": href, "text": body})
    return results


def _search_toutiao(query: str, refresh: bool = False) -> dict:
    cache = CACHE_DIR / "toutiao" / f"{_cache_key(query, date.today().isoformat())}.json"
    if not refresh and cache.exists():
        return _load_json(cache, {"results": []})
    url = "https://so.toutiao.com/search?keyword=" + quote(query) + "&pd=synthesis"
    error = None
    results: list[dict] = []
    # Toutiao occasionally serves an empty shell to back-to-back requests.
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
        except requests.RequestException as exc:
            error = type(exc).__name__
            results = []
            break
        if resp.status_code != 200:
            error = f"HTTP {resp.status_code}"
            results = []
            break
        results = _parse_toutiao(resp.text)
        if results:
            break
        if attempt == 0:
            time.sleep(random.uniform(3, 5))
    if not results:
        stale = _recent_cache(query, "toutiao")
        if stale:
            results = stale["results"]
    data = {"error": error, "results": results}
    if results:
        _save_json(cache, data)
    return data


def _search_any(query: str, refresh: bool = False) -> dict:
    global _SO_BLOCKED
    if not _SO_BLOCKED:
        data = _search(query, refresh=refresh)
        if data.get("error") == "captcha":
            _SO_BLOCKED = True
        if data.get("results"):
            return data
    return _search_toutiao(query, refresh=refresh)


def _parse_date_loose(text: str) -> str | None:
    # Toutiao's visible text breaks CJK tokens and sometimes digits ("20 24年").
    text = re.sub(r"(?<=\d)\s+(?=\d)", "", text or "")
    matches = list(DATE_RE.finditer(text))
    for m in reversed(matches):
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            continue
    today = date.today()
    m = re.search(r"(\d+)\s*天前", text or "")
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    if re.search(r"(\d+)\s*小时前", text or ""):
        return today.isoformat()
    for marker, days in (("昨天", 1), ("前天", 2), ("今天", 0)):
        if marker in (text or ""):
            return (today - timedelta(days=days)).isoformat()
    return None


def _resolve(href: str, refresh: bool = False) -> str:
    if "so.com/link" not in href:
        return href
    cache = CACHE_DIR / "link" / f"{_cache_key(href)}.json"
    if not refresh and cache.exists():
        return _load_json(cache, {}).get("url") or href
    target = href
    try:
        resp = requests.get(href, headers=HEADERS, timeout=10, allow_redirects=True)
        if resp.url and "so.com/link" not in resp.url:
            target = resp.url
        else:
            for cand in re.findall(r'https?://[^\s"\'<>]+', resp.text):
                low = cand.lower()
                if not any(x in low for x in ("so.com", "360.cn", "qhimg", "qhstatic", "sogoucdn")):
                    target = cand
                    break
    except requests.RequestException:
        target = href
    _save_json(cache, {"url": target})
    time.sleep(0.8)
    return target


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _fetch_amount(url: str) -> float | None:
    cache = CACHE_DIR / "html" / f"{_cache_key(url)}.json"
    if cache.exists():
        return _load_json(cache, {}).get("amount_wan")
    amount = None
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            resp.encoding = resp.apparent_encoding
            text = _clean(resp.text)
            for m in re.finditer(
                r"(中标金额|成交金额|中标价|中标总价|预算金额|采购预算)[^。；]{0,60}", text
            ):
                amount = _amount(m.group(0))
                if amount:
                    break
    except requests.RequestException:
        pass
    _save_json(cache, {"amount_wan": amount})
    time.sleep(0.8)
    return amount


def _bank_item(bank: dict, row: dict) -> dict | None:
    title = row["title"]
    body = row["text"]
    flat = re.sub(r"\s+", "", f"{title}{body}")
    if any(word in flat for word in BLOCK_TITLES):
        return None
    tokens = _bank_tokens(bank)
    if not tokens or not any(tok in flat for tok in tokens):
        return None
    if not any(word in flat for word in STAGE_WORDS):
        return None
    category = _category(flat)
    if not category:
        return None
    if _non_it(flat):
        return None
    published = _parse_date_loose(body)
    if not published:
        return None
    return {
        "title": title,
        "href": row["href"],
        "text": body,
        "date": published,
        "category": category,
        "stage": _stage(re.sub(r"\s+", "", title), flat),
        "amount_wan": _amount(flat),
    }


def _same_project(a: dict, b: dict) -> bool:
    if not (a.get("amount_wan") and b.get("amount_wan")):
        return False
    if a.get("category") != b.get("category"):
        return False
    try:
        delta = abs((date.fromisoformat(a["date"]) - date.fromisoformat(b["date"])).days)
    except ValueError:
        return False
    if delta > 60:
        return False
    low, high = sorted((a["amount_wan"], b["amount_wan"]))
    return high > 0 and (high - low) / high <= 0.05


def collect_bank(bank: dict, since: str, refresh: bool, sleep: float,
                 fetch_detail: bool, verbose: bool = True) -> dict:
    short = bank.get("short_name") or bank["name"]
    rows: list[dict] = []
    errors = []
    for query in QUERIES:
        data = _search_any(query.format(short=short), refresh=refresh)
        if data.get("error"):
            errors.append({"query": query.format(short=short), "error": data["error"]})
        for row in data.get("results", []):
            item = _bank_item(bank, row)
            if item:
                rows.append(item)
        time.sleep(random.uniform(sleep, sleep + 1.5))

    if len(rows) < 2:
        data = _search_any(EXTRA_QUERY.format(short=short), refresh=refresh)
        if data.get("error"):
            errors.append({"query": EXTRA_QUERY.format(short=short), "error": data["error"]})
        for row in data.get("results", []):
            item = _bank_item(bank, row)
            if item:
                rows.append(item)
        time.sleep(random.uniform(sleep, sleep + 1.5))

    # Keep the most recent entries per normalized title and URL.
    seen = set()
    kept = []
    for item in sorted(rows, key=lambda x: x["date"], reverse=True):
        # Same project often appears on several portals; keep one per title prefix.
        key = re.sub(r"\W+", "", item["title"])[:20]
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    kept = [item for item in kept if item["date"] >= since][:20]

    for item in kept:
        target = _resolve(item["href"], refresh=refresh)
        item["url"] = target
        item["source_domain"] = _domain(target)
        item["source_type"] = "official" if _is_official(item["source_domain"], bank) else "media"

    if fetch_detail:
        budget = 3
        for item in kept:
            if budget <= 0:
                break
            if item["amount_wan"] is None and item["url"] != item["href"]:
                item["amount_wan"] = _fetch_amount(item["url"])
                budget -= 1

    # Amounts discovered from detail pages can reveal the same contract twice,
    # and different outlets report the same award with a slightly different sum.
    merged = []
    for item in kept:
        if item.get("amount_wan") and any(
            _same_project(item, other) for other in merged
        ):
            continue
        merged.append(item)
    kept = merged

    for item in kept:
        item.pop("href", None)
        item.pop("text", None)
        item["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    summary = _summarize(kept)
    if verbose:
        marks = "".join("●" if item["amount_wan"] else "○" for item in kept)
        print(f"{short}: {len(kept)} 条 {marks}")
    return {
        "name": bank["name"],
        "short_name": short,
        "group": bank.get("group"),
        "items": kept,
        "errors": errors,
        **summary,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _summarize(items: list[dict]) -> dict:
    today = date.today()
    def within(days: int) -> float:
        cutoff = (today - timedelta(days=days)).isoformat()
        return round(sum(i["amount_wan"] for i in items
                         if i.get("amount_wan") and i["date"] >= cutoff), 2)

    by_year: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for item in items:
        by_year[item["date"][:4]] = by_year.get(item["date"][:4], 0) + 1
        by_category[item["category"]] = by_category.get(item["category"], 0) + 1
    return {
        "it_tender_12m_wan": within(365) or None,
        "it_tender_24m_wan": within(730) or None,
        "it_tender_count": len(items),
        "it_tender_with_amount": sum(1 for i in items if i.get("amount_wan")),
        "it_tender_latest": items[0]["date"] if items else None,
        "by_year": by_year,
        "by_category": by_category,
    }


def select_banks(group: str, limit: int, bank_name: str | None,
                 retry_failed: bool = False) -> list[dict]:
    data = registry.load_registry()
    banks = data["banks"]
    if bank_name:
        by_id = {b["id"]: b for b in banks}
        if bank_name in by_id:
            return [by_id[bank_name]]
        hits = [b for b in banks
                if bank_name in (b.get("name"), b.get("short_name"))]
        if not hits:
            hits = [b for b in banks if bank_name in b.get("name", "")]
        if not hits:
            raise SystemExit(f"未找到银行: {bank_name}")
        return hits[:1]
    picked = [b for b in banks if group == "all" or b.get("group") == group]
    if retry_failed:
        coverage = _load_json(COVERAGE_PATH, {})
        failed = {row["id"] for row in coverage.get("no_result", [])}
        picked = [b for b in picked if b["id"] in failed]
    picked.sort(key=lambda b: b["name"])
    if limit > 0:
        picked = picked[:limit]
    return picked


def main() -> None:
    parser = argparse.ArgumentParser(description="采集银行 IT 招投标公开信息")
    parser.add_argument("--group", default="city",
                        choices=["city", "rural", "village", "other", "all"])
    parser.add_argument("--limit", type=int, default=0,
                        help="每组抓取数量，0 表示全部")
    parser.add_argument("--bank", default=None, help="银行名称或 id")
    parser.add_argument("--retry-failed", dest="retry_failed", action="store_true",
                        help="只重试上次没有命中记录的银行")
    parser.add_argument("--since", default="2024-01-01")
    parser.add_argument("--sleep", type=float, default=2.5,
                        help="请求间隔基准秒数")
    parser.add_argument("--refresh", action="store_true", help="忽略当日缓存")
    parser.add_argument("--fetch-detail", dest="fetch_detail", action="store_true",
                        default=True)
    parser.add_argument("--no-fetch-detail", dest="fetch_detail",
                        action="store_false")
    args = parser.parse_args()

    banks = select_banks(args.group, args.limit, args.bank, args.retry_failed)
    store = _load_json(BY_BANK_PATH, {})
    existing = store.get("banks", {})
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results = []
    for i, bank in enumerate(banks, 1):
        try:
            entry = collect_bank(bank, args.since, args.refresh, args.sleep,
                                 args.fetch_detail)
        except Exception as exc:  # keep the batch going on one bad bank
            print(f"[{i}/{len(banks)}] {bank.get('short_name')} 失败: {type(exc).__name__}")
            continue
        existing[bank["id"]] = entry
        results.append(entry)
        if i % 20 == 0:
            _save_json(BY_BANK_PATH, {"as_of": date.today().isoformat(),
                                      "note": NOTE, "banks": existing})

    _save_json(BY_BANK_PATH, {"as_of": date.today().isoformat(),
                              "note": NOTE, "banks": existing})
    _write_coverage(existing, started, len(banks))
    print(f"完成 {len(results)}/{len(banks)} 家，"
          f"命中 {sum(1 for e in results if e['it_tender_count'])} 家")


NOTE = (
    "公开网页检索到的银行 IT 招标/中标/结果公告与厂商披露，仅保存标题、链接、"
    "摘要、日期和公开金额；金额为不完全统计，不等同于年度 IT 投入，缺失不推算。"
)


def _write_coverage(existing: dict, started: str, searched_now: int) -> None:
    entries = list(existing.values())
    with_items = [e for e in entries if e.get("it_tender_count")]
    with_amount = [e for e in with_items if e.get("it_tender_with_amount")]
    by_group: dict[str, dict] = {}
    for entry in entries:
        group = entry.get("group") or "other"
        node = by_group.setdefault(group, {"searched": 0, "with_items": 0,
                                           "with_amount": 0, "items": 0})
        node["searched"] += 1
        node["items"] += entry.get("it_tender_count") or 0
        if entry.get("it_tender_count"):
            node["with_items"] += 1
        if entry.get("it_tender_with_amount"):
            node["with_amount"] += 1
    coverage = {
        "as_of": date.today().isoformat(),
        "started_at": started,
        "searched_now": searched_now,
        "searched": len(entries),
        "with_items": len(with_items),
        "with_amount": len(with_amount),
        "items": sum(e.get("it_tender_count") or 0 for e in entries),
        "by_group": by_group,
        "no_result": [{"id": k, "name": v.get("short_name")}
                      for k, v in existing.items() if not v.get("it_tender_count")],
        "errors": [{"id": k, "errors": v.get("errors")}
                   for k, v in existing.items() if v.get("errors")],
    }
    _save_json(COVERAGE_PATH, coverage)


if __name__ == "__main__":
    main()
