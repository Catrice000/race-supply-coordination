"""真实本地向量检索评测，不调用对话API，不读取或测试密钥。

36道合成开发题：24条可定位事实、12条无关问题。测检索命中与无关拒绝，
不能称为最终答案准确率、提示注入防御率或客户验收通过率。
"""
import argparse
import json
from pathlib import Path
import sys

from rank_bm25 import BM25Okapi
from race_supply import phase1_rag as rag


def main():
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device",choices=["cpu","dml","auto"],default="auto")
    parser.add_argument("--min-score",type=float,default=0.50)
    parser.add_argument("--output",type=Path,default=root/"work/evaluation.json")
    args=parser.parse_args()
    dataset=json.loads((root/"tests/fixtures/rag_cases.json").read_text(encoding="utf-8"))
    text="# 合成测试规则（不得用于真实赛事）\n\n"+"\n\n".join(f"## {r['topic']}\n{r['rule']}" for r in dataset["rules"])
    chunks=rag.split_document(text)
    config=rag.APIConfig("","Xenova/bge-small-zh-v1.5","UNUSED",backend="local",device=args.device)
    index=rag.load_or_build_index(chunks,config,root/"work/eval_cache")
    bm25=BM25Okapi([rag.tokenize(c.text) for c in chunks])
    questions=[r["question"] for r in dataset["rules"]]+dataset["unanswerable"]
    query_vectors=rag.embed_texts(questions,config,is_query=True)
    rows=[]
    for i,question in enumerate(questions):
        hits=rag.hybrid_search(question,chunks,index,bm25,query_vectors[i:i+1],args.min_score)
        positive=i<len(dataset["rules"])
        expected=dataset["rules"][i]["expected"] if positive else None
        passed=any(expected in h["chunk"].text for h in hits) if positive else not hits
        rows.append({"id":i+1,"question":question,"answerable":positive,"expected_excerpt":expected,
                     "passed":passed,"hits":[{"id":h["chunk"].id,"cosine":h["cosine"]} for h in hits]})
    positives=rows[:24];negatives=rows[24:]
    result={"dataset":"synthetic-development-36","model":config.model,"device":args.device,
            "min_score":args.min_score,"chunk_count":len(chunks),"notice":dataset["notice"],
            "metrics":{"evidence_recall_at_3":sum(r["passed"] for r in positives)/24,
                       "unrelated_rejection_rate":sum(r["passed"] for r in negatives)/12},"cases":rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result["metrics"],ensure_ascii=False));print(f"报告：{args.output}")
    # 开发回归门槛；调整阈值前应阅读逐题失败记录，而不是只追求综合数字。
    if result["metrics"]["evidence_recall_at_3"]<0.90 or result["metrics"]["unrelated_rejection_rate"]<0.90:
        return 1
    return 0


if __name__=="__main__":sys.exit(main())
