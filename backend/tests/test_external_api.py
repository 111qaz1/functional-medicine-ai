from __future__ import annotations

import hashlib
import hmac
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
from app.api.routes import router
from app.core.bootstrap import build_container
from app.core.settings import AppSettings


class ExternalApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_patcher = patch.dict(os.environ, {"FM_EXTERNAL_TRUST_SHARED_SECRET": "test-shared-secret"})
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
        self.app.include_router(router)
        self.app.include_router(external_router)
        self.client = TestClient(self.app)
        self.other_client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.other_client.close()
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def _signed_trust_payload(self, doctor_id: str, doctor_name: str) -> dict:
        payload = {
            "issuer": "customer-system",
            "doctor_id": doctor_id,
            "doctor_name": doctor_name,
            "timestamp": int(time.time()),
            "nonce": f"nonce-{doctor_id}-12345",
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
        return payload

    def _external_token(self, client: TestClient, doctor_id: str, doctor_name: str) -> str:
        token = client.post(
            "/api/v1/auth/token",
            json=self._signed_trust_payload(doctor_id, doctor_name),
        )
        self.assertEqual(token.status_code, 200, token.text)
        payload = token.json()
        self.assertEqual(payload["token_type"], "bearer")
        return payload["access_token"]

    def test_external_bearer_token_isolates_owned_cases(self) -> None:
        token_a = self._external_token(self.client, "doctor-a", "甲方医生A")
        token_b = self._external_token(self.other_client, "doctor-b", "甲方医生B")

        created = self.client.post(
            "/api/v1/cases",
            headers={"Authorization": f"Bearer {token_a}"},
            json={"customer_name": "外部病例A"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        case_id = created.json()["case_id"]

        denied = self.other_client.post(
            f"/api/v1/cases/{case_id}/attachments",
            headers={"Authorization": f"Bearer {token_b}"},
            files={"files": ("labs.txt", b"WBC 5.50 10 9/L 3.5-9.5", "text/plain")},
            data={"attachment_type": "case"},
        )
        self.assertEqual(denied.status_code, 403, denied.text)

    def test_external_token_rejects_invalid_signature(self) -> None:
        payload = self._signed_trust_payload("doctor-bad", "伪造医生")
        payload["signature"] = "0" * 64

        response = self.client.post("/api/v1/auth/token", json=payload)

        self.assertEqual(response.status_code, 401, response.text)

    def test_external_recommendation_endpoint_returns_json_contract(self) -> None:
        token = self._external_token(self.client, "doctor-main", "甲方主治医生")
        created = self.client.post(
            "/api/v1/cases",
            headers={"Authorization": f"Bearer {token}"},
            json={"customer_name": "外部推荐测试"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        case_id = created.json()["case_id"]

        uploaded = self.client.post(
            f"/api/v1/cases/{case_id}/attachments",
            headers={"Authorization": f"Bearer {token}"},
            files={
                "files": (
                    "labs.txt",
                    "25-OH维生素D 18 ng/mL 30-100\n空腹血糖 6.2 mmol/L 3.9-5.6",
                    "text/plain",
                )
            },
            data={"attachment_type": "case"},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.assertEqual(uploaded.json()["results"][0]["status"], "parsed")

        generated = self.client.post(
            f"/api/v1/cases/{case_id}/nutrition-recommendations",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(generated.status_code, 200, generated.text)
        payload = generated.json()
        self.assertEqual(payload["case_id"], case_id)
        self.assertTrue(payload["draft_id"].startswith("draft_"))
        self.assertIn("manual_review_required", payload)
        self.assertIsInstance(payload["recommendations"], list)
        for item in payload["recommendations"]:
            self.assertIn("sku_id", item)
            self.assertIn("dosage", item)
            self.assertIn("warnings", item)

        report_url = self.client.get(
            f"/api/v1/drafts/{payload['draft_id']}/report-download",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(report_url.status_code, 409, report_url.text)


if __name__ == "__main__":
    unittest.main()
