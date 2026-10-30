"""FastAPI本机单企业原型：员工聊天/私有上传，管理员知识发布/记录/来源图谱。

路由为async；SQLite、同步模型API和ONNX推理通过线程执行，避免阻塞事件循环。
此阶段不提供库存写回、自动审批或外部消息发送。
"""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import threading

from fastapi import FastAPI, Depends, File, Form, HTTPException, Request, Response, UploadFile, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from race_supply import phase1_rag as rag
from race_supply.storage import Store


class LoginInput(BaseModel):
    username: str = Field(min_length=1,max_length=64)
    password: str = Field(min_length=1,max_length=256)


class ChatInput(BaseModel):
    question: str = Field(min_length=1,max_length=500)
    conversation_id: int | None = None
    document_ids: list[int] = Field(default_factory=list,max_length=20)
    retrieval_only: bool = False


class StatusInput(BaseModel):
    status: str
    confirmed: bool = False


_encoder_lock = threading.Lock()


def retrieve_and_answer(question, documents, cache_dir, retrieval_only=False, history=None, system_prompt=None):
    """装配层复用单文件核心；锁保护共享tokenizer/DirectML会话。

    检索语料由路由完成权限与版本过滤后传入，禁止先全库搜索再过滤top3。
    """
    from rank_bm25 import BM25Okapi
    chunks, sources = [], {}
    for doc in documents:
        for c in rag.split_document(doc["content"]):
            chunk = replace(c,id=f"S{len(chunks)+1}")
            chunks.append(chunk)
            sources[chunk.id] = {"document_id":doc["id"],"title":doc["title"],
                                 "filename":doc.get("filename") or doc["title"],
                                 "page":next((int(m.group(1)) for m in reversed(list(re.finditer(r"^# PDF第(\d+)页$",doc["content"],re.M))) if m.start()<=c.start),None),
                                 "version":doc["version"],"kind":doc["kind"],
                                 "lines":[c.line_start,c.line_end],"heading":c.heading}
    if not chunks:
        return {"answer":"资料不足：请上传个人资料，或请管理员发布共享知识。","sources":[],"status":"insufficient"}
    backend=os.getenv("RAG_EMBEDDING_BACKEND","local")
    if backend not in {"local","api"}:
        raise ValueError("RAG_EMBEDDING_BACKEND需为local或api")
    config=rag.APIConfig(os.getenv("EMBEDDING_BASE_URL","https://openrouter.ai/api/v1"),
                         os.getenv("EMBEDDING_MODEL","Xenova/bge-small-zh-v1.5" if backend=="local" else ""),
                         os.getenv("EMBEDDING_API_KEY_ENV","OPENROUTER_API_KEY"),backend,os.getenv("RAG_DEVICE","auto"))
    config.validate()
    with _encoder_lock:
        index=rag.load_or_build_index(chunks,config,cache_dir)
        bm25=BM25Okapi([rag.tokenize(c.text) for c in chunks])
        hits=rag.hybrid_search(question,chunks,index,bm25,rag.embed_texts([question],config,is_query=True),
                               float(os.getenv("RAG_MIN_SCORE","0.50")))
    public_sources=[{**sources[h["chunk"].id],"id":h["chunk"].id,"excerpt":h["chunk"].text,
                     "cosine":h["cosine"]} for h in hits]
    if not hits:
        return {"answer":"资料不足：没有达到相关性门槛的来源，请补充站点、批次或正式资料。","sources":[],"status":"insufficient"}
    if retrieval_only:
        return {"answer":"已找到候选资料，请核对来源。当前为仅检索模式，尚未生成业务结论。",
                "sources":public_sources,"status":"retrieved"}
    llm=rag.APIConfig(os.getenv("LLM_BASE_URL","https://openrouter.ai/api/v1"),os.getenv("LLM_MODEL",""),
                      os.getenv("LLM_API_KEY_ENV","OPENROUTER_API_KEY"))
    # 每个S编号在返回sources中映射到真实文档与版本，模型不能自造路径。
    answer=rag.grounded_answer(question,hits,"已授权文档，S编号对应返回的来源列表",llm,history=history,system_prompt=system_prompt)
    return {"answer":answer,"sources":public_sources,"status":"insufficient" if answer.startswith("资料不足") else "answered"}


def create_app(data_dir: Path | None = None, *, answerer=None, allowed_hosts=None, admin_indexer=None):
    """工厂可注入测试替身；默认从本机.env.local加载，绝不创建默认账户。"""
    from dotenv import load_dotenv
    load_dotenv(".env.local",override=False)
    folder=Path(data_dir or os.getenv("RACE_DATA_DIR","work/web")).resolve()
    store=Store(folder/"race_supply.sqlite3")
    from race_supply.auth import JwtAuth
    auth=JwtAuth(store,folder)
    app=FastAPI(title="赛事补给协同助手",docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store=store
    app.state.answerer=answerer or retrieve_and_answer
    app.state.chat_lock=asyncio.Lock()
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=allowed_hosts or ["localhost","127.0.0.1","[::1]"])

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        # 不开放跨域Cookie鉴权。浏览器修改请求需自定义头，跨站表单无法携带。
        if request.method in {"POST","PUT","PATCH","DELETE"}:
            if request.headers.get("X-Requested-With")!="race-supply":
                return JSONResponse({"code":"REQUEST_ORIGIN_REQUIRED","message":"缺少请求来源标识","detail":"缺少请求来源标识"},status_code=403)
            origin=request.headers.get("origin")
            if origin and origin!=str(request.base_url).rstrip("/"):
                return JSONResponse({"code":"CROSS_ORIGIN_DENIED","message":"拒绝跨站请求","detail":"拒绝跨站请求"},status_code=403)
            # 实际读取并限长，不仅依赖可伪造/缺失的Content-Length。
            raw=bytearray()
            async for piece in request.stream():
                raw.extend(piece)
                if len(raw)>1_100_000:
                    return JSONResponse({"code":"BODY_TOO_LARGE","message":"请求体超过本机原型1MB限制","detail":"请求体超过本机原型1MB限制"},status_code=413)
            request._body=bytes(raw)
        response=await call_next(request)
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Cache-Control"]="no-store"
        response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        return response

    async def user(request: Request):
        found=await asyncio.to_thread(auth.verify,request.cookies.get("race_session",""))
        if not found:
            raise HTTPException(401,"请先登录")
        return found

    async def admin(current=Depends(user)):
        if current["role"]!="admin":
            raise HTTPException(403,"需要管理员权限")
        return current

    @app.post("/auth/login")
    @app.post("/api/login")
    async def login(body: LoginInput, request: Request, response: Response):
        try:
            token,current=await asyncio.to_thread(store.login,body.username,body.password,request.client.host if request.client else "local")
        except PermissionError:
            raise HTTPException(401,"用户名或密码错误") from None
        except ValueError as error:
            raise HTTPException(429,str(error)) from None
        token=await asyncio.to_thread(auth.issue,token,current)
        response.set_cookie("race_session",token,httponly=True,samesite="strict",max_age=900,
                            secure=os.getenv("COOKIE_SECURE","0")=="1")
        await asyncio.to_thread(store.audit,current["id"],"login","local")
        return current

    @app.get("/auth/me")
    @app.get("/api/me")
    async def me(current=Depends(user)):
        return current

    @app.post("/auth/logout")
    @app.post("/api/logout")
    async def logout(request: Request,response: Response,current=Depends(user)):
        await asyncio.to_thread(store.logout,request.cookies.get("race_session",""))
        response.delete_cookie("race_session")
        return {"ok":True}

    from race_supply.admin import register_admin
    admin_data=register_admin(app,store,folder,admin,indexer=admin_indexer)
    from race_supply.agent_api import register_agent
    def agent_search(query,current):
        docs=store.documents(current,chat_only=True)
        if len(docs)>20 or sum(len(d["content"]) for d in docs)>200_000:
            raise ValueError("可见资料超过本机检索范围，请管理员整理知识库")
        return retrieve_and_answer(query,docs,folder/"rag_cache",True)
    register_agent(app,store,folder,user,admin,admin_data,agent_search)

    @app.get("/api/documents")
    async def documents(current=Depends(user)):
        rows=await asyncio.to_thread(store.documents,current)
        eligible={r["id"] for r in await asyncio.to_thread(store.documents,current,chat_only=True)}
        return [{k:v for k,v in r.items() if k!="content"} | {"chat_eligible":r["id"] in eligible} for r in rows]

    @app.get("/api/documents/{doc_id}")
    async def document(doc_id: int,current=Depends(user)):
        rows=await asyncio.to_thread(store.documents,current)
        found=next((r for r in rows if r["id"]==doc_id),None)
        if not found:
            raise HTTPException(404,"资料不存在或无权查看")
        return found

    @app.post("/api/documents")
    async def upload(background_tasks: BackgroundTasks,file: UploadFile=File(...),title: str=Form(...),series: str=Form(...),
                     scope: str=Form("personal"),kind: str=Form("scenario"),current=Depends(user)):
        if scope not in {"personal","shared"} or kind not in {"policy","scenario"}:
            raise HTTPException(422,"资料类型无效")
        if scope=="shared" and current["role"]!="admin":
            raise HTTPException(403,"只有管理员可维护共享知识")
        if not title.strip() or len(title)>120 or not series.strip() or len(series)>120:
            raise HTTPException(422,"标题及系列名需为1至120字符")
        if Path(file.filename or "").suffix.lower() not in {".md",".txt"}:
            raise HTTPException(422,"当前仅接受UTF-8 Markdown或TXT")
        raw=await file.read(1_000_001)
        await file.close()
        if len(raw)>1_000_000:
            raise HTTPException(413,"文件不能超过1MB")
        try:
            content=raw.decode("utf-8-sig")
            if not content.strip() or "\x00" in content or len(content)>100_000:
                raise ValueError()
            rag.split_document(content)
        except (UnicodeError,ValueError):
            raise HTTPException(422,"需提供非空UTF-8文本，最多10万字符") from None
        try:
            did=await asyncio.to_thread(store.add_document,current["id"],title.strip(),series.strip(),scope,kind,content)
        except ValueError as error:
            raise HTTPException(422,str(error)) from None
        if scope=="shared":
            # 兼容旧聊天页入口，共享资料同样必须完成索引后才能发布。
            with store.connection() as db:
                db.execute("UPDATE documents SET filename=?,processing='queued' WHERE id=?",(Path(file.filename or 'document.md').name,did))
                db.execute("INSERT INTO uploads VALUES(?,?)",(did,raw))
            jid=admin_data.enqueue(did)
            background_tasks.add_task(admin_data.process,jid)
        await asyncio.to_thread(store.audit,current["id"],"upload",str(did))
        return {"id":did,"status":"draft"}

    @app.post("/api/documents/{doc_id}/status")
    async def change_status(doc_id: int,body: StatusInput,current=Depends(admin)):
        if not body.confirmed:
            raise HTTPException(422,"发布/归档需要人工确认")
        try:
            await asyncio.to_thread(store.change_status,doc_id,body.status)
        except ValueError as error:
            raise HTTPException(422,str(error)) from None
        await asyncio.to_thread(store.audit,current["id"],body.status,str(doc_id))
        return {"ok":True}

    @app.get("/api/conversations")
    async def conversations(current=Depends(user)):
        return await asyncio.to_thread(store.conversations,current)

    @app.get("/api/conversations/{cid}")
    async def history(cid: int,current=Depends(user)):
        try:
            await asyncio.to_thread(store.conversation,current["id"],cid)
        except PermissionError:
            raise HTTPException(404,"会话不存在") from None
        return await asyncio.to_thread(store.messages,cid)

    @app.post("/api/chat")
    async def chat(body: ChatInput,current=Depends(user)):
        if not body.question.strip():
            raise HTTPException(422,"问题不能为空")
        if app.state.chat_lock.locked():
            raise HTTPException(429,"本机模型正在处理请求，请稍后重试")
        async with app.state.chat_lock:
            docs=await asyncio.to_thread(store.documents,current,chat_only=True)
            if body.document_ids:
                if set(body.document_ids)-{d["id"] for d in docs}:
                    raise HTTPException(403,"所选资料未生效或无权访问")
                docs=[d for d in docs if d["id"] in body.document_ids]
            if len(docs)>20 or sum(len(d["content"]) for d in docs)>200_000:
                raise HTTPException(422,"请缩小资料范围：最多20份、总计20万字符")
            try:
                cid=await asyncio.to_thread(store.conversation,current["id"],body.conversation_id)
            except PermissionError:
                raise HTTPException(404,"会话不存在") from None
            await asyncio.to_thread(store.add_message,cid,"user",body.question)
            try:
                prompt=await asyncio.to_thread(admin_data.current_prompt)
                if app.state.answerer is retrieve_and_answer:
                    result=await asyncio.to_thread(app.state.answerer,body.question,docs,folder/"rag_cache",body.retrieval_only,system_prompt=prompt["content"])
                else:
                    result=await asyncio.to_thread(app.state.answerer,body.question,docs,folder/"rag_cache",body.retrieval_only)
                # 等待模型期间若文档被撤回，不能继续返回已撤回的来源内容。
                visible={d["id"] for d in await asyncio.to_thread(store.documents,current,chat_only=True)}
                if {d["id"] for d in docs}-visible:
                    raise ValueError("资料版本已撤回，请重新提问")
                if any(d["kind"]=="scenario" for d in docs):
                    result["answer"]="【含演练资料，不等于正式制度】\n"+result["answer"]
                await asyncio.to_thread(store.add_message,cid,"assistant",result["answer"],json.dumps(result["sources"],ensure_ascii=False),prompt["id"])
                await asyncio.to_thread(store.audit,current["id"],"query",json.dumps([d["id"] for d in docs]))
                return {"conversation_id":cid,**result}
            except (ValueError,RuntimeError,OSError) as error:
                await asyncio.to_thread(store.audit,current["id"],"query_failed",type(error).__name__)
                # 不返回供应商响应、请求头或堆栈；错误同时存入历史，避免留下假成功记录。
                await asyncio.to_thread(store.add_message,cid,"system","查询未完成，请检查本机配置、资料或稍后重试。")
                raise HTTPException(422,"查询未完成：请检查模型配置、输入长度、资料版本与引用校验；详见本机运行说明。") from None

    @app.get("/api/admin/logs")
    async def logs(current=Depends(admin)):
        await asyncio.to_thread(store.audit,current["id"],"view_logs","latest200")
        return await asyncio.to_thread(store.logs)

    @app.get("/api/admin/graph")
    async def graph(current=Depends(admin)):
        """可追溯的来源图谱：文档→版本→明确文本实体，不推断业务因果。"""
        rows=await asyncio.to_thread(store.documents,current,chat_only=True)
        rows=[d for d in rows if d["scope"]=="shared"]
        nodes,edges={},[]
        for d in rows[:100]:
            did=f"doc:{d['id']}"
            nodes[did]={"id":did,"label":f"{d['title']} v{d['version']}","type":"document","kind":d["kind"]}
            # 只抽取明确标注的站点/物资/批次，降低把普通词当实体的误识别。
            for line_no,line in enumerate(d["content"].splitlines(),1):
                for match in re.finditer(r"(站点|物资|批次)[：:]\s*([^，。；;\n]{1,40})",line):
                    typ,value=match.groups()
                    eid=f"entity:{typ}:{value.strip()}"
                    nodes[eid]={"id":eid,"label":value.strip(),"type":typ}
                    if len(edges)<500:
                        edges.append({"source":did,"target":eid,"relation":"原文提及","document_id":d["id"],"line":line_no,"quote":match.group(0)})
        return {"nodes":list(nodes.values()),"edges":edges,"notice":"来源图谱：边表示原文提及，不代表已确认的业务关系；最多100份文档/500条边。"}

    static=Path(__file__).parent/"static"
    app.mount("/static",StaticFiles(directory=static),name="static")

    @app.get("/")
    async def index():
        return FileResponse(static/"index.html")

    # POST SSE使用fetch消费；先完成鉴权与参数验证，再开始事件流。
    from fastapi.responses import StreamingResponse
    from fastapi.exceptions import RequestValidationError
    import uuid

    from starlette.exceptions import HTTPException as StarletteHTTPException
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, error):
        message=str(error.detail)
        if message.isascii():
            message={404:"接口不存在",405:"请求方法不支持",400:"请求格式不正确"}.get(error.status_code,"请求未完成，请检查参数与权限")
        return JSONResponse({"code":f"HTTP_{error.status_code}","message":message,"detail":message,"request_id":uuid.uuid4().hex},status_code=error.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse({"code":"INVALID_INPUT","message":"参数无效，请检查问题长度和会话编号","request_id":uuid.uuid4().hex},status_code=422)

    class StreamInput(BaseModel):
        session_id: int | None = None
        question: str = Field(min_length=1,max_length=500)
        retrieval_only: bool = False

    @app.get("/sessions")
    async def session_list(current=Depends(user)):
        return await asyncio.to_thread(store.conversations,current)

    @app.get("/sessions/{session_id}/messages")
    async def session_messages(session_id: int,current=Depends(user)):
        return await history(session_id,current)

    @app.post("/chat")
    async def stream_chat(body: StreamInput,request: Request,current=Depends(user)):
        if not body.question.strip():
            raise HTTPException(422,"问题不能为空")
        if app.state.chat_lock.locked():
            raise HTTPException(409,"当前正在回答，请稍后重试")
        try:
            cid=await asyncio.to_thread(store.conversation,current["id"],body.session_id)
        except PermissionError:
            raise HTTPException(404,"会话不存在或无权访问") from None
        docs=await asyncio.to_thread(store.documents,current,chat_only=True)
        if len(docs)>20 or sum(len(d["content"]) for d in docs)>200_000:
            raise HTTPException(422,"请缩小资料范围，最多20份、20万字符")
        # 仅完整问答对进入上下文，失败或中断消息不计入轮数。
        old=await asyncio.to_thread(store.messages,cid)
        prompt=await asyncio.to_thread(admin_data.current_prompt)
        pairs=[]
        for i in range(len(old)-1):
            if old[i]["role"]=="user" and old[i+1]["role"]=="assistant":
                pairs.append(old[i:i+2])
        context=[{"role":m["role"],"content":m["content"]} for pair in pairs[-6:] for m in pair]
        while sum(len(m["content"]) for m in context)>12000:
            context=context[2:]
        await app.state.chat_lock.acquire()
        async def events():
            def event(name,data):
                return "event: "+name+"\ndata: "+json.dumps(data,ensure_ascii=False)+"\n\n"
            completed=False
            try:
                yield event("session",{"session_id":cid})
                yield event("status",{"stage":"retrieving","message":"正在检索补给资料…"})
                await asyncio.to_thread(store.add_message,cid,"user",body.question)
                yield event("status",{"stage":"generating","message":"正在核对来源并整理回答…"})
                result=await asyncio.to_thread(retrieve_and_answer,body.question,docs,folder/"rag_cache",body.retrieval_only,context,prompt["content"])
                visible={d["id"] for d in await asyncio.to_thread(store.documents,current,chat_only=True)}
                if {d["id"] for d in docs}-visible:
                    raise ValueError("资料已撤回")
                answer=result["answer"]
                if any(d["kind"]=="scenario" for d in docs):
                    answer="【含演练资料，不等于正式制度】\n"+answer
                # 整个回答通过摘录校验后再推送，避免未校验文字先展示。
                for offset in range(0,len(answer),16):
                    yield event("delta",{"text":answer[offset:offset+16]})
                    await asyncio.sleep(0.015)
                yield event("sources",{"items":result["sources"]})
                mid=await asyncio.to_thread(store.add_message,cid,"assistant",answer,json.dumps(result["sources"],ensure_ascii=False),prompt["id"])
                completed=True
                yield event("done",{"status":"completed","session_id":cid,"message_id":mid,"prompt_version":prompt["id"]})
            except asyncio.CancelledError:
                raise
            except Exception:
                yield event("error",{"code":"GENERATION_FAILED","message":"回答未完成，请检查资料、模型配置与引用校验后重试","retryable":True})
            finally:
                if not completed:
                    await asyncio.to_thread(store.add_message,cid,"system","回答失败或连接中断；该轮不计入历史上下文。")
                app.state.chat_lock.release()
        return StreamingResponse(events(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

    return app
