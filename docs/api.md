# 接口说明

当前提供 Python 函数和命令行，没有 HTTP API。

## 阶段1资料问答

模块：`race_supply.phase1_rag`。依赖安装后可用`python -m race_supply.phase1_rag`或`race-supply-rag`运行，参数见`--help`。

| 核心函数 | 用途 |
| --- | --- |
| `split_document(text, size=500)` | 按字符切片，保留片段编号与原文行范围 |
| `embed_texts(texts, config, is_query=False)` | 本地DirectML/CPU或兼容在线API向量化 |
| `load_or_build_index(chunks, config, cache_dir)` | 校验并复用或重建FAISS索引 |
| `hybrid_search(question, chunks, index, bm25, query_vector)` | BM25与向量检索，经RRF融合返回top3 |
| `build_prompt(question, hits, source)` | 组装赛事领域限制、资料与来源 |
| `call_llm(messages, config)` | 独立对话API调用，支持替换供应商 |
| `check_citations(answer, hits)` | 拦截不属于本次检索片段的方括号引用 |

`APIConfig`封装地址、模型、密钥环境变量名、向量后端与设备。函数仍集中在单文件，便于阶段1验证；引用编号正确不等于答案获得充分事实支持。

示例：`python examples/rag_demo.py --device dml --question "签收核对什么？"`默认只检索；加`--answer`才调用配置的对话API。`--self-test`不联网、不计费。

配置详见[配置说明](../config/README.md)和[GPU运行说明](本地GPU运行说明.md)。

## 早期内存业务演示

导入模块：`race_supply.core.supplies`。

| 函数 | 参数 | 返回值与条件 |
| --- | --- | --- |
| `create_request(station, item, quantity)` | 站点、物资、正整数数量 | 返回待调度的 `SupplyRequest` |
| `assign_request(request, courier)` | 待调度需求、配送人员 | 返回已派单的新记录 |
| `confirm_receipt(request, receiver)` | 已派单需求、签收人员 | 返回已签收的新记录 |

空白文本、非法数量和不允许的状态转换抛出 `ValueError`。数量不接受布尔值、小数或字符串。以上函数不会修改原记录，也不会自动保存数据。

```python
from race_supply.core.supplies import create_request, assign_request, confirm_receipt

request = create_request("演示站点A", "饮用水（箱）", 10)
request = assign_request(request, "演示配送员")
request = confirm_receipt(request, "演示站点负责人")
print(request.status.value)  # 已签收
```

安装后运行 `python -m race_supply.main --demo` 或 `race-supply --demo`。`--help` 显示帮助，`--version` 显示版本。不传参数显示帮助。
