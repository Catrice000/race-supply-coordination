"""运营台账真实 HTTP 路径回归，覆盖事务、版本、权限与 Agent 快照衔接。"""

import csv
import io
import json
import unittest

import test_web as base
from race_supply.operations import CSV_FIELDS


class OperationsTests(unittest.TestCase):
    setUpClass = classmethod(base.WebTests.setUpClass.__func__)
    tearDownClass = classmethod(base.WebTests.tearDownClass.__func__)
    tearDown = base.WebTests.tearDown
    client = base.WebTests.client

    def setUp(self):
        base.WebTests.setUp(self)
        self.prefix = "/admin/operations"
        self.employee = self.alice.get("/api/me").json()["id"]
        self.other = self.bob.get("/api/me").json()["id"]
        self.event_body = {
            "code": "RACE-TEST",
            "name": "测试赛事",
            "event_date": "2026-11-06",
            "location": "演练场地",
            "notes": "",
        }
        self.event = self.post("/events", self.event_body)
        self.station_body = {
            "event_id": self.event["id"],
            "code": "S01",
            "name": "五公里补给站",
            "kilometer": 5.0,
            "contact": "演练联系人",
            "employee_ids": [self.employee],
        }
        self.station = self.post("/stations", self.station_body)
        self.material_body = {
            "code": "water",
            "name": "饮用水",
            "unit": "份",
            "category": "water",
            "notes": "",
        }
        self.material = self.post("/materials", self.material_body)
        self.record_body = {
            "station_id": self.station["id"],
            "material_id": self.material["id"],
            "batch_code": "B001",
            "record_code": "OUT-01",
            "kind": "outbound",
            "quantity": "1",
            "unit": "箱",
            "operator": "测试经手人",
            "occurred_on": "2026-11-06",
            "notes": "",
        }

    def post(self, path, body):
        response = self.admin.post(self.prefix + path, json=body)
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def add_record(self, **updates):
        return self.post("/ledger", self.record_body | updates)

    def publish(self):
        return self.post(
            "/publish-batch",
            {
                "station_id": self.station["id"],
                "batch_code": "B001",
                "confirmed": True,
            },
        )

    def csv(self, rows):
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        return stream.getvalue().encode("utf-8")

    def import_csv(self, rows):
        return self.admin.post(
            self.prefix + "/import",
            files={"file": ("ledger.csv", self.csv(rows), "text/csv")},
        )

    def test_employee_cannot_read_or_modify_admin_operations(self):
        for path in [
            "",
            "/catalog",
            "/summary",
            "/ledger",
            "/reviews",
            "/changes",
            "/ledger-template.csv",
            "/export.csv",
        ]:
            with self.subTest(path=path):
                self.assertEqual(403, self.alice.get(self.prefix + path).status_code)
        for path, body in [
            ("/events", self.event_body),
            ("/stations", self.station_body),
            ("/materials", self.material_body),
            ("/ledger", self.record_body),
        ]:
            with self.subTest(path=path):
                self.assertEqual(
                    403, self.alice.post(self.prefix + path, json=body).status_code
                )

    def test_admin_page_and_catalog(self):
        page = self.admin.get(self.prefix)
        self.assertEqual(200, page.status_code)
        self.assertIn("每一站，都有据可查", page.text)
        catalog = self.admin.get(self.prefix + "/catalog").json()
        self.assertEqual("RACE-TEST", catalog["events"][0]["code"])
        self.assertEqual([self.employee], catalog["stations"][0]["employee_ids"])
        self.assertEqual(2, len(catalog["employees"]))

    def test_event_update_uses_version(self):
        body = self.event_body | {"version": 1, "name": "更新赛事"}
        path = self.prefix + "/events/" + str(self.event["id"])
        first = self.admin.put(path, json=body)
        self.assertEqual(200, first.status_code)
        self.assertEqual(2, first.json()["version"])
        self.assertEqual(409, self.admin.put(path, json=body).status_code)
        body |= {"version": 2, "code": "CHANGED"}
        self.assertEqual(422, self.admin.put(path, json=body).status_code)

    def test_closed_event_blocks_new_records(self):
        self.post(
            "/events/" + str(self.event["id"]) + "/state",
            {"state": "closed", "version": 1},
        )
        response = self.admin.post(self.prefix + "/ledger", json=self.record_body)
        self.assertEqual(409, response.status_code)
        self.assertEqual("EVENT_CLOSED", response.json()["code"])
        self.post(
            "/events/" + str(self.event["id"]) + "/state",
            {"state": "active", "version": 2},
        )
        self.add_record()

    def test_invalid_state_transition(self):
        response = self.admin.post(
            self.prefix + "/events/" + str(self.event["id"]) + "/state",
            json={"state": "planned", "version": 1},
        )
        self.assertEqual(409, response.status_code)

    def test_duplicate_catalog_codes_are_conflicts(self):
        for path, body in [
            ("/events", self.event_body),
            ("/stations", self.station_body),
            ("/materials", self.material_body),
        ]:
            with self.subTest(path=path):
                result = self.admin.post(self.prefix + path, json=body)
                self.assertEqual(409, result.status_code)
                self.assertEqual("DUPLICATE_OR_REFERENCE", result.json()["code"])

    def test_unknown_station_employee_rejected(self):
        body = self.station_body | {"code": "S02", "employee_ids": [999999]}
        response = self.admin.post(self.prefix + "/stations", json=body)
        self.assertEqual(422, response.status_code)
        body["employee_ids"] = [self.admin.get("/api/me").json()["id"]]
        self.assertEqual(
            422, self.admin.post(self.prefix + "/stations", json=body).status_code
        )

    def test_invalid_date_quantity_and_blank_fields(self):
        for patch in [
            {"quantity": "-1"},
            {"quantity": "NaN"},
            {"quantity": "1e3"},
            {"quantity": "1.00001"},
            {"occurred_on": "2026-02-31"},
            {"operator": "   "},
        ]:
            with self.subTest(patch=patch):
                response = self.admin.post(
                    self.prefix + "/ledger", json=self.record_body | patch
                )
                self.assertEqual(422, response.status_code)

    def test_duplicate_record_is_idempotent_but_conflict_is_blocked(self):
        first = self.add_record()
        second = self.add_record()
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["reused"])
        response = self.admin.post(
            self.prefix + "/ledger", json=self.record_body | {"quantity": "2"}
        )
        self.assertEqual(409, response.status_code)
        self.assertEqual(1, self.admin.get(self.prefix + "/ledger").json()["total"])

    def test_filter_and_pagination(self):
        self.add_record()
        self.add_record(record_code="REC-01", kind="receipt", quantity="20", unit="份")
        result = self.admin.get(self.prefix + "/ledger?kind=receipt&query=B001").json()
        self.assertEqual(1, result["total"])
        self.assertEqual("20", result["items"][0]["quantity"])
        first = self.admin.get(self.prefix + "/ledger?size=1&page=1").json()
        second = self.admin.get(self.prefix + "/ledger?size=1&page=2").json()
        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])
        self.assertEqual(
            422, self.admin.get(self.prefix + "/ledger?page=0").status_code
        )
        self.assertEqual(
            422, self.admin.get(self.prefix + "/ledger?size=9999").status_code
        )

    def test_summary_does_not_mix_units(self):
        self.add_record(quantity="0.1", unit="箱")
        self.add_record(record_code="OUT-02", quantity="0.2", unit="箱")
        self.add_record(record_code="REC-01", quantity="20", unit="份", kind="receipt")
        summary = self.admin.get(self.prefix + "/summary").json()
        totals = {r["unit"]: r for r in summary["totals"]}
        self.assertEqual("0.3", totals["箱"]["outbound"])
        self.assertEqual("20", totals["份"]["receipt"])
        self.assertEqual(2, len(totals))

    def test_void_keeps_original_and_excludes_summary(self):
        record = self.add_record()
        result = self.post(
            "/ledger/" + str(record["id"]) + "/void",
            {"version": 1, "reason": "登记单位有误"},
        )
        self.assertEqual("登记单位有误", result["void_reason"])
        self.assertEqual("1", result["quantity"])
        self.assertEqual([], self.admin.get(self.prefix + "/summary").json()["totals"])
        self.assertEqual(1, self.admin.get(self.prefix + "/ledger").json()["total"])
        retry = self.admin.post(
            self.prefix + "/ledger/" + str(record["id"]) + "/void",
            json={"version": 1, "reason": "再次尝试作废"},
        )
        self.assertEqual(409, retry.status_code)

    def test_csv_import_rolls_back_all_rows_on_conflict(self):
        self.add_record()
        response = self.import_csv(
            [
                self.record_body | {"record_code": "NEW-01"},
                self.record_body | {"quantity": "2"},
            ]
        )
        self.assertEqual(409, response.status_code)
        self.assertEqual(1, self.admin.get(self.prefix + "/ledger").json()["total"])

    def test_csv_import_retry_and_validation(self):
        first = self.import_csv([self.record_body])
        self.assertEqual(200, first.status_code)
        self.assertEqual(1, first.json()["created"])
        second = self.import_csv([self.record_body])
        self.assertEqual(1, second.json()["reused"])
        invalid = self.import_csv([self.record_body | {"quantity": "bad"}])
        self.assertEqual(422, invalid.status_code)
        self.assertIn("第2行", invalid.json()["message"])

    def test_csv_export_neutralizes_formulas(self):
        self.add_record(notes="=SUM(1,1)")
        response = self.admin.get(self.prefix + "/export.csv")
        self.assertEqual(200, response.status_code)
        rows = list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))
        self.assertEqual("'=SUM(1,1)", rows[0]["notes"])
        self.assertEqual(
            'attachment; filename="race-ledger.csv"',
            response.headers["content-disposition"],
        )

    def test_snapshot_uses_station_permissions_and_material_codes(self):
        self.add_record()
        published = self.publish()
        store = self.app.state.store
        with store.connection() as db:
            row = db.execute(
                "SELECT * FROM agent_batches WHERE id=?",
                (published["record_snapshot_id"],),
            ).fetchone()
            payload = json.loads(row["payload"])
        self.assertEqual([self.employee], payload["employee_ids"])
        self.assertEqual("water", payload["records"][0]["material_id"])
        self.assertEqual("RACE-TEST", payload["event_id"])

    def test_editing_ledger_revokes_old_snapshot(self):
        self.add_record()
        snapshot = self.publish()
        self.add_record(record_code="REC-01", kind="receipt", quantity="20", unit="份")
        with self.app.state.store.connection() as db:
            self.assertEqual(
                0,
                db.execute(
                    "SELECT active FROM agent_batches WHERE id=?",
                    (snapshot["record_snapshot_id"],),
                ).fetchone()[0],
            )

    def test_station_permission_change_revokes_old_snapshot(self):
        self.add_record()
        snapshot = self.publish()
        response = self.admin.put(
            self.prefix + "/stations/" + str(self.station["id"]),
            json=self.station_body | {"version": 1, "employee_ids": [self.other]},
        )
        self.assertEqual(200, response.status_code)
        with self.app.state.store.connection() as db:
            row = db.execute(
                "SELECT active FROM agent_batches WHERE id=?",
                (snapshot["record_snapshot_id"],),
            ).fetchone()
        self.assertEqual(0, row[0])

    def test_batch_publish_requires_manual_confirmation(self):
        self.add_record()
        response = self.admin.post(
            self.prefix + "/publish-batch",
            json={
                "station_id": self.station["id"],
                "batch_code": "B001",
                "confirmed": False,
            },
        )
        self.assertEqual(422, response.status_code)

    def test_return_records_not_misrepresented_as_receipts(self):
        self.add_record(kind="return")
        response = self.admin.post(
            self.prefix + "/publish-batch",
            json={
                "station_id": self.station["id"],
                "batch_code": "B001",
                "confirmed": True,
            },
        )
        self.assertEqual(422, response.status_code)
        self.assertEqual("EMPTY_BATCH", response.json()["code"])

    def test_material_identity_cannot_be_changed(self):
        response = self.admin.put(
            self.prefix + "/materials/" + str(self.material["id"]),
            json=self.material_body | {"version": 1, "unit": "箱"},
        )
        self.assertEqual(422, response.status_code)
        response = self.admin.put(
            self.prefix + "/materials/" + str(self.material["id"]),
            json=self.material_body | {"version": 1, "name": "饮用水新版名称"},
        )
        self.assertEqual(200, response.status_code)

    def test_changes_contain_before_and_after(self):
        record = self.add_record()
        self.post(
            "/ledger/" + str(record["id"]) + "/void",
            {"version": 1, "reason": "登记错误更正"},
        )
        changes = self.admin.get(self.prefix + "/changes").json()
        latest = changes["items"][0]
        self.assertEqual("void", latest["action"])
        self.assertIsNone(json.loads(latest["before_json"])["void_reason"])
        self.assertEqual(
            "登记错误更正", json.loads(latest["after_json"])["void_reason"]
        )

    def test_review_requires_pending_state_and_records_conclusion(self):
        # 管理台处理已提交草稿。这里预置状态，建稿门禁由 Agent 测试覆盖。
        import time

        store = self.app.state.store
        with store.connection() as db:
            db.execute(
                """INSERT INTO review_drafts
                (id,user_id,validation_id,kind,content,state,created,idempotency_key)
                VALUES(?,?,?,?,?,?,?,?)""",
                (
                    "review-test",
                    self.employee,
                    "fixture-validation",
                    "receipt_discrepancy",
                    json.dumps({"batch_id": "B001", "issues": []}),
                    "pending_review",
                    time.time(),
                    "fixture-review-test",
                ),
            )
        request = {
            "expected_state": "pending_review",
            "decision": "needs_information",
            "note": "请补充站点签收原始凭证",
        }
        path = self.prefix + "/reviews/review-test"
        self.assertEqual(403, self.alice.post(path, json=request).status_code)
        response = self.admin.post(path, json=request)
        self.assertEqual(200, response.status_code)
        self.assertEqual("needs_information", response.json()["state"])
        self.assertEqual(409, self.admin.post(path, json=request).status_code)
        rows = self.admin.get(self.prefix + "/reviews?state=needs_information").json()
        self.assertEqual(1, len(rows))
        self.assertEqual(request["note"], rows[0]["decisions"][0]["note"])
        self.assertEqual("admin", rows[0]["decisions"][0]["username"])

    def test_invalid_csv_headers_do_not_write_records(self):
        response = self.admin.post(
            self.prefix + "/import",
            files={"file": ("bad.csv", b"quantity,unit\n12,box\n", "text/csv")},
        )
        self.assertEqual(422, response.status_code)
        self.assertEqual("CSV_HEADER", response.json()["code"])
        rows = self.admin.get(self.prefix + "/ledger").json()
        self.assertEqual(0, rows["total"])

    def test_no_employee_batch_cannot_be_published(self):
        self.add_record()
        response = self.admin.put(
            self.prefix + "/stations/" + str(self.station["id"]),
            json=self.station_body | {"version": 1, "employee_ids": []},
        )
        self.assertEqual(200, response.status_code)
        response = self.admin.post(
            self.prefix + "/publish-batch",
            json={
                "station_id": self.station["id"],
                "batch_code": "B001",
                "confirmed": True,
            },
        )
        self.assertEqual(422, response.status_code)
        self.assertEqual("NO_EMPLOYEES", response.json()["code"])


if __name__ == "__main__":
    unittest.main()
