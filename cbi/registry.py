"""Download and parse the official NFRA banking institution registry."""
from __future__ import annotations

import hashlib
import io
import json
import re
from collections import Counter
from datetime import datetime, timezone

import pdfplumber
import requests

import config
from cbi import geo

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def download_pdf(url: str | None = None, force: bool = False) -> bytes:
    path = config.REGISTRY_LOCAL_PDF
    if path.exists() and not force:
        return path.read_bytes()
    last_err = None
    for candidate in ([url] if url else []) + config.REGISTRY_PDF_CANDIDATES:
        try:
            resp = requests.get(candidate, headers=HEADERS, timeout=120)
            resp.raise_for_status()
            if not resp.content.startswith(b"%PDF"):
                raise ValueError("not a PDF")
            path.write_bytes(resp.content)
            return resp.content
        except Exception as exc:  # pragma: no cover - network dependent
            last_err = exc
    raise RuntimeError(f"unable to download registry PDF: {last_err}")


def _clean(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\n", " ")).strip()


def _is_number(value: str) -> bool:
    return bool(re.fullmatch(r"\d{1,5}", value))


def parse_pdf(data: bytes) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for raw in table:
                    if not raw or len(raw) < 5:
                        continue
                    cells = [_clean(c) for c in raw]
                    if not _is_number(cells[0]):
                        continue
                    # Columns: 序号, 中文全称, 英文全称, 机构编码, 机构类型, 监管责任单位
                    name = cells[1]
                    code = cells[3] if len(cells) > 5 else cells[2]
                    bank_type = cells[4] if len(cells) > 5 else cells[3]
                    regulator = cells[5] if len(cells) > 5 else (cells[4] if len(cells) > 4 else "")
                    en_name = cells[2] if len(cells) > 5 else ""
                    if not name or code in seen:
                        continue
                    if "中文全称" in name or "机构" in name:
                        continue
                    seen.add(code)
                    rows.append({
                        "seq": int(cells[0]),
                        "name": name,
                        "en_name": "" if en_name in {"无", "-", "None"} else en_name,
                        "code": code,
                        "type": bank_type,
                        "regulator": regulator,
                    })
    return rows


def type_group(bank_type: str) -> str:
    if bank_type == "城市商业银行":
        return "city"
    if bank_type in {"农村商业银行", "农村合作银行", "农村信用社", "农村资金互助社"}:
        return "rural"
    if bank_type == "村镇银行":
        return "village"
    return "other"


def _bank_id(code: str, name: str) -> str:
    key = code or hashlib.md5(name.encode("utf-8")).hexdigest()[:12]
    return "bk_" + re.sub(r"[^0-9a-zA-Z]", "", key).lower()


def _listed(name: str, short: str) -> str | None:
    for key, market in config.LISTED_BANKS.items():
        if key == short or key in name or key == name:
            return market
    return None


def normalize(rows: list[dict], as_of: str) -> list[dict]:
    banks: list[dict] = []
    for row in rows:
        bank_type = row["type"]
        if bank_type not in config.BANK_TYPES:
            continue
        province, city = geo.resolve_region(row["name"], row["code"], row["regulator"])
        short = geo.short_name(row["name"])
        listed = _listed(row["name"], short)
        banks.append({
            "id": _bank_id(row["code"], row["name"]),
            "code": row["code"],
            "name": row["name"],
            "en_name": row["en_name"],
            "short_name": short,
            "type": bank_type,
            "type_label": config.TYPE_LABELS.get(bank_type, bank_type),
            "group": type_group(bank_type),
            "province": province,
            "city": city,
            "regulator": row["regulator"],
            "listed": listed,
            "as_of": as_of,
            "asset": None,
            "asset_year": None,
            "revenue": None,
            "profit": None,
            "employees": None,
            "branches": None,
            "customers": None,
            "deposits": None,
            "loans": None,
            "overseas": None,
            "business_tags": [],
            "it_spend": None,
            "digital_tier": None,
            "penalty_count": 0,
            "penalty_amount": None,
            "completeness": 25 if listed else 15,
            "sources": [{
                "kind": "registry",
                "title": "国家金融监督管理总局银行业金融机构法人名单",
                "url": config.REGISTRY_PDF_CANDIDATES[0],
                "as_of": as_of,
            }],
        })
    banks.sort(key=lambda b: (b["province"], b["type"], b["name"]))
    return banks


def build_registry(url: str | None = None, force: bool = False) -> dict:
    data = download_pdf(url, force=force)
    rows = parse_pdf(data)
    if len(rows) < 1000:
        raise RuntimeError(f"registry parse produced only {len(rows)} rows")
    banks = normalize(rows, config.REGISTRY_AS_OF)
    counts = Counter(b["type"] for b in banks)
    groups = Counter(b["group"] for b in banks)
    payload = {
        "as_of": config.REGISTRY_AS_OF,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": config.REGISTRY_PDF_CANDIDATES[0],
        "total": len(banks),
        "counts": dict(counts),
        "groups": dict(groups),
        "banks": banks,
    }
    config.REGISTRY_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return payload


def load_registry() -> dict:
    if not config.REGISTRY_PATH.exists():
        return build_registry()
    return json.loads(config.REGISTRY_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    result = build_registry()
    print(json.dumps({
        "total": result["total"],
        "counts": result["counts"],
        "groups": result["groups"],
    }, ensure_ascii=False, indent=2))
