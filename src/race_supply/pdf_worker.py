"""PDF文字提取子进程。父进程限制输入大小、执行时限，不执行PDF脚本或附件。"""
import io
import json
import sys


def extract(raw):
    from pypdf import PdfReader
    reader=PdfReader(io.BytesIO(raw))
    if reader.is_encrypted:
        raise ValueError("加密PDF请先解密后上传")
    if len(reader.pages)>50:
        raise ValueError("本机试点PDF不能超过50页")
    pages=[]
    for number,page in enumerate(reader.pages,1):
        text=page.extract_text() or ""
        # 任一空白/图片页均要求人工检查，避免默默遗漏扫描页上的规则。
        if not text.strip():
            raise ValueError(f"第{number}页未提取到文字，需要OCR或人工提供文本")
        pages.append({"number":number,"text":text})
        if sum(len(p["text"]) for p in pages)>100_000:
            raise ValueError("提取文本超过10万字符")
    if not pages:
        raise ValueError("PDF没有可提取页面")
    return pages


if __name__=="__main__":
    try:
        print(json.dumps({"pages":extract(sys.stdin.buffer.read(1_000_001))},ensure_ascii=True))
    except Exception as error:
        # 不返回内部文件路径或解析器堆栈；已知输入错误才显示中文原因。
        print(json.dumps({"error":str(error) if isinstance(error,ValueError) else "PDF解析失败，请检查格式"},ensure_ascii=True))
