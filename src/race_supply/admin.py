"""管理员路由与本机持久化任务：共享知识、提示词版本、对话人工审计。

任务落SQLite后运行；重启时未完成任务明确标记失败，可人工重建。
本机只运行一个Uvicorn worker；生产任务队列和OCR不在本阶段范围。
"""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import time

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field

from race_supply import phase1_rag as rag

DEFAULT_PROMPT="你是赛事补给协同助手，只根据提供的有效资料回答。不确定时明确说‘现有资料中未找到依据’。回答附引用来源，不得编造补给数量、单位换算、签收要求或审批权限。区分正式制度与演练资料。"


class PromptInput(BaseModel):
    content: str = Field(min_length=1,max_length=8000)
    expected_version: int


class ConfirmInput(BaseModel):
    confirmed: bool = False


class FlagInput(BaseModel):
    category: str = "suspected_rule_violation"
    reason: str = Field(min_length=1,max_length=1000)


class ReviewInput(BaseModel):
    status: str
    conclusion: str = Field(min_length=1,max_length=1000)


class AdminData:
    def __init__(self, store, folder, indexer=None):
        self.store,self.folder,self.indexer=store,folder,indexer or self.build_index
        with store.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS prompt_versions(id INTEGER PRIMARY KEY,content TEXT NOT NULL,creator INTEGER,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS active_prompt(singleton INTEGER PRIMARY KEY CHECK(singleton=1),version_id INTEGER NOT NULL REFERENCES prompt_versions(id));
            CREATE TABLE IF NOT EXISTS uploads(document_id INTEGER PRIMARY KEY REFERENCES documents(id),raw BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS kb_chunks(document_id INTEGER NOT NULL REFERENCES documents(id),ordinal INTEGER NOT NULL,text TEXT NOT NULL,line_start INTEGER,line_end INTEGER,page INTEGER,heading TEXT,PRIMARY KEY(document_id,ordinal));
            CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY,document_id INTEGER NOT NULL REFERENCES documents(id),status TEXT NOT NULL,stage TEXT NOT NULL,error TEXT,created REAL NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS review_flags(id INTEGER PRIMARY KEY,message_id INTEGER NOT NULL REFERENCES messages(id),category TEXT NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',creator INTEGER NOT NULL,reviewer INTEGER,conclusion TEXT,created REAL NOT NULL,updated REAL NOT NULL);
            """)
            if not db.execute("SELECT 1 FROM active_prompt").fetchone():
                pid=db.execute("INSERT INTO prompt_versions(content,created) VALUES(?,?)",(DEFAULT_PROMPT,time.time())).lastrowid
                db.execute("INSERT INTO active_prompt VALUES(1,?)",(pid,))
            db.execute("UPDATE documents SET processing='failed' WHERE id IN (SELECT document_id FROM jobs WHERE status IN ('queued','running'))")
            db.execute("UPDATE jobs SET status='failed',stage='interrupted',error='服务重启，任务未完成，请重新索引' WHERE status IN ('queued','running')")

    def current_prompt(self):
        with self.store.connection() as db:
            return dict(db.execute("SELECT p.* FROM prompt_versions p JOIN active_prompt a ON a.version_id=p.id").fetchone())

    def save_prompt(self, content, expected, uid):
        if not content.strip():raise ValueError("提示词不能为空白")
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT version_id FROM active_prompt").fetchone()[0]!=expected:
                raise ValueError("提示词已被其他管理员修改，请刷新后重试")
            pid=db.execute("INSERT INTO prompt_versions(content,creator,created) VALUES(?,?,?)",(content,uid,time.time())).lastrowid
            db.execute("UPDATE active_prompt SET version_id=?",(pid,))
        self.store.audit(uid,"prompt_update",str(pid))
        return self.current_prompt()

    def rows(self, sql, params=()):
        with self.store.connection() as db:
            return [dict(r) for r in db.execute(sql,params)]

    def document(self,did):
        rows=self.rows("SELECT * FROM documents WHERE id=? AND scope='shared' AND deleted=0",(did,))
        if not rows:raise HTTPException(404,"共享文档不存在或已删除")
        return rows[0]

    def enqueue(self,did):
        self.document(did)
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM jobs WHERE document_id=? AND status IN ('queued','running')",(did,)).fetchone():
                raise HTTPException(409,"该文档正在处理中")
            if not db.execute("SELECT 1 FROM uploads WHERE document_id=?",(did,)).fetchone():
                raise HTTPException(422,"旧文档未保留上传原件，请重新上传新版本")
            # 重建期间撤下发布状态，避免使用新旧混合的检索结果。
            db.execute("UPDATE documents SET processing='queued',status='draft' WHERE id=?",(did,))
            now=time.time()
            return db.execute("INSERT INTO jobs(document_id,status,stage,created,updated) VALUES(?,'queued','queued',?,?)",(did,now,now)).lastrowid

    def stage(self,jid,stage):
        with self.store.connection() as db:
            db.execute("UPDATE jobs SET status='running',stage=?,updated=? WHERE id=?",(stage,time.time(),jid))

    def build_index(self,chunks,did):
        import os
        from race_supply.web import _encoder_lock
        backend=os.getenv("RAG_EMBEDDING_BACKEND","local")
        config=rag.APIConfig(os.getenv("EMBEDDING_BASE_URL","https://openrouter.ai/api/v1"),
                             os.getenv("EMBEDDING_MODEL","Xenova/bge-small-zh-v1.5" if backend=="local" else ""),
                             os.getenv("EMBEDDING_API_KEY_ENV","OPENROUTER_API_KEY"),backend,os.getenv("RAG_DEVICE","auto"))
        config.validate()
        with _encoder_lock:
            rag.load_or_build_index(chunks,config,self.folder/"document_indexes"/str(did))

    def process(self,jid):
        job=self.rows("SELECT * FROM jobs WHERE id=?",(jid,))[0]
        did=job["document_id"]
        try:
            doc=self.document(did)
            raw=self.rows("SELECT raw FROM uploads WHERE document_id=?",(did,))[0]["raw"]
            self.stage(jid,"parsing")
            if Path(doc["filename"]).suffix.lower()==".pdf":
                # 隔离PDF解析，超时结束子进程，不让解析器阻塞服务事件循环。
                result=subprocess.run([sys.executable,"-m","race_supply.pdf_worker"],input=raw,capture_output=True,timeout=30)
                parsed=json.loads(result.stdout)
                if "error" in parsed:raise ValueError(parsed["error"])
                pages=parsed["pages"]
            else:
                pages=[{"number":None,"text":raw.decode("utf-8-sig")}]
            text=""; records=[]
            for page in pages:
                fragment=(f"# PDF第{page['number']}页\n\n" if page["number"] else "")+page["text"]
                base=len(text);line_offset=text.count("\n")
                for chunk in rag.split_document(fragment):
                    records.append((chunk,page["number"],line_offset,base))
                text+=fragment+"\n\n"
            if not text.strip() or len(text)>100_000 or "\x00" in text:
                raise ValueError("需提供非空UTF-8文本，最多10万字符")
            self.stage(jid,"embedding")
            self.indexer([r[0] for r in records],did)
            with self.store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT deleted FROM documents WHERE id=?",(did,)).fetchone()[0]:
                    raise ValueError("处理期间文档已删除")
                db.execute("DELETE FROM kb_chunks WHERE document_id=?",(did,))
                for i,(chunk,page,offset,_) in enumerate(records,1):
                    db.execute("INSERT INTO kb_chunks VALUES(?,?,?,?,?,?,?)",(did,i,chunk.text,chunk.line_start+offset,chunk.line_end+offset,page,chunk.heading))
                db.execute("UPDATE documents SET content=?,processing='ready' WHERE id=?",(text,did))
                db.execute("UPDATE jobs SET status='completed',stage='ready',error=NULL,updated=? WHERE id=?",(time.time(),jid))
        except Exception as error:
            reason=str(error) if isinstance(error,ValueError) else "解析或向量化失败，请检查模型配置与文档格式后重试"
            if isinstance(error,UnicodeError):
                reason="Markdown必须使用UTF-8编码，请转换编码后重新上传"
            elif not any('\u4e00'<=char<='\u9fff' for char in reason):
                reason="解析或向量化失败，请检查模型配置与文档格式后重试"
            with self.store.connection() as db:
                db.execute("UPDATE jobs SET status='failed',stage='failed',error=?,updated=? WHERE id=?",(reason[:300],time.time(),jid))
                db.execute("UPDATE documents SET processing='failed' WHERE id=? AND deleted=0",(did,))


def register_admin(app,store,folder,admin,*,indexer=None):
    data=AdminData(store,folder,indexer)
    app.state.admin_data=data
    router=APIRouter(dependencies=[Depends(admin)])

    @router.get("/admin/overview")
    async def overview():
        return {"documents":data.rows("SELECT processing,count(*) AS count FROM documents WHERE scope='shared' AND deleted=0 GROUP BY processing"),
                "pending_flags":data.rows("SELECT count(*) AS count FROM review_flags WHERE status='pending'")[0]["count"]}

    @router.get("/admin/documents")
    async def documents(status: str | None=None):
        return data.rows("SELECT id,title,filename,series,version,status,processing,kind,created FROM documents WHERE scope='shared' AND deleted=0"+(" AND status=?" if status else "")+" ORDER BY id DESC",(status,) if status else ())

    @router.post("/admin/documents",status_code=202)
    async def upload(background_tasks: BackgroundTasks,file: UploadFile=File(...),title: str=Form(...),series: str=Form(...),kind: str=Form("scenario"),current=Depends(admin)):
        if not title.strip() or not series.strip() or max(len(title),len(series))>120 or kind not in {"scenario","policy"}:
            raise HTTPException(422,"标题和系列需为1至120字符，资料性质无效")
        name=Path((file.filename or "").replace("\\","/")).name
        if Path(name).suffix.lower() not in {".md",".pdf"} or len(name)>200:
            raise HTTPException(422,"仅支持Markdown或文字型PDF，文件名不能超过200字符")
        raw=await file.read(1_000_001);await file.close()
        if not raw or len(raw)>1_000_000:raise HTTPException(413,"文件需为1字节至1MB")
        try:
            did=await asyncio.to_thread(store.add_document,current["id"],title.strip(),series.strip(),"shared",kind,"处理中，尚未生效")
        except ValueError as error:raise HTTPException(422,str(error)) from None
        with store.connection() as db:
            db.execute("UPDATE documents SET filename=?,processing='queued' WHERE id=?",(name,did))
            db.execute("INSERT INTO uploads VALUES(?,?)",(did,raw))
        jid=data.enqueue(did)
        background_tasks.add_task(data.process,jid)
        store.audit(current["id"],"kb_upload",str(did))
        return {"document_id":did,"job_id":jid,"status":"queued"}

    @router.get("/admin/documents/{did}")
    async def document(did: int):return data.document(did)

    @router.get("/admin/documents/{did}/chunks")
    async def chunks(did: int):
        data.document(did)
        return data.rows("SELECT * FROM kb_chunks WHERE document_id=? ORDER BY ordinal",(did,))

    @router.get("/admin/jobs/{jid}")
    async def job(jid: int):
        rows=data.rows("SELECT * FROM jobs WHERE id=?",(jid,))
        if not rows:raise HTTPException(404,"处理任务不存在")
        return rows[0]

    @router.post("/admin/documents/{did}/reindex",status_code=202)
    async def reindex(did: int,background_tasks: BackgroundTasks,current=Depends(admin)):
        jid=data.enqueue(did);background_tasks.add_task(data.process,jid)
        store.audit(current["id"],"kb_reindex",str(did))
        return {"job_id":jid}

    async def transition(did,body,current,status):
        data.document(did)
        if not body.confirmed:raise HTTPException(422,"需人工确认操作")
        try:await asyncio.to_thread(store.change_status,did,status)
        except ValueError as error:raise HTTPException(422,str(error)) from None
        store.audit(current["id"],status,str(did))
        return {"ok":True}

    @router.post("/admin/documents/{did}/publish")
    async def publish(did: int,body: ConfirmInput,current=Depends(admin)):
        return await transition(did,body,current,"published")

    @router.post("/admin/documents/{did}/archive")
    async def archive(did: int,body: ConfirmInput,current=Depends(admin)):
        return await transition(did,body,current,"archived")

    @router.delete("/admin/documents/{did}")
    async def delete(did: int,current=Depends(admin)):
        data.document(did)
        with store.connection() as db:
            db.execute("UPDATE documents SET deleted=1,status='archived' WHERE id=?",(did,))
        store.audit(current["id"],"kb_soft_delete",str(did))
        return {"ok":True,"message":"已撤回检索并软删除，历史审计保留"}

    @router.get("/admin/prompts/current")
    async def prompt():return data.current_prompt()

    @router.put("/admin/prompts/current")
    async def save_prompt(body: PromptInput,current=Depends(admin)):
        try:return data.save_prompt(body.content,body.expected_version,current["id"])
        except ValueError as error:raise HTTPException(409,str(error)) from None

    @router.get("/admin/prompts/versions")
    async def versions():return data.rows("SELECT * FROM prompt_versions ORDER BY id DESC LIMIT 100")

    @router.post("/admin/prompts/versions/{pid}/activate")
    async def activate(pid: int,current=Depends(admin)):
        with store.connection() as db:
            if not db.execute("SELECT 1 FROM prompt_versions WHERE id=?",(pid,)).fetchone():raise HTTPException(404,"模板版本不存在")
            db.execute("UPDATE active_prompt SET version_id=?",(pid,))
        store.audit(current["id"],"prompt_activate",str(pid))
        return data.current_prompt()

    def audit_rows(employee_id=None,from_ts=None,to_ts=None,flag_status=None):
        conditions,params=[],[]
        if from_ts is not None and to_ts is not None and from_ts>to_ts:raise HTTPException(422,"开始时间不能晚于结束时间")
        for clause,value in [("c.user_id=?",employee_id),("m.created>=?",from_ts),("m.created<=?",to_ts)]:
            if value is not None:conditions.append(clause);params.append(value)
        if flag_status:
            if flag_status not in {"pending","confirmed","dismissed"}:raise HTTPException(422,"复核状态无效")
            conditions.append("EXISTS(SELECT 1 FROM review_flags f WHERE f.message_id=m.id AND f.status=?)");params.append(flag_status)
        return data.rows("SELECT c.id,c.user_id,u.username,count(m.id) AS matched_messages,max(m.created) AS last_message FROM conversations c JOIN users u ON c.user_id=u.id JOIN messages m ON m.conversation_id=c.id"+(" WHERE "+" AND ".join(conditions) if conditions else "")+" GROUP BY c.id ORDER BY last_message DESC LIMIT 200",params)

    @router.get("/admin/conversations")
    async def conversations(employee_id: int | None=None,from_ts: float | None=None,to_ts: float | None=None,flag_status: str | None=None,current=Depends(admin)):
        store.audit(current["id"],"audit_search","filtered")
        return audit_rows(employee_id,from_ts,to_ts,flag_status)

    @router.get("/admin/conversations/{cid}")
    async def conversation(cid: int,current=Depends(admin)):
        if not data.rows("SELECT 1 FROM conversations WHERE id=?",(cid,)):raise HTTPException(404,"会话不存在")
        store.audit(current["id"],"audit_view",str(cid))
        return {"messages":store.messages(cid),"flags":data.rows("SELECT f.* FROM review_flags f JOIN messages m ON m.id=f.message_id WHERE m.conversation_id=? ORDER BY f.id",(cid,))}

    @router.post("/admin/messages/{mid}/flags")
    async def flag(mid: int,body: FlagInput,current=Depends(admin)):
        if body.category not in {"suspected_rule_violation","missing_evidence","citation_issue","other"} or not body.reason.strip():raise HTTPException(422,"标记类别或理由无效")
        with store.connection() as db:
            if not db.execute("SELECT 1 FROM messages WHERE id=? AND role IN ('user','assistant')",(mid,)).fetchone():raise HTTPException(404,"问答消息不存在")
            now=time.time()
            fid=db.execute("INSERT INTO review_flags(message_id,category,reason,creator,created,updated) VALUES(?,?,?,?,?,?)",(mid,body.category,body.reason,current["id"],now,now)).lastrowid
        store.audit(current["id"],"flag_create",str(fid))
        return {"id":fid,"status":"pending"}

    @router.patch("/admin/flags/{fid}")
    async def review(fid: int,body: ReviewInput,current=Depends(admin)):
        if body.status not in {"confirmed","dismissed","pending"} or not body.conclusion.strip():raise HTTPException(422,"需提供复核状态和处理说明")
        with store.connection() as db:
            if not db.execute("SELECT 1 FROM review_flags WHERE id=?",(fid,)).fetchone():raise HTTPException(404,"复核标记不存在")
            db.execute("UPDATE review_flags SET status=?,conclusion=?,reviewer=?,updated=? WHERE id=?",(body.status,body.conclusion,current["id"],time.time(),fid))
        store.audit(current["id"],"flag_review",str(fid))
        return {"ok":True}

    env=Environment(loader=FileSystemLoader(Path(__file__).parent/"templates"),autoescape=select_autoescape(["html"]))
    def render(view,current,**context):
        return HTMLResponse(env.get_template("admin.html").render(view=view,current=current,**context))

    @router.get("/admin",response_class=HTMLResponse)
    async def overview_page(current=Depends(admin)):
        return render("overview",current,overview=await overview())

    @router.get("/admin/knowledge",response_class=HTMLResponse)
    async def knowledge_page(current=Depends(admin)):
        return render("knowledge",current,documents=await documents())

    @router.get("/admin/knowledge/{did}",response_class=HTMLResponse)
    async def detail_page(did: int,current=Depends(admin)):
        return render("detail",current,document=data.document(did),chunks=await chunks(did),jobs=data.rows("SELECT * FROM jobs WHERE document_id=? ORDER BY id DESC LIMIT 5",(did,)))

    @router.get("/admin/prompt-editor",response_class=HTMLResponse)
    async def prompt_page(current=Depends(admin)):
        return render("prompts",current,prompt=data.current_prompt(),versions=await versions())

    @router.get("/admin/audit",response_class=HTMLResponse)
    async def audit_page(employee_id: int | None=None,from_ts: float | None=None,to_ts: float | None=None,flag_status: str | None=None,current=Depends(admin)):
        store.audit(current["id"],"audit_search","page")
        return render("audit",current,conversations=audit_rows(employee_id,from_ts,to_ts,flag_status),
                      employees=data.rows("SELECT id,username FROM users ORDER BY username"))

    app.include_router(router)
    return data
