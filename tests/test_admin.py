"""管理员能力回归：任务、JWT、模板快照和审计；向量任务使用明确的测试替身。"""
import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import jwt
import test_web as base


class AdminTests(unittest.TestCase):
    # 复用隔离数据库与登录夹具，不重复计数已有Web回归用例。
    setUpClass=classmethod(base.WebTests.setUpClass.__func__)
    tearDownClass=classmethod(base.WebTests.tearDownClass.__func__)
    setUp=base.WebTests.setUp
    tearDown=base.WebTests.tearDown
    client=base.WebTests.client
    def kb_upload(self,raw=None,name="rules.md"):
        return self.admin.post("/admin/documents",data={"title":"签收规则","series":"试点签收","kind":"scenario"},
                               files={"file":(name,raw if raw is not None else "# 签收\n\n核对签收批次和数量。".encode())})

    def test_jwt_claims_and_tampering(self):
        token=self.alice.cookies.get("race_session")
        claims=jwt.decode(token,options={"verify_signature":False})
        self.assertEqual(900,claims["exp"]-claims["iat"])
        self.alice.cookies.clear()
        self.alice.cookies.set("race_session",token+"tampered")
        self.assertEqual(401,self.alice.get("/auth/me").status_code)

    def test_jwt_expiration_and_logout(self):
        token=self.alice.cookies.get("race_session")
        self.alice.post("/auth/logout")
        self.alice.cookies.clear();self.alice.cookies.set("race_session",token)
        self.assertEqual(401,self.alice.get("/auth/me").status_code)

    def test_current_db_role_wins(self):
        uid=self.admin.get("/auth/me").json()["id"]
        with self.app.state.store.connection() as db:db.execute("UPDATE users SET role='employee' WHERE id=?",(uid,))
        self.assertEqual(403,self.admin.get("/admin/prompts/current").status_code)

    def test_all_admin_read_pages_require_role(self):
        for path in ["/admin","/admin/knowledge","/admin/prompt-editor","/admin/audit","/admin/overview","/admin/documents","/admin/prompts/current","/admin/prompts/versions","/admin/conversations"]:
            self.assertEqual(403,self.alice.get(path).status_code,path)

    def test_ssr_pages(self):
        for path in ["/admin","/admin/knowledge","/admin/prompt-editor","/admin/audit"]:
            result=self.admin.get(path)
            self.assertEqual(200,result.status_code,result.text)
            self.assertIn("text/html",result.headers["content-type"])

    def test_upload_auto_chunks_and_ready(self):
        r=self.kb_upload();self.assertEqual(202,r.status_code,r.text)
        did,jid=r.json()["document_id"],r.json()["job_id"]
        self.assertEqual("completed",self.admin.get(f"/admin/jobs/{jid}").json()["status"])
        self.assertTrue(self.admin.get(f"/admin/documents/{did}/chunks").json())
        self.assertEqual([],self.alice.get("/api/documents").json())

    def test_failed_index_cannot_publish(self):
        self.app.state.admin_data.indexer=lambda *a: (_ for _ in ()).throw(RuntimeError("provider failed"))
        r=self.kb_upload().json();did=r["document_id"]
        self.assertEqual("failed",self.admin.get(f"/admin/jobs/{r['job_id']}").json()["status"])
        self.assertEqual(422,self.admin.post(f"/admin/documents/{did}/publish",json={"confirmed":True}).status_code)

    def test_bad_utf8_job_error(self):
        r=self.kb_upload(b"\xff\xfe").json()
        self.assertEqual("failed",self.admin.get(f"/admin/jobs/{r['job_id']}").json()["status"])

    def test_soft_delete_removes_from_retrieval(self):
        r=self.kb_upload().json();did=r["document_id"]
        self.assertEqual(200,self.admin.post(f"/admin/documents/{did}/publish",json={"confirmed":True}).status_code)
        self.assertTrue(self.alice.get("/api/documents").json())
        self.assertEqual(200,self.admin.delete(f"/admin/documents/{did}").status_code)
        self.assertEqual([],self.alice.get("/api/documents").json())
        self.assertEqual(404,self.admin.get(f"/admin/documents/{did}").status_code)

    def test_prompt_versions_conflict_and_rollback(self):
        old=self.admin.get("/admin/prompts/current").json()
        r=self.admin.put("/admin/prompts/current",json={"content":"只根据有效赛事资料回答，说明来源。","expected_version":old["id"]})
        self.assertEqual(200,r.status_code,r.text)
        self.assertNotEqual(old["id"],r.json()["id"])
        self.assertEqual(409,self.admin.put("/admin/prompts/current",json={"content":"冲突版本","expected_version":old["id"]}).status_code)
        self.assertEqual(old["id"],self.admin.post(f"/admin/prompts/versions/{old['id']}/activate").json()["id"])

    def test_sse_uses_prompt_snapshot_and_records_version(self):
        seen=[]
        def fake(question,docs,cache,only,context,prompt):
            seen.append(prompt)
            return {"answer":"测试回答","sources":[],"status":"answered"}
        old=self.app.state.admin_data.current_prompt()
        new=self.app.state.admin_data.save_prompt("赛事测试模板，必须引用来源。",old["id"],1)
        with patch("race_supply.web.retrieve_and_answer",side_effect=fake):
            r=self.alice.post("/chat",json={"session_id":None,"question":"签收要求"})
        self.assertIn("event: done",r.text)
        self.assertEqual([new["content"]],seen)
        conversations=self.alice.get("/sessions").json()
        messages=self.alice.get(f"/sessions/{conversations[0]['id']}/messages").json()
        self.assertEqual(new["id"],messages[-1]["prompt_version"])

    def test_audit_filters_flags_and_review(self):
        cid=self.alice.post("/api/chat",json={"question":"签收异常"}).json()["conversation_id"]
        detail=self.admin.get(f"/admin/conversations/{cid}").json();mid=detail["messages"][-1]["id"]
        uid=self.alice.get("/api/me").json()["id"]
        r=self.admin.post(f"/admin/messages/{mid}/flags",json={"reason":"需要核对签收数量","category":"suspected_rule_violation"})
        self.assertEqual("pending",r.json()["status"])
        rows=self.admin.get("/admin/conversations",params={"employee_id":uid,"from_ts":time.time()-100,"to_ts":time.time()+10,"flag_status":"pending"}).json()
        self.assertEqual([cid],[r["id"] for r in rows])
        fid=r.json()["id"]
        self.assertEqual(200,self.admin.patch(f"/admin/flags/{fid}",json={"status":"dismissed","conclusion":"已人工核实，为演练数据"}).status_code)
        self.assertEqual([],self.admin.get("/admin/conversations",params={"flag_status":"pending"}).json())

    def test_mutation_denied_for_employee(self):
        self.assertEqual(403,self.alice.put("/admin/prompts/current",json={"content":"x","expected_version":1}).status_code)
        self.assertEqual(403,self.alice.delete("/admin/documents/1").status_code)

    def test_ssr_escapes_document_text(self):
        raw=b'# Header\n<script>alert(1)</script>'
        did=self.kb_upload(raw).json()["document_id"]
        html=self.admin.get(f"/admin/knowledge/{did}").text
        self.assertIn("&lt;script&gt;",html)
        self.assertNotIn("<script>alert(1)</script>",html)

    def test_scanned_pdf_requires_ocr(self):
        import io
        from pypdf import PdfWriter
        w=PdfWriter();w.add_blank_page(width=595,height=842)
        buf=io.BytesIO();w.write(buf)
        r=self.kb_upload(buf.getvalue(),"scanned.pdf").json()
        job=self.admin.get(f"/admin/jobs/{r['job_id']}").json()
        self.assertEqual("failed",job["status"])
        self.assertIn("OCR",job["error"])

    def test_text_pdf_extracts_page_and_chunks(self):
        import io
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
        w=PdfWriter();page=w.add_blank_page(width=595,height=842)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):w._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 20 700 Td (Check supply batch number and quantity.) Tj ET')
        page[NameObject('/Contents')]=w._add_object(stream)
        buf=io.BytesIO();w.write(buf)
        r=self.kb_upload(buf.getvalue(),"policy.pdf").json()
        self.assertEqual("completed",self.admin.get(f"/admin/jobs/{r['job_id']}").json()["status"])
        chunks=self.admin.get(f"/admin/documents/{r['document_id']}/chunks").json()
        self.assertEqual(1,chunks[0]["page"])
        self.assertIn("supply batch",chunks[0]["text"])


if __name__=="__main__":unittest.main()
