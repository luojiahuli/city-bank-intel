"""Official-website profile collector.

Discovers each bank's official site, extracts public pages (home / about /
business / news), and asks DeepSeek to summarise only what those pages say:
institution features, key business lines and recent moves. Every summary keeps
the page URLs it was derived from; missing sites are stored as ``no_site``
instead of being guessed.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

import config
from cbi import registry
from cbi.news_agent import ENV, _cache_key, _load_json, _save_json

OUT_PATH = config.DATA_DIR / "enrichment" / "website.json"
CACHE_DIR = config.CACHE_DIR / "website"
for d in (CACHE_DIR, CACHE_DIR / "search", CACHE_DIR / "html", CACHE_DIR / "llm"):
    d.mkdir(parents=True, exist_ok=True)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Domains that are never a bank's own site.
BLOCK_DOMAINS = (
    "baike.baidu.com", "wikipedia.org", "investing.com", "apps.apple.com",
    "pbc.gov.cn", "gov.cn", "gov.hk", "weixin.qq.com", "sina.com.cn",
    "sohu.com", "163.com", "eastmoney.com", "10jqka.com.cn", "jrj.com.cn",
    "cnstock.com", "hexun.com", "yicai.com", "caixin.com", "qixin.com",
    "tianyancha.com", "aiqicha.baidu.com", "企查查", "linkedin.com",
    "zhihu.com", "douban.com", "zhaopin.com", "liepin.com", "51job.com",
    "credit.china.cn", "creditchina.gov.cn", "xueqiu.com", "jin10.com",
    "tavily.com", "cebnet.com.cn", "cnfol.com", "jinrongfawu.com",
    "sznews.com", "sdu.edu.cn", "fenbi.com", "shuididp.cn", "media.beer",
    "facebook.com", "nowcoder.com", "weibo.com", "bilibili.com", "douyin.com",
    "xiaohongshu.com", "csdn.net", "github.com", "gitee.com", "cnblogs.com",
    "qq.com", "so.com", "bing.com", "google.com", "yinhangkahao.com",
    "chakahao.com", "cngold.org", "rocketreach.co", "dianhua.cn",
    "bbxin.com", "gaoxiaojob.com", "chinadas.net", "flipbuilder.com",
    "36kr.com", "mydama.org.cn", "apps.microsoft.com", "moomoo.com",
    "qcc.com", "qichacha.com", "bawc.cn", "51job.com", "shuidi.cn",
    "sina.cn", "china.com", "inews.qq.com", "dlztb.com", "maps.google",
)

MULTI_SUFFIX = ("com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "com.hk")

# Official domains verified from public sources; used before search results
# for banks whose search hits are dominated by unrelated sites.
DOMAIN_OVERRIDES = {
    "长沙银行": "https://www.cscb.cn/",
    "威海市商业银行": "https://www.whccb.com/",
    "富滇银行": "https://www.fudian-bank.com/",
    "泰安银行": "https://www.taccb.com.cn/",
    "自贡银行": "https://www.zgbank.com.cn/",
    "本溪银行": "https://www.bankofbx.com/",
}


def _root_of(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    parts = host.split(".")
    if len(parts) > 3 and ".".join(parts[-2:]) in MULTI_SUFFIX:
        host = ".".join(parts[-3:])
    elif len(parts) > 2:
        host = ".".join(parts[-2:])
    return host

ABOUT_WORDS = ("关于我们", "关于我行", "银行简介", "公司简介", "银行概况", "本行概况")
BUSINESS_WORDS = (
    "公司业务", "个人业务", "零售业务", "公司金融", "个人金融", "普惠金融",
    "小微金融", "国际业务", "投行业务", "金融市场", "业务产品", "产品与服务",
)
NEWS_WORDS = ("新闻", "动态", "资讯", "公告", "要闻", "行内新闻", "媒体报道")

DATE_RE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")
WS_RE = re.compile(r"\s+")


class LegacyTLSAdapter(HTTPAdapter):
    """Some bank sites still require legacy renegotiation / lower ciphers."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.options |= 0x4  # OP_LEGACY_SERVER_CONNECT
        except Exception:
            pass
        try:
            ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        except Exception:
            pass
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def _session(host: str) -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    if host:
        s.mount(f"https://{host}", LegacyTLSAdapter())
    return s


def _clean(text: str) -> str:
    return WS_RE.sub(" ", text or "").strip()


def _tokens(bank: dict) -> list[str]:
    name = bank.get("name") or ""
    base = re.sub(r"(股份有限公司|有限责任公司|有限公司)$", "", name)
    short = bank.get("short_name") or ""
    out = {name, base, short}
    repl = {
        "农商行": "农村商业银行",
        "农合行": "农村合作银行",
        "农信社": "农村信用合作联社",
    }
    for suffix, full in repl.items():
        if short.endswith(suffix):
            out.add(short[: -len(suffix)] + full)
    # Rebranded short names: 威海市商业银行 → 威海银行.
    for suffix in ("市商业银行", "商业银行", "银行"):
        if short.endswith(suffix):
            core = short[: -len(suffix)]
            if len(core) >= 2:
                out.add(core + "银行")
    return [t for t in out if len(t) >= 4]


def _blocked(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(b in host for b in BLOCK_DOMAINS)


def _bank_like(text: str) -> bool:
    words = ("存款", "贷款", "理财", "客户", "网点", "金融", "账户", "银行")
    return sum(1 for w in words if w in text) >= 3


def _tavily_rows(query: str, depth: str, use_cache: bool) -> list[dict]:
    key = ENV.get("TAVILY_API_KEY")
    if not key:
        raise RuntimeError("TAVILY_API_KEY is not configured")
    cpath = CACHE_DIR / "search" / f"{_cache_key(query, 'general', depth)}.json"
    if use_cache and cpath.exists():
        return _load_json(cpath, [])
    resp = requests.post(
        config.TAVILY_URL,
        json={
            "api_key": key,
            "query": query,
            "topic": "general",
            "search_depth": depth,
            "max_results": 8,
            "include_answer": False,
            "include_raw_content": False,
        },
        timeout=45,
    )
    resp.raise_for_status()
    rows = resp.json().get("results") or []
    _save_json(cpath, rows)
    return rows


def _rank_candidates(bank: dict, rows: list[dict]) -> list[str]:
    tokens = _tokens(bank)
    short = bank.get("short_name") or ""
    base = re.sub(r"(股份有限公司|有限责任公司|有限公司)$", "", bank.get("name") or "")
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for row in rows:
        url = (row.get("url") or "").strip()
        if not url or _blocked(url):
            continue
        title = _clean(row.get("title") or "")
        content = _clean(row.get("content") or "")
        score = 0
        if title in (short, base) or title == f"{short}官网":
            score = 6
        elif title.startswith(short) or title.startswith(base):
            score = 4
        elif any(t in title for t in tokens):
            score = 3
        elif any(t in content for t in tokens):
            score = 1
        else:
            continue
        if len(title) <= 24:
            score += 1
        exact = (urlparse(url).hostname or "").lower()
        base = _root_of(url)
        hosts: list[str] = []
        for host in (exact, base):
            if not host:
                continue
            # Bank sites are overwhelmingly served on www.<root>.
            if not host.startswith("www."):
                try:
                    ipaddress.ip_address(host)
                except ValueError:
                    hosts.append("www." + host)
            hosts.append(host)
        host_seen: set[str] = set()
        for host in hosts:
            if host in host_seen:
                continue
            host_seen.add(host)
            for scheme in ("https", "http"):
                root = f"{scheme}://{host}/"
                if root not in seen:
                    seen.add(root)
                    scored.append((score, root))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [root for _, root in scored[:6]]


def search_site_candidates(bank: dict, use_cache: bool = True) -> list[str]:
    short = bank.get("short_name") or bank["name"]
    rows = _tavily_rows(f"{short} 官方网站", "advanced", use_cache)
    roots = _rank_candidates(bank, rows)
    if not roots:
        rows += _tavily_rows(f"{short} 官网", "basic", use_cache)
        roots = _rank_candidates(bank, rows)
    return roots


MD_LINK_RE = re.compile(r"\[([^\]]{1,60})\]\((https?://[^)\s]+)\)")


def _tavily_extract(url: str) -> dict | None:
    """Fallback for sites that block datacenter requests (WAF / HTTP 412)."""
    key = ENV.get("TAVILY_API_KEY")
    if not key:
        return None
    cpath = CACHE_DIR / "html" / f"{_cache_key(url, 'extract')}.json"
    if cpath.exists():
        return _load_json(cpath, None) or None
    endpoint = config.TAVILY_URL.rsplit("/", 1)[0] + "/extract"
    raw = ""
    final_url = url
    for depth in ("basic", "advanced"):
        try:
            resp = requests.post(
                endpoint,
                json={
                    "api_key": key,
                    "urls": [url],
                    "extract_depth": depth,
                    "format": "markdown",
                },
                timeout=60,
            )
            resp.raise_for_status()
            rows = resp.json().get("results") or []
        except Exception:
            rows = []
        if rows:
            raw = rows[0].get("raw_content") or ""
            final_url = rows[0].get("url") or url
            if len(_clean(re.sub(r"!\[[^\]]*\]\([^)]*\)", "", raw))) >= 600:
                break
        if depth == "advanced":
            break
    text = _clean(MD_LINK_RE.sub(r"\1", re.sub(r"!\[[^\]]*\]\([^)]*\)", "", raw)))
    if len(text) < 40:
        _save_json(cpath, {})
        return None
    host = urlparse(url).netloc
    links = []
    for m in MD_LINK_RE.finditer(raw):
        href = m.group(2)
        if urlparse(href).netloc == host:
            links.append({"text": _clean(m.group(1))[:40], "url": href})
    heading = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", raw)
    mt = re.search(r"^#{1,2}\s+(\S.*)$", heading, re.M)
    page = {
        "url": final_url,
        "title": _clean(mt.group(1)) if mt else "",
        "text": text,
        "links": links[:400],
        "via": "tavily_extract",
    }
    _save_json(cpath, page)
    return page


def fetch_page(url: str, timeout: int = 10, allow_extract: bool = False) -> dict | None:
    parsed = urlparse(url)
    key = _cache_key(url)
    cpath = CACHE_DIR / "html" / f"{key}.json"
    if cpath.exists():
        cached = _load_json(cpath, None)
        if cached:
            return cached

    # A normal attempts first; the legacy TLS adapter is only worth retrying
    # after an SSL/TLS failure, not after a timeout or connection error.
    error: Exception | None = None
    for legacy in (False, True):
        if legacy and not (error and "ssl" in str(error).lower()):
            break
        try:
            if legacy:
                session = _session(parsed.netloc)
            else:
                session = requests.Session()
                session.headers.update(HEADERS)
            resp = session.get(url, timeout=timeout, allow_redirects=True)
            if resp.status_code >= 400:
                break
            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.text, "lxml")
            for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
                tag.decompose()
            links = []
            for a in soup.find_all("a", href=True):
                text = _clean(a.get_text(" ", strip=True))[:40]
                href = urljoin(resp.url, a["href"])
                if not text or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                    continue
                if urlparse(href).netloc != urlparse(resp.url).netloc:
                    continue
                links.append({"text": text, "url": href})
            page = {
                "url": resp.url,
                "title": _clean(soup.title.get_text(strip=True)) if soup.title else "",
                "text": _clean(soup.get_text(" ", strip=True)),
                "links": links[:400],
            }
            if len(page["text"]) >= 40:
                _save_json(cpath, page)
                return page
            break
        except Exception as exc:
            error = exc
            continue
    if allow_extract:
        return _tavily_extract(url)
    return None


def _pick_pages(home: dict, limit: int = 5) -> list[dict]:
    base = urlparse(home["url"])
    picks: list[tuple[int, str, str]] = []
    seen = {home["url"]}
    for link in home.get("links") or []:
        text = link["text"]
        href = link["url"]
        if href in seen or _blocked(href):
            continue
        if urlparse(href).netloc != base.netloc:
            continue
        if any(w in text for w in ABOUT_WORDS):
            picks.append((0, href, text))
        elif any(w in text for w in BUSINESS_WORDS):
            picks.append((1, href, text))
        elif any(w in text for w in NEWS_WORDS):
            picks.append((2, href, text))
        seen.add(href)
    picks.sort(key=lambda x: x[0])
    out = []
    used = set()
    counts = {0: 0, 1: 0, 2: 0}
    quota = {0: 1, 1: 2, 2: 2}
    for rank, href, text in picks:
        if counts[rank] >= quota[rank]:
            continue
        counts[rank] += 1
        used.add(href)
        out.append({"url": href, "label": text})
        if len(out) >= limit:
            break
    return out


def _recent_items(pages: list[dict], limit: int = 6) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for page in pages:
        for link in page.get("links") or []:
            text = link["text"]
            if len(text) < 8 or text in seen:
                continue
            date = ""
            m = DATE_RE.search(text)
            if m:
                date = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            else:
                m = DATE_RE.search(link["url"])
                if m:
                    date = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            if not date:
                continue
            clean_title = _clean(DATE_RE.sub(" ", text)).strip(" -—·")[:90]
            if not clean_title or clean_title in seen:
                continue
            seen.add(clean_title)
            items.append({"title": clean_title, "date": date, "url": link["url"]})
    items.sort(key=lambda x: x["date"], reverse=True)
    return items[:limit]


def _profile_cache(bank_id: str, pages: list[dict]) -> Path:
    payload = json.dumps(
        [{"url": p["url"], "text": p["text"][:200]} for p in pages],
        ensure_ascii=False,
    )
    return CACHE_DIR / "llm" / f"{_cache_key(bank_id, payload)}.json"


def _fallback_profile(pages: list[dict], recent: list[dict]) -> dict:
    text = pages[0]["text"] if pages else ""
    nav = " ".join(l.get("text", "") for l in (pages[0].get("links") if pages else []) or [])
    business = []
    for word in BUSINESS_WORDS:
        if word in nav and word not in business:
            business.append(word)
    features = text[:120] if text else ""
    return {
        "features": features,
        "key_business": business[:6],
        "recent_moves": [f"{i['date']} {i['title']}" for i in recent[:5]],
        "evidence": [],
        "confidence": "低",
        "basis": "官网正文截取（未调用大模型）",
    }


def summarize_website(bank: dict, pages: list[dict], recent: list[dict],
                      use_llm: bool = True) -> dict:
    if not use_llm or not ENV.get("DEEPSEEK_API_KEY"):
        return _fallback_profile(pages, recent)

    cpath = _profile_cache(bank["id"], pages)
    if cpath.exists():
        return _load_json(cpath, {})

    payload_pages = [
        {"url": p["url"], "title": p.get("title") or "", "text": p["text"][:4200]}
        for p in pages[:5]
    ]
    system = (
        "你是银行公开官网信息整理助手。只能依据用户提供的官网页面文本写作，"
        "禁止补充文本中未出现的事实、数字或战略。官网信息可能滞后，用“官网显示”限定。"
        "输出 JSON 对象：{\"features\":\"不超过120字的机构特征\","
        "\"key_business\":[\"3-6个关键业务，每项不超过14字\"],"
        "\"recent_moves\":[\"2-5条最近动向，每条不超过60字，尽量带时间\"],"
        "\"evidence\":[{\"text\":\"不超过50字原文摘录\",\"url\":\"对应页面URL\"}],"
        "\"confidence\":\"高|中|低\"}。"
        "只保留与机构定位、业务条线、数字化、绿色/普惠/科技金融、近期新闻相关的内容。"
        "若文本不足以判断，减少条目并把 confidence 设为低。不要编造 URL。"
    )
    user = (
        f"银行：{bank.get('name')}（{bank.get('province')}，{bank.get('type_label')}）\n"
        f"官网页面：{json.dumps(payload_pages, ensure_ascii=False)}\n"
        f"带日期的近期标题：{json.dumps(recent[:8], ensure_ascii=False)}"
    )
    url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    try:
        resp = requests.post(
            url,
            json={
                "model": config.LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            headers={
                "Authorization": f"Bearer {ENV['DEEPSEEK_API_KEY']}",
                "Content-Type": "application/json",
            },
            timeout=90,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception as exc:
        print(f"  LLM failed for {bank['short_name']}: {type(exc).__name__}")
        return _fallback_profile(pages, recent)

    valid_urls = {p["url"] for p in pages}
    evidence = []
    for ev in parsed.get("evidence") or []:
        u = (ev.get("url") or "").strip()
        if u in valid_urls and ev.get("text"):
            evidence.append({"text": _clean(ev["text"])[:80], "url": u})
    if not evidence and pages:
        evidence = [{"text": pages[0]["text"][:80], "url": pages[0]["url"]}]

    moves = [_clean(t) for t in (parsed.get("recent_moves") or []) if t][:5]
    moves.sort(key=lambda s: (DATE_RE.search(s).group(0) if DATE_RE.search(s) else ""),
               reverse=True)
    profile = {
        "features": _clean(parsed.get("features") or "")[:160],
        "key_business": [t for t in (parsed.get("key_business") or []) if t][:6],
        "recent_moves": moves,
        "evidence": evidence,
        "confidence": parsed.get("confidence") if parsed.get("confidence") in ("高", "中", "低") else "低",
        "basis": "官网文本抽取（DeepSeek）",
    }
    if profile["features"]:
        _save_json(cpath, profile)
    return profile


URL_RE = re.compile(r"https?://[a-zA-Z0-9._-]+")


SOGOU_BAD = (
    "sogou", "qq.com", "qpic.cn", "bcebos.com", "soso.com", "myqcloud.com",
    "w3.org", "bilibili.com", "hdslb.com", "mzstatic", "chinaz.com",
    "ddooo.com", "dianhua.cn", "shouye-wang.com", "cup.edu.cn", "cjlu.edu.cn",
    "nmbys.cn", "singlewindow", "dzcom",
)


def _sogou_candidates(bank: dict, use_cache: bool = True) -> list[str]:
    short = bank.get("short_name") or bank["name"]
    cpath = BAIDU_CACHE / f"{_cache_key(short, 'sogou-mobile')}.json"
    if use_cache and cpath.exists():
        return _load_json(cpath, [])
    try:
        resp = requests.get(
            "https://m.sogou.com/web/searchList.jsp",
            params={"keyword": f"{short}官网"},
            headers={"User-Agent": MOBILE_UA, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception:
        return []
    if len(resp.text) < 20_000:
        return []
    counts: dict[str, int] = {}
    raw_urls = [unquote(m.group(1)) for m in re.finditer(r"url=([^&\"'\s]+)", resp.text)]
    raw_urls += [m.group(0) for m in URL_RE.finditer(resp.text)]
    for raw in raw_urls:
        host = urlparse(raw).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if "." not in host or any(b in host for b in SOGOU_BAD + BAIDU_BAD):
            continue
        if _blocked("https://" + host):
            continue
        counts[host] = counts.get(host, 0) + 1
    ranked = sorted(counts, key=lambda h: counts[h], reverse=True)
    roots: list[str] = []
    for host in ranked[:6]:
        root_host = _root_of("https://" + host)
        for h in (host, f"www.{host}", root_host, f"www.{root_host}"):
            root = f"https://{h}/" if h else ""
            if root and root not in roots:
                roots.append(root)
    roots = roots[:10]
    if roots:
        _save_json(cpath, roots)
    return roots


def _so_candidates(bank: dict, use_cache: bool = True) -> list[str]:
    """360 search fallback — reachable when Baidu rate-limits us."""
    short = bank.get("short_name") or bank["name"]
    cpath = BAIDU_CACHE / f"{_cache_key(short, 'so-mobile')}.json"
    if use_cache and cpath.exists():
        return _load_json(cpath, [])
    try:
        resp = requests.get(
            "https://m.so.com/s",
            params={"q": f"{short}官网"},
            headers={"User-Agent": MOBILE_UA, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception:
        return []
    if len(resp.text) < 20_000:
        return []
    counts: dict[str, int] = {}
    for m in URL_RE.finditer(resp.text):
        host = urlparse(m.group(0)).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if "." not in host or any(b in host for b in BAIDU_BAD):
            continue
        if any(b in host for b in ("so.com", "360tres", "360kuai", "qhimg",
                                    "qhres", "360.cn", "sm.cn", "w3.org")):
            continue
        if _blocked("https://" + host):
            continue
        counts[host] = counts.get(host, 0) + 1
    ranked = sorted(counts, key=lambda h: counts[h], reverse=True)
    roots: list[str] = []
    for host in ranked[:6]:
        root_host = _root_of("https://" + host)
        for h in (host, f"www.{host}", root_host, f"www.{root_host}"):
            root = f"https://{h}/" if h else ""
            if root and root not in roots:
                roots.append(root)
    roots = roots[:10]
    if roots:
        _save_json(cpath, roots)
    return roots
BAIDU_BAD = (
    "baidu.com", "hao123.com", "bdstatic.com", "w3.org", "bcebos.com",
    "bdimg.com", "wappass", "passport", "map.baidu", "zhidao.baidu",
)
BAIDU_CACHE = CACHE_DIR / "baidu"
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def _baidu_candidates(bank: dict, use_cache: bool = True) -> list[str]:
    """Domestic-search fallback: Tavily sometimes misses Chinese bank sites."""
    short = bank.get("short_name") or bank["name"]
    cpath = BAIDU_CACHE / f"{_cache_key(short, 'baidu-mobile')}.json"
    if use_cache and cpath.exists():
        return _load_json(cpath, [])
    text = ""
    for attempt, endpoint in enumerate(("https://www.baidu.com/s", "https://m.baidu.com/s")):
        if attempt:
            time.sleep(4)
        try:
            resp = requests.get(
                endpoint,
                params={"wd": f"{short}官网"},
                headers={"User-Agent": MOBILE_UA, "Accept-Language": "zh-CN,zh;q=0.9"},
                timeout=15,
            )
            resp.raise_for_status()
        except Exception:
            continue
        if len(resp.text) >= 20_000:
            text = resp.text
            break
    if not text:
        return []
    counts: dict[str, int] = {}
    for m in re.finditer(r"官网(?:是|：|:)\s*(https?://[a-zA-Z0-9._-]+)", text):
        host = urlparse(m.group(1)).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if host and not _blocked("https://" + host):
            counts[host] = counts.get(host, 0) + 5
    for m in URL_RE.finditer(text):
        host = urlparse(m.group(0)).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if "." not in host or any(b in host for b in BAIDU_BAD):
            continue
        if _blocked("https://" + host):
            continue
        counts[host] = counts.get(host, 0) + 1
    ranked = sorted(counts, key=lambda h: counts[h], reverse=True)
    roots: list[str] = []
    for host in ranked[:6]:
        root_host = _root_of("https://" + host)
        for h in (host, f"www.{host}", root_host, f"www.{root_host}"):
            if not h:
                continue
            root = f"https://{h}/"
            if root not in roots:
                roots.append(root)
    roots = roots[:10]
    if roots:
        _save_json(cpath, roots)
    return roots


def _site_query_pages(bank: dict, home_url: str, use_cache: bool) -> list[dict]:
    """Find same-host about/business/news pages via a site: search.

    Needed for WAF-protected sites whose homepage HTML cannot be fetched, and
    cheap insurance when the homepage text is thin.
    """
    host = urlparse(home_url).hostname or ""
    short = bank.get("short_name") or bank["name"]
    out: list[dict] = []
    seen: set[str] = {home_url}
    queries = (
        (f"site:{host} {short} 简介 业务", "advanced"),
        (f"site:{host} {short} 新闻 动态 公告", "basic"),
    )
    for query, depth in queries:
        try:
            rows = _tavily_rows(query, depth, use_cache)
        except Exception:
            continue
        for row in rows:
            url = (row.get("url") or "").strip()
            if not url or url in seen or _blocked(url):
                continue
            if urlparse(url).netloc != host:
                continue
            seen.add(url)
            out.append({"url": url, "label": _clean(row.get("title") or "")})
    return out


def collect_bank(bank: dict, use_cache: bool = True, use_llm: bool = True) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        candidates = search_site_candidates(bank, use_cache=use_cache)
    except Exception as exc:
        return {"status": "search_failed", "error": type(exc).__name__, "fetched_at": now}
    override = DOMAIN_OVERRIDES.get(bank.get("short_name") or "")
    if override:
        candidates = [override] + [u for u in candidates if u != override]

    tokens = _tokens(bank)
    home = None
    tried_sets = [list(candidates)]
    for fallback in (_so_candidates, _sogou_candidates):
        extra = [u for u in fallback(bank, use_cache) if u not in candidates]
        if extra:
            tried_sets.append(extra)
    for tried in tried_sets:
        for root in tried:
            page = fetch_page(root, timeout=8, allow_extract=True)
            if not page:
                continue
            head = f"{page.get('title') or ''} {page['text']}"
            matched = any(t in head for t in tokens)
            if matched and (_bank_like(page["text"]) or page.get("via") == "tavily_extract"):
                home = page
                break
        if home:
            break
    if not home:
        return {
            "status": "no_site",
            "searched_at": now,
            "candidates": candidates,
            "note": "未找到可通过公开网络访问的银行官网。",
        }

    extra = _pick_pages(home)
    pages = [home]
    allow_extract = home.get("via") == "tavily_extract"
    seen_urls = {home["url"]}
    for item in extra:
        page = fetch_page(item["url"], allow_extract=allow_extract)
        if page and len(page["text"]) >= 80 and page["url"] not in seen_urls:
            seen_urls.add(page["url"])
            pages.append(page)

    if sum(len(p["text"]) for p in pages) < 1200:
        for item in _site_query_pages(bank, home["url"], use_cache)[:6]:
            page = fetch_page(item["url"], allow_extract=True)
            if not page or len(page["text"]) < 80 or page["url"] in seen_urls:
                continue
            seen_urls.add(page["url"])
            pages.append(page)

    recent = _recent_items(pages)
    profile = summarize_website(bank, pages, recent, use_llm=use_llm)
    sources = [{
        "kind": "官网",
        "title": page.get("title") or bank["short_name"],
        "url": page["url"],
        "fetched_at": now,
    } for page in pages]

    return {
        "status": "ok" if profile.get("features") else "no_content",
        "website": home["url"],
        "homepage_title": home.get("title") or "",
        "features": profile.get("features") or "",
        "key_business": profile.get("key_business") or [],
        "recent_moves": profile.get("recent_moves") or [],
        "recent_items": recent,
        "evidence": profile.get("evidence") or [],
        "confidence": profile.get("confidence") or "低",
        "basis": profile.get("basis") or "",
        "pages": [{"url": p["url"], "title": p.get("title") or ""} for p in pages],
        "sources": sources,
        "fetched_at": now,
        "text_chars": sum(len(p["text"]) for p in pages),
    }


def select_banks(groups: set[str], limit: int = 0) -> list[dict]:
    data = registry.load_registry()
    banks = [b for b in data["banks"] if b.get("group") in groups]
    banks.sort(key=lambda b: (b.get("asset") or 0, b.get("name") or ""), reverse=True)
    return banks if limit <= 0 else banks[:limit]


def collect(groups: set[str], limit: int = 0, force: bool = False,
            use_llm: bool = True, bank_ids: list[str] | None = None,
            shard: int = 0, shards: int = 1, verbose: bool = True) -> dict:
    data = registry.load_registry()
    by_id = {b["id"]: b for b in data["banks"]}
    if bank_ids:
        banks = [by_id[i] for i in bank_ids if i in by_id]
    else:
        banks = select_banks(groups, limit)
    if shards > 1:
        banks = banks[shard::shards]

    out_path = OUT_PATH if shards <= 1 else OUT_PATH.with_name(
        f"website.shard{shard}.json")
    store = _load_json(out_path, {})
    ok = 0
    no_site = 0
    failed = 0
    for i, bank in enumerate(banks, 1):
        existing = store.get(bank["id"]) or {}
        if existing.get("status") == "ok" and not force:
            ok += 1
            if verbose:
                print(f"[{i}/{len(banks)}] {bank['short_name']}: cached")
            continue
        try:
            item = collect_bank(bank, use_cache=not force, use_llm=use_llm)
        except Exception as exc:
            item = {
                "status": "failed",
                "error": type(exc).__name__,
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        store[bank["id"]] = item
        status = item.get("status")
        if status == "ok":
            ok += 1
        elif status == "no_site":
            no_site += 1
        else:
            failed += 1
        if verbose:
            tgt = item.get("website") or "—"
            print(f"[{i}/{len(banks)}] {bank['short_name']}: {status} · {tgt}")
        if i % 5 == 0:
            _save_json(out_path, store)
        time.sleep(0.6)

    _save_json(out_path, store)
    return {
        "banks_checked": len(banks),
        "ok": ok,
        "no_site": no_site,
        "failed": failed,
        "groups": sorted(groups),
    }


def merge_shards(shards: int) -> dict:
    merged = _load_json(OUT_PATH, {})
    for i in range(shards):
        part = _load_json(OUT_PATH.with_name(f"website.shard{i}.json"), {})
        for bank_id, item in part.items():
            current = merged.get(bank_id) or {}
            if current.get("status") == "ok" and item.get("status") != "ok":
                continue
            merged[bank_id] = item
    _save_json(OUT_PATH, merged)
    return {
        "merged": len(merged),
        "ok": sum(1 for v in merged.values() if v.get("status") == "ok"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank official-website profile agent")
    parser.add_argument("--group", default="city",
                        help="comma-separated: city,rural,village,other")
    parser.add_argument("--limit", type=int, default=0, help="0 = all in group")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--bank-id", action="append", default=[])
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--merge-shards", type=int, default=0)
    args = parser.parse_args()
    if args.merge_shards:
        print(json.dumps(merge_shards(args.merge_shards), ensure_ascii=False, indent=2))
        return
    groups = {g.strip() for g in args.group.split(",") if g.strip()}
    result = collect(groups, limit=args.limit, force=args.force,
                     use_llm=not args.no_llm, bank_ids=args.bank_id or None,
                     shard=args.shard, shards=args.shards)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
