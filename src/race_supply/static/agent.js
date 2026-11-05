/* 人工确认独立于模型上下文：确认凭证只在浏览器和提交接口之间传递。 */
async function agentApi(path, options = {}) {
    const response = await fetch(path, {
        ...options,
        headers: {
            'X-Requested-With': 'race-supply',
            'Content-Type': 'application/json'
        }
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || result.detail || 'Agent操作未完成');
    return result;
}

function agentElement(tag, text, parent) {
    const node = document.createElement(tag);
    if (tag === 'button') node.type = 'button';
    if (text !== undefined) node.textContent = text;
    parent?.append(node);
    return node;
}
document.querySelectorAll('[data-agent-import]').forEach(form => form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button');
    button.disabled = true;
    try {
        const body = JSON.parse(form.querySelector('textarea').value);
        await agentApi('/admin/agent/' + form.dataset.agentImport, {
            method: 'POST',
            body: JSON.stringify(body)
        });
        location.reload();
    } catch (error) {
        document.getElementById('agent-notice').textContent = error.message;
    } finally {
        button.disabled = false;
    }
}));
async function refreshAgentDrafts() {
    const box = document.getElementById('agent-drafts');
    if (!box) return;
    try {
        const drafts = await agentApi('/agent/drafts');
        box.replaceChildren();
        for (const d of drafts) {
            const button = agentElement('button', `${d.kind} · ${d.state} · 查看`, box);
            button.onclick = async () => {
                try {
                    const draft = await agentApi('/agent/drafts/' + d.id);
                    const section = agentElement('section', undefined, box);
                    agentElement('p', '草稿状态：' + ({
                        draft: '未提交草稿',
                        pending_review: '待人工复核'
                    } [draft.state] || draft.state), section);
                    agentElement('p', readableAgentAnswer(JSON.stringify(draft.content)), section);
                    if (draft.state === 'draft') {
                        const submit = agentElement('button', '查看无误，确认提交人工复核', section);
                        submit.onclick = async () => {
                            if (!confirm('确认将此草稿提交内部人工复核？此操作不会正式派单、改库存或确认签收。')) return;
                            submit.disabled = true;
                            try {
                                const proof = await agentApi(`/agent/drafts/${d.id}/confirmation`, {
                                    method: 'POST',
                                    body: JSON.stringify({
                                        confirmed: true
                                    })
                                });
                                const result = await agentApi(`/agent/drafts/${d.id}/submit`, {
                                    method: 'POST',
                                    body: JSON.stringify({
                                        confirmation_token: proof.confirmation_token
                                    })
                                });
                                agentElement('p', result.message, section);
                                submit.remove();
                            } catch (error) {
                                agentElement('p', error.message, section);
                                submit.disabled = false;
                            }
                        };
                    }
                } catch (error) {
                    agentElement('p', error.message, box);
                }
            };
        }
        if (!drafts.length) agentElement('p', '暂无复核草稿', box);
    } catch (error) {
        box.textContent = error.message;
    }
}
document.getElementById('refresh-agent-drafts')?.addEventListener('click', refreshAgentDrafts);
// 草稿预览来自服务端校验快照。按钮只发送同意/拒绝，不允许客户端替换草稿内容。
function showDraftPreview(preview, parent) {
    const panel = agentElement('section', undefined, parent);
    agentElement('strong', '待确认创建 · ' + ({
        receipt_discrepancy: '签收差异',
        missing_records: '记录缺项'
    } [preview.draft_type] || preview.draft_type), panel);
    agentElement('p', readableAgentAnswer(JSON.stringify(preview.content)), panel);
    const accept = agentElement('button', '确认创建草稿', panel),
        reject = agentElement('button', '取消创建', panel);
    const feedback = agentElement('p', '创建后可在草稿列表中查看，再另行确认提交。', panel);
    async function decide(confirmed) {
        accept.disabled = reject.disabled = true;
        try {
            await agentApi('/agent/draft-previews/' + preview.run_id + '/decision', {
                method: 'POST',
                body: JSON.stringify({
                    confirmed
                })
            });
            accept.remove();
            reject.remove();
            feedback.textContent = confirmed ? '已创建草稿，可在下方草稿列表查看。' : '已取消，未创建草稿。';
            await refreshAgentDrafts();
        } catch (error) {
            feedback.textContent = error.message;
            accept.disabled = reject.disabled = false;
        }
    }
    accept.onclick = () => decide(true);
    reject.onclick = () => decide(false);
}

async function refreshDraftPreviews() {
    const box = document.getElementById('agent-previews');
    if (!box) return;
    try {
        const previews = await agentApi('/agent/draft-previews');
        box.replaceChildren();
        for (const preview of previews) showDraftPreview(preview, box);
    } catch (error) {
        box.textContent = error.message;
    }
}
document.getElementById('refresh-agent-drafts')?.addEventListener('click', refreshDraftPreviews);

// SSE 预览单独成帧，收到后立即展示。断线时从服务端重新读取待确认预览。
async function streamAgentChat(body, onPreview) {
    const response = await fetch('/agent/chat/stream', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'race-supply'
        },
        body: JSON.stringify(body)
    });
    if (!response.ok) {
        const error = await response.json();
        throw new Error(error.message || 'Agent 请求失败');
    }
    const reader = response.body.getReader(),
        decoder = new TextDecoder();
    let pending = '';
    try {
        while (true) {
            const {
                done,
                value
            } = await reader.read();
            if (done) throw new Error('连接中断，请刷新草稿列表查看是否已有待确认预览');
            pending += decoder.decode(value, {
                stream: true
            });
            let boundary;
            while ((boundary = pending.indexOf('\n\n')) >= 0) {
                const frame = pending.slice(0, boundary);
                pending = pending.slice(boundary + 2);
                if (frame.startsWith(':')) continue; // 代理保活注释不承载业务数据。
                const [event, data] = frame.split('\n');
                const payload = JSON.parse(data.slice(6));
                if (event === 'event: status') notice(payload.message);
                if (event === 'event: draft_preview') onPreview(payload);
                if (event === 'event: error') throw new Error(payload.message);
                if (event === 'event: done') return payload;
            }
        }
    } finally {
        await reader.cancel().catch(() => {});
    }
}

document.getElementById('chat-form')?.addEventListener('submit', async event => {
    if (!document.getElementById('agent-mode')?.checked) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const question = document.getElementById('question').value.trim();
    if (!question) return;
    const allowDraft = document.getElementById('agent-allow-draft').checked;
    document.getElementById('agent-allow-draft').checked = false;
    const send = document.getElementById('send');
    send.disabled = true;
    addMessage('user', question);
    document.getElementById('question').value = '';
    notice('Agent正在选择工具并核对前置条件…');
    try {
        const result = await streamAgentChat({
            session_id: conversation,
            question,
            allow_draft: allowDraft
        }, preview => {
            addMessage('assistant', '请核对草稿预览');
            showDraftPreview(preview, document.getElementById('messages').lastElementChild);
        });
        conversation = result.session_id;
        addMessage('assistant', result.answer, result.sources);
        const row = document.getElementById('messages').lastElementChild;
        const details = agentElement('details', undefined, row);
        agentElement('summary', '本次工具调用记录', details);
        agentElement('pre', JSON.stringify(result.trace, null, 2), details);
        notice();
        await refreshHistory();
        await refreshAgentDrafts();
    } catch (error) {
        notice(error.message);
        addMessage('system', error.message);
        await refreshDraftPreviews();
        await refreshAgentDrafts();
    } finally {
        send.disabled = false;
    }
}, {
    capture: true
});
