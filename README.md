# 中国中小银行关系网络

面向全国银行业法人的关系网络与机会图谱：区域、业务、IT 投入、董监高、行政处罚与华为能力机会。
前端为静态站点（D3 v7 + Canvas），数据由本地 LangGraph 智能体按公开来源增量更新。

- 数据基准：机构名录以 NFRA 公开法人名单 PDF 为准（当前 2024-12-31 版，3,811 家法人）。
- 仅使用公开信息；缺失字段显示“未披露”，不推算、不补全。
- 处罚为 NFRA 官方公开处罚记录（近 5 年，持续增量抓取）。
- 董监高仅收录公开披露的董事、监事与高级管理人员；跨行关系线必须有原文明确记载，同名仅进入人工核验清单。
- 华为机会分为规则分 + 公开证据；无证据封顶 55 分并标注“推断/待验证”。

## 快速开始

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 填入 TAVILY_API_KEY / DEEPSEEK_API_KEY
```

## 常用命令

```bash
# 机构名录与静态图谱（首次/名录变更后）
python3 -m cbi.registry
python3 -m cbi.build

# 单类采集（可轮换、可缓存）
python3 -m cbi.news_agent --limit 20
python3 -m cbi.financial_agent --limit 20 --group city
python3 -m cbi.governance_agent --limit 10
python3 -m cbi.website_agent --group city
python3 -m cbi.nfra_penalty crawl --max-pages 10

# LangGraph 编排（自动持久化 + 重建 + 校验）
python3 -m cbi.graph_agents --mode daily --limit 20 --penalties 10
python3 -m cbi.graph_agents --mode weekly --limit 20 --group city
python3 -m cbi.graph_agents --mode bootstrap --limit 124 --concurrency 4
```

`--mode bootstrap` 用于 124 家城商行的首轮全覆盖；日常与周更通过 `scripts/install_cron.sh` 安装。

## 自动化

- `scripts/daily.sh`：每日轮换新闻 + 董监高采集 + 少量处罚增量，完成后提交并推送。
- `scripts/weekly.sh`：每周财务数据增量（上市行结构化接口、非上市行公开披露）。
- `scripts/deploy.sh`：提交 `frontend/data` 与数据文件并推送；GitHub Actions 部署 Cloudflare Pages，
  本地 `wrangler` 作为兜底。
- `scripts/install_cron.sh`：安装 06:12 每日、周日 07:24 每周任务。

GitHub Actions 需要仓库 Secrets：`CLOUDFLARE_API_TOKEN`、`CLOUDFLARE_ACCOUNT_ID`。

## 目录

```
cbi/                采集与构建（registry/news/financial/governance/penalty/build/graph_agents）
data/               原始数据与缓存（cache/state 不入库）
frontend/           静态站点与分片数据
scripts/            定时与部署脚本
```

## 免责声明

本站为公开信息整理，不保证完整与实时，不构成投资建议。处罚与人事信息以监管机构及银行官方披露为准。
