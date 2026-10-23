"""业务入口使用的输入校验。"""


def require_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}不能为空，且必须为字符串")
    return value.strip()


def require_quantity(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("数量必须为正整数")
    return value
