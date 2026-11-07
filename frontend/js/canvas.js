/* Canvas graph renderer: precomputed layout, LOD, quadtree hit testing. */

const VIEW_HUBS = {
  region: ["province"],
  business: ["business"],
  focus: ["focus"],
  it: ["it", "digital"],
  huawei: ["huawei"],
  governance: ["province"],
  similar: [],
};

const EDGE_STYLE = {
  region: { color: "rgba(140,163,190,0.16)", width: 0.7 },
  business: { color: "rgba(244,114,182,0.18)", width: 0.7 },
  focus: { color: "rgba(251,191,36,0.16)", width: 0.7 },
  similar: { color: "rgba(56,189,248,0.45)", width: 0.9 },
  it: { color: "rgba(96,165,250,0.15)", width: 0.65 },
  digital: { color: "rgba(167,139,250,0.13)", width: 0.65 },
  huawei: { color: "rgba(255,138,76,0.14)", width: 0.7 },
  huaweiHigh: { color: "rgba(255,138,76,0.24)", width: 0.85 },
  governance: { color: "rgba(226,232,240,0.3)", width: 0.9 },
};

const FONT = '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif';

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

function easeOutCubic(t) {
  return 1 - Math.pow(1 - t, 3);
}

export class GraphCanvas {
  constructor(canvas, handlers = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { alpha: false });
    this.onSelect = handlers.onSelect || (() => {});
    this.onHover = handlers.onHover || (() => {});
    this.onBackground = handlers.onBackground || (() => {});
    this.onZoom = handlers.onZoom || (() => {});

    this.nodes = [];
    this.banks = [];
    this.nodeById = new Map();
    this.edges = { region: [] };
    this.activeView = "region";
    this.visible = null;
    this.highlight = new Set();
    this.selectedId = null;
    this.hoverId = null;

    this.transform = { k: 0.5, x: 0, y: 0 };
    this.w = 0;
    this.h = 0;
    this.dpr = 1;
    this._quad = null;
    this._liveCount = new Map();
    this._provinceOf = new Map();
    this._dirty = true;
    this._raf = 0;
    this._anim = null;
    this._drag = null;
    this._fitted = false;

    this._bindEvents();
    if (typeof ResizeObserver !== "undefined") {
      this._ro = new ResizeObserver(() => this._resize());
      this._ro.observe(canvas.parentElement || canvas);
    } else {
      window.addEventListener("resize", () => this._resize());
    }
    this._resize();
  }

  /* ---------------- data ---------------- */

  setData(nodes, regionLinks = []) {
    this.nodes = nodes;
    this.banks = nodes.filter((n) => n.type === "bank");
    this.nodeById = new Map(nodes.map((n) => [n.id, n]));
    this._provinceNodes = nodes.filter((n) => n.type === "province");
    const byName = new Map(this._provinceNodes.map((n) => [n.name, n]));
    this._provinceOf = new Map();
    for (const bank of this.banks) {
      const prov = byName.get(bank.province) || byName.get("其他");
      if (prov) this._provinceOf.set(bank.id, prov);
    }
    this.edges.region = regionLinks;
    this._recount();
    this._rebuildQuad();
    this._fitted = false;
    this.fit();
    this._mark();
  }

  setView(view) {
    if (!VIEW_HUBS[view]) return;
    this.activeView = view;
    this._recount();
    this._rebuildQuad();
    this._mark();
  }

  setEdges(view, links) {
    this.edges[view] = Array.isArray(links) ? links : [];
    if (view === "similar") {
      const ids = new Set();
      for (const link of this.edges[view]) {
        ids.add(link.source);
        ids.add(link.target);
      }
      this._similarIds = ids;
    }
    if (view === this.activeView) {
      this._rebuildQuad();
      this._mark();
    }
  }

  setVisible(set) {
    this.visible = set || null;
    this._recount();
    this._rebuildQuad();
    this._mark();
  }

  setSelected(id) {
    this.selectedId = id || null;
    this._mark();
  }

  setHighlight(ids) {
    this.highlight = new Set(ids || []);
    this._mark();
  }

  neighbors(id, view = this.activeView) {
    const out = new Set();
    for (const link of this.edges[view] || []) {
      if (link.source === id) out.add(link.target);
      else if (link.target === id) out.add(link.source);
    }
    return [...out]
      .map((nid) => this.nodeById.get(nid))
      .filter((n) => n && this._visible(n));
  }

  get zoom() {
    return this.transform.k;
  }

  /* ---------------- visibility helpers ---------------- */

  _visible(node) {
    if (!node) return false;
    if (node.type === "bank") {
      if (!this.visible || this.visible.has(node.id)) {
        if (this.activeView === "similar" && this._similarIds) {
          return this._similarIds.has(node.id);
        }
        return true;
      }
      return false;
    }
    if (node.type === "province") {
      return (VIEW_HUBS[this.activeView] || []).includes("province") &&
        (this._liveCount.get(node.id) || 0) > 0;
    }
    return (VIEW_HUBS[this.activeView] || []).includes(node.type);
  }

  _recount() {
    const counts = new Map();
    for (const bank of this.banks) {
      if (this.visible && !this.visible.has(bank.id)) continue;
      const prov = this._provinceOf.get(bank.id);
      if (prov) counts.set(prov.id, (counts.get(prov.id) || 0) + 1);
    }
    this._liveCount = counts;
  }

  _rebuildQuad() {
    if (typeof d3 === "undefined" || !d3.quadtree) return;
    const pts = this.nodes.filter((n) => this._visible(n));
    this._quad = d3.quadtree(pts, (d) => d.x, (d) => d.y);
  }

  _mark() {
    this._dirty = true;
    if (!this._raf) this._raf = requestAnimationFrame((ts) => this._tick(ts));
  }

  _tick(ts) {
    this._raf = 0;
    if (this._anim) this._stepAnim(ts);
    if (this._dirty) {
      this._dirty = false;
      this._render();
    }
    if (this._dirty || this._anim) {
      this._raf = requestAnimationFrame((t) => this._tick(t));
    }
  }

  /* ---------------- viewport ---------------- */

  _resize() {
    const parent = this.canvas.parentElement || this.canvas;
    const rect = parent.getBoundingClientRect();
    const w = Math.max(1, Math.round(rect.width));
    const h = Math.max(1, Math.round(rect.height));
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const oldW = this.w;
    const oldH = this.h;
    this.w = w;
    this.h = h;
    this.dpr = dpr;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    if (!this._fitted) {
      this.fit();
      return;
    }
    // Keep the same world point centered when the detail panel resizes the stage.
    if (oldW && oldH && (oldW !== w || oldH !== h)) {
      const k = this.transform.k;
      const cx = (oldW / 2 - this.transform.x) / k;
      const cy = (oldH / 2 - this.transform.y) / k;
      this.transform.x = w / 2 - cx * k;
      this.transform.y = h / 2 - cy * k;
    }
    this._mark();
  }

  fit(nodes = null, { animate = false, maxK = 2.4 } = {}) {
    const pool = (nodes && nodes.length ? nodes : this.nodes.filter((n) => this._visible(n)))
      .filter((n) => Number.isFinite(n.x) && Number.isFinite(n.y));
    if (!pool.length || !this.w) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of pool) {
      minX = Math.min(minX, n.x);
      minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x);
      maxY = Math.max(maxY, n.y);
    }
    const pad = this.w < 640 ? 24 : 56;
    const bw = Math.max(1, maxX - minX);
    const bh = Math.max(1, maxY - minY);
    const k = clamp(Math.min((this.w - pad * 2) / bw, (this.h - pad * 2) / bh), 0.12, maxK);
    const tx = (this.w - bw * k) / 2 - minX * k;
    const ty = (this.h - bh * k) / 2 - minY * k;
    this._fitted = true;
    if (animate) {
      this._animateTo(k, tx, ty, 420);
    } else {
      this.transform = { k, x: tx, y: ty };
      this.onZoom(k);
      this._mark();
    }
  }

  fitToProvince(province) {
    const prov = this._provinceNodes.find((n) => n.name === province);
    if (!prov) return;
    const banks = this.nodes.filter(
      (n) => n.type === "bank" && n.province === province && this._visible(n)
    );
    this.fit([prov, ...banks], { animate: true, maxK: 2.6 });
  }

  focusNode(node, k = 2) {
    if (!node) return;
    const kk = clamp(k, 0.2, 5);
    const mobile = this.w < 768;
    const tx = this.w / 2 - node.x * kk;
    const ty = (mobile ? this.h * 0.34 : this.h / 2) - node.y * kk;
    this._animateTo(kk, tx, ty, 380, node.id);
  }

  zoomBy(factor) {
    const k = clamp(this.transform.k * factor, 0.12, 6);
    this._zoomAt(this.w / 2, this.h / 2, k);
  }

  _zoomAt(mx, my, k) {
    const t = this.transform;
    const wx = (mx - t.x) / t.k;
    const wy = (my - t.y) / t.k;
    this.transform = { k, x: mx - wx * k, y: my - wy * k };
    this.onZoom(k);
    this._mark();
  }

  _animateTo(k, x, y, dur = 380, focusId = null) {
    this._anim = {
      from: { ...this.transform },
      to: { k, x, y },
      start: performance.now(),
      dur,
      focusId,
    };
    this._mark();
  }

  _stepAnim(ts) {
    const a = this._anim;
    if (!a) return;
    const t = clamp((ts - a.start) / a.dur, 0, 1);
    const e = easeOutCubic(t);
    this.transform = {
      k: a.from.k + (a.to.k - a.from.k) * e,
      x: a.from.x + (a.to.x - a.from.x) * e,
      y: a.from.y + (a.to.y - a.from.y) * e,
    };
    if (t >= 1) {
      if (a.focusId) this.selectedId = a.focusId;
      this._anim = null;
    }
    this.onZoom(this.transform.k);
    this._dirty = true;
  }

  /* ---------------- input ---------------- */

  _localPoint(evt) {
    const rect = this.canvas.getBoundingClientRect();
    return { mx: evt.clientX - rect.left, my: evt.clientY - rect.top };
  }

  _bindEvents() {
    const cv = this.canvas;
    this._pointers = new Map();
    this._pinch = null;

    const pinchState = () => {
      const pts = [...this._pointers.values()];
      if (pts.length < 2) return null;
      const [a, b] = pts;
      return {
        dist: Math.hypot(a.x - b.x, a.y - b.y),
        cx: (a.x + b.x) / 2,
        cy: (a.y + b.y) / 2,
      };
    };

    cv.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      this._anim = null;
      this._pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

      if (this._pointers.size === 2) {
        const p = pinchState();
        this._pinch = {
          dist: Math.max(1, p.dist),
          cx: p.cx,
          cy: p.cy,
          k: this.transform.k,
          x: this.transform.x,
          y: this.transform.y,
        };
        this._drag = null;
        this.hoverId = null;
        this.onHover(null);
        cv.classList.add("dragging");
        cv.style.cursor = "grabbing";
        return;
      }
      if (this._pointers.size > 2) return;

      const hit = this._hitTest(e);
      this._drag = {
        x: e.clientX,
        y: e.clientY,
        startX: e.clientX,
        startY: e.clientY,
        tx: this.transform.x,
        ty: this.transform.y,
        moved: false,
        node: hit && hit.type === "bank" ? hit : null,
        nx: hit ? hit.x : 0,
        ny: hit ? hit.y : 0,
      };
      cv.setPointerCapture(e.pointerId);
      cv.classList.add("dragging");
      cv.style.cursor = "grabbing";
    });

    cv.addEventListener("pointermove", (e) => {
      if (this._pointers.has(e.pointerId)) {
        this._pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      }

      if (this._pinch && this._pointers.size >= 2) {
        const p = pinchState();
        if (!p) return;
        const rect = cv.getBoundingClientRect();
        const startMx = this._pinch.cx - rect.left;
        const startMy = this._pinch.cy - rect.top;
        const wx = (startMx - this._pinch.x) / this._pinch.k;
        const wy = (startMy - this._pinch.y) / this._pinch.k;
        const mx = p.cx - rect.left;
        const my = p.cy - rect.top;
        const k = clamp(this._pinch.k * (p.dist / this._pinch.dist), 0.12, 6);
        this.transform = { k, x: mx - wx * k, y: my - wy * k };
        this.onZoom(k);
        this._mark();
        return;
      }

      if (this._drag) {
        const dx = e.clientX - this._drag.startX;
        const dy = e.clientY - this._drag.startY;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) this._drag.moved = true;
        if (this._drag.moved) {
          if (this._drag.node) {
            const k = this.transform.k;
            this._drag.node.x = this._drag.nx + dx / k;
            this._drag.node.y = this._drag.ny + dy / k;
            this._rebuildQuad();
          } else {
            this.transform = {
              k: this.transform.k,
              x: this._drag.tx + dx,
              y: this._drag.ty + dy,
            };
          }
          this._mark();
        }
        return;
      }

      if (e.pointerType === "touch") return;

      const node = this._hitTest(e);
      const id = node ? node.id : null;
      if (id !== this.hoverId) {
        this.hoverId = id;
        cv.style.cursor = id ? (node.type === "bank" ? "grab" : "pointer") : "";
        this.onHover(node, e);
        this._mark();
      } else if (id) {
        this.onHover(node, e);
      }
    });

    const end = (e) => {
      this._pointers.delete(e.pointerId);

      if (this._pinch) {
        if (this._pointers.size < 2) this._pinch = null;
        try { cv.releasePointerCapture(e.pointerId); } catch (_) { /* noop */ }
        if (!this._pointers.size) {
          cv.classList.remove("dragging");
          cv.style.cursor = "";
        }
        return;
      }

      if (!this._drag) {
        if (!this._pointers.size) {
          cv.classList.remove("dragging");
          cv.style.cursor = "";
        }
        return;
      }

      const drag = this._drag;
      const wasClick = !drag.moved;
      this._drag = null;
      cv.classList.remove("dragging");
      cv.style.cursor = "";
      try { cv.releasePointerCapture(e.pointerId); } catch (_) { /* noop */ }
      if (drag.node && drag.moved) {
        this._rebuildQuad();
        this._mark();
        return;
      }
      if (wasClick) {
        const node = this._hitTest(e);
        if (node) this.onSelect(node, e);
        else this.onBackground(e);
      }
    };
    cv.addEventListener("pointerup", end);
    cv.addEventListener("pointercancel", (e) => {
      this._pointers.delete(e.pointerId);
      if (this._pointers.size < 2) this._pinch = null;
      this._drag = null;
      cv.classList.remove("dragging");
      cv.style.cursor = "";
    });

    cv.addEventListener("pointerleave", (e) => {
      if (this._drag || e.pointerType === "touch") return;
      this.hoverId = null;
      this.onHover(null);
      this._mark();
    });

    cv.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        const { mx, my } = this._localPoint(e);
        const factor = Math.exp(-e.deltaY * 0.0016);
        const k = clamp(this.transform.k * factor, 0.12, 6);
        this._zoomAt(mx, my, k);
      },
      { passive: false }
    );

    cv.addEventListener("dblclick", () => this.fit(null, { animate: true }));
  }

  _hitTest(evt) {
    if (!this._quad) return null;
    const { mx, my } = this._localPoint(evt);
    const t = this.transform;
    const wx = (mx - t.x) / t.k;
    const wy = (my - t.y) / t.k;
    const cand = this._quad.find(wx, wy, Math.max(16, 44 / t.k));
    if (!cand) return null;
    const sx = cand.x * t.k + t.x;
    const sy = cand.y * t.k + t.y;
    const dist = Math.hypot(mx - sx, my - sy);
    return dist <= Math.max(8, this._screenRadius(cand) + 5) ? cand : null;
  }

  _screenRadius(node) {
    const k = this.transform.k;
    if (node.type === "bank") return Math.max(1, (node.size || 3) * 0.65 * k);
    if (node.type === "province") {
      const n = this._liveCount.get(node.id) || 0;
      return clamp(8 + Math.sqrt(n) * 1.2, 10, 30);
    }
    return clamp(9 + (node.size || 10) * 0.25, 10, 16);
  }

  /* ---------------- render ---------------- */

  _render() {
    const { ctx, w, h, dpr } = this;
    if (!w || !h) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#070b14";
    ctx.fillRect(0, 0, w, h);

    const t = this.transform;
    const margin = 70;
    const screen = new Map();
    for (const n of this.nodes) {
      if (!this._visible(n)) continue;
      const sx = n.x * t.k + t.x;
      const sy = n.y * t.k + t.y;
      if (sx < -margin || sy < -margin || sx > w + margin || sy > h + margin) continue;
      screen.set(n.id, { sx, sy });
    }

    const k = t.k;
    if (k >= 0.13) this._drawEdges(screen, k);
    for (const n of this.nodes) {
      if (n.type === "bank" && this._visible(n)) this._drawBank(n, screen.get(n.id), k);
    }
    for (const n of this.nodes) {
      if (n.type !== "bank" && this._visible(n)) this._drawHub(n, screen.get(n.id), k);
    }
    this._drawLabels(screen, k);
  }

  _drawEdges(screen, k) {
    const { ctx } = this;
    const links = this.edges[this.activeView] || [];
    if (!links.length) return;

    const isHuawei = this.activeView === "huawei";
    const buckets = new Map();
    for (const link of links) {
      const a = screen.get(link.source);
      const b = screen.get(link.target);
      if (!a || !b) continue;
      let styleKey = this.activeView;
      if (this.activeView === "it") {
        styleKey = String(link.target).startsWith("dig_") ? "digital" : "it";
      }
      let style = EDGE_STYLE[styleKey] || EDGE_STYLE.region;
      if (isHuawei && (link.score || 0) >= 62) style = EDGE_STYLE.huaweiHigh;
      let path = buckets.get(style);
      if (!path) {
        path = new Path2D();
        buckets.set(style, path);
      }
      path.moveTo(a.sx, a.sy);
      path.lineTo(b.sx, b.sy);
    }
    for (const [style, path] of buckets) {
      ctx.strokeStyle = style.color;
      ctx.lineWidth = style.width;
      ctx.stroke(path);
    }

    if (this.highlight.size && this.selectedId) {
      const lit = new Path2D();
      for (const link of links) {
        const touchesSel = link.source === this.selectedId || link.target === this.selectedId;
        const other = link.source === this.selectedId ? link.target : link.source;
        if (!touchesSel || !this.highlight.has(other)) continue;
        const a = screen.get(link.source);
        const b = screen.get(link.target);
        if (!a || !b) continue;
        lit.moveTo(a.sx, a.sy);
        lit.lineTo(b.sx, b.sy);
      }
      ctx.strokeStyle = this.activeView === "huawei"
        ? "rgba(255,168,112,0.72)"
        : "rgba(196,214,236,0.62)";
      ctx.lineWidth = 1.35;
      ctx.stroke(lit);
    }
  }

  _drawBank(node, pos, k) {
    if (!pos) return;
    const { ctx } = this;
    const r = Math.max(1, (node.size || 3) * 0.65 * k);
    const alpha = k < 0.22 ? 0.62 : 0.94;
    ctx.globalAlpha = alpha;
    ctx.fillStyle = node.color || "#94a3b8";
    if (r < 1.5) {
      ctx.fillRect(pos.sx - r, pos.sy - r, r * 2, r * 2);
    } else {
      ctx.beginPath();
      ctx.arc(pos.sx, pos.sy, r, 0, Math.PI * 2);
      ctx.fill();
      if (r > 4.2) {
        ctx.strokeStyle = "rgba(8,13,23,0.65)";
        ctx.lineWidth = 0.8;
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;

    if (this.highlight.has(node.id)) {
      ctx.beginPath();
      ctx.arc(pos.sx, pos.sy, r + 2.6, 0, Math.PI * 2);
      ctx.strokeStyle = "rgba(226,240,255,0.55)";
      ctx.lineWidth = 1;
      ctx.stroke();
    }
    if (node.id === this.selectedId || node.id === this.hoverId) {
      ctx.beginPath();
      ctx.arc(pos.sx, pos.sy, r + 4.2, 0, Math.PI * 2);
      ctx.strokeStyle = node.id === this.selectedId ? "#ffffff" : "rgba(255,255,255,0.75)";
      ctx.lineWidth = node.id === this.selectedId ? 1.8 : 1.1;
      ctx.stroke();
    }
  }

  _drawHub(node, pos, k) {
    if (!pos) return;
    const { ctx } = this;
    const r = this._screenRadius(node);
    const selected = node.id === this.selectedId;
    const hovered = node.id === this.hoverId;

    ctx.beginPath();
    ctx.arc(pos.sx, pos.sy, r, 0, Math.PI * 2);
    ctx.fillStyle = withAlpha(node.color || "#94a3b8", node.type === "province" ? 0.18 : 0.2);
    ctx.fill();
    ctx.lineWidth = selected ? 2 : 1.1;
    ctx.strokeStyle = selected
      ? "#ffffff"
      : hovered
        ? withAlpha(node.color || "#94a3b8", 0.95)
        : withAlpha(node.color || "#94a3b8", 0.62);
    ctx.stroke();

    if (node.type === "province") {
      const n = this._liveCount.get(node.id) || 0;
      if (r >= 11) {
        ctx.fillStyle = "rgba(226,235,247,0.86)";
        ctx.font = `600 ${Math.min(12, Math.max(8, r * 0.46))}px ${FONT}`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(String(n), pos.sx, pos.sy);
      }
    } else {
      ctx.beginPath();
      ctx.arc(pos.sx, pos.sy, Math.max(2, r * 0.32), 0, Math.PI * 2);
      ctx.fillStyle = withAlpha(node.color || "#94a3b8", 0.95);
      ctx.fill();
    }
  }

  _drawLabels(screen, k) {
    const { ctx, w, h } = this;
    ctx.textBaseline = "middle";
    ctx.lineJoin = "round";
    const placed = [];

    const fits = (box, force) => {
      if (force) return true;
      return !placed.some(
        (p) => box.left < p.right + 2 && box.right > p.left - 2 &&
          box.top < p.bottom + 2 && box.bottom > p.top - 2
      );
    };
    const place = (text, x, y, align, force, style) => {
      ctx.font = style.font;
      const tw = ctx.measureText(text).width;
      const left = align === "center" ? x - tw / 2 : align === "right" ? x - tw : x;
      const box = { left, right: left + tw, top: y - 7, bottom: y + 7 };
      if (!fits(box, force)) return false;
      placed.push(box);
      ctx.textAlign = align;
      ctx.textBaseline = "middle";
      ctx.strokeStyle = "rgba(7,11,20,0.92)";
      ctx.lineWidth = 3.2;
      ctx.strokeText(text, x, y);
      ctx.fillStyle = style.fill;
      ctx.fillText(text, x, y);
      return true;
    };

    // Hubs, largest province clusters first, so important labels win collisions.
    const hubs = this.nodes
      .filter((n) => n.type !== "bank" && this._visible(n) && screen.has(n.id))
      .sort((a, b) => {
        const ca = a.type === "province" ? this._liveCount.get(a.id) || 0 : a.size || 0;
        const cb = b.type === "province" ? this._liveCount.get(b.id) || 0 : b.size || 0;
        return cb - ca;
      });
    for (const node of hubs) {
      const pos = screen.get(node.id);
      const r = this._screenRadius(node);
      let label = node.name;
      if (node.type === "province") {
        label = `${node.name} ${this._liveCount.get(node.id) || 0}`;
      } else if (node.short) {
        label = node.short;
      }
      const align = node.type === "it" || node.type === "digital"
        ? "left"
        : node.type === "huawei" ? "right" : "center";
      let x = pos.sx;
      let y = pos.sy;
      if (node.type === "it" || node.type === "digital") {
        x = pos.sx + r + 8;
      } else if (node.type === "huawei") {
        x = pos.sx - r - 8;
      } else if (node.type === "business") {
        y = pos.sy + r + 13;
      } else {
        y = pos.sy + r + 12;
        if (y > h - 24) y = pos.sy - r - 11;
      }
      const fill = node.type === "huawei"
        ? "rgba(255,184,140,0.92)"
        : node.type === "province" ? "rgba(196,209,226,0.88)" : withAlpha(node.color || "#94a3b8", 0.92);
      const focused = node.id === this.hoverId || node.id === this.selectedId;
      place(label, x, y, align, focused, { font: `600 11px ${FONT}`, fill });
    }

    // Bank labels only for higher zoom or focus.
    const showBanks = k >= 1.25;
    if (!showBanks && !this.hoverId && !this.selectedId) return;
    const bankNodes = this.banks
      .filter((n) => this._visible(n) && screen.has(n.id))
      .sort((a, b) => (b.asset || b.size || 0) - (a.asset || a.size || 0));
    for (const node of bankNodes) {
      const focused = node.id === this.hoverId || node.id === this.selectedId;
      const labelled =
        node.group === "other" || !!node.listed || node.asset ||
        (node.size || 0) >= 6 || this.highlight.has(node.id);
      if (!focused && !(showBanks && labelled)) continue;
      const pos = screen.get(node.id);
      if (pos.sx < 24 || pos.sy < 16 || pos.sx > w - 24 || pos.sy > h - 14) continue;
      let label = node.name;
      if (label.length > 13) label = label.slice(0, 12) + "…";
      const y = pos.sy + Math.max(7, this._screenRadius(node) + 7);
      place(label, pos.sx, y, "center", focused, {
        font: `500 10px ${FONT}`,
        fill: focused ? "#ffffff" : "rgba(212,224,239,0.82)",
      });
    }
  }
}

/* ---------------- helpers ---------------- */

function withAlpha(hex, alpha) {
  if (!hex || hex[0] !== "#") return `rgba(148,163,184,${alpha})`;
  const v = hex.length === 4
    ? hex.slice(1).split("").map((c) => c + c).join("")
    : hex.slice(1);
  const n = parseInt(v, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}

