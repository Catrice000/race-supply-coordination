# 赛事补给协同助手源码归档

本目录收录问答助手原型及可运行的 Web MVP。安装和账户初始化见[仓库 README](../README.md)。

| 交付项 | 源码入口 | 验证入口 |
| --- | --- | --- |
| 问答助手归档 | [问答助手运行与演示记录](问答助手归档.md)、[命令行问答原型](race_supply/phase1_rag.py) | [独立检索示例](../examples/rag_demo.py) |
| MVP 工程 | [FastAPI 应用](race_supply/web.py)、[SQLite 存储](race_supply/storage.py)、[员工界面](race_supply/static/index.html)、[管理后台模板](race_supply/templates/) | 员工聊天、管理后台、运营工作台 |
| Agent 框架 | [工具与执行循环](race_supply/agent.py)、[接口与确认流程](race_supply/agent_api.py) | [工具约束](../docs/Agent工具与调用约束.md)、[编排与评测](../docs/Agent编排与评测.md) |
| 知识库 | [文档与知识管理](race_supply/admin.py)、[PDF 处理](race_supply/pdf_worker.py)、[混合检索](race_supply/phase1_rag.py) | 管理员进入 `/admin/knowledge` 上传、索引、发布，员工检索已发布资料 |

员工从浏览器提问，FastAPI 校验身份并按访问权限检索个人资料和共享知识。普通问答返回带引用的结果，Agent 模式还可查询已发布的批次快照、执行规则校验和预览内部复核草稿。员工确认后才能创建草稿，提交人工复核需要再次确认。运营台账与知识文档分别管理。

项目为单企业本机 MVP。交付要求与验收边界见[需求定义](../docs/需求定义文档.md)。知识库资料可使用[演练报告](../docs/discovery/discovery-report.md)作为示例。
