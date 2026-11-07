/* Right-hand detail panel: lazy shard loading and tabbed rendering. */

const TABS = [
  ["overview", "概览"],
  ["site", "官网"],
  ["finance", "财务"],
  ["news", "新闻"],
  ["penalties", "处罚"],
  ["governance", "董监高"],
  ["huawei", "华为机会"],
  ["sources", "来源"],
];

const GROUP_LABEL = { city: "城商行", rural: "农商行", village: "村镇银行", other: "其他银行" };

const HUB_KICKER = {
  province: "区域枢纽",
  business: "业务枢纽",
  focus: "重点产业枢纽",
  it: "IT 投入分组",
  digital: "数字化活跃度分组",
  huawei: "华为能力枢纽",
};

const KIND_LABEL = {
  registry: "机构名录",
  financial: "财务",
  market: "市场数据",
  news: "新闻",
  penalty: "行政处罚",
  governance: "董监高",
  report: "财报",
  other: "其他",
};

const nf = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 });

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeUrl(url) {
  return /^https?:\/\//i.test(String(url || "")) ? url : "";
}

function num(v, digits = 1) {
  if (v == null || Number.isNaN(Number(v))) return null;
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: digits }).format(Number(v));
}

function compact(v) {
  if (v == null || Number.isNaN(Number(v))) return null;
  const n = Number(v);
  if (Math.abs(n) >= 1e8) return `${nf.format(n / 1e8)} 亿`;
  if (Math.abs(n) >= 1e4) return `${nf.format(n / 1e4)} 万`;
  return nf.format(n);
}

function money(v) {
  const n = num(v);
  return n == null ? null : `${n} 亿元`;
}

function count(v, unit) {
  const c = compact(v);
  return c == null ? null : `${c}${unit}`;
}

function dateText(v) {
  if (!v) return "";
  return String(v).slice(0, 10);
}

function metric(label, value, unit = "", wide = false) {
  const body = value == null
    ? `<div class="v missing">未披露</div>`
    : `<div class="v">${esc(value)}${unit ? `<small>${esc(unit)}</small>` : ""}</div>`;
  return `<div class="metric${wide ? " wide" : ""}"><div class="k">${esc(label)}</div>${body}</div>`;
}

function badge(text, cls = "") {
  return `<span class="badge ${cls}">${esc(text)}</span>`;
}

function empty(text) {
  return `<div class="empty-state">${esc(text)}</div>`;
}

function block(title, inner) {
  return `<div class="block"><div class="block-title">${esc(title)}</div>${inner}</div>`;
}

function tag(text, cls = "") {
  return `<span class="tag ${cls}">${esc(text)}</span>`;
}

export class DetailPanel {
  constructor({ panel, toggle, kicker, title, sub, badges, tabs, body, onBankLink }) {
    this.panel = panel;
    this.toggle = toggle;
    this.kickerEl = kicker;
    this.titleEl = title;
    this.subEl = sub;
    this.badgesEl = badges;
    this.tabsEl = tabs;
    this.bodyEl = body;
    this.onBankLink = onBankLink || (() => {});

    this.activeTab = "overview";
    this.current = null;
    this.cache = new Map();
    this.token = 0;

    this.toggle.addEventListener("click", () => this._toggle());
    this.tabsEl.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-tab]");
      if (!btn || !this.current || this.current.kind !== "bank") return;
      this.activeTab = btn.dataset.tab;
      this._renderTabs();
      this.render();
    });
    this.bodyEl.addEventListener("click", (e) => {
      const link = e.target.closest("[data-bank-id]");
      if (!link) return;
      e.preventDefault();
      this.onBankLink(link.dataset.bankId);
    });
    this._setArrow();
  }

  open() {
    this.panel.classList.remove("collapsed");
    this._setArrow();
  }

  close() {
    this.panel.classList.add("collapsed");
    this._setArrow();
  }

  _toggle() {
    this.panel.classList.toggle("collapsed");
    this._setArrow();
  }

  _setArrow() {
    this.toggle.textContent = this.panel.classList.contains("collapsed") ? "◀" : "▶";
  }

  showBank(node) {
    this.open();
    this.current = { kind: "bank", node, data: null };
    this._renderHeader({
      kicker: "银行画像",
      title: node.name,
      sub: node.full_name || node.province || "",
      badges: [
        badge(node.type_label || "", "cyan"),
        node.province && badge(node.province),
        node.city && badge(node.city),
        node.listed && badge(`上市 ${node.listed}`, "green"),
        node.top_opportunity && badge(`华为机会 ${node.top_opportunity} · ${node.top_capability || ""}`, "orange"),
      ].filter(Boolean),
    });
    this._renderTabs();
    this._load(node);
  }

  showHub(node, connections = [], total = connections.length) {
    this.open();
    this.current = { kind: "hub", node, connections, total };
    this._renderHeader({
      kicker: HUB_KICKER[node.type] || "网络枢纽",
      title: node.name,
      sub: node.desc || node.entry || `共关联 ${total} 家银行`,
      badges: [
        node.category && badge(node.category, "orange"),
        node.entry && badge(node.entry),
        node.count != null && badge(`${node.count} 家`),
      ].filter(Boolean),
    });
    this.tabsEl.innerHTML = `<button class="panel-tab active">概览</button>`;
    this.render();
  }

  _renderHeader({ kicker, title, sub, badges }) {
    this.kickerEl.textContent = kicker || "节点详情";
    this.titleEl.textContent = title || "";
    this.subEl.textContent = sub || "";
    this.badgesEl.innerHTML = (badges || []).join("");
  }

  _renderTabs() {
    this.tabsEl.innerHTML = TABS.map(
      ([id, label]) =>
        `<button class="panel-tab${id === this.activeTab ? " active" : ""}" data-tab="${id}">${label}</button>`
    ).join("");
  }

  async _load(node) {
    const token = ++this.token;
    const cached = this._cacheGet(node.id);
    if (cached) {
      this.current.data = cached;
      this.render();
      return;
    }
    this.bodyEl.innerHTML = empty("读取银行详情…");
    try {
      const res = await fetch(`data/banks/${node.shard}/${node.id}.json`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this._cacheSet(node.id, data);
      if (token !== this.token) return;
      this.current.data = data;
      this.render();
    } catch (err) {
      if (token !== this.token) return;
      this.bodyEl.innerHTML = empty(`详情加载失败：${err.message}。请刷新页面重试。`);
    }
  }

  _cacheGet(id) {
    if (!this.cache.has(id)) return null;
    const value = this.cache.get(id);
    this.cache.delete(id);
    this.cache.set(id, value);
    return value;
  }

  _cacheSet(id, value) {
    this.cache.set(id, value);
    while (this.cache.size > 60) {
      this.cache.delete(this.cache.keys().next().value);
    }
  }

  render() {
    if (!this.current) return;
    if (this.current.kind === "hub") {
      this.bodyEl.innerHTML = this._hubHtml(this.current);
      this.bodyEl.scrollTop = 0;
      return;
    }
    if (!this.current.data) return;
    const data = this.current.data;
    this._renderHeader({
      kicker: "银行画像",
      title: data.short_name || data.name,
      sub: data.name || "",
      badges: this._bankBadges(data),
    });
    switch (this.activeTab) {
      case "site":
        this.bodyEl.innerHTML = this._siteHtml(data);
        break;
      case "finance":
        this.bodyEl.innerHTML = this._financeHtml(data);
        break;
      case "news":
        this.bodyEl.innerHTML = this._newsHtml(data);
        break;
      case "penalties":
        this.bodyEl.innerHTML = this._penaltyHtml(data);
        break;
      case "governance":
        this.bodyEl.innerHTML = this._governanceHtml(data);
        break;
      case "huawei":
        this.bodyEl.innerHTML = this._huaweiHtml(data);
        break;
      case "sources":
        this.bodyEl.innerHTML = this._sourcesHtml(data);
        break;
      default:
        this.bodyEl.innerHTML = this._overviewHtml(data);
    }
    this.bodyEl.scrollTop = 0;
  }

  _bankBadges(data) {
    const out = [];
    if (data.type_label) out.push(badge(data.type_label, "cyan"));
    if (data.province) out.push(badge(data.province));
    if (data.listed) out.push(badge(`上市 ${data.listed}`, "green"));
    if (data.profit_status === "盈利") out.push(badge("盈利", "green"));
    if (data.profit_status === "亏损") out.push(badge("亏损", "red"));
    if (data.penalty_checked) {
      const n = data.penalty_count || 0;
      out.push(badge(n ? `处罚 ${n} 次` : "处罚 0 次", n ? "red" : "green"));
    }
    if (data.top_opportunity) {
      out.push(badge(`华为机会 ${data.top_opportunity} · ${data.top_capability || ""}`, "orange"));
    }
    out.push(badge(`完整度 ${data.completeness ?? 0}%`));
    return out;
  }

  /* ---------------- bank tabs ---------------- */

  _overviewHtml(d) {
    const local = [d.city, d.province].filter(Boolean).join(" · ");
    const metrics = [
      metric("总资产", money(d.asset)),
      metric("净利润", money(d.profit)),
      metric("营业收入", money(d.revenue)),
      metric("员工数", count(d.employees, "人")),
      metric("网点数", count(d.branches, "家")),
      metric("客户数", count(d.customers, "户")),
      metric("存款余额", money(d.deposits)),
      metric("贷款余额", money(d.loans)),
      metric("IT 投入", money(d.it_spend)),
      metric("数字化活跃度", d.digital_tier ? `${d.digital_tier}` : null),
    ].join("");

    const tags = (d.business_tags || []).map((t) => tag(t)).join("");
    const businessBlock = block(
      "主要业务",
      `<div class="tags">${tags || tag("未披露", "warn")}</div>` +
        (d.business_inferred
          ? `<div class="note" style="margin-top:10px">业务标签结合机构类型与所在省份产业结构推断，尚未由年报或官网原文确认。</div>`
          : "")
    );

    const sp = d.site_profile;
    const siteBlock = sp
      ? block(
          "官网画像",
          `<div class="note">${esc(sp.features || "")}</div>` +
            `<div class="tags" style="margin-top:9px">${(sp.key_business || []).slice(0, 6).map((t) => tag(t)).join("")}</div>` +
            (sp.recent_moves && sp.recent_moves.length
              ? `<div class="news-meta" style="margin-top:10px"><span>最近动向</span></div>` +
                sp.recent_moves.slice(0, 3).map((m) => `<div class="opp-why">· ${esc(m)}</div>`).join("")
              : "") +
            `<div class="side-note" style="margin-top:8px">依据官网公开页面整理，详见「官网」页签。</div>`
        )
      : "";

    const focusEntries = (d.business_focus && d.business_focus.length)
      ? d.business_focus
      : (d.region_industries || []).map((t) => ({
          tag: t, basis: "区域产业结构推断（待核验）",
        }));
    const focusRows = focusEntries.map((f) => {
      const confirmed = f.basis === "公开报道";
      const src = f.url
        ? ` <a href="${esc(f.url)}" target="_blank" rel="noopener">原文</a>` : "";
      const quote = f.quote ? `<div class="opp-why">“${esc(f.quote)}”</div>` : "";
      return `<div class="news-item">
        <div class="news-meta">${tag(f.tag, confirmed ? "green" : "warn")}${tag(confirmed ? "公开报道" : "区域推断", confirmed ? "green" : "warn")}${src}</div>
        ${quote || `<div class="note">${esc(f.title || "按省级产业结构推断，待该行公开报道核验。")}</div>`}
      </div>`;
    }).join("");
    const focusBlock = block(
      "重点产业",
      focusEntries.length
        ? focusRows + (d.focus_summary ? `<div class="note">${esc(d.focus_summary)}</div>` : "")
        : `<div class="note">尚未取得该行公开报道或区域产业结构数据。</div>`
    );

    const simRows = (d.similar_banks || []).map((s) =>
      `<div class="news-item"><a href="#" data-bank-id="${esc(s.id)}">${esc(s.name)}</a>
        <div class="news-meta"><span>相似度 ${(s.similarity * 100).toFixed(1)}%</span><span>${esc((s.fields || []).join("、"))}</span></div>
      </div>`).join("");
    const similarBlock = block(
      "相似银行（公开财务结构）",
      simRows || `<div class="note">尚未取得足够公开财务指标进行相似度计算。</div>`
    );

    const opp = (d.opportunities || [])[0];
    const oppBlock = opp
      ? block(
          "华为机会（最高分）",
          `<div class="opp-card">
            <div class="opp-top"><b>${esc(opp.capability)}</b><span class="opp-score">${opp.score}<small>/100</small></span></div>
            <div class="opp-meta">${tag(opp.category || "")}${tag(`切入：${opp.entry || "待定"}`)}${tag(opp.confidence, opp.confidence === "公开证据" ? "green" : "warn")}</div>
            <div class="opp-why">${esc(opp.rationale || "")}</div>
            <div class="opp-bar"><i style="width:${Math.max(0, Math.min(100, opp.score))}%"></i></div>
          </div>
          <div class="note">详见「华为机会」页签的完整评分与证据。</div>`
        )
      : "";

    const pct = Math.max(0, Math.min(100, d.completeness ?? 0));
    const completeness = block(
      "数据完整度",
      `<div class="cov-row"><span>${pct} / 100</span>
        <div class="cov-track"><div class="cov-fill" style="width:${pct}%"></div></div>
        <b>${pct}%</b></div>
        <div class="side-note">资产、营收、净利润、员工、处罚、董监高、新闻等字段越多，完整度越高。</div>`
    );

    const notes = (d.data_notes || []).map((n) => `<div class="note">${esc(n)}</div>`).join("");

    return `
      <div class="kv" style="margin-bottom:6px">
        <dt>机构全称</dt><dd>${esc(d.name || "—")}</dd>
        <dt>所在地</dt><dd>${esc(local || "未披露")}</dd>
        <dt>监管单位</dt><dd>${esc(d.regulator || "未披露")}</dd>
        <dt>官方网站</dt><dd>${d.website ? `<a href="${esc(safeUrl(d.website))}" target="_blank" rel="noopener">${esc(d.website)}</a>` : '<span style="color:#4e5b70">未找到</span>'}</dd>
        <dt>数据基准日</dt><dd>${esc(d.as_of || "未披露")}</dd>
      </div>
      <div class="metric-grid" style="margin-top:14px">${metrics}</div>
      ${businessBlock}
      ${siteBlock}
      ${focusBlock}
      ${similarBlock}
      ${oppBlock}
      ${completeness}
      ${notes ? block("数据说明", notes) : ""}
    `;
  }

  _siteHtml(d) {
    const sp = d.site_profile;
    const siteLine = d.website
      ? `<div class="kv" style="margin-bottom:4px">
          <dt>官方网站</dt><dd><a href="${esc(safeUrl(d.website))}" target="_blank" rel="noopener">${esc(d.website)}</a></dd>
          <dt>抓取时间</dt><dd>${esc(String(sp && sp.fetched_at || "").slice(0, 10) || "—")}</dd>
        </div>`
      : `<div class="note">未找到可直接访问的银行官网。</div>`;
    if (!sp) {
      return siteLine + (d.site_checked
        ? `<div class="note" style="margin-top:10px">官网搜索已完成，但未取得可用文本；不代表该行没有官网。</div>`
        : `<div class="note" style="margin-top:10px">官网画像尚未采集。</div>`);
    }
    const biz = (sp.key_business || []).map((t) => tag(t)).join("");
    const moves = (sp.recent_moves || [])
      .map((m) => `<div class="news-item"><div class="p-main">${esc(m)}</div></div>`)
      .join("");
    const items = (sp.recent_items || [])
      .map((it) => `<div class="news-item">
        <a href="${esc(safeUrl(it.url))}" target="_blank" rel="noopener">${esc(it.title)}</a>
        <div class="news-meta"><span>${esc(it.date || "")}</span></div>
      </div>`)
      .join("");
    const ev = (sp.evidence || []).slice(0, 4)
      .map((e) => `<div class="source-item">
        <div class="s-kind">官网原文摘录</div>
        <div class="opp-why">“${esc(e.text || "")}”</div>
        <a href="${esc(safeUrl(e.url))}" target="_blank" rel="noopener">${esc(e.url || "")}</a>
      </div>`)
      .join("");
    return siteLine + `
      ${block("机构特征", `<div class="note">${esc(sp.features || "未披露")}</div>`)}
      ${block("关键业务", `<div class="tags">${biz || tag("未披露", "warn")}</div>`)}
      ${block("最近动向", moves || `<div class="note">官网未提供带日期的近期条目。</div>`)}
      ${items ? block("官网近期公告 / 新闻", items) : ""}
      ${block("来源与置信度", `<div class="news-meta">${tag(`置信度 ${sp.confidence || "低"}`)}${tag(sp.basis || "官网")}</div>${ev}`)}
    `;
  }

  _financeHtml(d) {
    const rows = [
      ["总资产", money(d.asset), d.asset_year ? `截至 ${d.asset_year}` : ""],
      ["营业收入", money(d.revenue)],
      ["净利润", money(d.profit)],
      ["盈利状态", d.profit_status || "未披露"],
      ["资产档位", d.asset_tier || "未披露"],
      ["员工数", count(d.employees, "人")],
      ["网点数", count(d.branches, "家")],
      ["客户数", count(d.customers, "户")],
      ["存款余额", money(d.deposits)],
      ["贷款余额", money(d.loans)],
      ["IT 投入", money(d.it_spend)],
      ["数字化活跃度", d.digital_tier || "未采集"],
      ["海外业务", d.overseas == null ? "未披露" : d.overseas ? "有" : "无"],
      ["上市板块", d.listed || "未上市"],
    ];
    const disclosed = rows.filter(([, v]) => v && v !== "未披露" && v !== "未采集").length;
    const body = rows
      .map(
        ([k, v, noteText]) =>
          `<dt>${esc(k)}</dt><dd>${v && v !== "未披露" && v !== "未采集" ? esc(v) : '<span style="color:#4e5b70">未披露</span>'}${
            noteText ? `<span style="color:#5a6a82;font-size:10px"> · ${esc(noteText)}</span>` : ""
          }</dd>`
      )
      .join("");
    const financialSources = (d.sources || []).filter((s) =>
      ["financial", "market", "report"].includes(s.kind)
    );
    const src = financialSources
      .map((s) => {
        const url = safeUrl(s.url);
        return `<div class="source-item"><div class="s-kind">${esc(KIND_LABEL[s.kind] || s.kind || "来源")}${
          s.as_of ? ` · ${esc(s.as_of)}` : ""
        }</div>${
          url
            ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(s.title || url)}</a>`
            : esc(s.title || "")
        }</div>`;
      })
      .join("");
    return `
      <div class="note">已披露 ${disclosed} / ${rows.length} 项。未在公开资料中确认的字段一律标注“未披露”，不做推算。</div>
      <div class="kv" style="margin-top:14px">${body}</div>
      ${src ? block("财务来源", src) : ""}
    `;
  }

  _newsHtml(d) {
    const news = (d.news || []).slice().sort((a, b) =>
      String(b.date || b.published || "").localeCompare(String(a.date || a.published || ""))
    );
    if (!news.length) {
      return empty("尚未采集到该行的 AI / 数字化转型公开报道。每日会轮换约 20 家城商行，7 天覆盖全部城商行。");
    }
    const items = news
      .map((n) => {
        const url = safeUrl(n.url);
        const title = url
          ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(n.title || url)}</a>`
          : `<span>${esc(n.title || "未命名新闻")}</span>`;
        const tags = [...(n.ai_tags || []), ...(n.digital_signals || [])]
          .slice(0, 6)
          .map((t) => tag(t))
          .join("");
        return `<div class="news-item">${title}
          <div class="news-meta"><span>${esc(dateText(n.date || n.published) || "日期未披露")}</span>${
            n.source ? `<span>${esc(n.source)}</span>` : ""
          }</div>
          ${n.summary ? `<div class="news-summary">${esc(n.summary)}</div>` : ""}
          ${tags ? `<div class="tags" style="margin-top:6px">${tags}</div>` : ""}
        </div>`;
      })
      .join("");
    return `<div class="note">共 ${news.length} 条公开报道。摘要由智能体从原文抽取，链接保留原文出处。</div>${items}`;
  }

  _penaltyHtml(d) {
    const rows = (d.penalties || []).slice().sort((a, b) =>
      String(b.date || "").localeCompare(String(a.date || ""))
    );
    const amount = d.penalty_amount;
    if (!d.penalty_checked) {
      return empty("行政处罚数据尚未完成采集（不代表该机构没有处罚）。采集范围为国家金融监督管理总局近 5 年公开处罚信息，支行记录归并至母行。");
    }
    const summary = `<div class="note">共 ${rows.length} 条记录${
      amount ? ` · 罚没合计约 ${nf.format(amount)} 万元` : ""
    }。支行罚单已按机构名归并至母行。</div>`;
    if (!rows.length) return `${summary}<div style="margin-top:12px">${empty("在已采集范围内未发现处罚记录。")}</div>`;
    const items = rows
      .map((p) => {
        const url = safeUrl(p.url);
        return `<div class="penalty-item">
          <div class="p-date">${esc(dateText(p.date) || "日期未披露")}${p.agency ? ` · ${esc(p.agency)}` : ""}</div>
          <div class="p-main">${p.branch ? `<span class="p-org">${esc(p.branch)}</span> ` : ""}${esc(p.violation || p.reason || "")}</div>
          <div class="p-main">${esc(p.penalty || p.content || "")}${
            p.amount_wan || p.amount
              ? ` · <span class="p-amount">罚款 ${nf.format(p.amount_wan || p.amount)} 万元</span>`
              : ""
          }</div>
          <div class="p-doc">${p.doc_no ? `文号 ${esc(p.doc_no)}` : ""}${
            url ? ` · <a href="${esc(url)}" target="_blank" rel="noopener">查看原文</a>` : ""
          }</div>
        </div>`;
      })
      .join("");
    return summary + items;
  }

  _governanceHtml(d) {
    const people = d.governance || [];
    const shares = d.governance_shares || [];
    if (!people.length && !shares.length) {
      return empty("尚未采集该行董监高名单。计划优先覆盖 123 家城商行，并以“曾共享董监高”生成银行间关系线。");
    }
    const peopleHtml = people
      .map((p) => {
        const role = p.role || p.position || "董监高";
        const term = [p.since || p.start, p.until || p.end].filter(Boolean).join(" – ");
        return `<div class="person-row"><div class="pos">${esc(role)}</div><div><span class="name">${esc(p.name || "未披露")}</span>${
          term ? `<div class="term">任期 ${esc(term)}</div>` : ""
        }</div></div>`;
      })
      .join("");
    const shareHtml = shares
      .map((s) => {
        const banks = Array.isArray(s.banks) ? s.banks : Array.isArray(s.other_banks) ? s.other_banks
          : s.bank_id ? [{ id: s.bank_id, name: s.bank }] : [];
        const chips = banks
          .filter((b) => b && b.id)
          .map((b) => `<a class="tag" href="#" data-bank-id="${esc(b.id)}">${esc(b.name || b.id)}</a>`)
          .join(" ");
        return `<div class="note" style="margin-top:6px">${esc(s.person || s.name || "未知")}${
          s.role ? `（${esc(s.role)}）` : ""
        } 曾与以下银行共享董监高：${chips || "关联银行待补充"}</div>`;
      })
      .join("");
    return `
      <div class="note">董监高信息来自年报、监管披露与公开简历；共享任职关系仅表示曾在同一时期担任董监高，需以原披露为准。</div>
      ${people.length ? block(`董监高名单（${people.length}）`, `<div class="person-grid">${peopleHtml}</div>`) : ""}
      ${shares.length ? block("共享董监高关系", shareHtml) : ""}
    `;
  }

  _huaweiHtml(d) {
    const opps = d.opportunities || [];
    if (!opps.length) return empty("尚未生成华为能力机会评分。");
    const cards = opps
      .map((o) => {
        const score = Math.max(0, Math.min(100, Number(o.score) || 0));
        const bandCls = o.band === "高" ? "orange" : o.band === "中" ? "amber" : "cyan";
        const evidence = (o.evidence || [])
          .map((ev) => {
            const url = safeUrl(ev.url);
            return `<div class="note" style="margin-top:8px">${
              ev.quote ? `“${esc(ev.quote)}”` : esc(ev.title || ev.signal || "公开证据")
            }${url ? `<br><a href="${esc(url)}" target="_blank" rel="noopener">${esc(ev.title || ev.source || "原文")}</a>` : ""}</div>`;
          })
          .join("");
        return `<div class="opp-card">
          <div class="opp-top"><b>${esc(o.capability || "")}</b><span class="opp-score">${score}<small>/100</small></span></div>
          <div class="opp-meta">${tag(o.category || "")}${tag(`档位 ${o.band || "—"}`, bandCls)}${tag(
            o.confidence || "推断/待验证",
            o.confidence === "公开证据" ? "green" : "warn"
          )}</div>
          <div class="opp-why">${esc(o.rationale || "")}</div>
          <div class="opp-entry">切入方向：${esc(o.entry || "待定")}</div>
          <div class="opp-bar"><i style="width:${score}%"></i></div>
          ${evidence || `<div class="note" style="margin-top:8px">未检索到公开证据，分数为规则推断并封顶 55 分，需结合行方实际需求验证。</div>`}
        </div>`;
      })
      .join("");
    return `
      <div class="note">评分 = 规则分（规模、上市、数字化活跃度、合规压力、业务结构）+ 公开证据加分。无证据时封顶 55 分并标注“推断/待验证”。仅整理公开信息，不代表华为内部判断。</div>
      ${cards}
    `;
  }

  _sourcesHtml(d) {
    const seen = new Set();
    const sources = (d.sources || []).filter((s) => {
      const key = `${s.kind}|${s.url}|${s.title}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    const items = sources
      .map((s) => {
        const url = safeUrl(s.url);
        const date = s.as_of || s.published || "";
        return `<div class="source-item">
          <div class="s-kind">${esc(KIND_LABEL[s.kind] || s.kind || "来源")}${date ? ` · ${esc(dateText(date))}` : ""}</div>
          ${
            url
              ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(s.title || url)}</a>`
              : esc(s.title || "无链接来源")
          }
        </div>`;
      })
      .join("");
    const notes = (d.data_notes || []).map((n) => `<div class="note">${esc(n)}</div>`).join("");
    const meta = `<div class="kv">
      <dt>机构编码</dt><dd>${esc(d.code || "未披露")}</dd>
      <dt>英文名称</dt><dd>${esc(d.en_name || "未披露")}</dd>
      <dt>监管单位</dt><dd>${esc(d.regulator || "未披露")}</dd>
      <dt>数据基准日</dt><dd>${esc(d.as_of || "未披露")}</dd>
    </div>`;
    return `${meta}${block("公开来源", items || empty("尚未记录来源链接。"))}${notes ? block("数据说明", notes) : ""}`;
  }

  /* ---------------- hub ---------------- */

  _hubHtml({ node, connections, total }) {
    const rows = connections
      .map((item) => {
        const n = item.node || item;
        const meta = item.meta || [n.type_label, n.province].filter(Boolean).join(" · ");
        return `<div class="news-item"><a href="#" data-bank-id="${esc(n.id)}">${esc(n.name || n.full_name || n.id)}</a>
          <div class="news-meta"><span>${esc(meta || "")}</span>${
            n.asset ? `<span>总资产 ${esc(money(n.asset))}</span>` : ""
          }</div></div>`;
      })
      .join("");
    const intro = node.desc
      ? `<div class="note">${esc(node.desc)}</div>`
      : `<div class="note">${esc(HUB_KICKER[node.type] || "网络枢纽")}，点击下方银行可查看详情。</div>`;
    const entry = node.entry ? `<div class="kv" style="margin-top:12px"><dt>切入方向</dt><dd>${esc(node.entry)}</dd></div>` : "";
    const count = node.type === "province" ? node.count : (total ?? connections.length);
    const more = count > connections.length ? `，显示前 ${connections.length} 家` : "";
    return `
      <div class="kv">
        <dt>节点类型</dt><dd>${esc(HUB_KICKER[node.type] || node.type)}</dd>
        ${node.category ? `<dt>能力分类</dt><dd>${esc(node.category)}</dd>` : ""}
        ${count != null ? `<dt>关联银行</dt><dd>${esc(count)} 家</dd>` : ""}
      </div>
      ${intro}${entry}
      ${block(`关联银行（共 ${count} 家${more}）`, rows || empty("当前筛选下没有关联银行。"))}
    `;
  }
}
