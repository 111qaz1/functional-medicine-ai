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
from app.api.v2.router import router as v2_router
from app.core.bootstrap import build_container
from app.core.settings import AppSettings
from app.domain.models import (
    AbnormalFinding,
    AnalysisStatus,
    CaseAnalysis,
    DosageOptionSummary,
    DraftRecommendationItem,
    FinalGenerationStatus,
    Questionnaire,
    RecommendationDraft,
    ReviewDecision,
)


class V2WorkflowApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_patcher = patch.dict(
            os.environ,
            {"FM_EXTERNAL_TRUST_SHARED_SECRET": "test-shared-secret"},
        )
        self.env_patcher.start()
        self.root = Path(self.temp_dir.name)
        (self.root / "功能医学相关资料").mkdir(parents=True, exist_ok=True)
        settings = AppSettings(
            project_root=self.root,
            data_dir=Path(__file__).resolve().parents[1] / "app" / "data",
            runtime_dir=self.root / ".runtime",
            upload_dir=self.root / ".runtime" / "uploads",
            report_export_dir=self.root / ".runtime" / "reports",
            sqlite_path=self.root / ".runtime" / "test.sqlite3",
            knowledge_root=self.root / "功能医学相关资料",
            report_reference_path=self.root / "report-reference.pdf",
        )
        self.container = build_container(settings)
        self.app = FastAPI()
        self.app.state.container = self.container
        self.app.include_router(external_router)
        self.app.include_router(v2_router)
        self.client = TestClient(self.app)
        self.token = self._external_token("doctor-v2", "Synthetic doctor")
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self) -> None:
        self.client.close()
        self.container.case_analysis_service.shutdown()
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def _signed_trust_payload(doctor_id: str, doctor_name: str) -> dict:
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

    def _external_token(self, doctor_id: str, doctor_name: str) -> str:
        response = self.client.post(
            "/api/v1/auth/token",
            json=self._signed_trust_payload(doctor_id, doctor_name),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["access_token"]

    def _create_case(self) -> str:
        response = self.client.post(
            "/api/v2/cases",
            headers=self.headers,
            json={
                "customer_name": "Synthetic API case",
                "notes": "Synthetic test data only",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    @staticmethod
    def _analysis(case_id: str) -> CaseAnalysis:
        return CaseAnalysis(
            id="analysis-v2",
            case_id=case_id,
            status=AnalysisStatus.ready_for_review,
            snapshot_hash="internal-snapshot",
            model_version="internal-model",
            prompt_version="internal-prompt",
            progress_current=1,
            progress_total=1,
            case_summary="Synthetic summary",
            abnormal_findings=[
                AbnormalFinding(
                    id="finding-v2",
                    name="Synthetic marker",
                    source_file_id="file-v2",
                    source_file_name="synthetic.txt",
                    source_page=1,
                    source_text="Synthetic evidence",
                    marker_code="INTERNAL-MARKER",
                )
            ],
        )

    @staticmethod
    def _draft(case_id: str) -> RecommendationDraft:
        return RecommendationDraft(
            id="draft-v2",
            case_id=case_id,
            model_version="internal-model",
            prompt_version="internal-prompt",
            rule_version="internal-rules",
            case_summary=["Synthetic public summary"],
            recommended_skus=[
                DraftRecommendationItem(
                    sku_id="SKU-1",
                    display_name="Synthetic product one",
                    dosage="one daily",
                    dosage_option_id="default",
                    dosage_options=[
                        DosageOptionSummary(
                            option_id="default",
                            label="Default",
                            display_text="one daily",
                        ),
                        DosageOptionSummary(
                            option_id="alternate",
                            label="Alternate",
                            display_text="two daily",
                            requires_review=True,
                        ),
                    ],
                    reason="Synthetic reason",
                ),
                DraftRecommendationItem(
                    sku_id="SKU-2",
                    display_name="Synthetic product two",
                    dosage="one daily",
                    dosage_option_id="default",
                    dosage_options=[
                        DosageOptionSummary(
                            option_id="default",
                            label="Default",
                            display_text="one daily",
                        )
                    ],
                    reason="Synthetic reason",
                ),
            ],
        )

    def test_case_intake_partial_upload_questionnaire_and_ownership(self) -> None:
        invalid = self.client.post(
            "/api/v2/cases",
            headers=self.headers,
            json={"customer_name": "Synthetic", "owner_doctor_id": "forbidden"},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.headers["content-type"], "application/problem+json")
        self.assertEqual(invalid.json()["code"], "REQUEST_VALIDATION_FAILED")

        case_id = self._create_case()
        updated = self.client.put(
            f"/api/v2/cases/{case_id}/clinical-summary",
            headers=self.headers,
            json={"clinical_summary": "  Synthetic clinical summary  "},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["clinical_summary"], "Synthetic clinical summary")

        uploaded = self.client.post(
            f"/api/v2/cases/{case_id}/attachments",
            headers=self.headers,
            files=[
                ("files", ("labs.txt", b"Synthetic marker 12 U/L 1-10", "text/plain")),
                ("files", ("unsafe.exe", b"not allowed", "application/octet-stream")),
            ],
            data={"attachment_type": "medical_record"},
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        self.assertEqual(uploaded.json()["meta"]["accepted_count"], 1)
        self.assertEqual(uploaded.json()["meta"]["failed_count"], 1)
        self.assertEqual([item["status"] for item in uploaded.json()["items"]], ["parsed", "failed"])

        with patch.object(
            self.container.questionnaire_import_service,
            "parse",
            return_value=Questionnaire(chief_concerns=["Synthetic concern"]),
        ):
            questionnaire = self.client.post(
                f"/api/v2/cases/{case_id}/attachments",
                headers=self.headers,
                files={"files": ("questionnaire.txt", b"Synthetic questionnaire", "text/plain")},
                data={"attachment_type": "questionnaire"},
            )
        self.assertEqual(questionnaire.status_code, 201, questionnaire.text)
        self.assertEqual(questionnaire.json()["items"][0]["status"], "questionnaire_imported")

        other_token = self._external_token("doctor-other", "Other synthetic doctor")
        denied = self.client.get(
            f"/api/v2/cases/{case_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["code"], "CASE_ACCESS_DENIED")

    def test_analysis_operation_review_and_retry_contract(self) -> None:
        case_id = self._create_case()
        analysis = self._analysis(case_id)
        self.container.repository.save_case_analysis(analysis)

        with patch.object(
            self.container.case_analysis_service,
            "create_analysis",
            return_value=analysis,
        ) as create_analysis:
            started = self.client.post(
                f"/api/v2/cases/{case_id}/analyses",
                headers=self.headers,
                json={"third_party_processing_confirmed": True},
            )
        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(started.headers["location"], "/api/v2/operations/analysis-v2")
        create_analysis.assert_called_once_with(
            case_id,
            third_party_processing_confirmed=True,
        )

        polled = self.client.get("/api/v2/operations/analysis-v2", headers=self.headers)
        self.assertEqual(polled.status_code, 200, polled.text)
        self.assertEqual(polled.json()["status"], "succeeded")

        latest = self.client.get(
            f"/api/v2/cases/{case_id}/analyses/latest",
            headers=self.headers,
        )
        self.assertEqual(latest.status_code, 200, latest.text)
        self.assertNotIn("snapshot_hash", latest.text)
        self.assertNotIn("model_version", latest.text)

        queued = analysis.model_copy(
            update={
                "status": AnalysisStatus.reviewed,
                "final_generation_status": FinalGenerationStatus.queued,
                "final_generation_progress": 0,
            }
        )
        with patch.object(
            self.container.case_analysis_service,
            "review_and_generate",
            return_value=(queued, None, None),
        ) as review:
            submitted = self.client.post(
                f"/api/v2/cases/{case_id}/analyses/analysis-v2/reviews",
                headers=self.headers,
                json={
                    "reviewer_id": "doctor-v2",
                    "expected_revision": 1,
                    "finding_changes": [
                        {
                            "op": "update",
                            "id": "finding-v2",
                            "changes": {"name": "Doctor confirmed marker"},
                        }
                    ],
                },
            )
        self.assertEqual(submitted.status_code, 202, submitted.text)
        self.assertEqual(submitted.json()["stage"], "draft_generation")
        sent_finding = review.call_args.kwargs["abnormal_findings"][0]
        self.assertEqual(sent_finding.name, "Doctor confirmed marker")
        self.assertEqual(sent_finding.marker_code, "INTERNAL-MARKER")

        conflict = self.client.post(
            f"/api/v2/cases/{case_id}/analyses/analysis-v2/reviews",
            headers=self.headers,
            json={"reviewer_id": "doctor-v2", "expected_revision": 2},
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["code"], "ANALYSIS_REVISION_CONFLICT")

        with patch.object(
            self.container.case_analysis_service,
            "retry_draft_generation",
            return_value=queued,
        ):
            retried = self.client.post(
                f"/api/v2/cases/{case_id}/analyses/analysis-v2/draft-generation:retry",
                headers=self.headers,
            )
        self.assertEqual(retried.status_code, 202, retried.text)
        self.assertEqual(retried.headers["location"], "/api/v2/operations/analysis-v2")

    def test_draft_approval_report_and_pdf_contract(self) -> None:
        case_id = self._create_case()
        draft = self._draft(case_id)
        self.container.repository.save_draft(draft)

        fetched = self.client.get("/api/v2/drafts/draft-v2", headers=self.headers)
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertNotIn("internal_audit", fetched.text)
        self.assertNotIn("model_version", fetched.text)
        self.assertEqual(len(fetched.json()["recommended_skus"]), 2)

        invalid = self.client.post(
            "/api/v2/drafts/draft-v2/approval",
            headers=self.headers,
            json={
                "reviewer_id": "doctor-v2",
                "excluded_sku_ids": ["SKU-1"],
                "dosage_overrides": [{"sku_id": "SKU-1", "option_id": "default"}],
            },
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.headers["content-type"], "application/problem+json")

        pdf_path = self.root / ".runtime" / "reports" / "synthetic-report.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(b"%PDF-1.4\nsynthetic\n%%EOF")
        review = ReviewDecision(
            draft_id=draft.id,
            reviewer_id="doctor-v2",
            edits={
                "excluded_sku_ids": ["SKU-2"],
                "dosage_overrides": {
                    "SKU-1": {"option_id": "alternate", "note": "Synthetic doctor decision"}
                },
            },
            final_status="approved",
            publishable_report="Synthetic publishable report",
            pdf_report_path=str(pdf_path),
            pdf_report_filename=pdf_path.name,
            audit_log_id="audit-v2",
        )

        def save_review(*args, **kwargs):
            self.container.repository.save_review_decision(review)
            return review

        with patch.object(self.container.review_service, "approve", side_effect=save_review) as approve:
            approved = self.client.post(
                "/api/v2/drafts/draft-v2/approval",
                headers=self.headers,
                json={
                    "reviewer_id": "doctor-v2",
                    "publishable_summary": "Synthetic publishable report",
                    "excluded_sku_ids": ["SKU-2"],
                    "dosage_overrides": [
                        {
                            "sku_id": "SKU-1",
                            "option_id": "alternate",
                            "note": "Synthetic doctor decision",
                        }
                    ],
                },
            )
        self.assertEqual(approved.status_code, 200, approved.text)
        internal_edits = approve.call_args.kwargs["edits"]
        self.assertIsInstance(internal_edits["dosage_overrides"], dict)
        self.assertEqual(approved.json()["report_url"], "/api/v2/drafts/draft-v2/report.pdf")

        report = self.client.get("/api/v2/drafts/draft-v2/report", headers=self.headers)
        self.assertEqual(report.status_code, 200, report.text)
        self.assertEqual(report.json()["filename"], "synthetic-report.pdf")

        with patch.object(
            self.container.review_service,
            "ensure_pdf",
            return_value=(pdf_path, pdf_path.name),
        ):
            downloaded = self.client.get(
                "/api/v2/drafts/draft-v2/report.pdf",
                headers=self.headers,
            )
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertTrue(downloaded.headers["content-type"].startswith("application/pdf"))
        self.assertTrue(downloaded.content.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
