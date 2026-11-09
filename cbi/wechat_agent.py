"""Collect business-focus evidence from WeChat public-account articles.

Sogou's WeChat vertical is the only public index for 公众号 content. Results
are verified by bank-name tokens, then the LLM may only quote text that is
verbatim present in the article; every accepted tag keeps its article URL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

import requests

import config
from cbi import registry
from cbi.focus_agent import (
    OUT_PATH, REGION_BASIS, _extract_llm, _load_json, _save_json, _tokens,
)
from cbi.news_agent import ENV

CACHE_DIR = config.CACHE_DIR / "wechat"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
LIST_URL = "https://weixin.sogou.com/weixin?type=2&query="
BANK_WORDS = ("存款", "贷款", "理财", "客户", "网点", "金融", "账户", "银行")


def _key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://weixin.sogou.com/"})
    return s


def _strip(html: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
            .replace("&quot;", '"').replace("&ldquo;", "“")
            .replace("&rdquo;", "”").replace("&bull;", "·")
            .replace("&mdash;", "—").replace("&lt;", "<").replace("&gt;", ">"))
    text = re.sub(r"\s+", " ", text).strip()
    # Highlight tags inject spaces between CJK chars; collapse them so quotes match.
    text = re.sub(r"(?<=[一-鿿])\s+(?=[一-鿿])", "", text)
    text = re.sub(r"\s+(?=[，。、；：！？）】》,.;:!?)])", "", text)
    return text


def _search(session: requests.Session, bank: dict, use_cache: bool = True) -> list[dict]:
    short = bank.get("short_name") or ""
    core = re.sub(r"(农商行|银行)$", "", short) or short
    query = f"{short} {core} 普惠 小微 科技 绿色 产业 贷款 授信"
    cpath = CACHE_DIR / (_key("list3", query, date.today().isoformat()) + ".json")
    if use_cache and cpath.exists():
        return _load_json(cpath, [])
    rows: list[dict] = []
    try:
        resp = session.get(LIST_URL + urllib.parse.quote(query), timeout=15)
        if resp.status_code != 200 or len(resp.text) < 8000:
            time.sleep(4)
            resp = session.get(LIST_URL + urllib.parse.quote(query), timeout=20)
        if resp.status_code != 200 or len(resp.text) < 8000:
            return rows
        blocks = re.findall(
            r'<h3>\s*<a[^>]+href="(/link\?url=[^"]+)"[^>]*>(.*?)</a>\s*</h3>'
            r'\s*<p class="txt-info"[^>]*>(.*?)</p>',
            resp.text, re.S)
        seen = set()
        for href, title_html, summary_html in blocks:
            href = href.replace("&amp;", "&")
            title = _strip(title_html)
            summary = _strip(summary_html)
            if not title or href in seen:
                continue
            seen.add(href)
            rows.append({"href": href, "title": title[:120], "summary": summary[:300]})
            if len(rows) >= 8:
                break
    except Exception:
        return rows
    _save_json(cpath, rows)
    return rows


def _resolve(session: requests.Session, href: str) -> str | None:
    try:
        resp = session.get("https://weixin.sogou.com" + href, timeout=8)
        parts = re.findall(r"url \+= '([^']*)';", resp.text)
        target = "".join(parts).replace("@", "")
        return target if target.startswith(("https://mp.weixin.qq.com", "http://mp.weixin.qq.com")) else None
    except Exception:
        return None


def _fetch_article(session: requests.Session, url: str) -> dict | None:
    cpath = CACHE_DIR / (_key("article", url) + ".json")
    if cpath.exists():
        return _load_json(cpath, None)
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code != 200:
            return None
        html = resp.text
        title_m = re.search(r'id="activity-name"[^>]*>(.*?)</h1>', html, re.S)
        if not title_m:
            title_m = re.search(r"<title>(.*?)</title>", html, re.S)
        title = _strip(title_m.group(1)) if title_m else ""
        body_m = re.search(r'id="js_content"[^>]*>(.*?)\s*</div>\s*<script', html, re.S)
        text = _strip(body_m.group(1)) if body_m else ""
        if not text:
            return None
        link_m = re.search(r'var msg_link = "([^"]+)"', html)
        canonical = link_m.group(1).replace("&amp;", "&") if link_m and link_m.group(1) else url
        date_m = re.search(r'var ct = "(\d+)"', html)
        published = None
        if date_m:
            published = datetime.fromtimestamp(int(date_m.group(1)), tz=timezone.utc).date().isoformat()
        ids = [re.search(p, html) for p in
               (r'var biz\s*=\s*"([^"]*)"', r'var mid\s*=\s*"([^"]*)"',
                r'var idx\s*=\s*"([^"]*)"')]
        article_id = "|".join(m.group(1) for m in ids if m) or None
        page = {"title": title, "url": canonical, "text": text[:6000],
                "published": published, "article_id": article_id}
        _save_json(cpath, page)
        return page
    except Exception:
        return None


def _bank_like(text: str) -> bool:
    return sum(1 for w in BANK_WORDS if w in text) >= 2


def collect_bank(bank: dict, session: requests.Session, use_cache: bool = True,
                 use_llm: bool = True, max_articles: int = 6) -> dict | None:
    tokens = _tokens(bank)
    listing = _search(session, bank, use_cache=use_cache)
    rows: list[dict] = []
    for item in listing[:max_articles]:
        content = f"{item['title']}。{item.get('summary') or ''}".strip()
        head = f"{item['title']} {item.get('summary') or ''}"
        if not any(tok in head for tok in tokens):
            continue
        if not _bank_like(head):
            continue
        rows.append({
            "title": item["title"],
            "url": "https://weixin.sogou.com" + item["href"],
            "content": content[:600],
        })
    if not rows or not use_llm:
        return None

    parsed = _extract_llm(bank, rows)
    by_url = {r["url"]: r for r in rows}
    used: set[str] = set()
    confirmed: list[dict] = []
    for item in parsed.get("focus") or []:
        tag = (item.get("tag") or "").strip()
        url = (item.get("url") or "").strip()
        quote = re.sub(r"\s+", " ", item.get("quote") or "").strip()
        if tag not in config.FOCUS_TAGS or tag in used or url not in by_url:
            continue
        if len(quote) < 4 or quote not in by_url[url]["content"]:
            continue
        if not any(w in quote for w in config.FOCUS_TAGS[tag]):
            continue
        used.add(tag)
        confirmed.append({
            "tag": tag, "quote": quote[:120], "title": by_url[url]["title"],
            "basis": "公开报道", "confidence": 0.7,
            "source": "微信公众号（搜索摘要）", "list_url": url,
        })
    if not confirmed:
        return None

    # Resolve only the accepted articles to a clickable WeChat URL.
    for entry in confirmed:
        href = entry["list_url"].replace("https://weixin.sogou.com", "")
        target = _resolve(session, href)
        entry["url"] = target or entry["list_url"]
        time.sleep(0.4)
    return {
        "focus": confirmed[:4],
        "region": list(config.PROVINCE_INDUSTRIES.get(bank.get("province") or "", [])),
        "summary": "",
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sites": [{"title": e["title"], "url": e["url"]} for e in confirmed[:4]],
        "source": "wechat",
    }


def collect(limit: int = 0, group: str = "city", force: bool = False,
            use_llm: bool = True, bank_ids: list[str] | None = None,
            extra_ids: list[str] | None = None, verbose: bool = True) -> dict:
    data = registry.load_registry()["banks"]
    by_id = {b["id"]: b for b in data}
    if bank_ids:
        banks = [by_id[i] for i in bank_ids if i in by_id]
    else:
        banks = [b for b in data if group == "all" or b.get("group") == group]
        banks.sort(key=lambda b: (b.get("asset") or 0, b.get("name") or ""), reverse=True)
        if limit > 0:
            banks = banks[:limit]
    if extra_ids:
        seen = {b["id"] for b in banks}
        banks += [by_id[i] for i in extra_ids if i in by_id and i not in seen]

    store = _load_json(OUT_PATH, {})
    session = _session()
    ok = skipped = failed = 0
    for i, bank in enumerate(banks, 1):
        current = store.get(bank["id"]) or {}
        has_public = any(e.get("basis") == "公开报道" for e in current.get("focus") or [])
        if has_public and not force:
            skipped += 1
            if verbose:
                print(f"[{i}/{len(banks)}] {bank['short_name']}: already has public evidence")
            continue
        try:
            item = collect_bank(bank, session, use_cache=not force, use_llm=use_llm)
        except Exception as exc:
            item = None
            failed += 1
            if verbose:
                print(f"[{i}/{len(banks)}] {bank['short_name']}: failed {type(exc).__name__}")
        if item:
            merged = current.get("focus") or []
            tags = {e.get("tag") for e in merged if e.get("basis") == "公开报道"}
            for entry in item["focus"]:
                if entry["tag"] not in tags:
                    merged.append(entry)
                    tags.add(entry["tag"])
            item["focus"] = merged[:6]
            if not item.get("region"):
                item["region"] = current.get("region") or []
            store[bank["id"]] = item
            ok += 1
            if verbose:
                got = "、".join(e["tag"] for e in item["focus"])
                print(f"[{i}/{len(banks)}] {bank['short_name']}: {got}")
        else:
            if verbose:
                print(f"[{i}/{len(banks)}] {bank['short_name']}: no evidence")
        if i % 5 == 0:
            _save_json(OUT_PATH, store)
        time.sleep(0.8)
    _save_json(OUT_PATH, store)
    return {"checked": len(banks), "ok": ok, "skipped": skipped,
            "failed": failed, "total_with_focus": len(store)}


def main() -> None:
    parser = argparse.ArgumentParser(description="WeChat public-account business evidence agent")
    parser.add_argument("--group", default="city")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--bank-id", action="append", default=[])
    parser.add_argument("--extra-id", action="append", default=[])
    args = parser.parse_args()
    result = collect(limit=args.limit, group=args.group, force=args.force,
                     use_llm=not args.no_llm,
                     bank_ids=args.bank_id or None,
                     extra_ids=args.extra_id or None)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
