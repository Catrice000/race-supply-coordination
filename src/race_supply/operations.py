"""赛事运营台账。

管理员维护本机计划和交接记录，员工通过已有 Agent 授权快照查询。
所有修改使用 SQLite 事务，版本号用于阻止覆盖他人的更新。
台账数量保留十进制字符串，同一物资的不同单位分别汇总。
"""

import csv
import io
import json
import sqlite3
import time
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from race_supply.agent import BatchInput, Strict


class OperationsError(ValueError):
    def __init__(self, code, message, status=422):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


class EventInput(Strict):
    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=120)
    event_date: str
    location: str = Field(max_length=200)
    notes: str = Field(default="", max_length=2000)

    @field_validator("event_date")
    @classmethod
    def valid_date(cls, value):
        date.fromisoformat(value)
        return value

    @field_validator("code", "name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("编号和名称不能为空白")
        return value.strip()


class StationInput(Strict):
    event_id: int = Field(gt=0)
    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=120)
    kilometer: float = Field(ge=0, le=1000, allow_inf_nan=False)
    contact: str = Field(default="", max_length=120)
    employee_ids: list[int] = Field(default_factory=list, max_length=100)

    @field_validator("code", "name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("编号和名称不能为空白")
        return value.strip()

    @field_validator("employee_ids")
    @classmethod
    def unique_ids(cls, value):
        if any(i <= 0 for i in value) or len(value) != len(set(value)):
            raise ValueError("员工编号必须为不重复的正整数")
        return value


class MaterialInput(Strict):
    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=120)
    unit: str = Field(min_length=1, max_length=20)
    category: Literal["water", "food", "medical", "equipment", "other"] = "other"
    notes: str = Field(default="", max_length=1000)

    @field_validator("code", "name", "unit")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("物资编号、名称和单位不能为空白")
        return value.strip()


class LedgerInput(Strict):
    station_id: int = Field(gt=0)
    material_id: int = Field(gt=0)
    batch_code: str = Field(min_length=1, max_length=60)
    record_code: str = Field(min_length=1, max_length=60)
    kind: Literal["demand", "outbound", "receipt", "return"]
    quantity: str = Field(pattern=r"^\d{1,9}(\.\d{1,4})?$")
    unit: str = Field(min_length=1, max_length=20)
    operator: str = Field(min_length=1, max_length=120)
    occurred_on: str
    notes: str = Field(default="", max_length=1000)

    @field_validator("occurred_on")
    @classmethod
    def valid_date(cls, value):
        date.fromisoformat(value)
        return value

    @field_validator("batch_code", "record_code", "unit", "operator")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("记录必要字段不能为空白")
        return value.strip()


class Versioned(Strict):
    version: int = Field(ge=1)


class EventUpdate(EventInput, Versioned):
    pass


class StationUpdate(StationInput, Versioned):
    pass


class MaterialUpdate(MaterialInput, Versioned):
    pass


class StateInput(Versioned):
    state: Literal["planned", "active", "closed"]


class VoidInput(Versioned):
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        if len(value.strip()) < 3:
            raise ValueError("请填写具体作废理由")
        return value.strip()


class ReviewInput(Strict):
    expected_state: Literal["pending_review"]
    decision: Literal["resolved", "needs_information"]
    note: str = Field(min_length=3, max_length=2000)

    @field_validator("note")
    @classmethod
    def nonblank(cls, value):
        if len(value.strip()) < 3:
            raise ValueError("请填写具体复核意见")
        return value.strip()


class PublishInput(Strict):
    station_id: int = Field(gt=0)
    batch_code: str = Field(min_length=1, max_length=60)
    confirmed: bool


class Operations:
    """所有台账写入与审计使用同一事务，失败时一起回滚。"""

    def __init__(self, store):
        self.store = store
        with store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS ops_events(
                    id INTEGER PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    location TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'planned',
                    version INTEGER NOT NULL DEFAULT 1,
                    created REAL NOT NULL,
                    updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ops_stations(
                    id INTEGER PRIMARY KEY,
                    event_id INTEGER NOT NULL REFERENCES ops_events(id),
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    kilometer REAL NOT NULL,
                    contact TEXT NOT NULL,
                    employee_ids TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    created REAL NOT NULL,
                    updated REAL NOT NULL,
                    UNIQUE(event_id,code)
                );
                CREATE TABLE IF NOT EXISTS ops_materials(
                    id INTEGER PRIMARY KEY,
                    code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    category TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    created REAL NOT NULL,
                    updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ops_ledger(
                    id INTEGER PRIMARY KEY,
                    station_id INTEGER NOT NULL REFERENCES ops_stations(id),
                    material_id INTEGER NOT NULL REFERENCES ops_materials(id),
                    batch_code TEXT NOT NULL,
                    record_code TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    operator TEXT NOT NULL,
                    occurred_on TEXT NOT NULL,
                    notes TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    void_reason TEXT,
                    created REAL NOT NULL,
                    UNIQUE(station_id,batch_code,record_code)
                );
                CREATE INDEX IF NOT EXISTS ops_ledger_station
                    ON ops_ledger(station_id,batch_code);
                CREATE TABLE IF NOT EXISTS ops_changes(
                    id INTEGER PRIMARY KEY,
                    actor INTEGER NOT NULL REFERENCES users(id),
                    action TEXT NOT NULL,
                    entity TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ops_review_decisions(
                    id INTEGER PRIMARY KEY,
                    draft_id TEXT NOT NULL REFERENCES review_drafts(id),
                    reviewer INTEGER NOT NULL REFERENCES users(id),
                    decision TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created REAL NOT NULL
                );
            """)

    @staticmethod
    def record(db, table, identifier):
        # 表名完全由代码常量提供，用户输入只进入参数占位符。
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise OperationsError("NOT_FOUND", "记录不存在", 404)
        return dict(row)

    @staticmethod
    def change(db, actor, action, entity, identifier, before, after):
        db.execute(
            "INSERT INTO ops_changes(actor,action,entity,entity_id,before_json,after_json,created) VALUES(?,?,?,?,?,?,?)",
            (
                actor["id"],
                action,
                entity,
                str(identifier),
                json.dumps(before, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False),
                time.time(),
            ),
        )

    @staticmethod
    def version(row, expected):
        if row["version"] != expected:
            raise OperationsError(
                "VERSION_CONFLICT", "记录已被其他操作更新，请刷新后重试", 409
            )

    @staticmethod
    def writable_event(db, event_id):
        event = Operations.record(db, "ops_events", event_id)
        if event["state"] == "closed":
            raise OperationsError("EVENT_CLOSED", "赛事已归档，请先重新启用再修改", 409)
        return event

    @staticmethod
    def employees(db, identifiers):
        found = {r[0] for r in db.execute("SELECT id FROM users WHERE role='employee'")}
        if set(identifiers) - found:
            raise OperationsError("UNKNOWN_EMPLOYEE", "授权列表包含不存在的员工")

    def catalog(self):
        with self.store.connection() as db:
            return {
                "events": [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM ops_events ORDER BY event_date DESC,id DESC"
                    )
                ],
                "stations": [
                    dict(r) | {"employee_ids": json.loads(r["employee_ids"])}
                    for r in db.execute(
                        "SELECT s.*,e.name AS event_name,e.code AS event_code FROM ops_stations s JOIN ops_events e ON e.id=s.event_id ORDER BY e.event_date DESC,s.kilometer"
                    )
                ],
                "materials": [
                    dict(r)
                    for r in db.execute("SELECT * FROM ops_materials ORDER BY name")
                ],
                "employees": [
                    dict(r)
                    for r in db.execute(
                        "SELECT id,username FROM users WHERE role='employee' ORDER BY username"
                    )
                ],
            }

    def save_event(self, body, actor, identifier=None):
        now = time.time()
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            before = {}
            if identifier is None:
                cursor = db.execute(
                    "INSERT INTO ops_events(code,name,event_date,location,notes,created,updated) VALUES(?,?,?,?,?,?,?)",
                    (
                        body.code,
                        body.name,
                        body.event_date,
                        body.location,
                        body.notes,
                        now,
                        now,
                    ),
                )
                identifier = cursor.lastrowid
            else:
                before = self.writable_event(db, identifier)
                self.version(before, body.version)
                if before["code"] != body.code:
                    raise OperationsError(
                        "CODE_IMMUTABLE", "赛事编号用于快照关联，创建后不可修改"
                    )
                db.execute(
                    "UPDATE ops_events SET name=?,event_date=?,location=?,notes=?,version=version+1,updated=? WHERE id=?",
                    (
                        body.name,
                        body.event_date,
                        body.location,
                        body.notes,
                        now,
                        identifier,
                    ),
                )
            after = self.record(db, "ops_events", identifier)
            self.change(db, actor, "save", "event", identifier, before, after)
        return after

    def event_state(self, identifier, body, actor):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            before = self.record(db, "ops_events", identifier)
            self.version(before, body.version)
            transitions = {
                "planned": {"active", "closed"},
                "active": {"closed"},
                "closed": {"active"},
            }
            if body.state not in transitions[before["state"]]:
                raise OperationsError(
                    "INVALID_TRANSITION", "当前赛事状态不支持此操作", 409
                )
            db.execute(
                "UPDATE ops_events SET state=?,version=version+1,updated=? WHERE id=?",
                (body.state, time.time(), identifier),
            )
            after = self.record(db, "ops_events", identifier)
            self.change(db, actor, "state", "event", identifier, before, after)
        return after

    def save_station(self, body, actor, identifier=None):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.writable_event(db, body.event_id)
            self.employees(db, body.employee_ids)
            before = {}
            now = time.time()
            employees = json.dumps(body.employee_ids)
            if identifier is None:
                cursor = db.execute(
                    "INSERT INTO ops_stations(event_id,code,name,kilometer,contact,employee_ids,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        body.event_id,
                        body.code,
                        body.name,
                        body.kilometer,
                        body.contact,
                        employees,
                        now,
                        now,
                    ),
                )
                identifier = cursor.lastrowid
            else:
                before = self.record(db, "ops_stations", identifier)
                self.version(before, body.version)
                if before["event_id"] != body.event_id or before["code"] != body.code:
                    raise OperationsError(
                        "CODE_IMMUTABLE", "站点所属赛事和编号创建后不可修改"
                    )
                db.execute(
                    "UPDATE ops_stations SET name=?,kilometer=?,contact=?,employee_ids=?,version=version+1,updated=? WHERE id=?",
                    (
                        body.name,
                        body.kilometer,
                        body.contact,
                        employees,
                        now,
                        identifier,
                    ),
                )
                # 收紧站点授权时立即撤销该站点旧的 Agent 快照，避免旧快照继续泄露。
                if set(json.loads(before["employee_ids"])) != set(body.employee_ids):
                    event = self.record(db, "ops_events", body.event_id)
                    self.invalidate_snapshots(db, event["code"], body.code)
            after = self.record(db, "ops_stations", identifier)
            self.change(db, actor, "save", "station", identifier, before, after)
        return after

    @staticmethod
    def invalidate_snapshots(db, event_code, station_code, batch_code=None):
        for row in db.execute(
            "SELECT id,batch_id,payload FROM agent_batches WHERE active=1"
        ).fetchall():
            payload = json.loads(row["payload"])
            matches = (
                payload["event_id"] == event_code
                and payload["station_id"] == station_code
            )
            if matches and (batch_code is None or row["batch_id"] == batch_code):
                db.execute("UPDATE agent_batches SET active=0 WHERE id=?", (row["id"],))

    def save_material(self, body, actor, identifier=None):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            before = {}
            now = time.time()
            if identifier is None:
                cursor = db.execute(
                    "INSERT INTO ops_materials(code,name,unit,category,notes,created,updated) VALUES(?,?,?,?,?,?,?)",
                    (
                        body.code,
                        body.name,
                        body.unit,
                        body.category,
                        body.notes,
                        now,
                        now,
                    ),
                )
                identifier = cursor.lastrowid
            else:
                before = self.record(db, "ops_materials", identifier)
                self.version(before, body.version)
                if before["code"] != body.code or before["unit"] != body.unit:
                    raise OperationsError(
                        "CODE_IMMUTABLE", "物资编号和基准单位创建后不可修改"
                    )
                db.execute(
                    "UPDATE ops_materials SET name=?,category=?,notes=?,version=version+1,updated=? WHERE id=?",
                    (body.name, body.category, body.notes, now, identifier),
                )
            after = self.record(db, "ops_materials", identifier)
            self.change(db, actor, "save", "material", identifier, before, after)
        return after

    def add_record(self, body, actor):
        return self.add_records([body], actor)[0]

    def add_records(self, bodies, actor):
        """批量导入在单一事务内完成，任一行不合法就整体回滚。"""
        created = []
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for body in bodies:
                station = self.record(db, "ops_stations", body.station_id)
                event = self.writable_event(db, station["event_id"])
                self.record(db, "ops_materials", body.material_id)
                values = body.model_dump()
                existing = db.execute(
                    "SELECT * FROM ops_ledger WHERE station_id=? AND batch_code=? AND record_code=?",
                    (body.station_id, body.batch_code, body.record_code),
                ).fetchone()
                if existing:
                    if existing["void_reason"] is None and all(
                        existing[k] == v for k, v in values.items()
                    ):
                        created.append(dict(existing) | {"reused": True})
                        continue
                    raise OperationsError(
                        "RECORD_CONFLICT", "交接编号已存在且内容不同，请核对原记录", 409
                    )
                columns = ",".join(values)
                marks = ",".join("?" for _ in values)
                cursor = db.execute(
                    f"INSERT INTO ops_ledger({columns},created) VALUES({marks},?)",
                    (*values.values(), time.time()),
                )
                record = self.record(db, "ops_ledger", cursor.lastrowid)
                self.invalidate_snapshots(
                    db, event["code"], station["code"], body.batch_code
                )
                self.change(db, actor, "create", "ledger", cursor.lastrowid, {}, record)
                created.append(record)
        return created

    def void_record(self, identifier, body, actor):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            before = self.record(db, "ops_ledger", identifier)
            self.version(before, body.version)
            station = self.record(db, "ops_stations", before["station_id"])
            event = self.writable_event(db, station["event_id"])
            if before["void_reason"] is not None:
                raise OperationsError("ALREADY_VOID", "该记录已作废", 409)
            db.execute(
                "UPDATE ops_ledger SET void_reason=?,version=version+1 WHERE id=?",
                (body.reason, identifier),
            )
            self.invalidate_snapshots(
                db, event["code"], station["code"], before["batch_code"]
            )
            after = self.record(db, "ops_ledger", identifier)
            self.change(db, actor, "void", "ledger", identifier, before, after)
        return after

    def ledger(
        self, event_id=None, station_id=None, kind=None, query="", page=1, size=25
    ):
        clauses = ["1=1"]
        params = []
        for column, value in [
            ("s.event_id", event_id),
            ("l.station_id", station_id),
            ("l.kind", kind),
        ]:
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        if query:
            clauses.append(
                "(instr(l.batch_code,?)>0 OR instr(l.record_code,?)>0 OR instr(m.name,?)>0)"
            )
            params.extend([query] * 3)
        source = """FROM ops_ledger l JOIN ops_stations s ON s.id=l.station_id
                    JOIN ops_events e ON e.id=s.event_id
                    JOIN ops_materials m ON m.id=l.material_id"""
        where = " AND ".join(clauses)
        with self.store.connection() as db:
            count = db.execute(
                f"SELECT count(*) {source} WHERE {where}", params
            ).fetchone()[0]
            rows = db.execute(
                f"""SELECT l.*,s.name AS station_name,s.code AS station_code,
                    e.name AS event_name,e.code AS event_code,m.name AS material_name,
                    m.code AS material_code {source} WHERE {where}
                    ORDER BY l.occurred_on DESC,l.id DESC LIMIT ? OFFSET ?""",
                (*params, size, (page - 1) * size),
            )
            return {
                "items": [dict(r) for r in rows],
                "total": count,
                "page": page,
                "size": size,
            }

    def summary(self, event_id=None):
        """按物资和原始单位汇总，避免未经确认就把箱和份相加。"""
        with self.store.connection() as db:
            counts = {
                "events": db.execute("SELECT count(*) FROM ops_events").fetchone()[0],
                "stations": db.execute("SELECT count(*) FROM ops_stations").fetchone()[
                    0
                ],
                "materials": db.execute(
                    "SELECT count(*) FROM ops_materials"
                ).fetchone()[0],
                "pending": db.execute(
                    "SELECT count(*) FROM review_drafts WHERE state='pending_review'"
                ).fetchone()[0],
            }
            clauses = "l.void_reason IS NULL"
            params = ()
            if event_id is not None:
                clauses += " AND s.event_id=?"
                params = (event_id,)
            rows = db.execute(
                f"""SELECT l.*,m.name AS material_name FROM ops_ledger l
                    JOIN ops_materials m ON m.id=l.material_id
                    JOIN ops_stations s ON s.id=l.station_id WHERE {clauses}""",
                params,
            ).fetchall()
            groups = {}
            for row in rows:
                key = (row["material_id"], row["unit"])
                item = groups.setdefault(
                    key,
                    {
                        "material": row["material_name"],
                        "unit": row["unit"],
                        "demand": Decimal(0),
                        "outbound": Decimal(0),
                        "receipt": Decimal(0),
                        "return": Decimal(0),
                    },
                )
                item[row["kind"]] += Decimal(row["quantity"])
            totals = []
            for group in groups.values():
                totals.append(
                    {
                        k: str(v) if isinstance(v, Decimal) else v
                        for k, v in group.items()
                    }
                )
            activity = [
                dict(r)
                for r in db.execute(
                    """SELECT c.id,c.action,c.entity,c.entity_id,c.created,u.username
                   FROM ops_changes c JOIN users u ON u.id=c.actor ORDER BY c.id DESC LIMIT 15"""
                )
            ]
        return {"counts": counts, "totals": totals, "activity": activity}

    def snapshot(self, body):
        """生成待人工发布的 Agent 输入，发布时仍调用原有批次校验入口。"""
        with self.store.connection() as db:
            station = self.record(db, "ops_stations", body.station_id)
            event = self.record(db, "ops_events", station["event_id"])
            rows = db.execute(
                """SELECT l.*,m.code AS material_code FROM ops_ledger l
                   JOIN ops_materials m ON m.id=l.material_id
                   WHERE station_id=? AND batch_code=? AND void_reason IS NULL
                   AND kind IN ('demand','outbound','receipt') ORDER BY l.id""",
                (body.station_id, body.batch_code),
            ).fetchall()
        if not rows:
            raise OperationsError(
                "EMPTY_BATCH", "该批次没有可发布的需求、出库或签收记录"
            )
        if len(rows) > 500:
            raise OperationsError(
                "BATCH_TOO_LARGE", "单批次超过500行，请按真实业务批次拆分"
            )
        employees = json.loads(station["employee_ids"])
        if not employees:
            raise OperationsError("NO_EMPLOYEES", "请先为站点配置有权查询的员工")
        return BatchInput(
            batch_id=body.batch_code,
            event_id=event["code"],
            station_id=station["code"],
            employee_ids=employees,
            records=[
                {
                    "record_id": r["record_code"],
                    "material_id": r["material_code"],
                    "kind": r["kind"],
                    "quantity": r["quantity"],
                    "unit": r["unit"],
                }
                for r in rows
            ],
        )

    def reviews(self, state=None):
        where = "WHERE d.state=?" if state else ""
        with self.store.connection() as db:
            rows = db.execute(
                f"""SELECT d.*,u.username FROM review_drafts d
                    JOIN users u ON u.id=d.user_id {where} ORDER BY d.created DESC LIMIT 200""",
                (state,) if state else (),
            ).fetchall()
            decisions = [
                dict(r)
                for r in db.execute("""SELECT r.*,u.username FROM ops_review_decisions r
                   JOIN users u ON u.id=r.reviewer ORDER BY r.id DESC LIMIT 1000""")
            ]
        return [
            dict(row)
            | {
                "content": json.loads(row["content"]),
                "decisions": [d for d in decisions if d["draft_id"] == row["id"]],
            }
            for row in rows
        ]

    def review(self, identifier, body, actor):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.record(db, "review_drafts", identifier)
            if row["state"] != body.expected_state:
                raise OperationsError(
                    "REVIEW_CONFLICT", "草稿状态已变化，请刷新后复核", 409
                )
            db.execute(
                "UPDATE review_drafts SET state=? WHERE id=?",
                (body.decision, identifier),
            )
            db.execute(
                "INSERT INTO ops_review_decisions(draft_id,reviewer,decision,note,created) VALUES(?,?,?,?,?)",
                (identifier, actor["id"], body.decision, body.note, time.time()),
            )
            self.change(
                db,
                actor,
                "review",
                "draft",
                identifier,
                {"state": row["state"]},
                {"state": body.decision, "note": body.note},
            )
        return {"draft_id": identifier, "state": body.decision}

    def changes(self, page=1):
        with self.store.connection() as db:
            total = db.execute("SELECT count(*) FROM ops_changes").fetchone()[0]
            rows = db.execute(
                """SELECT c.*,u.username FROM ops_changes c JOIN users u ON u.id=c.actor
                   ORDER BY c.id DESC LIMIT 25 OFFSET ?""",
                ((page - 1) * 25,),
            )
            items = [dict(r) for r in rows]
        return {"items": items, "total": total, "page": page, "size": 25}


CSV_FIELDS = list(LedgerInput.model_fields)


def parse_ledger_csv(text):
    """先验证所有行，再开始写事务。错误提示带行号但不回显敏感原文。"""
    from pydantic import ValidationError

    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if reader.fieldnames != CSV_FIELDS:
        raise OperationsError("CSV_HEADER", "CSV 表头不匹配，请下载模板后填写")
    result = []
    for number, row in enumerate(reader, 2):
        if number > 501:
            raise OperationsError("CSV_TOO_LARGE", "每次最多导入500条记录")
        try:
            row["station_id"] = int(row["station_id"])
            row["material_id"] = int(row["material_id"])
            result.append(LedgerInput.model_validate(row))
        except (ValidationError, ValueError, TypeError):
            raise OperationsError(
                "CSV_ROW", f"第{number}行内容不合法，请检查日期、数量和编号"
            ) from None
    if not result:
        raise OperationsError("CSV_EMPTY", "CSV 没有数据行")
    return result


def csv_text(fields, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(fields)
    for row in rows:
        # 防止电子表格软件把用户输入识别为公式。
        values = [str(row.get(key, "")) for key in fields]
        writer.writerow(
            [
                "'" + v if v.lstrip().startswith(("=", "+", "-", "@")) else v
                for v in values
            ]
        )
    return "\ufeff" + output.getvalue()
