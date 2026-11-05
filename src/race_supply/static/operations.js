/* 运营台账界面。使用 textContent 渲染用户输入，所有写入都经过后端权限校验。 */
"use strict";

const ops = {
    page: "overview",
    catalog: { events: [], stations: [], materials: [], employees: [] },
    ledgerPage: 1,
    changePage: 1,
    editing: null,
};

const titles = {
    overview: "运营总览",
    events: "赛事与站点",
    materials: "物资目录",
    ledger: "交接台账",
    reviews: "差异复核",
    changes: "操作记录",
};

const labels = {
    planned: "筹备中",
    active: "进行中",
    closed: "已归档",
    demand: "需求",
    outbound: "出库",
    receipt: "签收",
    return: "余料回收",
    water: "饮水",
    food: "食品",
    medical: "医疗",
    equipment: "器材",
    other: "其他",
    draft: "未提交",
    pending_review: "待复核",
    resolved: "已完成",
    needs_information: "待补充",
    event: "赛事",
    station: "站点",
    material: "物资",
    ledger: "交接记录",
    save: "保存",
    create: "登记",
    state: "状态变更",
    void: "作废",
    review: "复核",
};

function node(tag, text, parent, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    if (tag === "button") element.type = "button";
    parent?.append(element);
    return element;
}

function notice(message, error = false) {
    const target = document.getElementById("ops-notice");
    target.textContent = message;
    target.classList.toggle("is-error", error);
    target.classList.toggle("has-message", Boolean(message));
}

async function request(url, options = {}) {
    const headers = { "X-Requested-With": "race-supply" };
    if (options.body && !(options.body instanceof FormData)) {
        headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(options.body);
    }
    const response = await fetch("/admin/operations" + url, { ...options, headers });
    const data = await response.json();
    if (!response.ok) {
        if (response.status === 401) {
            throw new Error("登录已过期，请返回员工入口重新登录管理员账号。");
        }
        throw new Error(data.message || "操作未完成，请检查输入并重试。");
    }
    return data;
}

function action(parent, text, handler, secondary = true) {
    const button = node("button", text, parent, secondary ? "secondary" : "");
    button.addEventListener("click", async () => {
        button.disabled = true;
        try {
            await handler();
        } catch (error) {
            notice(error.message, true);
        } finally {
            button.disabled = false;
        }
    });
    return button;
}

function table(targetId, headings, rows, render) {
    const root = document.getElementById(targetId);
    root.replaceChildren();
    if (!rows.length) {
        node("p", "暂无记录，可从新增或导入开始。", root, "ops-empty");
        return;
    }
    const grid = node("table", undefined, root);
    const header = node("tr", undefined, node("thead", undefined, grid));
    for (const heading of headings) node("th", heading, header);
    const body = node("tbody", undefined, grid);
    for (const record of rows) render(record, node("tr", undefined, body));
}

function cell(row, text) {
    return node("td", text, row);
}

function badge(parent, state) {
    return node("span", labels[state] || state, parent, "state-badge state-" + state);
}

function localTime(value) {
    return new Date(value * 1000).toLocaleString("zh-CN", { hour12: false });
}

function selectItems(select, items, placeholder = null, selected = "") {
    select.replaceChildren();
    if (placeholder !== null) {
        const option = node("option", placeholder, select);
        option.value = "";
    }
    for (const item of items) {
        const option = node("option", item.label, select);
        option.value = String(item.value);
        option.selected = String(item.value) === String(selected);
    }
}

function stationOptions() {
    return ops.catalog.stations.map((s) => ({
        value: s.id,
        label: s.event_name + " / " + s.code + " " + s.name,
    }));
}

async function loadCatalog() {
    ops.catalog = await request("/catalog");
    const filter = document.getElementById("filter-event");
    const previous = filter.value;
    selectItems(
        filter,
        ops.catalog.events.map((e) => ({ value: e.id, label: e.name })),
        "全部赛事",
        previous,
    );
    selectItems(document.getElementById("publish-station"), stationOptions(), "请选择站点");
}

async function showPage(page) {
    if (!Object.hasOwn(titles, page)) return;
    ops.page = page;
    document.getElementById("page-title").textContent = titles[page];
    for (const section of document.querySelectorAll(".ops-page")) {
        section.hidden = section.id !== "page-" + page;
    }
    for (const button of document.querySelectorAll("[data-page]")) {
        if (button.dataset.page === page) button.setAttribute("aria-current", "page");
        else button.removeAttribute("aria-current");
    }
    location.hash = page;
    const loaders = {
        overview: loadOverview,
        events: renderEvents,
        materials: renderMaterials,
        ledger: loadLedger,
        reviews: loadReviews,
        changes: loadChanges,
    };
    try {
        await loaders[page]();
    } catch (error) {
        notice(error.message, true);
    }
}

async function loadOverview() {
    const data = await request("/summary");
    const counts = document.getElementById("overview-counts");
    counts.replaceChildren();
    const metrics = [
        ["events", "赛事档案", "events"],
        ["stations", "补给站点", "events"],
        ["materials", "物资种类", "materials"],
        ["pending", "待复核事项", "reviews"],
    ];
    for (const [key, title, page] of metrics) {
        const item = node("button", undefined, counts, "metric-item");
        node("span", title, item);
        node("strong", data.counts[key], item);
        node("small", "查看详情", item);
        item.onclick = () => showPage(page);
    }
    table("overview-totals", ["物资", "单位", "需求", "出库", "签收", "回收"], data.totals, (r, tr) => {
        for (const key of ["material", "unit", "demand", "outbound", "receipt", "return"]) {
            cell(tr, r[key]);
        }
    });
    const list = document.getElementById("overview-activity");
    list.replaceChildren();
    for (const activity of data.activity) {
        const item = node("li", undefined, list);
        node("strong", activity.username + " " + (labels[activity.action] || activity.action), item);
        node("p", (labels[activity.entity] || activity.entity) + " #" + activity.entity_id, item);
        node("time", localTime(activity.created), item);
    }
    if (!data.activity.length) node("li", "登记第一条记录后，在这里查看操作轨迹。", list);
}

function renderEvents() {
    const container = document.getElementById("events-list");
    container.replaceChildren();
    for (const event of ops.catalog.events) {
        const card = node("article", undefined, container, "event-item");
        const head = node("div", undefined, card, "panel-heading");
        node("h2", event.name, head);
        badge(head, event.state);
        node("p", event.event_date + " / " + (event.location || "地点待补充"), card);
        node("p", "赛事编号 " + event.code, card, "subtle");
        const stations = ops.catalog.stations.filter((s) => s.event_id === event.id);
        node("p", stations.length + " 个站点", card);
        const controls = node("div", undefined, card, "button-row");
        if (event.state !== "closed") action(controls, "编辑", () => edit("event", event));
        const nextStates = event.state === "planned" ? ["active", "closed"] : event.state === "active" ? ["closed"] : ["active"];
        for (const state of nextStates) {
            action(controls, state === "closed" ? "归档赛事" : "启用赛事", async () => {
                if (!confirm("确认将赛事状态改为“" + labels[state] + "”？")) return;
                await request("/events/" + event.id + "/state", {
                    method: "POST",
                    body: { version: event.version, state },
                });
                await refresh();
                notice("赛事状态已更新。");
            });
        }
    }
    if (!ops.catalog.events.length) node("p", "先创建赛事，再添加沿线补给站点。", container, "ops-empty");
    table("stations-table", ["赛事", "站点", "公里点", "联系人", "授权员工", "操作"], ops.catalog.stations, (s, tr) => {
        cell(tr, s.event_name);
        cell(tr, s.code + " " + s.name);
        cell(tr, s.kilometer + " km");
        cell(tr, s.contact || "待补充");
        cell(tr, s.employee_ids.map((id) => ops.catalog.employees.find((e) => e.id === id)?.username || id).join("、") || "尚未授权");
        action(cell(tr), "编辑", () => edit("station", s));
    });
}

function renderMaterials() {
    table("materials-table", ["编号", "物资", "基准单位", "分类", "备注", "操作"], ops.catalog.materials, (m, tr) => {
        cell(tr, m.code);
        cell(tr, m.name);
        cell(tr, m.unit);
        cell(tr, labels[m.category]);
        cell(tr, m.notes || "—");
        action(cell(tr), "编辑", () => edit("material", m));
    });
}

function filters() {
    const params = new URLSearchParams();
    for (const [key, value] of new FormData(document.getElementById("ledger-filter"))) {
        if (value) params.set(key, value);
    }
    return params;
}

function pager(id, result, go) {
    const root = document.getElementById(id);
    root.replaceChildren();
    node("span", "共 " + result.total + " 条 · 第 " + result.page + " 页", root);
    const previous = action(root, "上一页", () => go(result.page - 1));
    previous.disabled = result.page <= 1;
    const next = action(root, "下一页", () => go(result.page + 1));
    next.disabled = result.page * result.size >= result.total;
}

async function loadLedger() {
    const params = filters();
    params.set("page", ops.ledgerPage);
    const result = await request("/ledger?" + params);
    const exportParams = new URLSearchParams();
    if (params.has("event_id")) exportParams.set("event_id", params.get("event_id"));
    document.getElementById("export-ledger").href = "/admin/operations/export.csv?" + exportParams;
    table("ledger-table", ["日期 / 记录", "赛事 / 站点", "批次", "类型", "物资", "数量", "经手人", "状态"], result.items, (r, tr) => {
        const when = cell(tr, r.occurred_on);
        node("small", r.record_code, when);
        const station = cell(tr, r.event_name);
        node("small", r.station_name, station);
        cell(tr, r.batch_code);
        badge(cell(tr), r.kind);
        cell(tr, r.material_name);
        cell(tr, r.quantity + " " + r.unit);
        cell(tr, r.operator);
        const actions = cell(tr);
        if (r.void_reason !== null) {
            node("span", "已作废", actions, "subtle");
            node("small", r.void_reason, actions);
        } else {
            action(actions, "作废", async () => {
                const reason = prompt("请填写作废原因，至少三个字。原记录将保留以供追溯。");
                if (!reason) return;
                await request("/ledger/" + r.id + "/void", {
                    method: "POST",
                    body: { reason, version: r.version },
                });
                await loadLedger();
                notice("记录已作废，关联旧快照已失效，请重新核对发布。");
            });
        }
    });
    pager("ledger-pager", result, async (page) => {
        ops.ledgerPage = page;
        await loadLedger();
    });
}

async function loadReviews() {
    const state = document.getElementById("review-state").value;
    const records = await request("/reviews" + (state ? "?state=" + state : ""));
    const container = document.getElementById("reviews-list");
    container.replaceChildren();
    if (!records.length) node("p", "当前没有此状态的复核事项。", container, "ops-empty");
    for (const record of records) {
        const section = node("article", undefined, container, "review-item");
        const head = node("div", undefined, section, "panel-heading");
        node("h3", (record.kind === "receipt_discrepancy" ? "签收差异" : "记录缺项") + " / " + record.content.batch_id, head);
        badge(head, record.state);
        node("p", "提交人 " + record.username + " · " + localTime(record.created), section, "subtle");
        for (const issue of record.content.issues || []) {
            node("p", issue.message, section);
            if (issue.outbound !== undefined) {
                node("p", "出库 " + issue.outbound + " / 签收 " + issue.receipt + " " + issue.unit, section);
            }
        }
        const detail = node("details", undefined, section);
        node("summary", "查看校验依据和完整内容", detail);
        node("pre", JSON.stringify(record.content, null, 2), detail);
        for (const decision of record.decisions) {
            node("p", decision.username + " · " + labels[decision.decision] + " · " + decision.note, section, "review-note");
        }
        if (record.state === "pending_review") {
            const label = node("label", "复核意见", section);
            const note = node("textarea", undefined, label);
            note.rows = 3;
            note.maxLength = 2000;
            const controls = node("div", undefined, section, "button-row");
            for (const decision of ["resolved", "needs_information"]) {
                action(controls, decision === "resolved" ? "记录复核完成" : "要求补充资料", async () => {
                    if (note.value.trim().length < 3) throw new Error("请填写至少三个字的复核意见。");
                    await request("/reviews/" + record.id, {
                        method: "POST",
                        body: { expected_state: "pending_review", decision, note: note.value.trim() },
                    });
                    await loadReviews();
                    notice("复核结论已保存。");
                }, decision !== "resolved");
            }
        }
    }
}

async function loadChanges() {
    const result = await request("/changes?page=" + ops.changePage);
    table("changes-table", ["时间", "操作者", "操作", "对象", "变更内容"], result.items, (r, tr) => {
        cell(tr, localTime(r.created));
        cell(tr, r.username);
        cell(tr, labels[r.action] || r.action);
        cell(tr, (labels[r.entity] || r.entity) + " #" + r.entity_id);
        const detail = node("details", undefined, cell(tr));
        node("summary", "展开前后对照", detail);
        node("h4", "变更前", detail);
        node("pre", JSON.stringify(JSON.parse(r.before_json), null, 2), detail);
        node("h4", "变更后", detail);
        node("pre", JSON.stringify(JSON.parse(r.after_json), null, 2), detail);
    });
    pager("changes-pager", result, async (page) => {
        ops.changePage = page;
        await loadChanges();
    });
}

// 表单元数据定义每个字段的业务类型，与后端严格请求模型对应。
function fieldsFor(type) {
    const events = ops.catalog.events.filter((e) => e.state !== "closed").map((e) => ({ value: e.id, label: e.name }));
    const materials = ops.catalog.materials.map((m) => ({ value: m.id, label: m.code + " " + m.name }));
    const definitions = {
        event: [
            ["code", "赛事编号", "text", true, 60],
            ["name", "赛事名称", "text", true, 120],
            ["event_date", "比赛日期", "date", true],
            ["location", "举办地点", "text", false, 200],
            ["notes", "备注", "textarea", false, 2000],
        ],
        station: [
            ["event_id", "所属赛事", "select", true, events],
            ["code", "站点编号", "text", true, 60],
            ["name", "站点名称", "text", true, 120],
            ["kilometer", "公里点", "number", true],
            ["contact", "联系人", "text", false, 120],
            ["employee_ids", "可查询员工", "employees", false],
        ],
        material: [
            ["code", "物资编号", "text", true, 60],
            ["name", "物资名称", "text", true, 120],
            ["unit", "基准单位", "text", true, 20],
            ["category", "分类", "select", true, ["water", "food", "medical", "equipment", "other"].map((v) => ({ value: v, label: labels[v] }))],
            ["notes", "备注", "textarea", false, 1000],
        ],
        ledger: [
            ["station_id", "站点", "select", true, stationOptions()],
            ["material_id", "物资", "select", true, materials],
            ["batch_code", "批次编号", "text", true, 60],
            ["record_code", "交接记录编号", "text", true, 60],
            ["kind", "交接类型", "select", true, ["demand", "outbound", "receipt", "return"].map((v) => ({ value: v, label: labels[v] }))],
            ["quantity", "数量", "decimal", true],
            ["unit", "记录单位", "text", true, 20],
            ["operator", "经手人", "text", true, 120],
            ["occurred_on", "交接日期", "date", true],
            ["notes", "备注", "textarea", false, 1000],
        ],
    };
    return definitions[type];
}

function edit(type, record = null) {
    ops.editing = { type, record };
    const form = document.getElementById("entity-form");
    form.reset();
    document.getElementById("form-error").textContent = "";
    document.getElementById("dialog-title").textContent = (record ? "编辑" : "新增") + labels[type];
    document.getElementById("form-help").textContent = type === "station"
        ? "员工只能查询授权站点发布的批次。修改授权后需要重新发布快照。"
        : type === "ledger" ? "交接编号在站点和批次内唯一。录错后作废原记录，再登记更正记录。" : "填写业务档案，编号用于后续记录关联。";
    const root = document.getElementById("entity-fields");
    root.replaceChildren();
    for (const [name, title, kind, required, extra] of fieldsFor(type)) {
        const label = node("label", title + (required ? " *" : ""), root);
        if (kind === "employees") {
            label.className = "wide-field";
            for (const employee of ops.catalog.employees) {
                const row = node("label", undefined, label, "check-label");
                const checkbox = node("input", undefined, row);
                checkbox.type = "checkbox";
                checkbox.name = name;
                checkbox.value = employee.id;
                checkbox.checked = record?.employee_ids?.includes(employee.id) || false;
                row.append(document.createTextNode(employee.username));
            }
            if (!ops.catalog.employees.length) node("small", "请先创建员工账号。", label);
            continue;
        }
        const tag = kind === "select" ? "select" : kind === "textarea" ? "textarea" : "input";
        const field = node(tag, undefined, label);
        field.name = name;
        field.required = required;
        if (kind === "select") selectItems(field, extra, "请选择", record?.[name] || "");
        else {
            if (tag === "input") field.type = kind === "decimal" ? "text" : kind;
            if (typeof extra === "number") field.maxLength = extra;
            field.value = record?.[name] ?? "";
            if (kind === "number") { field.min = "0"; field.max = "1000"; field.step = "0.1"; }
            if (kind === "decimal") { field.inputMode = "decimal"; field.pattern = "[0-9]{1,9}(\\.[0-9]{1,4})?"; }
            if (kind === "textarea") { field.rows = 3; label.className = "wide-field"; }
        }
        if (record && (name === "code" || name === "event_id" || (type === "material" && name === "unit"))) field.disabled = true;
    }
    document.getElementById("edit-dialog").showModal();
}

async function saveEntity(event) {
    event.preventDefault();
    const button = document.getElementById("save-entity");
    button.disabled = true;
    try {
        const { type, record } = ops.editing;
        const form = document.getElementById("entity-form");
        const data = {};
        for (const [name, , kind] of fieldsFor(type)) {
            if (kind === "employees") {
                data[name] = [...form.querySelectorAll('input[name="employee_ids"]:checked')].map((c) => Number(c.value));
            } else {
                const field = form.elements.namedItem(name);
                data[name] = ["event_id", "station_id", "material_id", "kilometer"].includes(name) ? Number(field.value) : field.value.trim();
            }
        }
        if (record) data.version = record.version;
        const collection = { event: "events", station: "stations", material: "materials", ledger: "ledger" }[type];
        await request("/" + collection + (record ? "/" + record.id : ""), {
            method: record ? "PUT" : "POST",
            body: data,
        });
        document.getElementById("edit-dialog").close();
        await refresh();
        notice(labels[type] + "已保存。");
    } catch (error) {
        document.getElementById("form-error").textContent = error.message;
    } finally {
        button.disabled = false;
    }
}

async function refresh() {
    await loadCatalog();
    await showPage(ops.page);
}

document.querySelectorAll("[data-page]").forEach((button) => {
    button.onclick = () => showPage(button.dataset.page);
});
document.querySelectorAll("[data-open-page]").forEach((button) => {
    button.onclick = () => showPage(button.dataset.openPage);
});
document.querySelectorAll("[data-new]").forEach((button) => {
    button.onclick = () => edit(button.dataset.new);
});
document.getElementById("close-dialog").onclick = () => document.getElementById("edit-dialog").close();
document.getElementById("entity-form").onsubmit = saveEntity;
document.getElementById("refresh-ops").onclick = () => refresh().catch((error) => notice(error.message, true));
document.getElementById("review-state").onchange = () => loadReviews().catch((error) => notice(error.message, true));
document.getElementById("ledger-filter").onsubmit = (event) => {
    event.preventDefault();
    ops.ledgerPage = 1;
    loadLedger().catch((error) => notice(error.message, true));
};
document.getElementById("csv-import").onsubmit = async (event) => {
    event.preventDefault();
    const button = event.target.querySelector("button");
    button.disabled = true;
    try {
        const result = await request("/import", { method: "POST", body: new FormData(event.target) });
        await loadLedger();
        notice("导入完成，新增 " + result.created + " 条，跳过已存在的 " + result.reused + " 条。");
    } catch (error) {
        notice(error.message, true);
    } finally {
        button.disabled = false;
    }
};
document.getElementById("publish-batch").onsubmit = async (event) => {
    event.preventDefault();
    const form = event.target;
    const button = form.querySelector("button");
    button.disabled = true;
    try {
        const data = new FormData(form);
        const result = await request("/publish-batch", {
            method: "POST",
            body: {
                station_id: Number(data.get("station_id")),
                batch_code: data.get("batch_code").trim(),
                confirmed: data.has("confirmed"),
            },
        });
        notice("批次快照 v" + result.version + " 已发布，授权员工可在聊天中查询核对。");
        form.elements.namedItem("confirmed").checked = false;
    } catch (error) {
        notice(error.message, true);
    } finally {
        button.disabled = false;
    }
};
window.addEventListener("hashchange", () => {
    const page = location.hash.slice(1);
    if (page !== ops.page) showPage(page);
});
loadCatalog().then(() => showPage(location.hash.slice(1) || "overview")).catch((error) => notice(error.message, true));
