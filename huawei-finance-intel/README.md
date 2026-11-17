# 华为金融合作图谱 · 公开案例看板

按**体量**、**痛点**、**案例阶段**梳理华为在金融行业的公开合作：谁被公开点名、项目走到哪一步、解决什么问题、由谁联合交付。

> 这是公开信息梳理，**不是华为官方客户名单**，也不构成投资或采购建议。未公开披露的客户与项目不在收录范围。

## 在线地址

- 看板：https://huawei-finance-intel.pages.dev/
- 数据：`data/finance.json`

## 看板内容

| 区块 | 内容 |
| --- | --- |
| 总览 | 全球客户数（5,600）、国家数、全球前 100 银行覆盖（53）、生态伙伴（11,000）与国内收录规模 |
| 01 体量分层 | 7 个体量层的机构数量与最高合作阶段（生产上线 / 试点 / 联合创新 / 生态适配） |
| 02 痛点 × 体量 | 10 类建设痛点的热力矩阵，可下钻到机构 |
| 03 能力覆盖 | GaussDB、鲲鹏、昇腾、华为云 Stack、OceanStor、FusionInsight、鸿蒙、盘古、CloudEngine |
| 04 时间线 | 2018–2025 年案例分布与阶段构成 |
| 05 机构与案例 | 29 家机构 / 37 个可回溯案例，支持体量、阶段、痛点、能力、年份、关键词组合筛选 |
| 06 生态伙伴 | 8 家联合交付伙伴及公开来源 |
| 07 方法与口径 | 收录范围、阶段判定、体量口径与免责声明 |

## 数据

`data/finance.json` 的结构：

```jsonc
{
  "updated": "2026-11-17",
  "scope": "…",
  "global_context": { "customers": 5600, "countries": 80, "top100_banks": 53, "partners": 11000, "as_of": "2025-09", "source": "…", "url": "…" },
  "tiers":   [{ "id": "state", "label": "国有大型银行", "range": "总资产 >15 万亿元", "color": "#e5484d" }],
  "pains":   [{ "id": "core", "label": "核心系统分布式改造 / 主机下移" }],
  "caps":    [{ "id": "gaussdb", "label": "GaussDB / 分布式数据库" }],
  "stages":  [{ "id": "live", "label": "生产上线", "color": "#2dd4bf" }],
  "institutions": [{
    "id": "psbc", "name": "中国邮政储蓄银行", "short": "邮储银行",
    "tier": "state", "type": "国有大型银行", "region": "全国",
    "asset_note": "总资产 >15 万亿元（2025 年报档）", "summary": "…",
    "cases": [{
      "year": 2022, "month": "04", "title": "…", "stage": "live",
      "pains": ["core", "db", "cloud"], "caps": ["gaussdb", "kunpeng", "huawei_cloud"],
      "summary": "…", "metrics": "…",
      "sources": [{ "title": "…", "url": "https://…" }]
    }]
  }],
  "partners": [{ "name": "神州信息", "role": "…", "summary": "…", "url": "https://…" }]
}
```

维护约定：

- 每个案例必须附至少 1 条公开来源链接；无法回溯的条目不写入。
- 金额、性能指标按原文转述；缺失字段留空，不推算。
- 机构资产仅在已披露时写入 `asset` / `asset_year`，否则只给分层与 `asset_note`。

## 本地预览

```bash
cd huawei-finance-intel
python3 -m http.server 8000
# 打开 http://localhost:8000/
```

无构建步骤：`index.html` + `styles.css` + `app.js`，图表与筛选逻辑均在浏览器端完成。

## 部署

GitHub：本仓库源码。

Cloudflare Pages：

```bash
npx wrangler pages deploy . --project-name huawei-finance-intel --branch main --commit-dirty=true
```

## 主要来源

- 华为官方新闻与案例库（huawei.com / huaweicloud.com）
- 华为 FinAgent Booster 发布稿（2025-09，全球客户与伙伴数据）
- 各银行、证券、保险机构与生态伙伴的公开新闻稿、年报与公众号文章
- 财经媒体对项目投产、联合创新与鸿蒙适配的公开报道

每条案例的具体来源链接见看板机构详情或 `data/finance.json` 中的 `sources` 字段。
