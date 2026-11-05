"""运营工作台路由，所有页面和接口均要求管理员身份。"""

import asyncio
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from race_supply.operations import (
    CSV_FIELDS,
    EventInput,
    EventUpdate,
    LedgerInput,
    MaterialInput,
    MaterialUpdate,
    Operations,
    OperationsError,
    PublishInput,
    ReviewInput,
    StateInput,
    StationInput,
    StationUpdate,
    VoidInput,
    csv_text,
    parse_ledger_csv,
)


def register_operations(app, store, admin, agent):
    service = Operations(store)
    app.state.operations = service
    router = APIRouter(dependencies=[Depends(admin)])
    # 本机单 worker 的写入排序，同时防止发布快照与台账修改交错。
    write_lock = asyncio.Lock()
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )

    @app.exception_handler(OperationsError)
    async def operations_error(request, error):
        return JSONResponse(
            {"code": error.code, "message": error.message},
            status_code=error.status,
        )

    async def mutate(method, *args):
        async with write_lock:
            try:
                return await asyncio.to_thread(method, *args)
            except sqlite3.IntegrityError:
                raise OperationsError(
                    "DUPLICATE_OR_REFERENCE",
                    "编号重复或关联记录不存在，请检查输入",
                    409,
                ) from None

    @router.get("/admin/operations", response_class=HTMLResponse)
    async def page(current=Depends(admin)):
        return HTMLResponse(env.get_template("operations.html").render(current=current))

    @router.get("/admin/operations/catalog")
    async def catalog():
        return await asyncio.to_thread(service.catalog)

    @router.get("/admin/operations/summary")
    async def summary(event_id: int | None = Query(default=None, gt=0)):
        return await asyncio.to_thread(service.summary, event_id)

    @router.post("/admin/operations/events")
    async def create_event(body: EventInput, current=Depends(admin)):
        return await mutate(service.save_event, body, current)

    @router.put("/admin/operations/events/{identifier}")
    async def update_event(identifier: int, body: EventUpdate, current=Depends(admin)):
        return await mutate(service.save_event, body, current, identifier)

    @router.post("/admin/operations/events/{identifier}/state")
    async def event_state(identifier: int, body: StateInput, current=Depends(admin)):
        return await mutate(service.event_state, identifier, body, current)

    @router.post("/admin/operations/stations")
    async def create_station(body: StationInput, current=Depends(admin)):
        return await mutate(service.save_station, body, current)

    @router.put("/admin/operations/stations/{identifier}")
    async def update_station(
        identifier: int, body: StationUpdate, current=Depends(admin)
    ):
        return await mutate(service.save_station, body, current, identifier)

    @router.post("/admin/operations/materials")
    async def create_material(body: MaterialInput, current=Depends(admin)):
        return await mutate(service.save_material, body, current)

    @router.put("/admin/operations/materials/{identifier}")
    async def update_material(
        identifier: int, body: MaterialUpdate, current=Depends(admin)
    ):
        return await mutate(service.save_material, body, current, identifier)

    @router.get("/admin/operations/ledger")
    async def ledger(
        event_id: int | None = Query(default=None, gt=0),
        station_id: int | None = Query(default=None, gt=0),
        kind: str | None = Query(
            default=None, pattern="^(demand|outbound|receipt|return)$"
        ),
        query: str = Query(default="", max_length=100),
        page: int = Query(default=1, ge=1),
        size: int = Query(default=25, ge=1, le=100),
    ):
        return await asyncio.to_thread(
            service.ledger, event_id, station_id, kind, query, page, size
        )

    @router.post("/admin/operations/ledger")
    async def add_record(body: LedgerInput, current=Depends(admin)):
        return await mutate(service.add_record, body, current)

    @router.post("/admin/operations/ledger/{identifier}/void")
    async def void_record(identifier: int, body: VoidInput, current=Depends(admin)):
        return await mutate(service.void_record, identifier, body, current)

    @router.get("/admin/operations/ledger-template.csv")
    async def template():
        return Response(
            csv_text(CSV_FIELDS, []),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="ledger-template.csv"'
            },
        )

    @router.post("/admin/operations/import")
    async def import_csv(file: UploadFile = File(...), current=Depends(admin)):
        raw = await file.read(900_001)
        if len(raw) > 900_000:
            raise OperationsError("CSV_TOO_LARGE", "CSV 文件最多900KB")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise OperationsError(
                "CSV_ENCODING", "请将文件保存为 UTF-8 编码的 CSV"
            ) from None
        rows = parse_ledger_csv(text)
        result = await mutate(service.add_records, rows, current)
        return {
            "total": len(result),
            "created": sum(not r.get("reused", False) for r in result),
            "reused": sum(r.get("reused", False) for r in result),
        }

    @router.get("/admin/operations/export.csv")
    async def export(
        event_id: int | None = Query(default=None, gt=0), current=Depends(admin)
    ):
        result = await asyncio.to_thread(
            service.ledger, event_id, None, None, "", 1, 5000
        )
        if result["total"] > 5000:
            raise OperationsError("EXPORT_TOO_LARGE", "导出超过5000条，请按赛事筛选")
        fields = CSV_FIELDS + [
            "event_name",
            "station_name",
            "material_name",
            "version",
            "void_reason",
        ]
        await asyncio.to_thread(
            store.audit, current["id"], "ledger_export", str(event_id or "all")
        )
        return Response(
            csv_text(fields, result["items"]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="race-ledger.csv"'},
        )

    @router.post("/admin/operations/publish-batch")
    async def publish_batch(body: PublishInput, current=Depends(admin)):
        if not body.confirmed:
            raise OperationsError("CONFIRM_REQUIRED", "请核对站点授权和台账后确认发布")
        async with write_lock:
            snapshot = await asyncio.to_thread(service.snapshot, body)
            result = await asyncio.to_thread(agent.import_batch, snapshot, current)
        return result

    @router.get("/admin/operations/reviews")
    async def reviews(
        state: str | None = Query(
            default=None, pattern="^(draft|pending_review|resolved|needs_information)$"
        )
    ):
        return await asyncio.to_thread(service.reviews, state)

    @router.post("/admin/operations/reviews/{identifier}")
    async def decide_review(identifier: str, body: ReviewInput, current=Depends(admin)):
        return await mutate(service.review, identifier, body, current)

    @router.get("/admin/operations/changes")
    async def changes(page: int = Query(default=1, ge=1)):
        return await asyncio.to_thread(service.changes, page)

    app.include_router(router)
    return service
