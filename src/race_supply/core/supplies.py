"""内存演示，实际业务规则需经访谈确认。"""

from dataclasses import dataclass, replace
from enum import Enum

from race_supply.utils.validation import require_quantity, require_text


class Status(str, Enum):
    PENDING = "待调度"
    ASSIGNED = "已派单"
    RECEIVED = "已签收"


@dataclass(frozen=True)
class SupplyRequest:
    station: str
    item: str
    quantity: int
    status: Status = Status.PENDING
    courier: str = ""
    receiver: str = ""


def create_request(station: str, item: str, quantity: int) -> SupplyRequest:
    return SupplyRequest(
        station=require_text(station, "站点"),
        item=require_text(item, "物资"),
        quantity=require_quantity(quantity),
    )


def assign_request(request: SupplyRequest, courier: str) -> SupplyRequest:
    if request.status != Status.PENDING:
        raise ValueError("仅待调度的需求可以派单")
    return replace(request, courier=require_text(courier, "配送人员"), status=Status.ASSIGNED)


def confirm_receipt(request: SupplyRequest, receiver: str) -> SupplyRequest:
    if request.status != Status.ASSIGNED:
        raise ValueError("仅已派单的需求可以签收")
    return replace(request, receiver=require_text(receiver, "签收人员"), status=Status.RECEIVED)
