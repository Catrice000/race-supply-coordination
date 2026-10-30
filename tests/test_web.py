"""本机Web权限/版本/持久化验证，模型调用注入替身，不使用真实密钥。"""
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from race_supply.web import create_app
from race_supply.storage import Store

HEADERS={"X-Requested-With":"race-supply"}


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 每个测试复制独立数据库；避免每轮重复计算3个600k轮密码派生。
        cls.base=tempfile.TemporaryDirectory()
        store=Store(Path(cls.base.name)/"base.sqlite3")
        for name,role in [("admin","admin"),("alice","employee"),("bob","employee")]:
            store.create_user(name,"only-for-tests-password",role)

    @classmethod
    def tearDownClass(cls):cls.base.cleanup()

    def setUp(self):
        import shutil
        self.tmp=tempfile.TemporaryDirectory();self.folder=Path(self.tmp.name)
        shutil.copyfile(Path(self.base.name)/"base.sqlite3",self.folder/"race_supply.sqlite3")
        self.seen=[]
        def fake(question,documents,cache_dir,retrieval_only):
            self.seen.append(documents)
            return {"answer":"测试替身回答","sources":[],"status":"retrieved"}
        self.app=create_app(self.folder,answerer=fake,allowed_hosts=["testserver"],admin_indexer=lambda chunks,did:None)
        self.admin=self.client("admin");self.alice=self.client("alice");self.bob=self.client("bob")

    def tearDown(self):
        for c in [self.admin,self.alice,self.bob]:c.close()
        self.tmp.cleanup()

    def client(self,name):
        c=TestClient(self.app,headers=HEADERS)
        self.assertEqual(200,c.post("/api/login",json={"username":name,"password":"only-for-tests-password"}).status_code)
        return c

    def upload(self,client,scope="personal",series="签收"):
        r=client.post("/api/documents",data={"title":"签收说明","series":series,"scope":scope,"kind":"scenario"},
                      files={"file":("rules.md","站点：补给A\n物资：饮用水\n批次：批次001\n签收核对批次与数量。".encode(),"text/markdown")})
        self.assertEqual(200,r.status_code,r.text);return r.json()["id"]

    def publish(self,did):
        r=self.admin.post(f"/api/documents/{did}/status",json={"status":"published","confirmed":True})
        self.assertEqual(200,r.status_code,r.text)

    def test_anonymous_blocked(self):
        with TestClient(self.app) as c:self.assertEqual(401,c.get("/api/documents").status_code)

    def test_csrf_header_required(self):
        with TestClient(self.app) as c:self.assertEqual(403,c.post("/api/login",json={}).status_code)

    def test_cross_origin_blocked(self):
        r=self.alice.post("/api/chat",headers={"Origin":"https://evil.example"},json={"question":"签收"})
        self.assertEqual(403,r.status_code)

    def test_employee_cannot_upload_shared(self):
        r=self.alice.post("/api/documents",data={"title":"x","series":"x","scope":"shared"},files={"file":("x.md",b"abc")})
        self.assertEqual(403,r.status_code)

    def test_private_doc_isolation_before_retrieval(self):
        did=self.upload(self.alice)
        self.assertEqual(404,self.bob.get(f"/api/documents/{did}").status_code)
        self.assertEqual(403,self.bob.post("/api/chat",json={"question":"签收","document_ids":[did]}).status_code)
        self.bob.post("/api/chat",json={"question":"签收"})
        self.assertEqual([],self.seen[-1])

    def test_draft_not_visible_to_employee(self):
        did=self.upload(self.admin,"shared")
        self.assertEqual([],self.alice.get("/api/documents").json())
        self.assertEqual(403,self.alice.post("/api/chat",json={"question":"签收","document_ids":[did]}).status_code)

    def test_publish_and_replacement(self):
        first=self.upload(self.admin,"shared");self.publish(first)
        second=self.upload(self.admin,"shared");self.publish(second)
        docs=self.alice.get("/api/documents").json()
        self.assertEqual([second],[d["id"] for d in docs])
        self.assertEqual(403,self.alice.post("/api/chat",json={"question":"签收","document_ids":[first]}).status_code)
        self.alice.post("/api/chat",json={"question":"签收"})
        self.assertEqual([second],[d["id"] for d in self.seen[-1]])

    def test_publish_requires_manual_confirmation(self):
        did=self.upload(self.admin,"shared")
        self.assertEqual(422,self.admin.post(f"/api/documents/{did}/status",json={"status":"published"}).status_code)

    def test_employee_admin_endpoints_denied(self):
        for path in ["/api/admin/logs","/api/admin/graph"]:
            self.assertEqual(403,self.alice.get(path).status_code)

    def test_conversation_ownership(self):
        cid=self.alice.post("/api/chat",json={"question":"签收"}).json()["conversation_id"]
        self.assertEqual(404,self.bob.get(f"/api/conversations/{cid}").status_code)
        self.assertEqual(404,self.bob.post("/api/chat",json={"question":"签收","conversation_id":cid}).status_code)

    def test_messages_persist_in_new_app(self):
        cid=self.alice.post("/api/chat",json={"question":"签收"}).json()["conversation_id"]
        other=Store(self.folder/"race_supply.sqlite3")
        self.assertEqual(2,len(other.messages(cid)))

    def test_graph_sources(self):
        did=self.upload(self.admin,"shared");self.publish(did)
        graph=self.admin.get("/api/admin/graph").json()
        self.assertEqual(3,len(graph["edges"]))
        self.assertTrue(all(e["document_id"]==did and e["quote"] for e in graph["edges"]))

    def test_wrong_file_encoding_rejected(self):
        r=self.alice.post("/api/documents",data={"title":"x","series":"x"},files={"file":("x.md",b"\xff\xfe\x00")})
        self.assertEqual(422,r.status_code)

    def test_oversize_body_rejected(self):
        r=self.alice.post("/api/chat",content=b"x"*1_100_001)
        self.assertEqual(413,r.status_code)

    def test_logout_revokes_session(self):
        self.alice.post("/api/logout")
        self.assertEqual(401,self.alice.get("/api/me").status_code)

    def test_short_password_never_authenticates(self):
        store=self.app.state.store
        store.create_user("length-test","invalid-password","employee")
        with self.assertRaises(PermissionError):store.login("length-test","x","test-length")

    def test_static_page_available(self):
        self.assertIn("赛事补给协同助手",self.alice.get("/").text)
        self.assertEqual(200,self.alice.get("/static/app.js").status_code)


if __name__=="__main__":unittest.main()
