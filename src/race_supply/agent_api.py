"""Agent HTTP装配：服务端绑定身份/会话，确认凭证不交给模型。"""
import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment,FileSystemLoader,select_autoescape
from pydantic import Field

from race_supply.agent import AgentService,AgentError,BatchInput,RuleInput,Strict,TOOLS,SKILLS,run_agent


class AgentInput(Strict):
    session_id: int | None = None
    question: str = Field(min_length=1,max_length=500)
    allow_draft: bool = False


class Confirmation(Strict):
    confirmed: bool = False


class Submission(Strict):
    confirmation_token: str = Field(min_length=1,max_length=100)


def register_agent(app,store,folder,user,admin,admin_data,searcher):
    service=AgentService(store,searcher)
    app.state.agent_service=service
    app.state.agent_step=None
    router=APIRouter()

    @app.exception_handler(AgentError)
    async def agent_error(request,error):
        return JSONResponse({"code":error.code,"message":error.message},status_code=422)

    @router.get("/agent/tools")
    async def tool_specs(current=Depends(user)):
        return {"tools":TOOLS,"skills":SKILLS,"notice":"提交工具仅在界面人工确认后由服务端调用，不开放给模型。"}

    @router.post("/agent/chat")
    async def chat(body: AgentInput,current=Depends(user)):
        if not body.question.strip():raise HTTPException(422,"问题不能为空")
        if app.state.chat_lock.locked():raise HTTPException(409,"当前模型正在处理请求，请稍后重试")
        async with app.state.chat_lock:
            try:cid=await asyncio.to_thread(store.conversation,current["id"],body.session_id)
            except PermissionError:raise HTTPException(404,"会话不存在或无权访问") from None
            prompt=await asyncio.to_thread(admin_data.current_prompt)
            old=await asyncio.to_thread(store.messages,cid)
            pairs=[old[i:i+2] for i in range(len(old)-1) if old[i]["role"]=="user" and old[i+1]["role"]=="assistant"]
            history=[{"role":m["role"],"content":m["content"]} for pair in pairs[-6:] for m in pair]
            while sum(len(m["content"]) for m in history)>12000:history=history[2:]
            await asyncio.to_thread(store.add_message,cid,"user",body.question)
            try:
                result=await asyncio.to_thread(run_agent,service,current,cid,body.question,body.allow_draft,prompt,step=app.state.agent_step,history=history)
                visible={d["id"] for d in await asyncio.to_thread(store.documents,current,chat_only=True)}
                if {s["document_id"] for s in result["sources"]}-visible:
                    raise AgentError("SOURCE_WITHDRAWN","处理期间资料已撤回，请重新查询")
                mid=await asyncio.to_thread(store.add_message,cid,"assistant",result["answer"],json.dumps(result["sources"],ensure_ascii=False),prompt["id"])
                return {"session_id":cid,"message_id":mid,**result}
            except Exception as error:
                await asyncio.to_thread(store.add_message,cid,"system","Agent运行未完成；若已创建草稿，请先查看草稿列表，避免重复创建。")
                if isinstance(error,AgentError):raise
                raise HTTPException(503,"Agent未完成，请检查模型工具调用支持与本机配置；已执行步骤可查看运行记录，不自动重试") from None

    @router.get("/agent/runs/{rid}")
    async def run_detail(rid: str,current=Depends(user)):
        with store.connection() as db:
            run=service.row(db,"SELECT * FROM agent_runs WHERE id=? AND user_id=?",(rid,current["id"]))
            if not run:raise HTTPException(404,"运行不存在或无权访问")
            trace=[dict(r) for r in db.execute("SELECT tool,args,result,created FROM agent_tool_audit WHERE run_id=? ORDER BY id",(rid,))]
        return {"run":run,"trace":trace}

    @router.get("/agent/runs")
    async def runs(current=Depends(user)):
        with store.connection() as db:
            return [dict(r) for r in db.execute("SELECT id,conversation_id,question,status,created FROM agent_runs WHERE user_id=? ORDER BY created DESC LIMIT 100",(current["id"],))]

    @router.get("/agent/drafts")
    async def drafts(current=Depends(user)):
        with store.connection() as db:
            return [dict(r) for r in db.execute("SELECT id,kind,state,created FROM review_drafts WHERE user_id=? ORDER BY created DESC LIMIT 100",(current["id"],))]

    @router.get("/agent/drafts/{did}")
    async def draft(did: str,current=Depends(user)):
        return await asyncio.to_thread(service.get_draft,did,current)

    @router.post("/agent/drafts/{did}/confirmation")
    async def confirmation(did: str,body: Confirmation,current=Depends(user)):
        if not body.confirmed:raise HTTPException(422,"请先查看草稿内容并人工确认")
        return await asyncio.to_thread(service.issue_confirmation,did,current)

    @router.post("/agent/drafts/{did}/submit")
    async def submit(did: str,body: Submission,current=Depends(user)):
        return await asyncio.to_thread(service.submit,did,body.confirmation_token,current)

    @router.post("/admin/agent/batches")
    async def import_batch(body: BatchInput,current=Depends(admin)):
        return await asyncio.to_thread(service.import_batch,body,current)

    @router.post("/admin/agent/rules")
    async def import_rules(body: RuleInput,current=Depends(admin)):
        return await asyncio.to_thread(service.import_rules,body,current)

    env=Environment(loader=FileSystemLoader(Path(__file__).parent/"templates"),autoescape=select_autoescape(["html"]))
    @router.get("/admin/agent",response_class=HTMLResponse)
    async def agent_page(current=Depends(admin)):
        with store.connection() as db:
            batches=[dict(r) for r in db.execute("SELECT id,batch_id,version,created FROM agent_batches WHERE active=1 ORDER BY created DESC LIMIT 100")]
            rules=[dict(r) for r in db.execute("SELECT r.id,r.document_id,d.title,d.status FROM agent_rules r JOIN documents d ON d.id=r.document_id WHERE r.active=1 AND d.deleted=0")]
            queue=[dict(r) for r in db.execute("SELECT d.id,d.kind,d.state,d.content,u.username FROM review_drafts d JOIN users u ON u.id=d.user_id ORDER BY d.created DESC LIMIT 100")]
        return HTMLResponse(env.get_template("agent_admin.html").render(current=current,batches=batches,rules=rules,queue=queue))

    app.include_router(router)
    return service
