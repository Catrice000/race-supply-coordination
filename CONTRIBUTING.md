# 贡献指南

1. Fork 仓库并创建功能分支。
2. 按 README 创建虚拟环境、安装项目。
3. 业务代码放在 `src/race_supply/core/`，通用函数放在 `utils/`。
4. 修改行为时补充测试，同步设计/接口文档与更新记录。
5. 运行 `python -m unittest discover -s tests -v` 和 `python -m race_supply.main --demo`。
6. 提交 Pull Request，说明问题、改动与验证结果。

业务规则请附访谈证据，或明确标为假设。示例使用虚构数据，请勿提交真实业务数据或密钥。缺陷与建议可通过 GitHub Issues 提交。
