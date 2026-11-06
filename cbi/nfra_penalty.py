"""Crawl NFRA administrative penalty disclosures and attribute them to banks.

Only public NFRA endpoints are used. The crawler is resumable: list pages and
document payloads are cached under ``data/cache/nfra`` and processed document
ids are tracked in ``data/cache/nfra/state.json``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

import config
from cbi import registry

BASE = "https://www.nfra.gov.cn"
LIST_URL = (
    BASE
    + "/cn/static/data/DocInfo/SelectDocByItemIdAndChild/"
    "data_itemId={item},pageIndex={page},pageSize={size}.json"
)
DYNAMIC_LIST_URL = BASE + "/cbircweb/DocInfo/SelectDocByItemIdAndChild"
DETAIL_URL = (
    BASE + "/cn/static/data/DocInfo/SelectByDocId/data_docId={doc}.json"
)
DYNAMIC_DETAIL_URL = BASE + "/cbircweb/DocInfo/SelectByDocId"
DETAIL_PAGE = BASE + "/cn/view/pages/ItemDetail.html?docId={doc}&itemId={item}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": BASE + "/cn/view/pages/ItemList.html?itemPId=931&itemId=4113",
}

ITEM_IDS = ("4113", "4114", "4115")

CACHE_DIR = config.CACHE_DIR / "nfra"
LIST_CACHE = CACHE_DIR / "lists"
DOC_CACHE = CACHE_DIR / "docs"
STATE_PATH = CACHE_DIR / "state.json"
OUT_PATH = config.PENALTIES_DIR / "by_bank.json"
COVERAGE_PATH = config.PENALTIES_DIR / "coverage.json"

for d in (CACHE_DIR, LIST_CACHE, DOC_CACHE):
    d.mkdir(parents=True, exist_ok=True)

NON_BANK_MARKERS = (
    "保险", "人寿", "财险", "再保险", "保险经纪", "保险代理",
    "信托", "金融租赁", "融资租赁", "消费金融", "汽车金融",
    "财务有限公司", "理财有限责任公司", "基金管理", "证券公司",
    "期货", "小额贷款", "融资担保", "典当", "资产管理公司",
    "金融资产", "会计师事务所", "律师事务所",
)

BRANCH_MARKERS = ("支行", "分行", "分理处", "营业部", "办事处", "营业所", "支行营业部")
PERSON_MARKERS = (
    "员工", "时任", "工作人员", "客户经理", "部门经理", "总经理", "行长助理",
    "副行长", "行长", "主任", "负责人", "责任人", "当事人", "原员工",
)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


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


def _clean(text) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text).replace("\xa0", " ")).strip()


# ---------------------------------------------------------------------------
# fetching


def fetch_list(session: requests.Session, item: str, page: int, size: int = 18,
               use_cache: bool = True) -> dict:
    path = LIST_CACHE / f"item{item}_p{page}_s{size}.json"
    if use_cache and path.exists():
        return _load_json(path, {})
    # Static CDN mirrors only the first three pages; older pages are served by
    # the dynamic endpoint.
    candidates = []
    if page <= 3:
        candidates.append((LIST_URL.format(item=item, page=page, size=size), None))
    candidates.append((DYNAMIC_LIST_URL, {"itemId": item, "pageSize": size, "pageIndex": page}))
    last_err = None
    for attempt in range(4):
        if attempt:
            time.sleep(3 * attempt)
        for url, params in candidates:
            try:
                resp = session.get(url, params=params, timeout=30)
                payload = resp.json()
                if resp.status_code == 200 and payload.get("rptCode") == 200:
                    _save_json(path, payload)
                    return payload
                last_err = f"HTTP {resp.status_code}"
            except Exception as exc:
                last_err = exc
            time.sleep(2.0)
    raise RuntimeError(f"list fetch failed: {last_err}")


def fetch_detail(session: requests.Session, doc_id: int, use_cache: bool = True,
                 item_id: str = "") -> dict:
    path = DOC_CACHE / f"{doc_id}.json"
    if use_cache and path.exists():
        return _load_json(path, {})
    payload = None
    last_err = None
    for url, params in (
        (DETAIL_URL.format(doc=doc_id), None),
        (DYNAMIC_DETAIL_URL, {"docId": doc_id}),
    ):
        try:
            resp = session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if data.get("rptCode") == 200:
                payload = data
                break
        except Exception as exc:
            last_err = exc
    if payload is None:
        raise RuntimeError(f"detail fetch failed: {last_err}")
    if item_id:
        payload.setdefault("_item_id", item_id)
    _save_json(path, payload)
    return payload


# ---------------------------------------------------------------------------
# parsing


def parse_table_rows(html: str) -> list[dict]:
    """Extract penalty rows from a docClob HTML table.

    NFRA publishes two layouts: a horizontal multi-party table and a vertical
    key/value form for a single party.
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [_clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        if not rows:
            continue

        # Horizontal layout: header row with 当事人 + 序号 / 违法事实.
        header_idx = None
        col: dict[str, int] = {}
        for i, cells in enumerate(rows[:4]):
            joined = "".join(cells)
            if len(cells) >= 3 and "当事人" in joined and (
                "序号" in joined or "违法" in joined or "处罚内容" in joined
            ):
                header_idx = i
                for j, name in enumerate(cells):
                    if "当事人" in name or "被处罚" in name:
                        col.setdefault("party", j)
                    elif "违法" in name or "违规" in name or "事实" in name:
                        col.setdefault("violation", j)
                    elif "处罚内容" in name or "处罚决定" in name:
                        col.setdefault("penalty", j)
                    elif "机关" in name:
                        col.setdefault("agency", j)
                    elif "文号" in name or "决定书" in name:
                        col.setdefault("doc_no", j)
                break
        if header_idx is not None and "party" in col:
            for cells in rows[header_idx + 1:]:
                if not any(cells):
                    continue
                def get(key):
                    idx = col.get(key)
                    if idx is None or idx >= len(cells):
                        return ""
                    return cells[idx]
                party = get("party")
                if not party or party in {"当事人名称", "序号"}:
                    continue
                out.append({
                    "party": party,
                    "violation": get("violation"),
                    "penalty": get("penalty"),
                    "agency": get("agency"),
                    "doc_no": get("doc_no"),
                })
            continue

        # Vertical key/value layout.
        kv: dict[str, str] = {}
        for cells in rows:
            for j in range(0, len(cells) - 1, 2):
                key = cells[j].replace(" ", "")
                val = cells[j + 1]
                if key and val:
                    kv[key] = val
        party = ""
        for key, val in kv.items():
            if "当事人" in key or "被处罚机构" in key or "被处罚单位" in key:
                party = val
                break
        if not party:
            continue
        violation = ""
        penalty = ""
        agency = ""
        doc_no = ""
        for key, val in kv.items():
            if ("违法" in key or "违规" in key or "事实" in key) and not violation:
                violation = val
            elif "文号" in key and not doc_no:
                doc_no = val
            elif "处罚决定" in key and "机关" not in key and not penalty:
                penalty = val
            elif "机关" in key and not agency:
                agency = val
        out.append({
            "party": party, "violation": violation, "penalty": penalty,
            "agency": agency, "doc_no": doc_no,
        })
    return out


AMOUNT_RE = re.compile(
    r"(?:罚款|罚没款?|处罚款|没收违法所得|没收非法所得|没收)[^0-9]{0,14}"
    r"([0-9]+(?:\.[0-9]+)?)\s*(万元|万|元)"
)
TOTAL_RE = re.compile(
    r"(?:合计|共计|罚没合计|总计)[^0-9]{0,14}([0-9]+(?:\.[0-9]+)?)\s*(万元|万|元)"
)


def parse_amount_wan(text: str, subjects: list[str] | None = None) -> float | None:
    if not text:
        return None
    t = text.replace(",", "").replace("，", "").replace(" ", "")
    if subjects:
        # A single disclosure row can cover the institution, its branches and
        # responsible individuals. Keep institution clauses only.
        tokens = [s for s in subjects if s]
        chunks = [c for c in re.split(r"(?=对)", t) if c]
        inst_words = ("银行", "支行", "分行", "信用社", "营业部", "分理处", "互助社")
        fine_words = ("罚款", "罚没", "没收")
        kept = [
            c for c in chunks
            if any(tok in c for tok in tokens)
            or (any(w in c for w in inst_words) and any(w in c for w in fine_words))
        ]
        if kept:
            t = "".join(kept)
    total = None
    for m in TOTAL_RE.finditer(t):
        total = (total or 0.0) + _to_wan(float(m.group(1)), m.group(2))
    if total is not None:
        return round(total, 2)
    amounts = []
    for m in AMOUNT_RE.finditer(t):
        amounts.append(_to_wan(float(m.group(1)), m.group(2)))
    if not amounts:
        # Bare "罚款人民币912.22万元" with unusual spacing still matches above;
        # fall back to any number next to 万元 if a 罚款 keyword exists.
        if "罚" in t or "没收" in t:
            for m in re.finditer(r"([0-9]+(?:\.[0-9]+)?)\s*(万元|万|元)", t):
                amounts.append(_to_wan(float(m.group(1)), m.group(2)))
                break
    if not amounts:
        return None
    # Avoid double counting the same phrase; sum distinct components.
    return round(sum(amounts), 2)


def _to_wan(value: float, unit: str) -> float:
    if unit == "元":
        return value / 10000.0
    return value


def _extract_doc_no(title: str, row_doc_no: str) -> str:
    if row_doc_no:
        return re.sub(r"\s+", "", _clean(row_doc_no))
    m = re.search(r"[（(]?([一-龥]{0,8}金监?[一-龥]{0,6}〔\d{4}〕[^）)]*号)", title or "")
    if m:
        return re.sub(r"\s+", "", m.group(1))
    m = re.search(r"(〔\d{4}〕[^（()）\s]*号)", title or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# entity resolution


class BankResolver:
    def __init__(self, banks: list[dict]):
        self.banks = banks
        self.full: list[tuple[str, str, dict]] = []
        self.short: list[tuple[str, dict]] = []
        self.short_map: dict[str, dict] = {}
        for bank in banks:
            name = _clean(bank.get("name"))
            if not name:
                continue
            base = re.sub(r"(股份有限公司|有限责任公司|有限公司)$", "", name)
            self.full.append((name, bank, base))
            short = _clean(bank.get("short_name"))
            if short and len(short) >= 3:
                self.short_map.setdefault(short, bank)
                if len(short) >= 4:
                    self.short.append((short, bank))
        self.full.sort(key=lambda x: len(x[0]), reverse=True)
        self.short.sort(key=lambda x: len(x[0]), reverse=True)

    def resolve(self, party: str) -> dict | None:
        p = _clean(party)
        p = re.sub(r"[（(]\s*以下简称[^）)]*[）)]", "", p)
        p = re.sub(r"(及相关责任人员|相关责任人员|等\d*人)$", "", p)
        p = p.strip(" ：:")
        if not p or p in {"无", "-"}:
            return None
        if any(marker in p for marker in NON_BANK_MARKERS):
            # Bank subsidiaries (e.g. 信用卡中心) do not carry these markers.
            return None
        # Individual staff sanctions are not institution penalties.
        if any(marker in p for marker in PERSON_MARKERS):
            return None
        # Persons / committees are not banks.
        if len(p) <= 4 and not any(k in p for k in ("银行", "信用社", "互助社")):
            return None
        for full, bank, base in self.full:
            if p == full:
                return {"bank": bank, "branch": "", "confidence": 1.0, "match": "full"}
            if p.startswith(full):
                return {"bank": bank, "branch": p, "confidence": 1.0, "match": "branch"}
        for full, bank, base in self.full:
            if len(base) < 4:
                continue
            if p == base:
                return {"bank": bank, "branch": "", "confidence": 0.95, "match": "base"}
            if p.startswith(base):
                rest = p[len(base):]
                if any(m in p for m in BRANCH_MARKERS):
                    return {"bank": bank, "branch": p, "confidence": 0.92, "match": "branch"}
                # e.g. "XX银行股份有限公司营业部" already covered by full prefix.
                if rest and rest[0] in "第-—·":
                    return {"bank": bank, "branch": p, "confidence": 0.9, "match": "branch"}
        for short, bank in self.short:
            if p == short:
                return {"bank": bank, "branch": "", "confidence": 0.85, "match": "short"}
            if p.startswith(short) and any(m in p for m in BRANCH_MARKERS):
                return {"bank": bank, "branch": p, "confidence": 0.8, "match": "short_branch"}
        return None


# ---------------------------------------------------------------------------
# crawl


def _default_state(since: str) -> dict:
    return {
        "since": since,
        "updated_at": None,
        "items": {item: {"next_page": 1, "done": False, "old_pages": 0,
                         "docs_seen": 0, "docs_kept": 0} for item in ITEM_IDS},
        "processed_docs": [],
    }


def _doc_meta(row: dict) -> dict:
    return {
        "doc_id": int(row.get("docId")),
        "title": _clean(row.get("docTitle") or row.get("docSubtitle")),
        "published": _clean(row.get("publishDate"))[:10],
    }


def crawl(since: str = "", max_docs: int = 0, max_pages: int = 0,
          sleep_s: float = 1.6, items: tuple[str, ...] = ITEM_IDS,
          use_cache: bool = True, old_page_limit: int = 3,
          verbose: bool = True) -> dict:
    today = date.today()
    since = since or (today - timedelta(days=365 * 5)).isoformat()
    state = _load_json(STATE_PATH, {}) or _default_state(since)
    state.setdefault("items", {})
    state.setdefault("processed_docs", [])
    processed = set(str(x) for x in state["processed_docs"])

    banks = registry.load_registry()["banks"]
    resolver = BankResolver(banks)
    by_bank: dict[str, list] = _load_json(OUT_PATH, {})
    existing_keys = {
        (bid, r.get("source_doc_id"), r.get("party"), r.get("violation"))
        for bid, rows in by_bank.items() for r in rows
    }

    session = _session()
    seen: list[dict] = []
    scanned_docs = 0
    matched_rows = 0
    matched_docs = 0
    stopped = False

    def log(msg: str) -> None:
        if verbose:
            print(msg, flush=True)

    for item in items:
        istate = state["items"].setdefault(
            item, {"next_page": 1, "done": False, "old_pages": 0,
                   "docs_seen": 0, "docs_kept": 0})
        if istate.get("done"):
            log(f"[{item}] already done")
            continue
        page = int(istate.get("next_page", 1))
        start_page = page
        failures = 0
        while not istate.get("done"):
            if max_pages and page - start_page >= max_pages:
                log(f"[{item}] reached --max-pages at page {page}")
                break
            try:
                payload = fetch_list(session, item, page, use_cache=use_cache)
            except Exception as exc:
                failures += 1
                log(f"[{item}] list page {page} failed ({failures}/4): {exc}")
                if failures >= 4:
                    break
                time.sleep(5 * failures)
                continue
            failures = 0
            data = payload.get("data") or {}
            rows = data.get("rows") or []
            total = int(data.get("total") or 0)
            if not rows:
                istate["done"] = True
                break
            is_old_page = True
            for row in rows:
                meta = _doc_meta(row)
                if not meta["doc_id"]:
                    continue
                meta["item_id"] = item
                seen.append(meta)
                istate["docs_seen"] = istate.get("docs_seen", 0) + 1
                scored = meta["published"] >= since
                if scored:
                    is_old_page = False
                    if str(meta["doc_id"]) not in processed:
                        istate["docs_kept"] = istate.get("docs_kept", 0) + 1
            istate["old_pages"] = (istate.get("old_pages", 0) + 1) if is_old_page else 0
            pages = max(1, (total + 17) // 18)
            log(f"[{item}] page {page}/{pages} rows={len(rows)} old_page={is_old_page} queued={istate.get('docs_kept', 0)}")
            if is_old_page and istate["old_pages"] >= old_page_limit:
                istate["done"] = True
                log(f"[{item}] reached 5-year boundary; done")
                break
            if page >= pages:
                istate["done"] = True
                break
            page += 1
            istate["next_page"] = page
            _save_json(STATE_PATH, {**state, "processed_docs": sorted(processed),
                                    "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    # Fetch details for documents within the window, newest first.
    queue = [m for m in seen if m["published"] >= since and str(m["doc_id"]) not in processed]
    queue.sort(key=lambda m: (m["published"], m["doc_id"]), reverse=True)
    if max_docs:
        queue = queue[:max_docs]
    log(f"detail queue: {len(queue)} docs (since {since})")

    for i, meta in enumerate(queue, 1):
        doc_id = meta["doc_id"]
        try:
            payload = fetch_detail(session, doc_id, use_cache=use_cache,
                                   item_id=meta["item_id"])
        except Exception as exc:
            log(f"  doc {doc_id} failed: {exc}")
            time.sleep(3)
            continue
        scanned_docs += 1
        data = payload.get("data") or {}
        title = _clean(data.get("docTitle") or meta["title"])
        published = _clean(data.get("publishDate") or meta["published"])[:10]
        if published < since:
            processed.add(str(doc_id))
            continue
        rows = parse_table_rows(data.get("docClob") or "")
        doc_hits = 0
        for row in rows:
            hit = resolver.resolve(row["party"])
            if not hit:
                continue
            bank = hit["bank"]
            subjects = [
                _clean(row["party"]).replace("及相关责任人员", ""),
                bank.get("name", ""),
                re.sub(r"(股份有限公司|有限责任公司|有限公司)$", "", bank.get("name", "")),
                bank.get("short_name", ""),
            ]
            record = {
                "date": published,
                "party": _clean(row["party"]),
                "branch": hit["branch"],
                "agency": _clean(row["agency"]).replace(" ", "") or _clean(data.get("docSource")),
                "violation": _clean(row["violation"]),
                "penalty": _clean(row["penalty"]),
                "amount_wan": parse_amount_wan(row["penalty"], subjects),
                "doc_no": _extract_doc_no(title, row.get("doc_no", "")),
                "url": DETAIL_PAGE.format(doc=doc_id, item=meta["item_id"]),
                "source_doc_id": doc_id,
                "title": title,
                "match_confidence": hit["confidence"],
                "match_type": hit["match"],
            }
            key = (bank["id"], record["source_doc_id"], record["party"], record["violation"])
            if key in existing_keys:
                continue
            existing_keys.add(key)
            by_bank.setdefault(bank["id"], []).append(record)
            doc_hits += 1
            matched_rows += 1
        if doc_hits:
            matched_docs += 1
        processed.add(str(doc_id))
        if i % 25 == 0 or doc_hits:
            log(f"  {i}/{len(queue)} doc {doc_id} {published} rows={len(rows)} matched={doc_hits}")
        if not use_cache:
            time.sleep(sleep_s)
        elif i < len(queue):
            time.sleep(sleep_s)
        if i % 20 == 0:
            _save_json(STATE_PATH, {**state, "processed_docs": sorted(processed),
                                    "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            _save_json(OUT_PATH, by_bank)

    for rows in by_bank.values():
        rows.sort(key=lambda r: (r.get("date") or "", r.get("source_doc_id") or 0), reverse=True)

    state["processed_docs"] = sorted(processed)
    state["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_json(STATE_PATH, state)
    _save_json(OUT_PATH, by_bank)

    coverage = {
        "since": since,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": state["items"],
        "processed_docs": len(processed),
        "scanned_this_run": scanned_docs,
        "matched_docs_this_run": matched_docs,
        "matched_rows_this_run": matched_rows,
        "banks_with_penalties": len(by_bank),
        "records": sum(len(v) for v in by_bank.values()),
        "partial": not all(v.get("done") for v in state["items"].values()),
    }
    _save_json(COVERAGE_PATH, coverage)
    return coverage


def reparse(since: str = "", verbose: bool = True) -> dict:
    """Rebuild by_bank.json from cached detail documents (no network)."""
    state = _load_json(STATE_PATH, {}) or _default_state(since or "2021-11-07")
    since = since or state.get("since") or "2021-11-07"
    banks = registry.load_registry()["banks"]
    resolver = BankResolver(banks)
    by_bank: dict[str, list] = {}
    seen_keys: set[tuple] = set()
    docs = 0
    matched_docs = 0
    for path in sorted(DOC_CACHE.glob("*.json")):
        payload = _load_json(path, {})
        data = payload.get("data") or {}
        published = _clean(data.get("publishDate"))[:10]
        if not published or published < since:
            continue
        doc_id = int(data.get("docId") or path.stem)
        item_id = str(payload.get("_item_id") or "")
        if not item_id:
            try:
                item_id = str((data.get("listTwoItem") or [{}])[0].get("ItemID") or "")
            except Exception:
                item_id = ""
        item_id = item_id if item_id in ITEM_IDS else "4114"
        title = _clean(data.get("docTitle"))
        docs += 1
        hits = 0
        for row in parse_table_rows(data.get("docClob") or ""):
            hit = resolver.resolve(row["party"])
            if not hit:
                continue
            bank = hit["bank"]
            subjects = [
                _clean(row["party"]).replace("及相关责任人员", ""),
                bank.get("name", ""),
                re.sub(r"(股份有限公司|有限责任公司|有限公司)$", "", bank.get("name", "")),
                bank.get("short_name", ""),
            ]
            record = {
                "date": published,
                "party": _clean(row["party"]),
                "branch": hit["branch"],
                "agency": _clean(row["agency"]).replace(" ", "") or _clean(data.get("docSource")),
                "violation": _clean(row["violation"]),
                "penalty": _clean(row["penalty"]),
                "amount_wan": parse_amount_wan(row["penalty"], subjects),
                "doc_no": _extract_doc_no(title, row.get("doc_no", "")),
                "url": DETAIL_PAGE.format(doc=doc_id, item=item_id),
                "source_doc_id": doc_id,
                "title": title,
                "match_confidence": hit["confidence"],
                "match_type": hit["match"],
            }
            key = (bank["id"], record["source_doc_id"], record["party"], record["violation"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            by_bank.setdefault(bank["id"], []).append(record)
            hits += 1
        if hits:
            matched_docs += 1

    for rows in by_bank.values():
        rows.sort(key=lambda r: (r.get("date") or "", r.get("source_doc_id") or 0), reverse=True)
    _save_json(OUT_PATH, by_bank)

    coverage = _load_json(COVERAGE_PATH, {})
    coverage.update({
        "since": since,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "processed_docs": len(state.get("processed_docs") or []),
        "items": state.get("items", {}),
        "matched_docs": matched_docs,
        "banks_with_penalties": len(by_bank),
        "records": sum(len(v) for v in by_bank.values()),
        "partial": not all(v.get("done") for v in (state.get("items") or {}).values()),
    })
    _save_json(COVERAGE_PATH, coverage)
    if verbose:
        print(json.dumps(coverage, ensure_ascii=False, indent=2))
    return coverage


def resolve_name(name: str) -> dict | None:
    banks = registry.load_registry()["banks"]
    return BankResolver(banks).resolve(name)


def main() -> None:
    parser = argparse.ArgumentParser(description="NFRA penalty crawler")
    parser.add_argument("command", nargs="?", default="crawl", choices=["crawl", "resolve", "reparse"])
    parser.add_argument("--since", default="")
    parser.add_argument("--max-docs", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=1.6)
    parser.add_argument("--items", default=",".join(ITEM_IDS))
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--old-pages", type=int, default=3)
    parser.add_argument("name", nargs="?", default="")
    args = parser.parse_args()

    if args.command == "resolve":
        print(json.dumps(resolve_name(args.name), ensure_ascii=False, indent=2))
        return
    if args.command == "reparse":
        reparse(since=args.since)
        return

    items = tuple(x.strip() for x in args.items.split(",") if x.strip())
    result = crawl(
        since=args.since, max_docs=args.max_docs, max_pages=args.max_pages,
        sleep_s=args.sleep, items=items, use_cache=not args.no_cache,
        old_page_limit=args.old_pages,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
