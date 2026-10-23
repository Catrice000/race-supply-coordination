"""安装项目后运行：python examples/demo.py。数据均为虚构。"""

import csv
from pathlib import Path

from race_supply.core.supplies import assign_request, confirm_receipt, create_request


def main() -> None:
    sample = Path(__file__).resolve().parents[1] / "data" / "sample_requests.csv"
    with sample.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            request = create_request(row["station"], row["item"], int(row["quantity"]))
            request = assign_request(request, row["courier"])
            request = confirm_receipt(request, row["receiver"])
            print(f"{request.station} | {request.item} × {request.quantity} | {request.status.value}")


if __name__ == "__main__":
    main()
