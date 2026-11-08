/* Bank search dropdown over the static search index. */

const MAX_RESULTS = 16;

function norm(text) {
  return String(text || "").toLowerCase().replace(/\s+/g, "");
}

function haystack(item) {
  return [
    item.name,
    item.full,
    item.short,
    item.province,
    item.city,
    item.type,
    ...(item.alias || []),
  ].map(norm).join("|");
}

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export class BankSearch {
  constructor(input, resultsEl, items, onPick, filterFn = null) {
    this.input = input;
    this.resultsEl = resultsEl;
    this.items = items;
    this.onPick = onPick;
    this.filterFn = filterFn;
    this.matches = [];
    this.activeIndex = -1;
    this._prepared = items.map((item) => ({ item, hay: haystack(item) }));

    this.input.addEventListener("input", () => this.search(this.input.value));
    this.input.addEventListener("focus", () => {
      if (this.input.value.trim()) this.search(this.input.value);
    });
    this.input.addEventListener("keydown", (e) => this._onKey(e));
    this.input.addEventListener("blur", () => {
      window.setTimeout(() => this.close(), 160);
    });
    this.resultsEl.addEventListener("mousedown", (e) => {
      const row = e.target.closest("[data-index]");
      if (!row) return;
      e.preventDefault();
      this._pick(Number(row.dataset.index));
    });
  }

  search(raw) {
    const tokens = String(raw || "").toLowerCase().trim().split(/\s+/).map(norm).filter(Boolean);
    const q = tokens.join("");
    if (!q) {
      this.close();
      return;
    }
    const hits = [];
    for (const entry of this._prepared) {
      if (this.filterFn && !this.filterFn(entry.item)) continue;
      if (!tokens.every((t) => entry.hay.includes(t))) continue;
      const name = norm(entry.item.name);
      let score = 10;
      if (name === q) score = 100;
      else if (name.startsWith(q)) score = 85;
      else if (norm(entry.item.short).startsWith(q)) score = 75;
      else if (norm(entry.item.full).startsWith(q)) score = 65;
      else if (entry.hay.includes(q)) score = 45;
      score += Math.min(18, Math.log10(Math.max(10, entry.item.asset || 10)) * 4);
      hits.push({ item: entry.item, score });
    }
    hits.sort((a, b) => b.score - a.score || (b.item.asset || 0) - (a.item.asset || 0));
    this.matches = hits.slice(0, MAX_RESULTS).map((h) => h.item);
    this.activeIndex = this.matches.length ? 0 : -1;
    this._render();
  }

  close() {
    this.resultsEl.classList.remove("open");
    this.resultsEl.innerHTML = "";
    this.matches = [];
    this.activeIndex = -1;
  }

  _render() {
    if (!this.matches.length) {
      this.resultsEl.innerHTML = `<div class="search-item"><b>未找到匹配银行</b><span>试试名称、简称或城市</span></div>`;
      this.resultsEl.classList.add("open");
      return;
    }
    this.resultsEl.innerHTML = this.matches
      .map((item, i) => {
        const meta = [item.type, item.province, item.city, item.listed ? `上市 ${item.listed}` : ""]
          .filter(Boolean)
          .join(" · ");
        const asset = item.asset
          ? `总资产 ${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(item.asset)} 亿元`
          : "资产未披露";
        return `<div class="search-item${i === this.activeIndex ? " active" : ""}" data-index="${i}">
          <b>${esc(item.name)}</b><span>${esc(meta)}${meta ? " · " : ""}${esc(asset)}</span>
        </div>`;
      })
      .join("");
    this.resultsEl.classList.add("open");
  }

  _onKey(e) {
    if (e.key === "Escape") {
      this.close();
      this.input.blur();
      return;
    }
    if (!this.matches.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const dir = e.key === "ArrowDown" ? 1 : -1;
      this.activeIndex = (this.activeIndex + dir + this.matches.length) % this.matches.length;
      this._render();
      const active = this.resultsEl.querySelector(".search-item.active");
      if (active) active.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();
      this._pick(this.activeIndex >= 0 ? this.activeIndex : 0);
    }
  }

  _pick(index) {
    const item = this.matches[index];
    if (!item) return;
    this.input.value = item.name;
    this.close();
    this.onPick(item);
  }
}
