(() => {
  'use strict';

  const STAGE_RANK = { live: 3, pilot: 2, joint: 1, eco: 0 };
  const TIER_ORDER = ['state', 'joint', 'city', 'rural', 'private', 'securities', 'insurance'];
  const TIER_SHORT = {
    state: '国有大行', joint: '股份行', city: '城商行', rural: '农商农信',
    private: '民营', securities: '证券', insurance: '保险',
  };
  const PAIN_SHORT = {
    core: '核心下移', db: '数据库', data: '数据平台', ai: 'AI 算力', agent: '智能体',
    harmony: '鸿蒙', cloud: '云原生', storage: '存算分离', network: '低时延网络', sec_core: '证券核心',
  };
  const CAP_SHORT = {
    gaussdb: 'GaussDB', kunpeng: '鲲鹏', ascend: '昇腾', huawei_cloud: '华为云 Stack',
    dorado: 'OceanStor', fusioninsight: 'FusionInsight', harmony: '鸿蒙生态',
    pangu: '盘古 / 智能体', network: 'CloudEngine',
  };
  const DIM_LABEL = { tier: '体量', pain: '痛点', cap: '能力', stage: '阶段', year: '年份' };

  const state = {
    tier: new Set(), pain: new Set(), cap: new Set(), stage: new Set(), year: new Set(), q: '',
  };

  let D = null;
  let INST = [];
  let CASES = [];
  const TIER_MAP = {};
  const PAIN_MAP = {};
  const CAP_MAP = {};
  const STAGE_MAP = {};

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
  const fmt = (n) => Number(n).toLocaleString('en-US');
  const clamp = (n, min, max) => Math.min(max, Math.max(min, n));

  async function boot() {
    try {
      const res = await fetch('data/finance.json', { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      D = await res.json();
    } catch (err) {
      document.body.insertAdjacentHTML('beforeend',
        '<div class="load-error">数据加载失败：' + esc(err.message) + '。请刷新页面，或确认 data/finance.json 可访问。</div>');
      return;
    }
    prep();
    renderHero();
    renderTiers();
    renderPains();
    renderCaps();
    renderTimeline();
    renderFilterChips();
    renderGrid();
    renderEco();
    bind();
    syncActive();
  }

  /* ---------------- prepare ---------------- */

  function prep() {
    D.tiers.forEach((t) => { TIER_MAP[t.id] = t; });
    D.pains.forEach((p) => { PAIN_MAP[p.id] = p; });
    D.caps.forEach((c) => { CAP_MAP[c.id] = c; });
    D.stages.forEach((s) => { STAGE_MAP[s.id] = s; });

    INST = D.institutions.map((raw) => {
      const cases = [...raw.cases].sort((a, b) => {
        const av = a.year * 100 + (a.month ? Number(a.month) : 0);
        const bv = b.year * 100 + (b.month ? Number(b.month) : 0);
        return bv - av;
      });
      const topStage = cases.reduce((best, c) => (
        STAGE_RANK[c.stage] > STAGE_RANK[best] ? c.stage : best
      ), 'eco');
      const painSet = new Set();
      const capSet = new Set();
      cases.forEach((c) => {
        c.pains.forEach((p) => painSet.add(p));
        c.caps.forEach((p) => capSet.add(p));
      });
      return {
        ...raw, cases, topStage, painSet, capSet,
        lastYear: Math.max(...cases.map((c) => c.year)),
      };
    });

    INST.sort((a, b) => (
      TIER_ORDER.indexOf(a.tier) - TIER_ORDER.indexOf(b.tier)
      || b.cases.length - a.cases.length
      || a.name.localeCompare(b.name, 'zh')
    ));

    CASES = INST.flatMap((inst) => inst.cases.map((c) => ({ ...c, inst })));
  }

  function stageColor(id) { return (STAGE_MAP[id] || {}).color || '#94a3b8'; }
  function tierColor(id) { return (TIER_MAP[id] || {}).color || '#94a3b8'; }

  /* ---------------- hero ---------------- */

  function renderHero() {
    const stamp = String(D.updated || '').slice(0, 10) || '—';
    $('#stamp').textContent = stamp;
    $('#footStamp').textContent = stamp;

    const tiersUsed = new Set(INST.map((i) => i.tier)).size;
    const capsUsed = new Set(CASES.flatMap((c) => c.caps)).size;

    $('#heroLead').innerHTML = '这份看板把华为在金融行业的公开合作拆成三条线：'
      + '<strong>体量</strong>（机构有多大）、<strong>痛点</strong>（改造花在什么地方）、'
      + '<strong>案例</strong>（合作走到了哪一步）。当前收录 '
      + `<strong>${INST.length} 家</strong>被公开点名的机构、<strong>${CASES.length} 个</strong>可回溯案例，`
      + `覆盖 <strong>${tiersUsed} 个体量层</strong>与 <strong>${capsUsed} 项</strong>华为能力。`
      + '华为未公开披露的客户与项目不在其中。';

    const g = D.global_context;
    const kpis = [
      { v: fmt(g.customers), label: '金融机构客户', sub: '全球累计 · 2025-09', cls: '' },
      { v: g.countries, label: '覆盖国家 / 地区', sub: '全球 · 2025-09', cls: '' },
      { v: `${g.top100_banks}<small>/100</small>`, label: '全球前 100 家银行', sub: '华为服务覆盖', cls: 'cyan', html: true },
      { v: fmt(g.partners), label: '全球生态伙伴', sub: '金融行业', cls: '' },
      { v: INST.length, label: '已收录机构', sub: '公开点名 · 中国', cls: 'accent' },
      { v: CASES.length, label: '可回溯案例', sub: '每条附公开来源', cls: 'accent' },
    ];

    $('#kpiGrid').innerHTML = kpis.map((k) => (
      `<div class="kpi ${k.cls}">
        <div class="kpi-value">${k.html ? k.v : esc(k.v)}</div>
        <div class="kpi-label">${esc(k.label)}</div>
        <div class="kpi-sub">${esc(k.sub)}</div>
      </div>`
    )).join('');

    $('#globalSource').innerHTML = '全球数据来源：'
      + `<a href="${esc(g.url)}" target="_blank" rel="noopener noreferrer">${esc(g.source)}</a>（${esc(g.as_of)}）`
      + '· 国内案例为公开报道逐条梳理，两组数字口径不同，不可直接相加。';
  }

  /* ---------------- 01 tier chart ---------------- */

  function renderTiers() {
    const stageCounts = {};
    D.stages.forEach((s) => { stageCounts[s.id] = 0; });
    INST.forEach((i) => { stageCounts[i.topStage] += 1; });

    $('#stageLegend').innerHTML = D.stages.map((s) => (
      `<span class="legend-item"><i class="legend-dot" style="background:${s.color}"></i>`
      + `${esc(s.label)}<span class="legend-count">${stageCounts[s.id]} 家</span></span>`
    )).join('');

    const byTier = TIER_ORDER.map((id) => {
      const insts = INST.filter((i) => i.tier === id);
      const byStage = {};
      D.stages.forEach((s) => { byStage[s.id] = 0; });
      insts.forEach((i) => { byStage[i.topStage] += 1; });
      return {
        tier: TIER_MAP[id],
        insts,
        byStage,
        cases: insts.reduce((n, i) => n + i.cases.length, 0),
      };
    });

    const maxInst = Math.max(1, ...byTier.map((b) => b.insts.length));

    $('#tierChart').innerHTML = byTier.map((b) => {
      const t = b.tier;
      const fillPct = (b.insts.length / maxInst) * 100;
      const segs = D.stages.filter((s) => b.byStage[s.id] > 0).map((s) => {
        const w = (b.byStage[s.id] / b.insts.length) * 100;
        return `<span class="tier-seg" style="width:${w}%;background:${s.color}"`
          + ` title="${esc(s.label)}：${b.byStage[s.id]} 家"></span>`;
      }).join('');
      return `<div class="tier-row" role="button" tabindex="0" data-f="tier:${t.id}" aria-label="筛选 ${esc(t.label)}">
        <div class="tier-label">
          <span class="tier-name"><i style="background:${t.color}"></i>${esc(t.label)}</span>
          <span class="tier-range">${esc(t.range)}</span>
        </div>
        <div class="tier-track"><div class="tier-fill" style="width:${fillPct}%">${segs}</div></div>
        <div class="tier-meta"><b>${b.insts.length}</b> 家 · ${b.cases} 个案例</div>
      </div>`;
    }).join('');

    renderReadings();
  }

  function renderReadings() {
    const core = CASES.filter((c) => c.pains.includes('core'));
    const ai = CASES.filter((c) => c.pains.includes('ai') || c.pains.includes('agent'));
    const dataCases = CASES.filter((c) => c.pains.includes('data') || c.pains.includes('storage'));
    const harmonyInsts = INST.filter((i) => i.painSet.has('harmony'));

    const items = [
      {
        k: '核心下移', v: core.length, unit: '个案例',
        note: `覆盖 ${new Set(core.map((c) => c.inst.id)).size} 家机构；国有大行、股份行与城农商行均已出现投产级项目。`,
      },
      {
        k: '大模型 / 智能体', v: ai.length, unit: '个案例',
        note: `覆盖 ${new Set(ai.map((c) => c.inst.id)).size} 家机构；从智能运维到轻量化平台，多数仍在试点与联合创新阶段。`,
      },
      {
        k: '数据平台 / 存算分离', v: dataCases.length, unit: '个案例',
        note: `覆盖 ${new Set(dataCases.map((c) => c.inst.id)).size} 家机构；数据仓库、数据湖与存算分离是核心之外的第二条改造主线。`,
      },
      {
        k: '鸿蒙生态', v: harmonyInsts.length, unit: '家机构',
        note: '以手机银行鸿蒙原生适配为主，集中在 2023–2025 年，属生态层合作。',
      },
    ];

    $('#keyReadings').innerHTML = items.map((it) => (
      `<div class="insight">
        <div class="insight-k">${esc(it.k)}</div>
        <div class="insight-v">${it.v}<small>${esc(it.unit)}</small></div>
        <div class="insight-note">${esc(it.note)}</div>
      </div>`
    )).join('');
  }

  /* ---------------- 02 pain heatmap ---------------- */

  function renderPains() {
    const tiers = TIER_ORDER.map((id) => TIER_MAP[id]);
    const counts = {};
    D.pains.forEach((p) => {
      counts[p.id] = {};
      TIER_ORDER.forEach((t) => { counts[p.id][t] = 0; });
    });
    CASES.forEach((c) => {
      c.pains.forEach((p) => {
        if (counts[p]) counts[p][c.inst.tier] += 1;
      });
    });

    let max = 1;
    D.pains.forEach((p) => TIER_ORDER.forEach((t) => {
      max = Math.max(max, counts[p.id][t]);
    }));

    const totals = {};
    const colTotals = {};
    D.pains.forEach((p) => {
      totals[p.id] = TIER_ORDER.reduce((n, t) => n + counts[p.id][t], 0);
    });
    TIER_ORDER.forEach((t) => {
      colTotals[t] = D.pains.reduce((n, p) => n + counts[p.id][t], 0);
    });

    const cells = [];
    cells.push('<div class="heat-corner">痛点 ＼ 体量</div>');
    cells.push(tiers.map((t) => (
      `<button class="heat-col-label" type="button" data-f="tier:${t.id}">${esc(TIER_SHORT[t.id])}</button>`
    )).join(''));

    D.pains.forEach((p) => {
      cells.push(`<button class="heat-row-label" type="button" data-f="pain:${p.id}" title="${esc(p.label)}">
        <span>${esc(p.label)}</span><span>${totals[p.id]}</span>
      </button>`);
      tiers.forEach((t) => {
        const n = counts[p.id][t.id];
        const alpha = n ? 0.09 + 0.58 * (n / max) : 0;
        const color = n >= max * 0.55 ? '#fff' : '#e9b6bb';
        const style = n ? `background:rgba(255,77,94,${alpha.toFixed(3)});color:${color}` : '';
        cells.push(n
          ? `<button class="heat-cell" type="button" data-f="pain:${p.id}|tier:${t.id}" style="${style}" title="${esc(p.label)} × ${esc(t.label)}：${n} 个案例">${n}</button>`
          : `<button class="heat-cell heat-zero" type="button" disabled title="${esc(p.label)} × ${esc(t.label)}：无公开案例">·</button>`);
      });
    });

    $('#heatMap').innerHTML = `<div class="scroll-hint">左右滑动查看全部 7 个体量层 →</div>
      <div class="heat-grid">${cells.join('')}</div>
      <div class="heat-foot">列合计：${tiers.map((t) => `${esc(TIER_SHORT[t.id])} ${colTotals[t.id]}`).join(' · ')}</div>`;

    const rows = D.pains.map((p) => ({ p, n: totals[p.id] })).sort((a, b) => b.n - a.n);
    const maxTotal = Math.max(1, ...rows.map((r) => r.n));
    $('#painRank').innerHTML = rows.map((r) => (
      `<button class="bar-row" type="button" data-f="pain:${r.p.id}" title="${esc(r.p.label)}">
        <span class="bar-name">${esc(PAIN_SHORT[r.p.id] || r.p.label)}</span>
        <span class="bar-track"><span class="bar-fill" style="width:${(r.n / maxTotal) * 100}%"></span></span>
        <span class="bar-val">${r.n}</span>
      </button>`
    )).join('');
  }

  /* ---------------- 03 capability coverage ---------------- */

  function renderCaps() {
    const totals = {};
    D.caps.forEach((c) => {
      totals[c.id] = CASES.filter((item) => item.caps.includes(c.id)).length;
    });
    const max = Math.max(1, ...D.caps.map((c) => totals[c.id]));
    const rows = [...D.caps].sort((a, b) => totals[b.id] - totals[a.id]);

    $('#capChart').innerHTML = rows.map((c) => (
      `<button class="bar-row" type="button" data-f="cap:${c.id}">
        <span class="bar-name">${esc(c.label)}</span>
        <span class="bar-track"><span class="bar-fill" style="width:${(totals[c.id] / max) * 100}%"></span></span>
        <span class="bar-val">${totals[c.id]} 例</span>
      </button>`
    )).join('');
  }

  /* ---------------- 04 timeline ---------------- */

  function renderTimeline() {
    const yearsPresent = CASES.map((c) => c.year);
    const min = Math.min(...yearsPresent);
    const max = Math.max(...yearsPresent);
    const years = [];
    for (let y = min; y <= max; y += 1) years.push(y);

    const byYear = years.map((y) => {
      const cs = CASES.filter((c) => c.year === y);
      const byStage = {};
      D.stages.forEach((s) => { byStage[s.id] = 0; });
      cs.forEach((c) => { byStage[c.stage] = (byStage[c.stage] || 0) + 1; });
      return { y, cs, byStage };
    });

    const maxCount = Math.max(1, ...byYear.map((b) => b.cs.length));
    const UNIT = 168;

    $('#timelineChart').innerHTML = byYear.map((b) => {
      const total = b.cs.length;
      const segs = D.stages.filter((s) => b.byStage[s.id] > 0).map((s) => {
        const h = clamp((b.byStage[s.id] / maxCount) * UNIT, 4, UNIT);
        return `<span class="tl-seg" style="height:${h}px;background:${s.color}"`
          + ` title="${esc(s.label)}：${b.byStage[s.id]} 个案例"></span>`;
      }).join('');
      const attrs = total ? ` role="button" tabindex="0" data-f="year:${b.y}"` : ' disabled';
      return `<button class="tl-col" type="button"${attrs} aria-label="${b.y} 年 ${total} 个案例">
        <span class="tl-total">${total || '·'}</span>
        <span class="tl-plot"><span class="tl-stack">${segs}</span></span>
        <span class="tl-year">${b.y}</span>
      </button>`;
    }).join('');

    const legend = document.createElement('div');
    legend.className = 'tl-legend';
    legend.innerHTML = D.stages.map((s) => (
      `<span class="legend-item"><i class="legend-dot" style="background:${s.color}"></i>${esc(s.label)}</span>`
    )).join('');
    $('.tl-panel').appendChild(legend);
  }

  /* ---------------- filters ---------------- */

  function renderFilterChips() {
    $('#tierChips').innerHTML = TIER_ORDER.map((id) => {
      const t = TIER_MAP[id];
      return `<button class="chip" type="button" data-f="tier:${id}"><i class="chip-dot" style="background:${t.color}"></i>${esc(TIER_SHORT[id])}</button>`;
    }).join('');

    $('#stageChips').innerHTML = D.stages.map((s) => (
      `<button class="chip" type="button" data-f="stage:${s.id}"><i class="chip-dot" style="background:${s.color}"></i>${esc(s.label)}</button>`
    )).join('');
  }

  function instMatches(i) {
    if (state.tier.size && !state.tier.has(i.tier)) return false;
    if (state.stage.size && !i.cases.some((c) => state.stage.has(c.stage))) return false;
    if (state.pain.size && !i.cases.some((c) => c.pains.some((p) => state.pain.has(p)))) return false;
    if (state.cap.size && !i.cases.some((c) => c.caps.some((p) => state.cap.has(p)))) return false;
    if (state.year.size && !i.cases.some((c) => state.year.has(c.year))) return false;
    if (state.q) {
      const hay = [
        i.name, i.short, i.type, i.region, i.summary,
        ...i.cases.flatMap((c) => [c.title, c.summary, c.metrics || '']),
      ].join(' ').toLowerCase();
      if (!hay.includes(state.q)) return false;
    }
    return true;
  }

  function cardHtml(i) {
    const t = TIER_MAP[i.tier];
    const st = STAGE_MAP[i.topStage];
    const painList = D.pains.filter((p) => i.painSet.has(p.id)).slice(0, 3);
    const tags = painList.map((p) => `<span class="tag">${esc(PAIN_SHORT[p.id] || p.label)}</span>`).join('');
    const more = i.painSet.size > painList.length
      ? `<span class="tag more">+${i.painSet.size - painList.length}</span>` : '';

    return `<article class="inst-card" role="button" tabindex="0" data-inst="${i.id}" style="--tier-c:${t.color}">
      <div class="card-top">
        <span class="badge badge-tier">${esc(TIER_SHORT[i.tier])}</span>
        <span class="badge badge-stage" style="--stage-c:${st.color}">${esc(st.label)}</span>
      </div>
      <div class="card-name">${esc(i.name)}</div>
      <div class="card-sub">${esc(i.type)} · ${esc(i.region)} · ${esc(i.asset_note)}</div>
      <p class="card-summary">${esc(i.summary)}</p>
      <div class="card-tags">${tags}${more}</div>
      <div class="card-foot">
        <span>${i.cases.length} 个案例 · 最近 ${i.lastYear}</span>
        <span class="cta">查看案例 →</span>
      </div>
    </article>`;
  }

  function renderGrid() {
    const list = INST.filter(instMatches);
    const caseCount = list.reduce((n, i) => n + i.cases.length, 0);

    $('#instGrid').innerHTML = list.map(cardHtml).join('');
    $('#instGrid').hidden = list.length === 0;
    $('#gridEmpty').hidden = list.length !== 0;
    $('#gridMeta').innerHTML = `显示 <b>${list.length}</b> / ${INST.length} 家机构`
      + '<span class="sep">·</span>'
      + `${caseCount} 个案例`;
  }

  function renderActiveFilters() {
    const chips = [];
    ['tier', 'pain', 'cap', 'stage', 'year'].forEach((dim) => {
      state[dim].forEach((v) => {
        chips.push(`<button class="afl-chip" type="button" data-remove="${dim}:${v}">${esc(DIM_LABEL[dim])}：${esc(labelFor(dim, v))}<i>✕</i></button>`);
      });
    });
    if (state.q) {
      chips.push(`<button class="afl-chip" type="button" data-remove="q:">搜索：${esc(state.q)}<i>✕</i></button>`);
    }

    const box = $('#activeFilters');
    if (!chips.length) {
      box.hidden = true;
      box.innerHTML = '';
      return;
    }
    box.hidden = false;
    box.innerHTML = '<span class="afl-label">已筛选</span>'
      + chips.join('')
      + '<button class="afl-clear" type="button" data-clear>清空全部</button>';
  }

  function labelFor(dim, v) {
    if (dim === 'tier') return (TIER_MAP[v] || {}).label || v;
    if (dim === 'pain') return (PAIN_MAP[v] || {}).label || v;
    if (dim === 'cap') return (CAP_MAP[v] || {}).label || v;
    if (dim === 'stage') return (STAGE_MAP[v] || {}).label || v;
    if (dim === 'year') return v + ' 年';
    return v;
  }

  function applyFilters() {
    renderGrid();
    renderActiveFilters();
    syncActive();
  }

  function activateFrom(el) {
    const spec = el.getAttribute('data-f');
    if (!spec) return;
    const pairs = spec.split('|').map((pair) => {
      const idx = pair.indexOf(':');
      return [pair.slice(0, idx), pair.slice(idx + 1)];
    });
    const value = ([dim, raw]) => (dim === 'year' ? Number(raw) : raw);
    const allOn = pairs.every(([dim, raw]) => state[dim] && state[dim].has(value([dim, raw])));
    pairs.forEach(([dim, raw]) => {
      const v = value([dim, raw]);
      if (!state[dim]) return;
      if (allOn) state[dim].delete(v);
      else state[dim].add(v);
    });
    applyFilters();
  }

  function clearAll() {
    ['tier', 'pain', 'cap', 'stage', 'year'].forEach((dim) => state[dim].clear());
    state.q = '';
    const input = $('#searchInput');
    if (input) input.value = '';
    applyFilters();
  }

  function syncActive() {
    $$('[data-f]').forEach((el) => {
      const spec = el.getAttribute('data-f');
      const on = spec.split('|').every((pair) => {
        const idx = pair.indexOf(':');
        const dim = pair.slice(0, idx);
        const raw = pair.slice(idx + 1);
        const v = dim === 'year' ? Number(raw) : raw;
        return state[dim] && state[dim].has(v);
      });
      el.classList.toggle('is-active', on);
    });
  }

  /* ---------------- 05 modal ---------------- */

  function openModal(id) {
    const i = INST.find((x) => x.id === id);
    if (!i) return;

    const t = TIER_MAP[i.tier];
    const st = STAGE_MAP[i.topStage];

    const cases = i.cases.map((c) => {
      const s = STAGE_MAP[c.stage];
      const date = `${c.year}${c.month ? '.' + c.month : ''}`;
      const pains = D.pains.filter((p) => c.pains.includes(p.id))
        .map((p) => `<span class="tag">${esc(p.label)}</span>`).join('');
      const caps = D.caps.filter((p) => c.caps.includes(p.id))
        .map((p) => `<span class="tag cap">${esc(CAP_SHORT[p.id] || p.label)}</span>`).join('');
      const metrics = c.metrics
        ? `<div class="case-metrics"><b>指标</b>${esc(c.metrics)}</div>` : '';
      const sources = c.sources.map((s2) => (
        `<a href="${esc(s2.url)}" target="_blank" rel="noopener noreferrer">${esc(s2.title)} ↗</a>`
      )).join('');

      return `<article class="case-item">
        <div class="case-rail"><span class="case-dot" style="--c:${s.color}"></span></div>
        <div>
          <div class="case-top">
            <span class="case-date">${date}</span>
            <span class="badge badge-stage" style="--stage-c:${s.color}">${esc(s.label)}</span>
          </div>
          <h3 class="case-title">${esc(c.title)}</h3>
          <p class="case-summary">${esc(c.summary)}</p>
          ${metrics}
          <div class="case-tags">${pains}${caps}</div>
          <div class="case-src"><span class="src-label">来源</span>${sources}</div>
        </div>
      </article>`;
    }).join('');

    $('#modalBody').innerHTML = `
      <div class="m-badges">
        <span class="badge badge-tier" style="--tier-c:${t.color}">${esc(t.label)}</span>
        <span class="badge badge-stage" style="--stage-c:${st.color}">${esc(st.label)}</span>
      </div>
      <h2 class="m-title" id="modalTitle">${esc(i.name)}</h2>
      <div class="m-meta">${esc(i.type)} · ${esc(i.region)} · ${esc(i.asset_note)}</div>
      <p class="m-summary">${esc(i.summary)}</p>
      <div class="m-stats">
        <span class="m-stat">${i.cases.length} 个公开案例</span>
        <span class="m-stat">最近披露 ${i.lastYear}</span>
        <span class="m-stat">覆盖 ${i.painSet.size} 类痛点</span>
        <span class="m-stat">涉及 ${i.capSet.size} 项华为能力</span>
      </div>
      <div class="case-list">${cases}</div>
      <p class="m-note">以上条目均来自公开信息，仅为合作事件的整理与转述；合作范围、项目状态与指标口径以机构及华为官方披露为准。未公开披露的合作不在本页。</p>`;

    $('#modal').hidden = false;
    document.body.classList.add('modal-open');
    const card = $('.modal-card');
    if (card) card.scrollTop = 0;
  }

  function closeModal() {
    $('#modal').hidden = true;
    document.body.classList.remove('modal-open');
  }

  /* ---------------- 06 ecosystem ---------------- */

  function renderEco() {
    $('#ecoGrid').innerHTML = D.partners.map((p) => (
      `<article class="eco-card">
        <div class="eco-head">
          <span class="eco-name">${esc(p.name)}</span>
          <span class="eco-role">${esc(p.role)}</span>
        </div>
        <p class="eco-summary">${esc(p.summary)}</p>
        <a class="eco-link" href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">查看公开来源 ↗</a>
      </article>`
    )).join('');
  }

  /* ---------------- events ---------------- */

  function bind() {
    $('#searchInput').addEventListener('input', (e) => {
      state.q = e.target.value.trim().toLowerCase();
      applyFilters();
    });

    $('#modal').addEventListener('click', (e) => {
      if (e.target.closest('[data-close]')) closeModal();
    });

    document.addEventListener('click', (e) => {
      const remove = e.target.closest('[data-remove]');
      if (remove) {
        const spec = remove.getAttribute('data-remove');
        const idx = spec.indexOf(':');
        const dim = spec.slice(0, idx);
        const raw = spec.slice(idx + 1);
        if (dim === 'q') {
          state.q = '';
          $('#searchInput').value = '';
        } else {
          state[dim].delete(dim === 'year' ? Number(raw) : raw);
        }
        applyFilters();
        return;
      }

      if (e.target.closest('[data-clear]')) {
        clearAll();
        return;
      }

      const f = e.target.closest('[data-f]');
      if (f) {
        activateFrom(f);
        return;
      }

      const card = e.target.closest('[data-inst]');
      if (card) openModal(card.getAttribute('data-inst'));
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !$('#modal').hidden) {
        closeModal();
        return;
      }
      if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[data-f], [data-inst]')
        && e.target.tagName !== 'BUTTON') {
        e.preventDefault();
        e.target.click();
      }
    });
  }

  boot();
})();
