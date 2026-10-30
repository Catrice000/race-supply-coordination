# 赛事补给协同助手

面向赛事企业员工与管理员的资料问答和补给协同项目。员工端规划为聊天页，支持上传、查询；管理员端规划为知识库维护与记录查看，后续服务端采用异步FastAPI。

**目前已实现阶段1命令行RAG原型**：读取本地赛事补给Markdown，每500字符切片，使用本地中文BGE模型向量化并存入FAISS，以BM25＋向量检索融合取top3，再调用可配置的大模型生成带来源的回答。Windows可通过DirectML使用GPU。网页、文件上传接口、登录权限、知识图谱展示和数据库仍属于后续规划，详见[架构方案](docs/架构方案.md)。

## 安装与运行

需要Python 3.10+。在仓库根目录执行（Windows PowerShell）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env.local
# 在.env.local中填写自己的DEEPSEEK_API_KEY
.\.venv\Scripts\python.exe -m race_supply.phase1_rag --document docs/discovery-report.md --device dml
```

也可通过`./scripts/run_rag.ps1 --document docs/discovery-report.md --device dml`启动。首次运行需联网下载模型。向量计算使用本地GPU，回答使用DeepSeek API，FAISS检索使用CPU；供应商可通过环境变量替换。

没有密钥时可先运行只检索示例，不调用对话API：

```powershell
.\.venv\Scripts\python.exe examples/rag_demo.py --device dml --question "签收数量不一致时如何处理？"
```

macOS/Linux安装同一依赖文件后使用`.venv/bin/python`及`--device cpu`，也可用`bash scripts/run_rag.sh --document docs/discovery-report.md --device cpu`启动。完整配置见[GPU运行说明](docs/本地GPU运行说明.md)与[配置说明](config/README.md)。

## 项目框架

```text
race-supply-coordination/
├── README.md                         # 项目介绍、安装和运行
├── LICENSE                           # MIT许可证
├── .gitignore                        # 密钥、依赖、模型与缓存排除规则
├── CHANGELOG.md                      # 更新记录
├── CONTRIBUTING.md                   # 贡献指南
├── requirements.txt                  # 项目安装＋RAG依赖入口
├── requirements-rag.txt              # RAG依赖版本
├── pyproject.toml                    # Python打包与命令行入口
├── .env.example                      # 无真实密钥的配置模板
├── src/race_supply/
│   ├── __init__.py
│   ├── main.py                       # 早期内存演示入口
│   ├── phase1_rag.py                 # 当前单文件问答原型与可复用核心
│   ├── core/supplies.py              # 早期需求、派单、签收演示逻辑
│   └── utils/validation.py           # 通用校验
├── docs/
│   ├── README.md                     # 全部文档导航
│   ├── 架构方案.md
│   ├── 本地GPU运行说明.md
│   ├── 业务现状.jpg
│   ├── 业务现状_耗时耗材标注.jpg
│   ├── AI数智化流程图.jpg
│   ├── 报价单.md
│   ├── 赛事运营行业与补给协同方案研究.docx
│   ├── discovery-report.md
│   ├── discovery/                    # Markdown/JSON原始访谈归档
│   ├── requirements.md
│   ├── design.md
│   └── api.md
├── tests/test_core.py                # 业务测试；RAG自检位于原型文件
├── examples/
│   ├── demo.py                       # 早期CSV业务演示
│   └── rag_demo.py                   # 调用src原型的检索示例
├── scripts/
│   ├── run_rag.ps1                   # Windows启动
│   ├── run_rag.sh                    # Bash启动
│   ├── build.sh                      # 构建wheel
│   └── deploy.sh                     # 本地安装wheel
├── config/
│   ├── README.md                     # 生效配置与缓存位置
│   └── default.yaml                  # 后续应用配置草案
├── data/
│   ├── README.md
│   └── sample_requests.csv           # 虚构演示数据
└── .github/
    ├── workflows/ci.yml
    ├── ISSUE_TEMPLATE/
    └── PULL_REQUEST_TEMPLATE.md
```

阶段1继续采用单文件形式；后续接入FastAPI时，再按架构方案拆分检索、模型适配与服务模块。

## 验证与早期演示

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m race_supply.phase1_rag --self-test
.\.venv\Scripts\python.exe -m race_supply.main --demo
.\.venv\Scripts\python.exe examples/demo.py
```

RAG自检不联网、不计费，覆盖切片、中文检索、FAISS持久化、HTTP适配与引用编号校验。2026-10-30已在RTX 4060 Laptop GPU上完成45片真实向量建库、混合检索、DeepSeek回答验证，详见运行说明。单次链路成功不代表答案质量评测已完成。

早期派单演示仅在内存中运行，退出不保存。构建与本地部署脚本需要Bash；部署脚本只安装基础Python包，问答依赖另用requirements-rag.txt安装。GitHub Actions运行基础检查及离线RAG自检，不使用客户密钥或下载向量模型。

## 业务资料与边界

已归档六个角色的36/36条合成演练事实。资料提出只读核对与追溯方向，重点包括关联编号、箱/份换算、版本交接和缺项清单。[需求分析](docs/requirements.md)保留证据映射及待确认问题。36/36仅表示采集覆盖率，不能视为真实需求确认或验收通过；报告提及的60条验收样本和80条基线记录尚未提供原始数据。

原型不能确认实时库存、实际签收状态或审批权限。模型、索引和本地密钥均不提交仓库。业务规则须提供正式依据或明确标注为假设。

[文档导航](docs/README.md) · [接口说明](docs/api.md) · [贡献指南](CONTRIBUTING.md) · [更新记录](CHANGELOG.md) · [MIT许可证](LICENSE)

[训练场景来源](https://fde-discovery-lab-67046.app.workbuddy.host/group-training?project=dlg-sports-supplies)：本项目为合成演练，不是赛事公司的官方系统。
