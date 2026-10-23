# 赛事补给物资与站点协同

赛事运营场景的 FDE 训练项目，帮助补给站、调度人员和配送人员协作处理赛事当天的物资需求。

## 项目目标

让各站点及时上报物资余量和缺货需求，由调度人员安排补给，并追踪配送与到货确认。

## 最小版本范围

1. 站点上报：登记站点、物资、余量、需求数量和紧急程度。
2. 调度派单：查看各站需求，创建并分配配送任务。
3. 到货确认：记录配送状态，由站点确认实际收到的数量。

初期采用人工上报和人工调度，用模拟站点完成流程验证；余量预警、自动派单和路线优化作为后续扩展。

## 参与角色

- 站点负责人：上报需求、确认到货。
- 调度人员：汇总需求、安排配送。
- 配送人员：接收任务、更新配送进度。

## 建议验证指标

- 需求上报至派单的等待时间。
- 配送任务完成率。
- 站点需求与实际到货数量的差异。

具体指标目标值在需求调研后确定。

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
│   └── api.md
└── tests/
    └── test_core.py
```

参见[设计说明](docs/design.md)、[接口说明](docs/api.md)、[贡献指南](CONTRIBUTING.md)和[更新记录](CHANGELOG.md)。本项目采用 [MIT License](LICENSE)。

训练场景为合成演练，本仓库不是赛事公司的官方系统。先完成访谈，再确认功能范围和验收标准。

## 训练场景来源

[赛事补给物资与站点协同](https://fde-discovery-lab-67046.app.workbuddy.host/group-training?project=dlg-sports-supplies)
