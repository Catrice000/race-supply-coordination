/* 纯原生聊天页；用textContent展示上传内容/回答，禁止将模型输出作为HTML执行。 */
const $=id=>document.getElementById(id);let current=null,conversation=null,documents=[];
function notice(text=""){$("notice").textContent=text;}
async function api(path,options={}){const response=await fetch(path,{...options,headers:{"X-Requested-With":"race-supply",...(options.body instanceof FormData?{}:{"Content-Type":"application/json"}),...options.headers}});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:"请求失败，请检查输入与权限");return data;}
function element(tag,text,parent,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;if(parent)parent.append(el);return el;}
function showView(id){for(const panel of ["chat-panel","knowledge-panel","logs-panel","graph-panel"])$(panel).hidden=panel!==id;}
function addMessage(role,text,sources=[]){const box=element("article",undefined,$("messages"),`message ${role}`);element("strong",role==="user"?"你":role==="system"?"系统":"补给助手",box);element("div",text,box);for(const s of sources){const detail=element("details",undefined,box);element("summary",`[${s.id}] ${s.title} · v${s.version} · 第${s.lines.join("—")}行`,detail);element("div",s.excerpt,detail,"source");}$('messages').scrollTop=$('messages').scrollHeight;}
async function refreshHistory(){const rows=await api("/api/conversations");$("history-list").replaceChildren();for(const row of rows){const b=element("button",`会话 ${row.id} · ${new Date(row.created*1000).toLocaleDateString()}`,$("history-list"));b.onclick=async()=>{try{const history=await api(`/api/conversations/${row.id}`);conversation=row.id;$("messages").replaceChildren();for(const m of history)addMessage(m.role,m.content,JSON.parse(m.sources));showView("chat-panel");}catch(e){notice(e.message);}};}}
async function refreshDocuments(){documents=await api("/api/documents");$("document-options").replaceChildren();$("knowledge-list").replaceChildren();for(const d of documents){if(d.chat_eligible){const label=element("label",undefined,$("document-options"));const check=element("input",undefined,label);check.type="checkbox";check.value=d.id;check.name="document";label.append(document.createTextNode(` ${d.title} v${d.version}${d.kind==="scenario"?"（演练）":""}`));}if(current.role==="admin"){const row=element("div",undefined,$("knowledge-list"),"docrow");element("strong",`${d.title} · v${d.version}`,row);element("p",`${d.scope==="shared"?"共享知识":"个人上传"} / ${d.status} / ${d.kind==="policy"?"正式制度标签":"演练资料"} / 系列 ${d.series}`,row);const view=element("button","查看原文",row);view.onclick=async()=>{try{const full=await api(`/api/documents/${d.id}`);const old=row.querySelector("pre");if(old)old.remove();else element("pre",full.content,row);}catch(e){notice(e.message);}};if(d.scope==="shared"&&d.status!=="archived"){const status=d.status==="draft"?"published":"archived";const b=element("button",status==="published"?"确认发布":"归档",row);b.onclick=async()=>{if(!confirm(status==="published"?"确认资料性质、来源和版本已核对？发布后同系列旧版将归档。":"确认撤回该版本？"))return;try{await api(`/api/documents/${d.id}/status`,{method:"POST",body:JSON.stringify({status,confirmed:true})});await refreshDocuments();notice("文档状态已更新");}catch(e){notice(e.message);}};}}}}
async function enter(user){current=user;$("identity").textContent=`${user.username} · ${user.role==="admin"?"管理员":"员工"}`;$("login-panel").hidden=true;$("workspace").hidden=false;$("logout").hidden=false;$("admin-nav").hidden=user.role!=="admin";await refreshDocuments();await refreshHistory();}
$("login-form").onsubmit=async event=>{event.preventDefault();try{const user=await api("/api/login",{method:"POST",body:JSON.stringify({username:$("username").value,password:$("password").value})});$("password").value="";await enter(user);notice();}catch(e){notice(e.message);}};
$("logout").onclick=async()=>{try{await api("/api/logout",{method:"POST"});location.reload();}catch(e){notice(e.message);}};
$("new-chat").onclick=()=>{conversation=null;$("messages").replaceChildren();};
document.querySelectorAll("[data-view]").forEach(button=>button.onclick=()=>showView(button.dataset.view));
for(const [id,scope] of [["upload-personal","personal"],["upload-shared","shared"]])$(id).onsubmit=async event=>{event.preventDefault();const form=event.target;const data=new FormData(form);data.set("scope",scope);if(!data.has("kind"))data.set("kind","scenario");try{await api("/api/documents",{method:"POST",body:data});form.reset();await refreshDocuments();notice(scope==="shared"?"草稿已上传，需人工确认后发布":"个人资料已上传，可选择后提问");}catch(e){notice(e.message);}};
// SSE按空行分帧，保留跨网络分片的UTF-8字符；不自动重试计费请求。
$("chat-form").onsubmit=async event=>{
 event.preventDefault();const question=$("question").value.trim();if(!question)return;
 $("send").disabled=true;document.querySelector(".empty")?.remove();addMessage("user",question);$("question").value="";
 let answer="",sources=[],finished=false;addMessage("assistant","");const box=$("messages").lastElementChild;const content=box.querySelector("div");
 try{
  const response=await fetch("/chat",{method:"POST",headers:{"Content-Type":"application/json","X-Requested-With":"race-supply"},body:JSON.stringify({session_id:conversation,question,retrieval_only:$("retrieval-only").checked})});
  if(!response.ok){const error=await response.json();throw new Error(error.message||error.detail||"请求失败");}
  const reader=response.body.getReader(),decoder=new TextDecoder();let pending="";
  while(true){const {done,value}=await reader.read();pending+=decoder.decode(value||new Uint8Array(),{stream:!done});let boundary;
   while((boundary=pending.indexOf("\n\n"))>=0){const frame=pending.slice(0,boundary);pending=pending.slice(boundary+2);const lines=frame.split("\n");const type=lines.find(l=>l.startsWith("event:"))?.slice(6).trim();const data=JSON.parse(lines.filter(l=>l.startsWith("data:")).map(l=>l.slice(5).trimStart()).join("\n")||"{}");
    if(type==="session")conversation=data.session_id;
    if(type==="status")notice(data.message);
    if(type==="delta"){answer+=data.text;content.textContent=answer;}
    if(type==="sources")sources=data.items;
    if(type==="error")throw new Error(data.message);
    if(type==="done")finished=true;
   }if(done)break;
  }
  if(!finished)throw new Error("连接中断，回答未完成，请手动重试");
  box.remove();addMessage("assistant",answer,sources);notice();await refreshHistory();
 }catch(e){notice(e.message);element("div","回答未完成："+e.message,box);}
 finally{$("send").disabled=false;}
};
$("refresh-logs").onclick=async()=>{try{$("logs").textContent=JSON.stringify(await api("/api/admin/logs"),null,2);}catch(e){notice(e.message);}};
$("refresh-graph").onclick=async()=>{try{const g=await api("/api/admin/graph");$("graph-notice").textContent=g.notice;const svg=$("graph");svg.replaceChildren();$("graph-evidence").replaceChildren();const nodes=g.nodes.slice(0,45),positions=new Map();nodes.forEach((n,i)=>positions.set(n.id,{x:500+390*Math.cos(i*2*Math.PI/nodes.length),y:300+240*Math.sin(i*2*Math.PI/nodes.length)}));function shape(tag,attrs){const e=document.createElementNS("http://www.w3.org/2000/svg",tag);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,v);svg.append(e);return e;}for(const edge of g.edges){const a=positions.get(edge.source),b=positions.get(edge.target);if(a&&b){const line=shape("line",{x1:a.x,y1:a.y,x2:b.x,y2:b.y});element("title",`${edge.relation} · 第${edge.line}行：${edge.quote}`,line);const p=element("p",`${edge.source} → ${edge.target}；第${edge.line}行：${edge.quote}`,$("graph-evidence"));p.className="hint";}}for(const node of nodes){const p=positions.get(node.id);shape("circle",{cx:p.x,cy:p.y,r:9,class:node.type==="document"?"document":"entity"});const text=shape("text",{x:p.x+12,y:p.y+4});text.textContent=node.label.slice(0,24);}if(g.nodes.length>45)notice("图中展示前45个节点，请缩小知识范围查看。");}catch(e){notice(e.message);}};
api("/api/me").then(enter).catch(()=>{});
