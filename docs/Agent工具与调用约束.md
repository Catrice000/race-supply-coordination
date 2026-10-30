# 赛事补给Agent：工具、状态机与使用说明

在聊天页勾选“启用Agent工具”后，模型可以自主选择检索、读取批次、校验和查看草稿；仅勾选“仅检索”仍走原来的无对话API检索。Agent模式会调用配置的对话API，不自动重试。若需要创建草稿，每次提问前还需明确勾选“本次允许生成内部复核草稿”，发送后该授权复选框自动重置。

本机没有连接真实库存/配送系统。批次来自管理员导入的结构化快照，知识来自已发布资料。工具提交只产生内部`pending_review`记录，不执行正式调度、审批、库存写回或签收。

## 已实现工具与Skill流程

| 工具 | 参数 | 前置条件 |
| --- | --- | --- |
| `search_knowledge` | query | 按当前用户权限检索有效知识；命中文档才能返回对应结构化规则快照 |
| `get_batch_records` | batch_id、可选event_id/station_id | 仅返回授权批次；同编号对应多个站点/赛事时要求补充定位 |
| `validate_batch` | record_snapshot_id、rule_snapshot_id | 本次运行先完成上述两次读取；规则需适用于该赛事/站点 |
| `create_review_draft` | validation_id、draft_type、idempotency_key | 当前用户明确授权、有效校验允许该类草稿；幂等键由服务端运行编号确定 |
| `get_draft` | draft_id | 仅查看本人草稿及真实状态 |
| `submit_for_review` | draft_id、confirmation_token | 不提供给模型；仅在界面人工确认后由后端执行对应提交逻辑 |

完整Function Calling JSON Schema见登录后的`GET /agent/tools`，定义由Pydantic严格参数模型生成，不接收额外字段或隐式类型转换。Skill注册信息也在该接口返回：

1. `knowledge_qa`：检索→有来源的回答/说明资料不足。
2. `batch_check`：检索规则＋读取批次→确定性校验→差异/缺项结果。
3. `receipt_review`：批次核对→用户明确允许→创建并查看草稿→界面人工确认→内部人工复核队列。

编排器最多6轮模型决策、12次工具调用；同轮最多4次调用，后端按顺序逐项校验。模型自由文本不会被当成业务操作成功依据。知识问答的最终JSON结论仍需通过来源与原文摘录校验；批次核对与草稿状态由工具真实结果渲染。

## 管理员准备数据

先进入`/admin/knowledge`上传并发布一份真实获授权资料，或明确标记为演练资料，再进入`/admin/agent`。

**以下全部为合成演练，不可作为实际换算或签收制度。**演练原文可使用：

```markdown
# 演练换算依据
饮用水每箱24份；签收核对批次与数量。
```

批次导入示例，employee_ids需改为实际员工ID：

```json
{
  "batch_id": "B001",
  "event_id": "EVT1",
  "station_id": "S01",
  "employee_ids": [2],
  "records": [
    {"record_id":"D1","kind":"demand","material_id":"water","quantity":"24","unit":"份"},
    {"record_id":"O1","kind":"outbound","material_id":"water","quantity":"1","unit":"箱"},
    {"record_id":"R1","kind":"receipt","material_id":"water","quantity":"20","unit":"份"}
  ]
}
```

规则导入示例，document_id需改为已发布的对应文档ID：

```json
{
  "document_id": 1,
  "event_id": "EVT1",
  "station_id": null,
  "confirmed": true,
  "conversions": [
    {"material_id":"water","base_unit":"份","factors":{"份":"1","箱":"24"},"source_quote":"饮用水每箱24份"}
  ]
}
```

数量、换算系数均用十进制字符串，后端使用Decimal计算，不使用模型推测或浮点数量。原文摘录必须实际存在，**系数是否受该原文支持仍由管理员人工核对**；字符串匹配不能证明语义正确。存在站点专用规则时，不允许通用规则覆盖。每次导入同赛事/站点批次或规则新版本，会使对应旧快照失效。

## 后端强制顺序与人工确认

```text
search_knowledge + get_batch_records
             ↓ 两项均完成
       validate_batch
             ↓ checks_passed 且 allowed_draft_types 包含目标类型
 用户本次明确授权 → create_review_draft → get_draft
             ↓ 浏览器人工查看并确认
 POST /agent/drafts/{id}/confirmation
             ↓ 120秒有效、绑定本人和草稿内容的随机凭证
 POST /agent/drafts/{id}/submit → pending_review
```

`checks_passed`只表示草稿的前置数据/依据检查通过，不表示业务没有异常。数量差异允许创建复核草稿；缺少换算依据则阻止建草稿。可唯一定位的记录缺失可生成`missing_records`草稿；正常数量匹配不能无故生成异常草稿。

校验凭证15分钟有效，绑定运行、用户、记录版本、规则版本和内容指纹。创建与凭证消费在同一数据库事务中完成；重复同一请求返回原草稿，不重复建单。记录/规则改变、文档撤回、规则新版本或新站点专用规则生效后，旧凭证不可继续使用。

确认凭证仅返回浏览器，不进入模型上下文、聊天记录或工具参数日志。后端存储其哈希，凭证一次性消费。即使模型自行调用`submit_for_review`并猜到合法凭证，模型调度入口也明确拒绝。退出/登录过期后接口仍要求重新鉴权。

## 接口与运行记录

- `POST /agent/chat`：`{session_id, question, allow_draft}`，返回回答、来源、工具轨迹、草稿ID与运行ID。当前Agent入口为一次JSON结果；原`POST /chat`继续提供SSE问答。
- `GET /agent/runs`、`GET /agent/runs/{id}`：本人运行与工具记录。失败不自动重放，先检查草稿列表，避免创建新运行导致重复业务意图。
- `GET /agent/drafts`、`GET /agent/drafts/{id}`：本人草稿及状态。
- 确认、提交接口如上；普通错误包含code和中文message。
- `POST /admin/agent/batches`、`POST /admin/agent/rules`：管理员导入快照和规则。
- `/admin/agent`：管理员查看数据与内部复核队列。

运行、读取顺序、校验结果、草稿、确认与工具日志保存在现有SQLite中。模型和传入文档不能修改这些权限判断。业务模板仍可由管理员编辑，但不能关闭程序校验。

## 验证边界

回归测试覆盖越权、跳过读取、伪造校验、缺少换算、版本失效、幂等、赛事/站点歧义、过期凭证、模型直接提交及一次性人工确认。浏览器完成Agent勾选→工具调用轨迹→草稿查看→人工确认→待复核状态验证，使用脚本模型替身，不调用对话API。

Function Calling已按现有OpenAI兼容API接入，模型供应商沿用现有配置。后续已补充DeepSeek真实模型测试：实际完成检索、读取批次、校验、生成草稿四步，详见[真实模型运行验证](真实模型运行验证.md)。脚本模型回归与单次真实调用都不能等同于全面的Agent准确率评测。
