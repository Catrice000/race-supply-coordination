# 接口说明

当前提供 Python 函数和命令行，没有 HTTP API。

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
