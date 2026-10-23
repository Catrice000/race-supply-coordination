"""命令行演示入口。"""

import argparse

from race_supply import __version__
from race_supply.core.supplies import assign_request, confirm_receipt, create_request


def main() -> None:
    parser = argparse.ArgumentParser(description="赛事补给物资与站点协同（内存演示）")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--demo", action="store_true", help="运行虚构需求的完整流程")
    args = parser.parse_args()
    if not args.demo:
        parser.print_help()
        return
    request = create_request("演示站点A", "饮用水（箱）", 10)
    print(f"{request.station}：{request.item} × {request.quantity}，{request.status.value}")
    request = assign_request(request, "演示配送员")
    print(f"{request.status.value}：配送人员 {request.courier}")
    request = confirm_receipt(request, "演示站点负责人")
    print(f"{request.status.value}：签收人员 {request.receiver}")


if __name__ == "__main__":
    main()
