"""Configuration for city-bank-intel."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
FRONTEND_DIR = ROOT / "frontend"
FRONTEND_DATA = FRONTEND_DIR / "data"

REGISTRY_DIR = DATA_DIR / "registry"
BANKS_DIR = DATA_DIR / "banks"
PENALTIES_DIR = DATA_DIR / "penalties"
NEWS_DIR = DATA_DIR / "news"
REPORTS_DIR = DATA_DIR / "reports"
CACHE_DIR = DATA_DIR / "cache"

REGISTRY_PATH = REGISTRY_DIR / "institutions.json"
PCAS_PATH = REGISTRY_DIR / "pcas-code.json"
GRAPH_PATH = FRONTEND_DATA / "graph.json"
MANIFEST_PATH = FRONTEND_DATA / "manifest.json"

for d in (DATA_DIR, REGISTRY_DIR, BANKS_DIR, PENALTIES_DIR, NEWS_DIR, REPORTS_DIR,
          CACHE_DIR, FRONTEND_DATA, FRONTEND_DATA / "edges", FRONTEND_DATA / "banks"):
    d.mkdir(parents=True, exist_ok=True)

# Official registry PDFs. The 2024-12-31 edition is machine-readable; the
# loader prefers a newer national edition when one is available.
REGISTRY_PDF_CANDIDATES = [
    # Add newer national PDFs here as they are published.
    "https://www.nfra.gov.cn/chinese/docfile/2025/e80bc363856b4032901e40a79d6a8486.pdf",
]
REGISTRY_LOCAL_PDF = REGISTRY_DIR / "nfra_banks.pdf"
REGISTRY_AS_OF = "2024-12-31"

PCAS_URL = (
    "https://raw.githubusercontent.com/modood/"
    "Administrative-divisions-of-China/master/dist/pcas-code.json"
)

BANK_TYPES = {
    "开发性金融机构", "政策性银行", "国有大型商业银行", "股份制商业银行",
    "城市商业银行", "民营银行", "外资法人银行", "住房储蓄银行", "直销银行",
    "农村商业银行", "农村合作银行", "农村信用社", "农村资金互助社", "村镇银行",
}

CORE_TYPES = {"城市商业银行", "农村商业银行", "村镇银行"}

# Types eligible for the full 3,619 universe shown in the UI.
TYPE_LABELS = {
    "城市商业银行": "城商行",
    "农村商业银行": "农商行",
    "村镇银行": "村镇银行",
    "农村合作银行": "农合行",
    "农村信用社": "农信社",
    "农村资金互助社": "资金互助社",
    "民营银行": "民营银行",
    "外资法人银行": "外资法人行",
    "国有大型商业银行": "国有大行",
    "股份制商业银行": "股份行",
    "开发性金融机构": "开发性金融机构",
    "政策性银行": "政策性银行",
    "住房储蓄银行": "住房储蓄银行",
    "直销银行": "直销银行",
}

BUSINESS_TAGS = [
    "公司金融", "零售金融", "普惠小微", "财富管理", "金融市场",
    "国际业务", "绿色金融", "科技金融", "养老金融",
]

BUSINESS_KEYS = {
    "公司金融": ["公司金融", "对公", "企业金融", "公司银行", "公司业务"],
    "零售金融": ["零售", "个人金融", "个人银行", "消费金融", "信用卡"],
    "普惠小微": ["普惠", "小微", "三农", "涉农", "乡村振兴"],
    "财富管理": ["财富管理", "理财", "私人银行", "资产管理"],
    "金融市场": ["金融市场", "同业", "资金业务", "债券", "投资银行", "投行"],
    "国际业务": ["国际业务", "跨境", "外汇", "贸易金融", "海外"],
    "绿色金融": ["绿色金融", "ESG", "碳金融", "绿色信贷"],
    "科技金融": ["科技金融", "科创", "科技型", "专精特新"],
    "养老金融": ["养老金融", "养老金", "适老"],
}

# Huawei capability modules, from public HiFS / Fintelligent material.
HUAWEI_CAPABILITIES = [
    {
        "id": "hw_core", "name": "金融数字核心 6.0", "short": "分布式新核心",
        "category": "核心系统",
        "desc": "分布式新核心、单元化多活、跨 Region 容灾、高稳数据库，面向核心系统现代化。",
        "keywords": ["核心系统", "分布式核心", "新核心", "主机下移", "单元化", "信创", "数据库替换", "GaussDB", "多活"],
        "entry": "核心系统改造/信创替换",
    },
    {
        "id": "hw_data", "name": "金融数据智能 6.0", "short": "数据智能",
        "category": "数据平台",
        "desc": "数据中台、流批融合、实时数据架构、监管报送与数据治理。",
        "keywords": ["数据中台", "数据治理", "数据平台", "实时数据", "流批", "监管报送", "数据仓库", "湖仓"],
        "entry": "数据中台/监管报送",
    },
    {
        "id": "hw_agent", "name": "Fintelligent · openJiuwen 智能体工厂", "short": "智能体工厂",
        "category": "AI 平台",
        "desc": "金融级多智能体开发、运行与治理平台，支持智能体规模化生产。",
        "keywords": ["智能体", "大模型", "Agent", "AI原生", "openJiuwen", "智能体平台", "AI应用"],
        "entry": "AI 原生应用/智能体平台",
    },
    {
        "id": "hw_token", "name": "Token 工厂 · TokeNexus", "short": "Token 工厂",
        "category": "AI 平台",
        "desc": "大模型 Token 规划、生产、调度与成本运营，支撑规模化推理。",
        "keywords": ["Token", "推理", "算力调度", "大模型平台", "模型服务", "降本"],
        "entry": "大模型算力/推理运营",
    },
    {
        "id": "hw_agentic_data", "name": "Financial Agentic Data", "short": "数据知识工厂",
        "category": "数据平台",
        "desc": "数据产线 + 知识产线，把数据供给升级为面向智能体的知识供给。",
        "keywords": ["知识库", "知识图谱", "RAG", "数据知识", "语料", "知识供给"],
        "entry": "金融知识库/知识工程",
    },
    {
        "id": "hw_aicc", "name": "AICC 智能交互", "short": "智能客服",
        "category": "业务场景",
        "desc": "智能客服、远程银行、语音交互，低时延、高分流。",
        "keywords": ["智能客服", "远程银行", "呼叫中心", "AICC", "语音", "坐席", "客户服务"],
        "entry": "智能客服/远程银行",
    },
    {
        "id": "hw_credit", "name": "智慧信贷尽调", "short": "智慧信贷",
        "category": "业务场景",
        "desc": "三层 Agent 协同的信贷尽调、文档解析与报告生成。",
        "keywords": ["信贷", "尽调", "贷款", "授信", "风控", "文档解析", "报告生成"],
        "entry": "信贷尽调/智能风控",
    },
    {
        "id": "hw_fraud", "name": "反欺诈智能体", "short": "反欺诈",
        "category": "业务场景",
        "desc": "实时反欺诈、风险识别与误报压降，适配高频交易与账户风险。",
        "keywords": ["反欺诈", "欺诈", "风险识别", "账户风险", "反洗钱", "AML", "合规"],
        "entry": "反欺诈/反洗钱/合规",
    },
    {
        "id": "hw_cloud", "name": "华为云 Stack · 数据不出域", "short": "金融云",
        "category": "基础设施",
        "desc": "金融云底座、Landing Zone、数据不出域与安全基线。",
        "keywords": ["华为云", "云平台", "数据不出域", "混合云", "信创云", "安全基线"],
        "entry": "金融云/混合云",
    },
    {
        "id": "hw_ascend", "name": "Atlas · 昇腾 AI 算力", "short": "AI 算力",
        "category": "基础设施",
        "desc": "通算/智算融合、Atlas 超节点、ModelArts 算力调度。",
        "keywords": ["算力", "昇腾", "Atlas", "GPU", "智算", "模型训练", "推理集群"],
        "entry": "AI 算力/智算中心",
    },
    {
        "id": "hw_network", "name": "星河 AI 金融数据中心网络", "short": "金融网络",
        "category": "基础设施",
        "desc": "高韧性金融数据中心网络、毫秒级故障切换与勒索防护。",
        "keywords": ["数据中心", "网络", "灾备", "多活", "勒索", "高可用"],
        "entry": "数据中心/灾备网络",
    },
    {
        "id": "hw_xinchuang", "name": "鲲鹏 / 欧拉 / 信创底座", "short": "信创底座",
        "category": "基础设施",
        "desc": "鲲鹏算力、openEuler、GaussDB 等全栈信创适配。",
        "keywords": ["信创", "鲲鹏", "欧拉", "国产化", "自主创新", "替代"],
        "entry": "信创/国产化替代",
    },
    {
        "id": "hw_mobile", "name": "鸿蒙 / 移动金融", "short": "移动金融",
        "category": "业务场景",
        "desc": "鸿蒙生态、移动银行、适老化与全渠道体验。",
        "keywords": ["鸿蒙", "HarmonyOS", "手机银行", "移动金融", "适老", "全渠道"],
        "entry": "移动银行/全渠道",
    },
    {
        "id": "hw_green", "name": "数字能源 / 绿色数据中心", "short": "绿色数据中心",
        "category": "基础设施",
        "desc": "数据中心能源、绿色低碳与智能运维。",
        "keywords": ["绿色数据中心", "低碳", "能耗", "智能运维", "数字能源"],
        "entry": "绿色数据中心/能源",
    },
]

# Province coordinates (lon, lat) used only for a readable geographic layout.
PROVINCE_CENTROIDS = {
    "北京市": (116.4, 39.9), "天津市": (117.2, 39.1), "河北省": (114.5, 38.0),
    "山西省": (112.5, 37.9), "内蒙古自治区": (111.7, 40.8), "辽宁省": (123.4, 41.8),
    "吉林省": (125.3, 43.9), "黑龙江省": (126.6, 45.8), "上海市": (121.5, 31.2),
    "江苏省": (118.8, 32.1), "浙江省": (120.2, 30.3), "安徽省": (117.3, 31.9),
    "福建省": (119.3, 26.1), "江西省": (115.9, 28.7), "山东省": (117.0, 36.7),
    "河南省": (113.6, 34.8), "湖北省": (114.3, 30.6), "湖南省": (113.0, 28.2),
    "广东省": (113.3, 23.1), "广西壮族自治区": (108.3, 22.8),
    "海南省": (110.3, 20.0), "重庆市": (106.5, 29.6), "四川省": (104.1, 30.7),
    "贵州省": (106.7, 26.6), "云南省": (102.7, 25.0), "西藏自治区": (91.1, 29.6),
    "陕西省": (108.9, 34.3), "甘肃省": (103.8, 36.1), "青海省": (101.8, 36.6),
    "宁夏回族自治区": (106.3, 38.5), "新疆维吾尔自治区": (87.6, 43.8),
    "台湾省": (121.0, 23.7), "香港特别行政区": (114.2, 22.3),
    "澳门特别行政区": (113.5, 22.2),
}

# Listed bank short names (A/H). Kept explicit so the UI never guesses.
LISTED_BANKS = {
    "北京银行": "A", "上海银行": "A", "江苏银行": "A", "南京银行": "A",
    "宁波银行": "A", "杭州银行": "A", "成都银行": "A", "长沙银行": "A",
    "贵阳银行": "A", "郑州银行": "A+H", "青岛银行": "A+H", "苏州银行": "A",
    "西安银行": "A", "厦门银行": "A", "重庆银行": "A+H", "兰州银行": "A",
    "齐鲁银行": "A", "青岛农商行": "A", "渝农商行": "A+H", "常熟银行": "A",
    "紫金银行": "A", "无锡银行": "A", "张家港行": "A", "苏农银行": "A",
    "江阴银行": "A", "沪农商行": "A", "瑞丰银行": "A", "兰州银行": "A",
    "徽商银行": "H", "哈尔滨银行": "H", "盛京银行": "H", "锦州银行": "H",
    "天津银行": "H", "中原银行": "H", "江西银行": "H", "九江银行": "H",
    "贵州银行": "H", "泸州银行": "H", "宜宾银行": "H", "晋商银行": "H",
    "甘肃银行": "H", "威海银行": "H", "东莞农商行": "H", "广州农商行": "H",
    "重庆农商行": "A+H", "上海农商行": "A", "江苏紫金农商行": "A",
    "无锡农商行": "A", "张家港农商行": "A", "苏州农商行": "A",
    "江阴农商行": "A", "浙江绍兴瑞丰农商行": "A", "九台农商行": "H",
}

# DeepSeek / Qwen via OpenAI-compatible endpoints.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-plus")

TAVILY_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"
