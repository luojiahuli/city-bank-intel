/* App orchestration: data loading, filters, views, search and panel wiring. */

import { GraphCanvas } from "./canvas.js?v=20261112a";
import { DetailPanel } from "./detail.js?v=20261112a";
import { BankSearch } from "./search.js?v=20261112a";
import { ConclusionModal } from "./insight.js?v=20261112a";

const GROUPS = [
  { key: "city", label: "城商行", color: "#4cc9f0" },
  { key: "rural", label: "农商行 / 农信社", color: "#6ee7a8" },
  { key: "village", label: "村镇银行", color: "#f4c95d" },
  { key: "other", label: "其他银行", color: "#b39ddb" },
];

const VIEW_LABEL = {
  region: "区域",
  business: "主要业务",
  focus: "重点产业",
  it: "IT 投入",
  huawei: "华为机会",
  governance: "董监高",
  similar: "相似银行",
};

const VIEW_NOTE = {
  region: "按省份聚合的机构分布；节点大小表示资产规模，未披露时按机构类型示意。",
  business: "业务边为按机构类型的结构性推断，待年报与官网披露补充后替换。",
  focus: "重点产业优先采用该行公开报道原文；无报道时按所在省份产业结构推断并标注“区域推断”。",
  it: "IT 投入与 AI 活跃度均来自公开披露；未披露的银行归入对应“未披露”分组。",
  huawei: "华为机会评分由规则分与公开证据组成；无公开证据时标注“推断/待验证”。",
  governance: "董监高名单与“共享董监高”关系线正在采集，计划优先覆盖 123 家城商行。",
  similar: "仅连接同时披露资产与营收、且至少两项财务指标可比的银行；规模比 0.05–20，近似度≥0.93。",
};

const state = {
  manifest: null,
  graph: null,
  nodes: [],
  banks: [],
  bankById: new Map(),
  nodeById: new Map(),
  canvas: null,
  detail: null,
  searchBox: null,
  drawer: null,
  visible: null,
  view: "region",
  edges: { region: [] },
  edgeLoaded: { region: true },
  filters: {
    // 中小银行本体：城商行 / 农商行 / 村镇银行。股份行、国有大行等在「其他银行」中默认关闭。
    groups: new Set(["city", "rural", "village"]),
    province: "",
    asset: "",
    profit: "",
    penalty: "",
    opp: 0,
    listed: false,
    siteOnly: false,
    allHuawei: false,
  },
};

const $ = (id) => document.getElementById(id);

const MOBILE_QUERY = window.matchMedia("(max-width: 768px)");
const TABLET_QUERY = window.matchMedia("(max-width: 1024px)");
const TOUCH_QUERY = window.matchMedia("(pointer: coarse)");

async function getJSON(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return res.json();
}

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtNum(v) {
  if (v == null || Number.isNaN(Number(v))) return "未披露";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(Number(v));
}

/* ---------------- boot ---------------- */

async function boot() {
  applyDeviceMode();
  bindStaticControls();
  try {
    $("loadingSub").textContent = "读取机构名录与关系数据…";
    const [manifest, graph] = await Promise.all([
      getJSON("data/manifest.json"),
      getJSON("data/graph.json"),
    ]);
    state.manifest = manifest;
    state.graph = graph;
    state.nodes = graph.nodes;
    state.banks = graph.nodes.filter((n) => n.type === "bank");
    state.bankById = new Map(state.banks.map((n) => [n.id, n]));
    state.nodeById = new Map(graph.nodes.map((n) => [n.id, n]));
    state.edges.region = graph.links;

    buildStats(manifest);
    buildSidebar(manifest);
    buildCoverage(manifest);
    buildLegend();
    setFooter(manifest);

    $("loadingSub").textContent = "初始化画布与图谱索引…";
    initCanvas();
    initDetail();
    getJSON("data/china.json")
      .then((geo) => state.canvas.setMap(geo))
      .catch(() => {});

    applyFilters();
    showView("region", { initial: true });
    hideLoading();

    // search.json is ~1.4 MB; loading it must not block the first paint.
    // The search box activates on arrival instead.
    getJSON("data/search.json")
      .then((searchItems) => {
        state.searchBox = new BankSearch($("searchInput"), $("searchResults"), searchItems, (item) => {
          const node = state.bankById.get(item.id);
          if (node) focusBank(node, true);
        }, (item) => state.filters.groups.has(item.group));
      })
      .catch(() => {});
  } catch (err) {
    console.error(err);
    const title = document.querySelector(".loading-title");
    if (title) title.textContent = "数据加载失败";
    const sub = $("loadingSub");
    if (sub) sub.textContent = `${err.message}。请确认通过本地 HTTP 服务访问，并已运行数据构建脚本。`;
  }
}

function initInsight() {
  state.insight = new ConclusionModal();
  state.insight.bind();
  state.insight.prefetch();
}

function initCanvas() {
  state.canvas = new GraphCanvas($("graphCanvas"), {
    onSelect: handleSelect,
    onHover: handleHover,
    onBackground: () => {
      hideTooltip();
      state.canvas.setSelected(null);
      state.canvas.setHighlight([]);
      updateHud();
    },
    onZoom: () => updateHud(),
  });
  state.canvas.setData(state.nodes, state.graph.links);
}

function initDetail() {
  state.detail = new DetailPanel({
    panel: $("detailPanel"),
    toggle: $("panelToggle"),
    kicker: $("panelKicker"),
    title: $("panelTitle"),
    sub: $("panelSub"),
    badges: $("panelBadges"),
    tabs: $("panelTabs"),
    body: $("panelBody"),
    onBankLink: (id) => {
      const node = state.bankById.get(id);
      if (node) focusBank(node, true);
    },
  });
}

function applyDeviceMode() {
  const root = document.documentElement;
  const sync = () => {
    root.classList.toggle("is-mobile", MOBILE_QUERY.matches);
    root.classList.toggle("is-touch", TOUCH_QUERY.matches);
  };
  sync();
  [MOBILE_QUERY, TOUCH_QUERY].forEach((q) => {
    if (q.addEventListener) q.addEventListener("change", sync);
  });
}

function setupDrawer() {
  const btn = $("filterBtn");
  const sidebar = $("sidebar");
  const backdrop = $("drawerBackdrop");
  if (!btn || !sidebar || !backdrop) return null;
  const setOpen = (open) => {
    sidebar.classList.toggle("open", open);
    backdrop.classList.toggle("show", open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  };
  btn.setAttribute("aria-expanded", "false");
  btn.addEventListener("click", () => setOpen(!sidebar.classList.contains("open")));
  backdrop.addEventListener("click", () => setOpen(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") setOpen(false);
  });
  return { close: () => setOpen(false) };
}

function bindStaticControls() {
  state.drawer = setupDrawer();
  $("fitBtn").addEventListener("click", () => state.canvas && state.canvas.fit(null, { animate: true }));
  $("zoomIn").addEventListener("click", () => state.canvas && state.canvas.zoomBy(1.3));
  $("zoomOut").addEventListener("click", () => state.canvas && state.canvas.zoomBy(1 / 1.3));
  $("zoomFit").addEventListener("click", () => state.canvas && state.canvas.fit(null, { animate: true }));

  document.querySelectorAll(".view-tab").forEach((tab) => {
    tab.addEventListener("click", () => showView(tab.dataset.view));
  });

  $("resetFilters").addEventListener("click", () => {
    resetFilters(false);
    if (state.drawer && TABLET_QUERY.matches) state.drawer.close();
    if (state.canvas) state.canvas.fit(null, { animate: true });
  });

  ["provinceFilter", "assetFilter", "profitFilter", "penaltyFilter", "oppFilter"].forEach((id) => {
    $(id).addEventListener("change", () => {
      readFilters();
      applyFilters();
      if (id === "provinceFilter" && state.filters.province && state.canvas) {
        state.canvas.fitToProvince(state.filters.province);
      }
      if (id === "provinceFilter" && state.drawer && TABLET_QUERY.matches) {
        state.drawer.close();
      }
    });
  });

  $("listedOnly").addEventListener("change", () => {
    readFilters();
    applyFilters();
  });

  $("siteOnly").addEventListener("change", () => {
    readFilters();
    applyFilters();
  });

  $("allHuawei").addEventListener("change", () => {
    readFilters();
    if (state.view === "huawei" && state.canvas) {
      state.canvas.setEdges("huawei", edgeLinksFor("huawei"));
    }
    applyFilters();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hideTooltip();
  });
}

/* ---------------- sidebar ---------------- */

function buildStats(m) {
  const listed = m.coverage ? m.coverage.listed : 0;
  const chips = [
    [m.total, "家法人银行"],
    [m.counts.group.city || 0, "城商行"],
    [m.counts.group.rural || 0, "农商行"],
    [m.counts.group.village || 0, "村镇银行"],
    [listed, "上市银行"],
    [m.total, "当前可见", true],
  ];
  $("topStats").innerHTML = chips
    .map(([value, label, highlight]) => {
      const id = label === "当前可见" ? ' id="visibleStat"' : "";
      return `<div class="stat-chip${highlight ? " highlight" : ""}"><b${id}>${fmtNum(value)}</b><span>${esc(label)}</span></div>`;
    })
    .join("");
}

function buildSidebar(m) {
  const counts = m.counts.group;
  $("groupFilters").innerHTML = GROUPS.map((g) => {
    const on = state.filters.groups.has(g.key);
    return `<div class="filter-row${on ? " active" : ""}" data-group="${g.key}" role="checkbox" aria-checked="${on}">
      <span class="dot" style="background:${g.color}"></span>${esc(g.label)}
      <span class="count">${counts[g.key] || 0}</span>
    </div>`;
  }).join("");
  $("groupFilters").addEventListener("click", (e) => {
    const row = e.target.closest("[data-group]");
    if (!row) return;
    const key = row.dataset.group;
    if (state.filters.groups.has(key)) state.filters.groups.delete(key);
    else state.filters.groups.add(key);
    row.classList.toggle("active", state.filters.groups.has(key));
    row.setAttribute("aria-checked", state.filters.groups.has(key) ? "true" : "false");
    applyFilters();
  });

  const provinces = Object.entries(m.counts.province || {}).sort((a, b) => {
    if (a[0] === "其他") return 1;
    if (b[0] === "其他") return -1;
    return a[0].localeCompare(b[0], "zh-Hans-CN");
  });
  $("provinceFilter").innerHTML =
    `<option value="">全部省份（${m.total}）</option>` +
    provinces.map(([name, count]) => `<option value="${esc(name)}">${esc(name)}（${count}）</option>`).join("");

  const note = m.disclaimer || "";
  $("dataNote").textContent = `数据基准日 ${m.registry_as_of || "—"}，覆盖 ${m.total} 家持牌银行法人。2025 年末官方口径 3,619 家包含非银行金融机构，本图谱仅保留银行法人。${note}`;
}

function buildCoverage(m) {
  const cov = m.coverage || {};
  const rows = [
    ["资产规模", "with_asset"],
    ["营业收入", "with_revenue"],
    ["净利润", "with_profit"],
    ["员工数", "with_employees"],
    ["上市标识", "listed"],
    ["新闻", "with_news"],
    ["官网画像", "with_site_profile"],
    ["处罚采集", "with_penalties"],
    ["董监高", "with_governance"],
  ];
  $("coverageBars").innerHTML = rows
    .map(([label, key]) => {
      const value = cov[key] || 0;
      const pct = m.total ? Math.round((value / m.total) * 1000) / 10 : 0;
      return `<div class="cov-row"><span>${esc(label)}</span>
        <div class="cov-track"><div class="cov-fill" style="width:${Math.min(100, pct)}%"></div></div>
        <b>${pct}%</b></div>`;
    })
    .join("");
}

function buildLegend() {
  const items = [
    ...GROUPS,
    { key: "province", label: "区域枢纽", color: "#94a3b8" },
    { key: "business", label: "业务枢纽", color: "#f472b6" },
    { key: "focus", label: "重点产业枢纽", color: "#fbbf24" },
    { key: "it", label: "IT / 数字化枢纽", color: "#60a5fa" },
    { key: "huawei", label: "华为能力枢纽", color: "#ff8a4c" },
    { key: "similar", label: "相似银行关系", color: "#38bdf8" },
  ];
  $("legendList").innerHTML = items
    .map(
      (item) => `<div class="legend-item"><span class="dot" style="background:${item.color}"></span>${esc(item.label)}</div>`
    )
    .join("");
}

function setFooter(m) {
  $("footerNote").textContent = m.disclaimer || "公开信息整理，不构成投资建议。";
  const built = m.generated_at ? String(m.generated_at).slice(0, 10) : "";
  $("footerStamp").textContent = `数据基准日 ${m.registry_as_of || "—"}${built ? ` · 构建 ${built}` : ""}`;
}

/* ---------------- filters ---------------- */

function readFilters() {
  const f = state.filters;
  f.province = $("provinceFilter").value;
  f.asset = $("assetFilter").value;
  f.profit = $("profitFilter").value;
  f.penalty = $("penaltyFilter").value;
  f.opp = Number($("oppFilter").value) || 0;
  f.listed = $("listedOnly").checked;
  f.siteOnly = $("siteOnly").checked;
  f.allHuawei = $("allHuawei").checked;
}

function resetFilters(silent) {
  const f = state.filters;
  f.groups = new Set(["city", "rural", "village"]);
  f.province = "";
  f.asset = "";
  f.profit = "";
  f.penalty = "";
  f.opp = 0;
  f.listed = false;
  f.siteOnly = false;
  f.allHuawei = false;
  document.querySelectorAll("#groupFilters .filter-row").forEach((row) => {
    const on = f.groups.has(row.dataset.key);
    row.classList.toggle("active", on);
    row.setAttribute("aria-checked", on ? "true" : "false");
  });
  ["provinceFilter", "assetFilter", "profitFilter", "penaltyFilter"].forEach((id) => {
    $(id).value = "";
  });
  $("oppFilter").value = "0";
  $("listedOnly").checked = false;
  $("siteOnly").checked = false;
  $("allHuawei").checked = false;
  if (state.view === "huawei" && state.canvas) {
    state.canvas.setEdges("huawei", edgeLinksFor("huawei"));
  }
  if (!silent) applyFilters();
}

function applyFilters() {
  const f = state.filters;
  const visible = new Set();
  for (const bank of state.banks) {
    if (!f.groups.has(bank.group)) continue;
    if (f.province && bank.province !== f.province) continue;
    if (f.asset && bank.asset_tier !== f.asset) continue;
    if (f.profit && bank.profit_status !== f.profit) continue;
    if (f.penalty === "yes" && !(bank.penalty_count > 0)) continue;
    if (f.penalty === "collected" && !bank.penalty_checked) continue;
    if (f.penalty === "no" && bank.penalty_checked) continue;
    if (f.opp && (bank.top_opportunity || 0) < f.opp) continue;
    if (f.listed && !bank.listed) continue;
    if (f.siteOnly && !bank.has_site) continue;
    visible.add(bank.id);
  }
  state.visible = visible;
  if (state.canvas) state.canvas.setVisible(visible);
  const stat = $("visibleStat");
  if (stat) stat.textContent = fmtNum(visible.size);
  updateHud();
}

/* ---------------- views & edges ---------------- */

async function loadEdges(view) {
  const data = await getJSON(`data/edges/${view}.json`);
  return data.links || [];
}

function edgeLinksFor(view) {
  if (view !== "huawei") return state.edges[view] || [];
  const raw = state.edges.huawei || [];
  if (state.filters.allHuawei) return raw;
  const best = new Map();
  for (const link of raw) {
    const prev = best.get(link.source);
    if (!prev || (link.score || 0) > (prev.score || 0)) best.set(link.source, link);
  }
  return [...best.values()];
}

function showView(view, { initial = false } = {}) {
  if (!VIEW_LABEL[view] || !state.canvas) return;
  state.view = view;
  document.querySelectorAll(".view-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.view === view);
  });
  state.canvas.setView(view);
  if (view === "region") {
    state.canvas.setEdges("region", state.graph.links);
    setViewMessage(viewNote(view));
  } else if (state.edgeLoaded[view]) {
    state.canvas.setEdges(view, edgeLinksFor(view));
    setViewMessage(viewNote(view));
  } else {
    setViewMessage("正在加载关系数据…");
    loadEdges(view)
      .then((links) => {
        state.edges[view] = links;
        state.edgeLoaded[view] = true;
        if (state.view !== view) return;
        state.canvas.setEdges(view, edgeLinksFor(view));
        if (view === "similar") state.canvas.fit(null, { animate: true });
        setViewMessage(viewNote(view));
        updateHud();
      })
      .catch((err) => {
        console.error(err);
        if (state.view !== view) return;
        setViewMessage("关系数据加载失败，请刷新页面重试。");
      });
  }
  state.canvas.fit(null, { animate: !initial });
  updateHud();
}

function viewNote(view) {
  if (view === "governance") {
    const hasEdges = (state.edges.governance || []).length > 0;
    return hasEdges ? "" : VIEW_NOTE.governance;
  }
  return VIEW_NOTE[view] || "";
}

function setViewMessage(text) {
  const el = $("viewMessage");
  if (!el) return;
  el.textContent = text || "";
  el.classList.toggle("show", Boolean(text));
}

/* ---------------- selection & hover ---------------- */

function handleSelect(node) {
  if (!node) return;
  hideTooltip();
  state.canvas.setSelected(node.id);
  if (node.type === "bank") {
    state.canvas.setHighlight([]);
    state.detail.showBank(node);
  } else {
    const neighbors = state.canvas.neighbors(node.id);
    const ranked = sortConnections(node, neighbors);
    const connections = ranked.slice(0, 60);
    state.canvas.setHighlight(neighbors.map((n) => n.id));
    state.detail.showHub(node, connections, ranked.length);
  }
  updateHud();
}

function sortConnections(hub, neighbors) {
  const links = state.canvas.edges[state.view] || [];
  const scores = new Map();
  if (hub.type === "huawei") {
    for (const link of links) {
      if (link.target === hub.id) scores.set(link.source, link);
    }
  }
  const decorated = neighbors.map((n) => {
    let meta = [n.type_label, n.city || n.province].filter(Boolean).join(" · ");
    if (hub.type === "huawei" && scores.has(n.id)) {
      const link = scores.get(n.id);
      meta = `机会分 ${link.score} · ${link.confidence || "推断/待验证"}`;
    }
    return { node: n, meta, weight: scores.get(n.id)?.score || n.asset || n.size || 0 };
  });
  decorated.sort((a, b) => b.weight - a.weight);
  return decorated;
}

function focusBank(node, refit) {
  if (!state.visible || !state.visible.has(node.id)) {
    resetFilters(true);
    applyFilters();
  }
  if (refit && state.canvas) state.canvas.focusNode(node, Math.max(1.9, state.canvas.zoom));
  state.canvas.setSelected(node.id);
  state.canvas.setHighlight([]);
  state.detail.showBank(node);
  updateHud();
}

function handleHover(node, evt) {
  if (!node || !evt) {
    hideTooltip();
    return;
  }
  const tt = $("tooltip");
  if (node.type === "bank") {
    const local = [node.province, node.city].filter(Boolean).join(" · ");
    tt.innerHTML = `<b>${esc(node.name)}</b>
      <div class="tt-sub">${esc(node.type_label || "")}${local ? ` · ${esc(local)}` : ""}</div>
      <div class="tt-row">总资产 <em>${node.asset ? `${fmtNum(node.asset)} 亿元` : "未披露"}</em></div>
      <div class="tt-row">华为机会 <em>${node.top_opportunity || "—"}${node.top_capability ? ` · ${esc(node.top_capability)}` : ""}</em></div>
      <div class="tt-row">完整度 <em>${node.completeness ?? 0}%</em></div>`;
  } else {
    const count = node.type === "province" ? `${node.count ?? 0} 家` : node.category || "";
    tt.innerHTML = `<b>${esc(node.name)}</b>
      <div class="tt-sub">${esc(node.type === "province" ? "区域枢纽" : node.type === "huawei" ? "华为能力" : "关系枢纽")}${count ? ` · ${esc(count)}` : ""}</div>
      ${node.desc ? `<div class="tt-sub" style="margin-top:6px">${esc(String(node.desc).slice(0, 110))}</div>` : ""}
      ${node.entry ? `<div class="tt-row">切入 <em>${esc(node.entry)}</em></div>` : ""}`;
  }
  const stage = document.querySelector(".stage").getBoundingClientRect();
  tt.style.display = "block";
  const tw = tt.offsetWidth;
  const th = tt.offsetHeight;
  let x = evt.clientX - stage.left + 16;
  let y = evt.clientY - stage.top + 16;
  if (x + tw > stage.width - 8) x = evt.clientX - stage.left - tw - 16;
  if (y + th > stage.height - 8) y = evt.clientY - stage.top - th - 14;
  tt.style.left = `${Math.max(8, x)}px`;
  tt.style.top = `${Math.max(8, y)}px`;
}

function hideTooltip() {
  const tt = $("tooltip");
  if (tt) tt.style.display = "none";
}

/* ---------------- HUD & loading ---------------- */

function updateHud() {
  const hud = $("hud");
  if (!hud) return;
  const total = state.manifest ? state.manifest.total : state.banks.length;
  let visible = state.visible ? state.visible.size : total;
  if (state.view === "similar") {
    const ids = new Set();
    for (const link of state.edges.similar || []) {
      ids.add(link.source);
      ids.add(link.target);
    }
    if (state.visible) {
      visible = [...ids].filter((id) => state.visible.has(id)).length;
    } else {
      visible = ids.size;
    }
  }
  const zoom = state.canvas ? state.canvas.zoom : 0;
  hud.innerHTML = `<span>视图 <em>${esc(VIEW_LABEL[state.view] || state.view)}</em></span>
    <span>可见 <em>${visible.toLocaleString()}</em> / ${total.toLocaleString()} 家</span>
    <span>缩放 <em>${zoom.toFixed(2)}×</em></span>`;
}

function hideLoading() {
  const el = $("loading");
  if (!el) return;
  el.classList.add("hide");
  window.setTimeout(() => el.remove(), 600);
}

// The conclusion modal is independent of the graph/search payloads, so bind it
// immediately. The main boot() chain can be slow (search.json is ~1.4 MB and
// must not gate the button), and users should be able to open it right away.
initInsight();
boot();
