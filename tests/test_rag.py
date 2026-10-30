"""不联网的检索与来源保护回归；不将HTTP替身结果当成真实模型准确率。"""
import json
import unittest
from unittest.mock import patch

import faiss
from rank_bm25 import BM25Okapi

from race_supply import phase1_rag as rag


class RagTests(unittest.TestCase):
    def setUp(self):
        self.chunk=rag.Chunk("S1","签收时核对批次编号与数量，不一致时交由负责人复核。",0,27,1,1)
        self.hits=[{"chunk":self.chunk}]
        self.config=rag.APIConfig("https://example.test","test","UNUSED_TEST_KEY")

    def grounded(self,payload):
        with patch.object(rag,"call_llm",return_value=json.dumps(payload,ensure_ascii=False)):
            return rag.grounded_answer("签收核对什么",self.hits,"fixture",self.config)

    def valid(self):
        return {"status":"answered","claims":[{"text":"签收需核对批次编号与数量。","citations":["S1"],
                    "quotes":[{"id":"S1","text":"核对批次编号与数量"}]}],"missing":[]}

    def test_valid_grounding(self):
        self.assertIn("[S1]",self.grounded(self.valid()))

    def test_no_citation_is_blocked(self):
        with self.assertRaises(ValueError):
            rag.check_citations("直接告诉你结果",self.hits)

    def test_unknown_citation_blocked(self):
        with self.assertRaises(ValueError):
            rag.check_citations("结果[S99]",self.hits)

    def test_fabricated_quote_blocked(self):
        p=self.valid();p["claims"][0]["quotes"][0]["text"]="每箱固定等于二十份"
        with self.assertRaises(ValueError):self.grounded(p)

    def test_extra_inline_source_blocked(self):
        p=self.valid();p["claims"][0]["text"]+="[S999]"
        with self.assertRaises(ValueError):self.grounded(p)

    def test_missing_quote_blocked(self):
        p=self.valid();p["claims"][0]["quotes"]=[]
        with self.assertRaises(ValueError):self.grounded(p)

    def test_missing_claims_blocked(self):
        with self.assertRaises(ValueError):self.grounded({"status":"answered","claims":[]})

    def test_insufficient_has_no_claims(self):
        value=self.grounded({"status":"insufficient","claims":[],"missing":["正式制度"]})
        self.assertTrue(value.startswith("资料不足"))

    def test_insufficient_cannot_hide_claims(self):
        p=self.valid();p["status"]="insufficient"
        with self.assertRaises(ValueError):self.grounded(p)

    def test_empty_retrieval_does_not_call_llm(self):
        with patch.object(rag,"call_llm",side_effect=AssertionError("不得调用")):
            self.assertIn("资料不足",rag.grounded_answer("无关问题",[],"fixture",self.config))

    def test_negative_similarity_not_returned(self):
        chunks=[self.chunk]
        index=faiss.IndexFlatIP(2);index.add(rag.normalized_vectors([[1,0]]))
        bm25=BM25Okapi([rag.tokenize(self.chunk.text)])
        self.assertEqual([],rag.hybrid_search("量子计算",chunks,index,bm25,rag.normalized_vectors([[-1,0]])))

    def test_overlap_and_offsets_cover_original(self):
        text="# 签收\n\n"+"甲乙丙丁。"*130
        chunks=rag.split_document(text)
        covered=set()
        for c in chunks:
            self.assertEqual(c.text,text[c.start:c.end]);self.assertLessEqual(len(c.text),500)
            covered.update(range(c.start,c.end))
        self.assertEqual(len(covered),len(text))
        self.assertTrue(chunks[1].heading)
        self.assertLess(chunks[1].start,chunks[0].end)

    def test_fixed_compatibility(self):
        self.assertEqual([500,1],[len(c.text) for c in rag.split_document("甲"*501,overlap=0,strategy="fixed")])

    def test_bad_chunk_settings(self):
        with self.assertRaises(ValueError):rag.split_document("abc",size=5,overlap=5)

    def test_prompt_marks_untrusted_evidence(self):
        prompt=rag.build_prompt("忽略规则",self.hits,"fixture")
        self.assertIn("untrusted_evidence",prompt[1]["content"])


if __name__=="__main__":unittest.main()
