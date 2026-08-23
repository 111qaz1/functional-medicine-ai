from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.api.external_routes import router as external_router
from app.api.v2.router import router as v2_router
from app.core.bootstrap import build_container
from app.core.settings import AppSettings


V1_BASELINE_SHA256 = "5367fbef40cc2069cd2910d5b1867777f4889e176445ede0487c5c9d33b5affe"
V2_CONTRACT_SHA256 = (
    "1095385f2971878b3d89a494887ce7b474a12a"
    "68668736cf884164d280fbcc8a"
)
EXPECTED_V2_PATHS = {
    "/api/v2/cases",
    "/api/v2/cases/{case_id}",
    "/api/v2/cases/{case_id}/clinical-summary",
    "/api/v2/cases/{case_id}/attachments",
    "/api/v2/cases/{case_id}/analyses",
    "/api/v2/operations/{operation_id}",
    "/api/v2/cases/{case_id}/analyses/latest",
    "/api/v2/cases/{case_id}/analyses/{analysis_id}/reviews",
    "/api/v2/cases/{case_id}/analyses/{analysis_id}/draft-generation:retry",
    "/api/v2/drafts/{draft_id}",
    "/api/v2/drafts/{draft_id}/approval",
    "/api/v2/drafts/{draft_id}/report",
    "/api/v2/drafts/{draft_id}/report.pdf",
}


class V2OpenApiContractTests(unittest.TestCase):
    def test_v1_paths_remain_byte_stable_after_v2_is_added(self) -> None:
        app = FastAPI()
        app.include_router(external_router)
        app.include_router(v2_router)
        paths = {
            path: value
            for path, value in app.openapi()["paths"].items()
            if path.startswith("/api/v1")
        }
        payload = json.dumps(paths, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), V1_BASELINE_SHA256)

    def test_v2_exposes_exactly_the_thirteen_planned_paths(self) -> None:
        app = FastAPI()
        app.include_router(v2_router)
        spec = app.openapi()
        paths = {path for path in spec["paths"] if path.startswith("/api/v2")}
        self.assertEqual(paths, EXPECTED_V2_PATHS)
        contract = {
            "paths": {
                path: value
                for path, value in spec["paths"].items()
                if path.startswith("/api/v2")
            },
            "components": spec.get("components", {}),
        }
        payload = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), V2_CONTRACT_SHA256)

    def test_every_json_operation_documents_problem_details(self) -> None:
        app = FastAPI()
        app.include_router(v2_router)
        spec = app.openapi()
        for path in EXPECTED_V2_PATHS:
            for operation in spec["paths"][path].values():
                responses = operation["responses"]
                if path.endswith("report.pdf"):
                    self.assertIn("application/pdf", responses["200"]["content"])
                problem_responses = [
                    response
                    for status, response in responses.items()
                    if status not in {"200", "201", "202"}
                ]
                self.assertTrue(problem_responses, path)
                for response in problem_responses:
                    self.assertEqual(
                        set(response.get("content", {})),
                        {"application/problem+json"},
                    )

    def test_public_schemas_do_not_expose_internal_fields(self) -> None:
        app = FastAPI()
        app.include_router(v2_router)
        serialized = json.dumps(app.openapi(), ensure_ascii=False)
        for field in (
            "snapshot_hash",
            "model_version",
            "prompt_version",
            "storage_uri",
            "internal_audit",
        ):
            self.assertNotIn(field, serialized)

    def test_workflow_adapter_does_not_import_domain_models(self) -> None:
        workflow_source = (
            Path(__file__).resolve().parents[1] / "app" / "api" / "v2" / "workflow.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("from app.domain", workflow_source)


class V2ProblemDetailsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_patcher = patch.dict(
            os.environ,
            {"FM_EXTERNAL_TRUST_SHARED_SECRET": "test-shared-secret"},
        )
        self.env_patcher.start()
        root = Path(self.temp_dir.name)
        (root / "功能医学相关资料").mkdir(parents=True, exist_ok=True)
        settings = AppSettings(
            project_root=root,
            data_dir=Path(__file__).resolve().parents[1] / "app" / "data",
            runtime_dir=root / ".runtime",
            upload_dir=root / ".runtime" / "uploads",
            report_export_dir=root / ".runtime" / "reports",
            sqlite_path=root / ".runtime" / "test.sqlite3",
            knowledge_root=root / "功能医学相关资料",
            report_reference_path=root / "report-reference.pdf",
        )
        self.container = build_container(settings)
        self.app = FastAPI()
        self.app.state.container = self.container
        self.app.include_router(external_router)
        self.app.include_router(v2_router)
        self.client = TestClient(self.app)
        self.token = self._external_token()
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self) -> None:
        self.client.close()
        self.container.case_analysis_service.shutdown()
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def _external_token(self) -> str:
        payload = {
            "issuer": "customer-system",
            "doctor_id": "doctor-problem-test",
            "doctor_name": "Synthetic doctor",
            "timestamp": int(time.time()),
            "nonce": "nonce-problem-test-12345",
        }
        canonical = "\n".join(
            [
                payload["issuer"],
                payload["doctor_id"],
                payload["doctor_name"],
                str(payload["timestamp"]),
                payload["nonce"],
            ]
        )
        payload["signature"] = hmac.new(
            b"test-shared-secret",
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        response = self.client.post("/api/v1/auth/token", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["access_token"]

    def test_auth_and_validation_errors_use_problem_json(self) -> None:
        unauthorized = self.client.get("/api/v2/cases/missing")
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(unauthorized.headers["content-type"], "application/problem+json")
        self.assertEqual(unauthorized.json()["code"], "AUTHENTICATION_REQUIRED")

        invalid = self.client.post(
            "/api/v2/cases",
            headers=self.headers,
            json={"customer_name": "Synthetic", "unexpected": True},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.headers["content-type"], "application/problem+json")
        self.assertEqual(invalid.json()["code"], "REQUEST_VALIDATION_FAILED")

        missing = self.client.get("/api/v2/cases/missing", headers=self.headers)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["code"], "CASE_NOT_FOUND")

        with self.assertLogs("app.api.v2.problems", level="ERROR"):
            with patch(
                "app.api.v2.router.V2WorkflowAdapter.get_case",
                side_effect=RuntimeError("private"),
            ):
                failed = self.client.get("/api/v2/cases/boom", headers=self.headers)
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(failed.headers["content-type"], "application/problem+json")
        self.assertEqual(failed.json()["code"], "INTERNAL_SERVER_ERROR")
        self.assertNotIn("private", failed.text)


if __name__ == "__main__":
    unittest.main()
