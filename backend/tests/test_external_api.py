from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import unittest
from dataclasses import replace
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

    def _external_case_with_draft(
        self,
        *,
        doctor_id: str = "doctor-main",
        doctor_name: str = "甲方主治医生",
    ) -> tuple[str, str, dict]:
        token = self._external_token(self.client, doctor_id, doctor_name)
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
        self.assertTrue(payload["recommendations"])
        return token, case_id, payload

    def _pollute_draft_reasons(self, draft_id: str) -> None:
        draft = self.container.repository.get_draft(draft_id)
        self.assertIsNotNone(draft)
        unsafe_reason = (
            "命中产品标签：肝胆；关联度 95%；RAG内部审查 product:sku_demo statement_abc "
            "内部知识证据 evidence_001 D:\\medical\\secret API Key ?????；支持肝胆代谢和营养恢复"
        )
        updated_items = [
            item.model_copy(
                update={
                    "reason": unsafe_reason,
                    "warnings": [*item.warnings, "如正在用药、孕哺或肝肾功能异常，请先咨询医生"],
                }
            )
            for item in draft.recommended_skus
        ]
        self.container.repository.save_draft(draft.model_copy(update={"recommended_skus": updated_items}))

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

    def test_external_attachment_accepts_long_unicode_filename(self) -> None:
        token = self._external_token(self.client, "doctor-long-name", "外部医生")
        created = self.client.post(
            "/api/v1/cases",
            headers={"Authorization": f"Bearer {token}"},
            json={"customer_name": "外部长文件名病例"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        case_id = created.json()["case_id"]
        original_name = f"{'病例总结' * 80}.txt"

        uploaded = self.client.post(
            f"/api/v1/cases/{case_id}/attachments",
            headers={"Authorization": f"Bearer {token}"},
            files={"files": (original_name, "合成临床总结", "text/plain")},
            data={"attachment_type": "case"},
        )

        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.assertEqual(uploaded.json()["results"][0]["filename"], original_name)
        stored_file = self.container.case_service.get_case(case_id).files[0]
        self.assertEqual(stored_file.filename, original_name)
        stored_path = Path(stored_file.storage_uri)
        self.assertTrue(stored_path.exists())
        self.assertRegex(stored_path.name, r"^[0-9a-f]{32}\.txt$")
        self.assertNotIn("病例总结", stored_path.name)

    def test_external_attachment_storage_failure_returns_safe_json(self) -> None:
        token = self._external_token(self.client, "doctor-storage-failure", "外部医生")
        created = self.client.post(
            "/api/v1/cases",
            headers={"Authorization": f"Bearer {token}"},
            json={"customer_name": "外部存储失败病例"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        case_id = created.json()["case_id"]

        with patch.object(
            self.container.recommendation_service.object_store,
            "save",
            side_effect=OSError("sensitive server path"),
        ):
            uploaded = self.client.post(
                f"/api/v1/cases/{case_id}/attachments",
                headers={"Authorization": f"Bearer {token}"},
                files={"files": ("summary.txt", "合成临床总结", "text/plain")},
                data={"attachment_type": "case"},
            )

        self.assertEqual(uploaded.status_code, 500, uploaded.text)
        self.assertEqual(
            uploaded.json(),
            {"detail": "文件保存失败，请检查服务器存储空间或目录权限。"},
        )
        self.assertEqual(self.container.case_service.get_case(case_id).files, [])

    def test_external_token_rejects_invalid_signature(self) -> None:
        payload = self._signed_trust_payload("doctor-bad", "伪造医生")
        payload["signature"] = "0" * 64

        response = self.client.post("/api/v1/auth/token", json=payload)

        self.assertEqual(response.status_code, 401, response.text)

    def test_external_recommendation_endpoint_returns_json_contract(self) -> None:
        token, case_id, payload = self._external_case_with_draft()

        self.assertEqual(payload["case_id"], case_id)
        self.assertIn("manual_review_required", payload)
        self.assertIsInstance(payload["recommendations"], list)
        for item in payload["recommendations"]:
            self.assertIn("sku_id", item)
            self.assertIn("dosage", item)
            self.assertIn("warnings", item)
            self.assertNotIn("course_period", item)
            self.assertNotIn("medical_advice", item)
            self.assertNotIn("advice_source", item)

        self._pollute_draft_reasons(payload["draft_id"])
        prescription = self.client.get(
            f"/api/v1/drafts/{payload['draft_id']}/prescription-items",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(prescription.status_code, 200, prescription.text)
        prescription_payload = prescription.json()
        self.assertEqual(prescription_payload["case_id"], case_id)
        self.assertEqual(prescription_payload["draft_id"], payload["draft_id"])
        self.assertNotIn("manual_review_required", prescription_payload)
        self.assertNotIn("course_period_default", prescription_payload)
        self.assertNotIn("items", prescription_payload)
        self.assertNotIn("dosage", prescription_payload)
        self.assertEqual(prescription_payload["advice_source"], "local_fallback")
        self.assertIsInstance(prescription_payload["medical_advice"], str)
        self.assertGreaterEqual(len(prescription_payload["medical_advice"]), 50)
        self.assertLessEqual(len(prescription_payload["medical_advice"]), 100)
        self.assertTrue(
            any(term in prescription_payload["medical_advice"] for term in ("处方级营养素", "身体当下所需营养"))
        )
        self.assertTrue(
            any(
                term in prescription_payload["medical_advice"]
                for term in ("肝脏解毒代谢支持", "免疫调节支持", "抗炎", "抗氧化", "代谢调节")
            )
        )
        serialized = json.dumps(prescription_payload, ensure_ascii=False)
        for forbidden in (
            "关联度",
            "命中产品标签",
            "RAG内部审查",
            "product:",
            "statement_",
            "内部知识证据",
            "evidence",
            "?????",
            "API Key",
            "治疗",
            "治愈",
            "疗效",
        ):
            self.assertNotIn(forbidden, serialized)

        denied_prescription = self.other_client.get(
            f"/api/v1/drafts/{payload['draft_id']}/prescription-items",
            headers={"Authorization": f"Bearer {self._external_token(self.other_client, 'doctor-other', 'Other Doctor')}"},
        )
        self.assertEqual(denied_prescription.status_code, 403, denied_prescription.text)

        report_url = self.client.get(
            f"/api/v1/drafts/{payload['draft_id']}/report-download",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(report_url.status_code, 409, report_url.text)

    def test_external_prescription_items_uses_llm_json_when_available(self) -> None:
        token, _, payload = self._external_case_with_draft(doctor_id="doctor-llm", doctor_name="LLM 医生")
        self.container.settings = replace(
            self.container.settings,
            llm_base_url="http://mock-llm",
            llm_api_key="mock-key",
            llm_model="mock-model",
            llm_api_style="chat",
        )
        remote_advice = "处方级营养素用于补充身体当下所需营养，予以血糖代谢支持、免疫调节支持等方向的营养支持，帮助平衡免疫、抗炎、抗氧化及代谢调节。"

        class DummyResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps({"medical_advice": remote_advice}, ensure_ascii=False),
                            }
                        }
                    ]
                }

        with patch("app.services.prescription_advice.httpx.Client") as client_factory:
            client_factory.return_value.__enter__.return_value.post.return_value = DummyResponse()
            response = self.client.get(
                f"/api/v1/drafts/{payload['draft_id']}/prescription-items",
                headers={"Authorization": f"Bearer {token}"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["advice_source"], "llm")
        self.assertEqual(body["medical_advice"], remote_advice)
        self.assertNotIn("items", body)

    def test_external_prescription_items_falls_back_when_llm_output_is_unsafe(self) -> None:
        token, _, payload = self._external_case_with_draft(doctor_id="doctor-unsafe-llm", doctor_name="LLM 医生")
        self.container.settings = replace(
            self.container.settings,
            llm_base_url="http://mock-llm",
            llm_api_key="mock-key",
            llm_model="mock-model",
            llm_api_style="chat",
        )
        remote_advice = "RAG内部审查显示 product:sku 命中产品标签，关联度 95%，建议对症治疗。"

        class DummyResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps({"medical_advice": remote_advice}, ensure_ascii=False),
                            }
                        }
                    ]
                }

        with patch("app.services.prescription_advice.httpx.Client") as client_factory:
            client_factory.return_value.__enter__.return_value.post.return_value = DummyResponse()
            response = self.client.get(
                f"/api/v1/drafts/{payload['draft_id']}/prescription-items",
                headers={"Authorization": f"Bearer {token}"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["advice_source"], "local_fallback")
        serialized = json.dumps(body, ensure_ascii=False)
        for forbidden in ("关联度", "命中产品标签", "RAG内部审查", "product:", "治疗", "治愈", "疗效"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
