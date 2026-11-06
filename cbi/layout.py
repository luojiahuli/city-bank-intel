"""Deterministic, geography-anchored layout for 3,800+ nodes."""
from __future__ import annotations

import hashlib
import math

import config

W = 2400
H = 1500


def _jitter(key: str, scale: float = 1.6) -> tuple[float, float]:
    h = hashlib.md5(key.encode("utf-8")).digest()
    a = (h[0] / 255.0 - 0.5) * 2 * scale
    b = (h[1] / 255.0 - 0.5) * 2 * scale
    return a, b


def province_positions() -> dict[str, tuple[float, float]]:
    pts: dict[str, tuple[float, float]] = {}
    for name, (lon, lat) in config.PROVINCE_CENTROIDS.items():
        x = 150 + (lon - 73.0) / (135.0 - 73.0) * (W - 330)
        y = 120 + (54.0 - lat) / (54.0 - 18.0) * (H - 260)
        pts[name] = (x, y)
    # A few iterations of hub repulsion keeps eastern clusters readable.
    names = list(pts)
    for _ in range(60):
        moved = False
        for i, a in enumerate(names):
            ax, ay = pts[a]
            for b in names[i + 1:]:
                bx, by = pts[b]
                dx, dy = bx - ax, by - ay
                dist = math.hypot(dx, dy) or 0.01
                if dist < 62:
                    push = (62 - dist) / 2
                    ux, uy = dx / dist, dy / dist
                    pts[a] = (ax - ux * push, ay - uy * push)
                    pts[b] = (bx + ux * push, by + uy * push)
                    moved = True
        if not moved:
            break
    return pts


def _group_order(bank: dict) -> int:
    return {"city": 0, "rural": 1, "village": 2}.get(bank.get("group"), 3)


def build_layout(banks: list[dict]) -> dict:
    hubs = province_positions()
    hubs["其他"] = (120.0, H - 90.0)
    by_province: dict[str, list[dict]] = {}
    for b in banks:
        by_province.setdefault(b.get("province") or "其他", []).append(b)

    positions: dict[str, tuple[float, float]] = {}
    for prov, group in by_province.items():
        cx, cy = hubs.get(prov, hubs["其他"])
        n = len(group)
        radius = min(112.0, 10.0 + 3.1 * math.sqrt(n))
        spacing = radius / max(1.0, math.sqrt(n + 1))
        group.sort(key=lambda b: (_group_order(b), -(b.get("asset") or 0), b["id"]))
        golden = math.pi * (3.0 - math.sqrt(5.0))
        for i, bank in enumerate(group):
            if n == 1:
                r = 0.0
            else:
                r = radius * math.sqrt((i + 0.35) / n)
            theta = i * golden
            jx, jy = _jitter(bank["id"], max(0.5, min(1.6, spacing * 0.3)))
            positions[bank["id"]] = (cx + math.cos(theta) * r + jx,
                                     cy + math.sin(theta) * r + jy)
    return {"hubs": hubs, "banks": positions, "width": W, "height": H}
