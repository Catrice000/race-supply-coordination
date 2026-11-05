"""统计本项目交付源码，输出可复核的代码规模报告。

统计范围为 src、tests、scripts、examples。排除环境、缓存、模型、
构建输出、文档、样例数据和第三方依赖。非空行包含注释，
因此同时提供 Python 的 token 代码行口径以避免混淆。
"""

import argparse
import io
import json
from pathlib import Path
import tokenize

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {".py", ".js", ".css", ".html", ".sh", ".ps1"}
GROUPS = ("src", "tests", "scripts", "examples")
EXCLUDED = {"__pycache__", "node_modules", "vendor", ".venv", "dist", "build"}


def python_code_lines(text):
    """按 token 标记行，排除空白、注释及独立字符串文档。

    多行字符串中的 SQL 等实际表达式仍属于代码。此值只用于 Python，
    不与 HTML、CSS 的物理行进行混合宣传。
    """
    import ast

    docs = set()
    tree = ast.parse(text)
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for item in ast.walk(tree):
        if isinstance(item, owners) and item.body:
            first = item.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                if isinstance(first.value.value, str):
                    docs.update(range(first.lineno, first.end_lineno + 1))
    ignored = {
        tokenize.ENCODING,
        tokenize.ENDMARKER,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.NEWLINE,
        tokenize.NL,
        tokenize.COMMENT,
    }
    included = set()
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type not in ignored:
            included.update(range(token.start[0], token.end[0] + 1))
    return len(included - docs)


def collect():
    records = []
    for group in GROUPS:
        for path in sorted((ROOT / group).rglob("*")):
            if not path.is_file() or path.suffix not in EXTENSIONS:
                continue
            relative = path.relative_to(ROOT)
            if EXCLUDED.intersection(relative.parts):
                continue
            text = path.read_text(encoding="utf-8-sig")
            lines = text.splitlines()
            record = {
                "path": relative.as_posix(),
                "group": group,
                "physical_lines": len(lines),
                "nonblank_lines": sum(bool(line.strip()) for line in lines),
            }
            if path.suffix == ".py":
                record["python_code_lines"] = python_code_lines(text)
            records.append(record)
    totals = {}
    for group in GROUPS:
        files = [r for r in records if r["group"] == group]
        totals[group] = {
            "files": len(files),
            "physical_lines": sum(r["physical_lines"] for r in files),
            "nonblank_lines": sum(r["nonblank_lines"] for r in files),
        }
    return {
        "scope": list(GROUPS),
        "note": "物理行和非空行均包含注释；第三方库、文档、数据和构建产物未计入。",
        "groups": totals,
        "total_physical_lines": sum(r["physical_lines"] for r in records),
        "total_nonblank_lines": sum(r["nonblank_lines"] for r in records),
        "files": records,
    }


def main():
    parser = argparse.ArgumentParser(description="统计源码规模")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON 报告")
    args = parser.parse_args()
    report = collect()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    for name, row in report["groups"].items():
        print(
            f"{name}: {row['files']} files, "
            f"{row['physical_lines']} physical lines, "
            f"{row['nonblank_lines']} nonblank lines"
        )
    print(f"Total physical lines: {report['total_physical_lines']}")
    print(f"Total nonblank lines: {report['total_nonblank_lines']}")
    print(report["note"])


if __name__ == "__main__":
    main()
