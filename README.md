# 赛事补给物资与站点协同

赛事运营场景的 FDE 训练项目，帮助补给站、调度人员和配送人员协作处理赛事当天的物资需求。

## 最新访谈结论

已归档六个角色的 **36/36 条合成演练事实**。报告指向的下一版方向是：对配送批次进行**只读核对与追溯**，重点处理关联编号、箱/份换算、版本交接和缺项清单。详见[访谈原文](docs/discovery/README.md)、[需求分析](docs/requirements.md)和[设计说明](docs/design.md)。

36/36 是采集覆盖率，不能视为需求已确认或验收通过。报告中 60 条验收样本与 80 条基线记录尚未提供原始数据。

## 原始功能设想（早期演示）

让各站点及时上报物资余量和缺货需求，由调度人员安排补给，并追踪配送与到货确认。

## 已有演示范围

1. 创建需求：记录站点、物资及正整数需求数量。
2. 模拟派单：指定配送人员，将需求状态改为已派单。
3. 模拟签收：记录签收人员，将需求状态改为已签收。

上述功能仅是早期本地内存演示。访谈报告限定外部系统只读，不开放写回、外部消息或执行接口，因此正式原型范围需按需求分析重新确认。

## 早期演示角色

- 站点负责人：上报需求、确认到货。
- 调度人员：汇总需求、安排配送。
- 配送人员：接收任务、更新配送进度。

## 报告建议验证指标

- 出库至站点的可追溯情况，以及单位转换依据。
- 结果是否关联来源与人工复核记录。
- 处理工时、返工次数和确认完整率。

依据报告 f42/f51，具体口径和目标值仍需确认，详见[验收草案](docs/requirements.md#验收草案与数字口径)。

## 当前状态

当前为 Python 项目骨架，包含内存版命令行演示。支持需求创建、派单和签收，退出后不保存数据。尚无网页、数据库或登录功能；业务流程仍需访谈验证。

## 安装与运行

需要 Python 3.10 或更高版本。在仓库根目录执行：

```sh
python -m venv .venv
# Windows PowerShell 激活环境：
.venv\Scripts\Activate.ps1
# macOS / Linux：source .venv/bin/activate
python -m pip install -r requirements.txt
python -m race_supply.main --demo
python -m unittest discover -s tests -v
```

演示使用虚构数据，展示“待调度 → 已派单 → 已签收”。

## 项目目录

```text
├── README.md
├── LICENSE
├── .gitignore
├── CHANGELOG.md
├── CONTRIBUTING.md
├── requirements.txt
├── pyproject.toml
├── src/
│   └── race_supply/
│       ├── __init__.py
│       ├── main.py
│       ├── core/
│       │   ├── __init__.py
│       │   └── supplies.py
│       └── utils/
│           ├── __init__.py
│           └── validation.py
├── docs/
│   ├── design.md
│   ├── api.md
│   ├── requirements.md
│   └── discovery/
│       ├── README.md
│       ├── discovery-report.md
│       └── discovery-report.json
├── tests/
│   └── test_core.py
├── examples/
│   └── demo.py
├── scripts/
│   ├── build.sh
│   └── deploy.sh
├── config/
│   └── default.yaml
├── data/
│   ├── README.md
│   └── sample_requests.csv
└── .github/
    ├── workflows/
    │   └── ci.yml
    ├── ISSUE_TEMPLATE/
    │   ├── bug_report.md
    │   └── feature_request.md
    └── PULL_REQUEST_TEMPLATE.md
```

参见[设计说明](docs/design.md)、[接口说明](docs/api.md)、[贡献指南](CONTRIBUTING.md)和[更新记录](CHANGELOG.md)。本项目采用 [MIT License](LICENSE)。

训练场景为合成演练，本仓库不是赛事公司的官方系统。角色事实采集已完成；继续确认待决问题、原型范围和验收标准。

## 示例、构建与本地部署

安装后运行 `python examples/demo.py`，读取仓库中的虚构 CSV 数据。

以下脚本需要 Bash；Windows 可使用 Git Bash 或 WSL。构建生成 wheel，部署在指定的新目录建立虚拟环境并安装 wheel，不会启动线上服务：

```sh
bash scripts/build.sh
bash scripts/deploy.sh ./work/local-demo
```

目标目录须不存在，部署后会输出运行命令。构建可能需要访问 Python 包索引。`config/default.yaml` 是未来应用的配置草案，当前命令行不会加载它。

GitHub Actions 在推送及 Pull Request 时验证 Python 3.10/3.12 的安装、测试、示例、构建与本地部署。Issue 和 Pull Request 已提供模板。

## 训练场景来源

[赛事补给物资与站点协同](https://fde-discovery-lab-67046.app.workbuddy.host/group-training?project=dlg-sports-supplies)
