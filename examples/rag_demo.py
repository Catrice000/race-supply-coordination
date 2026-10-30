"""复用src的检索示例，默认不调用付费对话API。

安装requirements.txt后运行：python examples/rag_demo.py --device dml
传入--answer才调用配置的LLM，其余参数直接交给原型CLI。
"""

from pathlib import Path
import sys

from race_supply.phase1_rag import main


if __name__ == "__main__":
    # 样例仍为演练报告；按本文件定位，不依赖调用者当前目录。
    args = sys.argv[1:]
    answer = "--answer" in args
    args = [arg for arg in args if arg != "--answer"]
    if "--document" not in args and not any(arg.startswith("--document=") for arg in args):
        document = Path(__file__).resolve().parents[1] / "docs" / "discovery-report.md"
        args = ["--document", str(document), *args]
    if not answer and "--retrieval-only" not in args:
        args.append("--retrieval-only")
    sys.argv = [sys.argv[0], *args]
    main()
