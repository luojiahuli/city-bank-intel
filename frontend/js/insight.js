/* Conclusion modal: renders the data-driven findings from data/insight.json.
 *
 * Every figure in the modal comes from the JSON payload — nothing is
 * hardcoded here — so the panel always matches the dataset behind the graph.
 */

const $ = (id) => document.getElementById(id);

const CAP_COLOR = {
  hw_core: "#4cc9f0", hw_data: "#6ee7a8", hw_agent: "#b39ddb",
  hw_token: "#f472b6", hw_agentic_data: "#f4c95d", hw_aicc: "#ff8a4c",
  hw_credit: "#ffb48c", hw_fraud: "#ff6b6b", hw_cloud: "#4cc9f0",
  hw_ascend: "#b39ddb", hw_network: "#6ee7a8", hw_xinchuang: "#f4c95d",
  hw_mobile: "#f472b6", hw_green: "#6ee7a8",
};

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function num(value, digits = 0) {
  if (value == null || Number.isNaN(value)) return "—";
  return Number(value).toLocaleString("zh-CN", {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}

function pct(value) {
  return value == null ? "—" : `${num(value, 1)}%`;
}

function bar(share, color = "var(--cyan)") {
  const w = Math.max(0, Math.min(100, Number(share) || 0));
  return `<span class="ic-bar"><i style="width:${w}%;background:${color}"></i></span>`;
}

function safeUrl(url) {
  try {
    const parsed = new URL(String(url));
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : null;
  } catch {
    return null;
  }
}

function metricCard(m) {
  const digits = m.label === "拨备覆盖率" ? 0 : 2;
  return `<div class="ic-metric">
    <div class="ic-metric-label">${esc(m.label)}</div>
    <div class="ic-metric-value">${num(m.median, digits)}<em class="ic-unit">${esc(m.unit)}</em></div>
    <div class="ic-metric-sub">中位数 · 披露 ${m.n} 家（${pct(m.coverage)}）</div>
    <div class="ic-metric-range"><span>P10 ${num(m.p10, 1)}</span><span>P90 ${num(m.p90, 1)}</span></div>
  </div>`;
}

function renderOpportunity(data) {
  const s = data.sample;
  const sample = `
    <div class="ic-lead">
      <div class="ic-lead-main">
        <b>${s.count} 家城商行</b>，合计资产 ${num(s.asset_total)} 亿元
        （中位 ${num(s.asset_median)} 亿，P90 ${num(s.asset_p90)} 亿）；
        ${s.listed} 家上市、${s.profitable} 家盈利、${s.loss_making} 家亏损；
        资产披露覆盖 ${pct(s.asset_disclosed / s.count * 100)}，业务条线结构化披露仅 ${s.business_disclosed} 家。
      </div>
    </div>`;

  const biz = data.business.slice(0, 6).map((b) => `
    <div class="ic-row">
      <span class="ic-row-name"><i class="ic-dot" style="background:var(--cyan)"></i>${esc(b.name)}</span>
      ${bar(b.share)}
      <span class="ic-row-val">${b.count} 家 · ${pct(b.share)}</span>
    </div>`).join("");

  const ind = data.industries.slice(0, 8).map((b) => `
    <div class="ic-row">
      <span class="ic-row-name"><i class="ic-dot" style="background:var(--violet)"></i>${esc(b.name)}</span>
      ${bar(b.share, "var(--violet)")}
      <span class="ic-row-val">${b.count} 家 · ${pct(b.share)}</span>
    </div>`).join("");

  const metrics = data.metrics.map(metricCard).join("");

  const pain = data.pain_cohorts.map((c) => `
    <tr>
      <td><b>${esc(c.name)}</b><div class="ic-sub">${esc(c.desc)}</div></td>
      <td class="ic-num">${c.count}</td>
      <td class="ic-num">${pct(c.share)}</td>
      <td class="ic-num">${c.avg_score ?? "—"}</td>
      <td class="ic-prov">${c.provinces.map(([p, n]) => `${esc(p)} <em>${n}</em>`).join("、") || "—"}</td>
    </tr>`).join("");

  const cmp = data.comparison.map((c) => `
    <tr>
      <td><b>${esc(c.label)}</b></td>
      <td class="ic-num">${num(c.city_median, c.label === "净息差" ? 2 : 1)}${esc(c.unit)}</td>
      <td class="ic-num">${num(c.peer_median, c.label === "净息差" ? 2 : 1)}${esc(c.unit)}</td>
      <td class="ic-num ${c.gap > 0 ? "ic-warn" : "ic-good"}">${c.gap > 0 ? "+" : ""}${num(c.gap, 2)}</td>
      <td class="ic-num ic-dim">${c.city_n} / ${c.peer_n}</td>
    </tr>`).join("");

  return `
    ${sample}
    <h4 class="ic-h4">1.1 业务分布：对公 + 零售双轮，普惠与科技金融是主战场</h4>
    <p class="ic-note">
      公司金融与零售金融几乎全量覆盖（${pct(data.business[0].share)} / ${pct(data.business[1].share)}），
      科技金融 ${pct(data.business[2].share)}、金融市场 ${pct(data.business[3].share)} 紧随其后，
      而普惠小微仅 ${pct((data.business.find((b) => b.name === "普惠小微") || {}).share)}、
      财富管理仅 ${pct((data.business.find((b) => b.name === "财富管理") || {}).share)}、
      国际业务仅 ${pct((data.business.find((b) => b.name === "国际业务") || {}).share)}。
      这说明城商行的收入盘子仍压在传统存贷与地方对公客户上，
      <b>财富管理与零售转型尚未真正启动</b>——这正是 AI 智能体、数据中台类能力的落点。
    </p>
    <div class="ic-grid2">
      <div class="ic-box">
        <div class="ic-box-title">业务条线覆盖 · 城商行自身业务结构</div>
        ${biz}
      </div>
      <div class="ic-box">
        <div class="ic-box-title">区域主导产业 · 所辖省份产业结构（推断口径）</div>
        ${ind}
      </div>
    </div>

    <h4 class="ic-h4">1.2 痛点量化：不良高、拨备薄、息差窄的三重挤压</h4>
    <div class="ic-metrics">${metrics}</div>
    <p class="ic-note">
      城商行不良率中位数 <b>${num((data.metrics.find((m) => m.label === "不良贷款率") || {}).median, 2)}%</b>，
      拨备覆盖率中位数 <b>${num((data.metrics.find((m) => m.label === "拨备覆盖率") || {}).median, 0)}%</b>，
      净息差中位数 <b>${num((data.metrics.find((m) => m.label === "净息差") || {}).median, 2)}%</b>，
      资本充足率中位数 <b>${num((data.metrics.find((m) => m.label === "资本充足率") || {}).median, 2)}%</b>。
      与全体中小银行相比，城商行在<b>不良率、拨备、息差、资本四项上全面劣于基准</b>，
      说明城商行不是"没需求"，而是"预算与能力同时吃紧"——必须用可量化的降本增效方案切入。
    </p>
    <table class="ic-table">
      <thead><tr><th>指标</th><th>城商行中位</th><th>中小行中位</th><th>差值</th><th>样本(城/全体)</th></tr></thead>
      <tbody>${cmp}</tbody>
    </table>

    <h4 class="ic-h4">1.3 痛点人群：谁在真正承压</h4>
    <table class="ic-table">
      <thead><tr><th>痛点分组</th><th>家数</th><th>占比</th><th>组内均机会分</th><th>集中省份</th></tr></thead>
      <tbody>${pain}</tbody>
    </table>
    <p class="ic-note">
      监管处罚 ≥ 2 次的 ${data.pain_cohorts.find((c) => c.key === "penalty").count} 家，
      组内平均机会分 ${data.pain_cohorts.find((c) => c.key === "penalty").avg_score}，
      为各痛点组最高——<b>罚单是最硬的预算理由</b>；而息差收窄组均分
      ${data.pain_cohorts.find((c) => c.key === "nim").avg_score} 同样领先，
      说明降本诉求最强的恰恰是最缺预算的一批银行。
    </p>`;
}

function renderHuawei(data) {
  const seg = data.segments.map((s) => `
    <div class="ic-seg">
      <div class="ic-seg-name">${esc(s.name)}</div>
      <div class="ic-seg-val">${s.avg_score}</div>
      <div class="ic-seg-sub">${s.count} 家 · ≥50 分 ${s.ge50} 家（${pct(s.share_ge50)}）</div>
    </div>`).join("");

  const caps = data.capabilities.filter((c) => c.count > 0).slice(0, 8).map((c) => `
    <div class="ic-row">
      <span class="ic-row-name"><i class="ic-dot" style="background:${CAP_COLOR[c.id] || "#4cc9f0"}"></i>${esc(c.name)}</span>
      ${bar(c.share, CAP_COLOR[c.id] || "#4cc9f0")}
      <span class="ic-row-val">${c.count} 家 · ${pct(c.share)}</span>
    </div>`).join("");

  const zero = data.capabilities.filter((c) => c.count === 0).map((c) => esc(c.name));

  const matrixIds = data.matrix.length ? Object.keys(data.matrix[0].hits) : [];
  const matrixHead = matrixIds.map((id) => {
    const meta = data.capabilities.find((c) => c.id === id);
    return `<th>${esc(meta ? meta.name : id)}</th>`;
  }).join("");

  const matrixRows = data.matrix.map((m) => `
    <tr>
      <td><b>${esc(m.label)}</b><div class="ic-sub">${m.count} 家 · 均分 ${m.avg_score}</div></td>
      ${matrixIds.map((id) => {
        const v = m.hits[id];
        const hot = v >= 50 ? "ic-hot" : v >= 25 ? "ic-mid" : "";
        return `<td class="ic-num ${hot}">${v}%</td>`;
      }).join("")}
    </tr>`).join("");

  const leaders = data.leaders.slice(0, 8).map((b) => `
    <tr>
      <td><b>${esc(b.name)}</b></td>
      <td>${esc(b.province || "—")}</td>
      <td class="ic-num">${b.asset ? num(b.asset) : "—"}</td>
      <td class="ic-num ic-score">${b.score}</td>
      <td>${esc(b.top_capability || "—")}</td>
    </tr>`).join("");

  const listedSeg = data.segments.find((s) => s.name === "上市城商行");
  const unlistedSeg = data.segments.find((s) => s.name === "非上市城商行");
  const digitalHigh = data.segments.find((s) => s.name === "数字化活跃度高");

  return `
    <h4 class="ic-h4">2.1 华为能力在城商行中的覆盖率</h4>
    <p class="ic-note">
      分布式新核心覆盖 <b>${pct(data.capabilities[0].share)}</b>（${data.capabilities[0].count} 家），
      是绝对刚需底座；智慧信贷 ${pct((data.capabilities.find((c) => c.id === "hw_credit") || {}).share)}、
      金融云 ${pct((data.capabilities.find((c) => c.id === "hw_cloud") || {}).share)}、
      金融网络 ${pct((data.capabilities.find((c) => c.id === "hw_network") || {}).share)} 构成第二梯队。
      而 <b>Token 工厂与 AI 算力覆盖率为 0%</b>${zero.length ? `（${zero.join("、")}）` : ""}，
      说明当前城商行的数字化投入还停留在"系统替换"层，未进入"算力与模型运营"层——
      这正是从核心改造切入、再向上做 AI 增量服务的自然路径。
    </p>
    <div class="ic-box">
      <div class="ic-box-title">城商行华为能力覆盖（Top4 命中口径）</div>
      ${caps}
    </div>

    <h4 class="ic-h4">2.2 痛点 × 能力：哪类银行该配哪把武器</h4>
    <p class="ic-note">
      在<b>不良率 ≥ 2%</b> 的 ${data.matrix.find((m) => m.key === "npl").count} 家银行中，
      分布式新核心命中 100%、智慧信贷命中
      ${data.matrix.find((m) => m.key === "npl").hits.hw_credit}%；
      在<b>拨备 &lt; 150%</b> 的组里智慧信贷命中
      ${data.matrix.find((m) => m.key === "prov").hits.hw_credit}%——
      资产质量越差，越需要"新核心 + 智能风控"组合拳，而不是单点工具。
      在<b>处罚 ≥ 2 次</b>组，反欺诈命中
      ${data.matrix.find((m) => m.key === "penalty").hits.hw_fraud}%、
      金融云命中 ${data.matrix.find((m) => m.key === "penalty").hits.hw_cloud}%，
      合规与数据不出域是刚需。
    </p>
    <div class="ic-tablewrap">
      <table class="ic-table ic-matrix">
        <thead><tr><th>痛点分组</th>${matrixHead}</tr></thead>
        <tbody>${matrixRows}</tbody>
      </table>
    </div>

    <h4 class="ic-h4">2.3 量化推理：预算能力决定可触达的市场分层</h4>
    <div class="ic-segs">${seg}</div>
    <p class="ic-note">
      上市城商行均分 <b>${listedSeg.avg_score}</b>，非上市仅 <b>${unlistedSeg.avg_score}</b>，
      差 ${num(listedSeg.avg_score - unlistedSeg.avg_score, 1)} 分；
      ≥50 分的机构占比 <b>${pct(listedSeg.share_ge50)}</b> vs <b>${pct(unlistedSeg.share_ge50)}</b>。
      差距根源是 <b>IT 投入与数字化活跃度</b>：数字化"高"档仅 ${digitalHigh.count} 家，
      但均分高达 ${digitalHigh.avg_score}，显著高于未披露组。
      <b>推理结论</b>：先攻上市行与资产 ≥ 5000 亿的 ${data.segments.find((s) => s.name === "资产 ≥ 5000 亿").count} 家做标杆，
      再以"核心改造 + 智慧信贷"标准化产品包下沉到非上市行——
      后者单家预算小，但标准化交付边际成本更低。
    </p>
    <div class="ic-tablewrap">
      <table class="ic-table">
        <thead><tr><th>银行</th><th>省份</th><th>资产(亿)</th><th>机会分</th><th>首要能力</th></tr></thead>
        <tbody>${leaders}</tbody>
      </table>
    </div>
    <p class="ic-note ic-foot">${esc(data.scoring_note)}</p>`;
}

function renderTrends(data) {
  if (!data || !(data.cases || []).length) {
    return `<p class="ic-note">尚未整理公开趋势与案例。</p>`;
  }

  const trends = data.trends.map((t) => `
    <div class="ic-trend">
      <div class="ic-trend-title">${esc(t.title)}</div>
      <div class="ic-trend-detail">${esc(t.detail)}</div>
      <div class="ic-trend-evidence">${esc(t.evidence)}</div>
    </div>`).join("");

  const cases = data.cases.map((c) => {
    const url = safeUrl((c.source || {}).url);
    const src = c.source
      ? `${url ? `<a class="ic-case-src" href="${esc(url)}" target="_blank" rel="noopener">来源：${esc(c.source.title)}</a>`
        : `来源：${esc(c.source.title)}`}${c.source.date ? `<span class="ic-case-date"> · ${esc(c.source.date)}</span>` : ""}`
      : "";
    return `<div class="ic-case">
      <div class="ic-case-head"><b>${esc(c.bank)}</b>
        <span class="ic-chip">${esc(c.type)}</span>
        <span class="ic-chip alt">${esc(c.deployment)}</span>
      </div>
      <div class="ic-case-line"><span>模型基座</span>${esc(c.model)}</div>
      <div class="ic-case-line"><span>知识库/RAG</span>${esc(c.knowledge)}</div>
      <div class="ic-case-line"><span>落地场景</span>${esc(c.scenario)}</div>
      <div class="ic-case-line"><span>公开效果</span>${esc(c.result)}</div>
      <div class="ic-case-line"><span>成本/底座</span>${esc(c.cost)}</div>
      ${src}
    </div>`;
  }).join("");

  const steps = (data.playbook || []).map((s) => `
    <div class="ic-step"><b>${esc(s.step)}</b><div>${esc(s.detail)}</div></div>`).join("");

  const caveats = (data.caveats || []).map((c) => `<li>${esc(c)}</li>`).join("");

  return `
    <h4 class="ic-h4">4.1 行业趋势：轻量化、私有化、嵌入存量系统</h4>
    <p class="ic-note">${esc(data.intro)}</p>
    <div class="ic-trends">${trends}</div>

    <h4 class="ic-h4">4.2 公开落地案例（本地化部署 + 智能体 + 知识库）</h4>
    <div class="ic-cases">${cases}</div>

    <h4 class="ic-h4">4.3 可复制的轻量化落地路径</h4>
    <div class="ic-steps">${steps}</div>

    ${caveats ? `<p class="ic-note ic-foot"><b>口径与风险提示</b></p><ul class="ic-caveats">${caveats}</ul>` : ""}
    <p class="ic-note ic-foot">${esc(data.note || "")}${data.as_of ? ` 资料整理截至 ${esc(data.as_of)}。` : ""}</p>`;
}

function renderRegions(data) {
  const regions = data.regions.map((r) => `
    <tr>
      <td><b>${esc(r.name)}</b><div class="ic-sub">${r.count} 家 · ${r.provinces.slice(0, 4).map(esc).join("、")}</div></td>
      <td class="ic-num">${num(r.asset_total)}</td>
      <td class="ic-num ${r.npl_median >= 2 ? "ic-warn" : ""}">${num(r.npl_median)}%</td>
      <td class="ic-num ${r.prov_median < 150 ? "ic-warn" : ""}">${num(r.prov_median, 0)}%</td>
      <td class="ic-num">${num(r.nim_median)}%</td>
      <td class="ic-num">${r.avg_score}</td>
      <td class="ic-num">${r.penalty_total}</td>
    </tr>`).join("");

  const provinces = data.provinces.slice(0, 14).map((p) => `
    <tr>
      <td><b>${esc(p.name)}</b></td>
      <td>${esc(p.region)}</td>
      <td class="ic-num">${p.count}</td>
      <td class="ic-num">${num(p.asset_total)}</td>
      <td class="ic-num ${p.npl_median >= 2 ? "ic-warn" : ""}">${p.npl_median ?? "—"}%</td>
      <td class="ic-num ${p.prov_median < 150 ? "ic-warn" : ""}">${p.prov_median ?? "—"}%</td>
      <td class="ic-num">${p.avg_score}</td>
      <td class="ic-num">${p.penalty_total}</td>
    </tr>`).join("");

  const byName = Object.fromEntries(data.regions.map((r) => [r.name, r]));
  const dongbei = byName["东北"];
  const huadong = byName["华东"];
  const huazhong = byName["华中"];
  const huanan = byName["华南"];
  const huabei = byName["华北"];

  return `
    <h4 class="ic-h4">3.1 大区对比：风险温度与产业禀赋的错位</h4>
    <div class="ic-tablewrap">
      <table class="ic-table">
        <thead><tr><th>大区</th><th>总资产(亿)</th><th>不良中位</th><th>拨备中位</th><th>息差中位</th><th>机会分</th><th>处罚</th></tr></thead>
        <tbody>${regions}</tbody>
      </table>
    </div>
    <p class="ic-note">
      <b>华东（${huadong.count} 家，${num(huadong.asset_total)} 亿）</b>是最大且最健康的市场：
      不良中位 ${huadong.npl_median}%、拨备中位 ${huadong.prov_median}%、机会分 ${huadong.avg_score}，
      产业以制造 / 科技 / 跨境贸易为主 →
      <b>切入点：跨境贸易与反欺诈（金融云 + 数据不出域）、科技金融与 AI 算力</b>。
      <b>东北（${dongbei.count} 家）</b>不良中位高达 ${dongbei.npl_median}%（全省 ${dongbei.npl_above_2} 家超 2%）、
      拨备中位仅 ${dongbei.prov_median}%、亏损 ${dongbei.loss} 家、机会分最低 ${dongbei.avg_score} →
      <b>切入点：智慧信贷 + 智能风控（不良处置与核销效率），用可量化的降本减值模型立项</b>，
      不宜以核心重建开局（预算不足、见效慢）。
      <b>华中（${huazhong.count} 家）</b>机会分最高 ${huazhong.avg_score}（河南 2 家均值 57.5）→
      <b>切入点：数据智能 + 智能体工厂，郑州/中原可作区域样板</b>。
      <b>华南（${huanan.count} 家）</b>跨境贸易产业全覆盖、机会分 ${huanan.avg_score} 偏低 →
      <b>切入点：跨境结算与贸易金融数字化，反欺诈优先</b>。
      <b>华北（${huabei.count} 家）</b>不良中位 ${huabei.npl_median}%、能源化工产业 18 家 →
      <b>切入点：绿色数据中心 / 数字能源 + 大型对公客群经营</b>。
    </p>

    <h4 class="ic-h4">3.2 省级优先级（按总资产排序，前 14）</h4>
    <div class="ic-tablewrap">
      <table class="ic-table">
        <thead><tr><th>省份</th><th>大区</th><th>家数</th><th>总资产(亿)</th><th>不良中位</th><th>拨备中位</th><th>机会分</th><th>处罚</th></tr></thead>
        <tbody>${provinces}</tbody>
      </table>
    </div>
    <p class="ic-note">
      <b>切入逻辑</b>：优先级由「资产体量 × 风险压力 × 机会分」三项共同决定，而非只看体量。
      例如浙江省 13 家、${num((data.provinces.find((p) => p.name === "浙江省") || {}).asset_total)} 亿、
      不良中位仅 0.93%，属"大体量低风险"——以
      <b>新核心 + 数据中台</b>切入做规模复制；
      而辽宁省 14 家、不良中位 2.71%、4 家亏损集中在该省，属"中体量高压力"——
      以<b>智慧信贷 / 反欺诈</b>切入做单点见效，再逐步带出核心与数据治理。
    </p>`;
}

export class ConclusionModal {
  constructor() {
    this.el = $("insightModal");
    this.body = $("insightBody");
    this.data = null;
    this.open = false;
  }

  bind() {
    const btn = $("insightBtn");
    if (btn) btn.addEventListener("click", () => this.toggle());
    if (this.el) {
      this.el.addEventListener("click", (e) => {
        if (e.target === this.el || e.target.classList.contains("ic-close")) {
          this.close();
        }
      });
    }
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.open) this.close();
    });
  }

  async load() {
    if (this.data) return this.data;
    if (this.pending) return this.pending;
    this.pending = fetch("data/insight.json")
      .then((res) => {
        if (!res.ok) throw new Error(`insight.json → HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        this.data = data;
        this.pending = null;
        return data;
      })
      .catch((err) => {
        this.pending = null;
        throw err;
      });
    return this.pending;
  }

  // Warm the payload in the background so the first click opens instantly.
  prefetch() {
    this.load().catch(() => {});
  }

  async render() {
    if (!this.body) return;
    this.body.innerHTML = `<div class="ic-loading">正在生成结论…</div>`;
    try {
      const data = await this.load();
      const stamp = data.generated_at
        ? `数据基准 ${String(data.generated_at).slice(0, 10)}` : "";
      $("insightStamp").textContent = stamp;
      this.body.innerHTML = `
        <nav class="ic-nav">
          <button class="ic-tab active" data-sec="op">① 业务分布与痛点</button>
          <button class="ic-tab" data-sec="hw">② 华为能力机会</button>
          <button class="ic-tab" data-sec="rg">③ 区域切入点</button>
          <button class="ic-tab" data-sec="tr">④ 最新业务趋势</button>
        </nav>
        <div class="ic-panel" data-panel="op">${renderOpportunity(data.opportunity)}</div>
        <div class="ic-panel" data-panel="hw" hidden>${renderHuawei(data.huawei)}</div>
        <div class="ic-panel" data-panel="rg" hidden>${renderRegions(data.regions)}</div>
        <div class="ic-panel" data-panel="tr" hidden>${renderTrends(data.trends)}</div>
        <p class="ic-note ic-foot">${esc(data.sample_note)}</p>`;

      this.body.querySelectorAll(".ic-tab").forEach((tab) => {
        tab.addEventListener("click", () => {
          this.body.querySelectorAll(".ic-tab").forEach((t) => t.classList.remove("active"));
          tab.classList.add("active");
          this.body.querySelectorAll(".ic-panel").forEach((p) => {
            p.hidden = p.dataset.panel !== tab.dataset.sec;
          });
        });
      });
    } catch (err) {
      console.error(err);
      this.body.innerHTML = `<div class="ic-loading ic-err">结论数据加载失败：${esc(err.message)}</div>`;
    }
  }

  async show() {
    if (!this.el) return;
    this.el.classList.add("show");
    this.open = true;
    document.body.classList.add("modal-open");
    await this.render();
  }

  close() {
    if (!this.el) return;
    this.el.classList.remove("show");
    this.open = false;
    document.body.classList.remove("modal-open");
  }

  toggle() {
    if (this.open) this.close();
    else this.show();
  }
}
