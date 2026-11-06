"""Geographic normalization: institution codes, regulator names, bank names."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import requests

import config

PROVINCE_SUFFIXES = ("省", "市", "自治区", "特别行政区")
PROVINCE_ALIASES = {
    "北京": "北京市", "天津": "天津市", "上海": "上海市", "重庆": "重庆市",
    "内蒙古": "内蒙古自治区", "广西": "广西壮族自治区", "西藏": "西藏自治区",
    "宁夏": "宁夏回族自治区", "新疆": "新疆维吾尔自治区",
    "香港": "香港特别行政区", "澳门": "澳门特别行政区",
}


def normalize_province(name: str | None) -> str | None:
    if not name:
        return None
    name = name.strip()
    if name in config.PROVINCE_CENTROIDS:
        return name
    for alias, full in PROVINCE_ALIASES.items():
        if name.startswith(alias):
            return full
    for full in config.PROVINCE_CENTROIDS:
        stem = full
        for suffix in PROVINCE_SUFFIXES:
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        if name.startswith(stem):
            return full
    return None


def _strip_city_suffix(name: str) -> str:
    for suffix in ("市辖区", "地区", "自治州", "盟", "市", "县", "区"):
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


@lru_cache(maxsize=1)
def load_pcas() -> dict:
    """Load province/city/area tree, caching locally."""
    path = config.PCAS_PATH
    if not path.exists():
        resp = requests.get(config.PCAS_URL, timeout=60)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def code_index() -> dict[str, tuple[str, str | None]]:
    """Map 6-digit administrative code -> (province, city)."""
    out: dict[str, tuple[str, str | None]] = {}
    for prov in load_pcas():
        pname = prov["name"]
        out[prov["code"]] = (pname, None)
        out[prov["code"][:2] + "0000"] = (pname, None)
        for city in prov.get("children") or []:
            cname = city["name"]
            out[city["code"]] = (pname, cname)
            # Some institution codes use the 4-digit city prefix + 00.
            out[city["code"][:4] + "00"] = (pname, cname)
            for area in city.get("children") or []:
                out[area["code"]] = (pname, cname)
    return out


@lru_cache(maxsize=1)
def city_to_province() -> dict[str, str]:
    out: dict[str, str] = {}
    for prov in load_pcas():
        pname = prov["name"]
        for city in prov.get("children") or []:
            out[city["name"]] = pname
            out[_strip_city_suffix(city["name"])] = pname
    return out


def province_from_code(code: str | None) -> tuple[str | None, str | None]:
    if not code:
        return None, None
    digits = re.sub(r"\D", "", code)
    candidates = []
    if len(digits) >= 6:
        # Institution code layout: B0340H 3 130600 001 -> region starts at index 7.
        if "H" in code:
            after = code.split("H", 1)[1]
            ad = re.sub(r"\D", "", after)
            if len(ad) >= 7:
                candidates.append(ad[1:7])
            if len(ad) >= 6:
                candidates.append(ad[:6])
        candidates.append(digits[:6])
    for cand in candidates:
        hit = code_index().get(cand)
        if hit:
            return hit
        # Try 4-digit city prefix
        hit = code_index().get(cand[:4] + "00")
        if hit:
            return hit
    return None, None


def province_from_regulator(regulator: str | None) -> tuple[str | None, str | None]:
    if not regulator:
        return None, None
    text = regulator.replace("国家金融监督管理总局", "").replace("金融监管局", "").replace(
        "金融监管分局", "").replace("监管局", "").replace("监管分局", "").strip()
    if not text or "总局" in regulator:
        return None, None
    prov = normalize_province(text)
    if prov:
        return prov, None
    # A city-level regulator: match the longest known city name.
    for city, p in sorted(city_to_province().items(), key=lambda kv: -len(kv[0])):
        if len(city) >= 2 and city in text:
            return p, city
    return None, None


def province_from_name(name: str) -> tuple[str | None, str | None]:
    prov = normalize_province(name)
    if prov:
        return prov, None
    best = None
    for city, p in sorted(city_to_province().items(), key=lambda kv: -len(kv[0])):
        if len(city) >= 2 and name.startswith(city):
            best = (p, city)
            break
    return best or (None, None)


def resolve_region(name: str, code: str | None, regulator: str | None) -> tuple[str, str | None]:
    for fn in (
        lambda: province_from_code(code),
        lambda: province_from_regulator(regulator),
        lambda: province_from_name(name),
    ):
        prov, city = fn()
        if prov:
            if not city:
                # Try to recover a city from the bank name.
                _, c2 = province_from_name(name)
                city = c2
            return prov, city
    return "其他", None


SHORT_SUFFIXES = (
    "股份有限公司", "有限责任公司", "有限公司", "农村商业银行", "农商银行",
    "城市商业银行", "村镇银行", "农村合作银行", "农村信用合作社", "农村信用社",
)


def short_name(full_name: str) -> str:
    name = full_name.strip()
    for suffix in ("股份有限公司", "有限责任公司", "有限公司"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    name = name.replace("农村商业银行", "农商行").replace("农村合作银行", "农合行")
    name = name.replace("农村信用合作社", "农信社").replace("农村信用社", "农信社")
    return name.strip()
