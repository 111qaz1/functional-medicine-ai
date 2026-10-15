from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

sys.path.append(str(Path(__file__).resolve().parents[1]))
from backend.tests import test_joolun_embed as legacy_tests
from app.api.v2.router import router
from app.api.v2.problems import V2ApiError
from app.api.v2.schemas import JoolunTokenSessionRequest
from app.domain.models import CaseAnalysis, AnalysisStatus, RecommendationDraft, ReviewDecision, DraftStatus
from app.services.joolun_java_auth import JoolunJavaAuth, utc_now
from backend.tests import test_api_v2_workflow as workflow_tests


class JavaLoginTests(unittest.TestCase):
    def setUp(self):
        self.fixture = legacy_tests.JoolunEmbedTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.container = self.fixture.container
        self.container.settings = replace(self.container.settings, joolun_auth_mode="java_token", joolun_embed_shared_secret=None, joolun_java_api_base_url="https://java.example/prod-api", joolun_issuer="joolun")
        self.service = self.container.joolun_java_auth = JoolunJavaAuth(self.container)
        self.fixture.app.include_router(router)
        self.client = self.fixture.client
        self.calls = []
        self.info = {"code": 200, "roles": ["doctor"], "user": {"status": "0", "delFlag": "0"}}
        self.home = {"code": 200, "data": {"doctorInfo": {"id": 2099664879912943617, "name": "Synthetic doctor"}}}
        self.service.transport = httpx.MockTransport(self.upstream)
        self.payload = JoolunTokenSessionRequest(external_patient_id="2099664879912943618", external_encounter_id="encounter", patient_name="Synthetic", parent_origin="http://localhost:7600")

    def upstream(self, request):
        self.calls.append(request)
        return httpx.Response(200, json=self.info if request.url.path.endswith("getInfo") else self.home)

    def create(self):
        return self.service.create(self.payload, "synthetic-java-token")

    def expire_cache(self):
        for value in self.service._cache.values():
            value["until"] = utc_now() - timedelta(seconds=1)

    def api_headers(self, result):
        return {"Authorization": "Bearer " + result["access_token"], "Cookie": "Admin-Token=synthetic-java-token"}

    def test_trusted_identity_long_ids_and_no_persisted_credential(self):
        result = self.create()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(result["doctor"]["role"], "doctor")
        binding = self.container.repository.get_joolun_login_binding(result["access_token"])
        self.assertEqual(binding["external_doctor_id"], "2099664879912943617")
        self.assertNotIn("synthetic-java-token", json.dumps(binding))
        self.assertNotIn(b"synthetic-java-token", self.container.settings.sqlite_path.read_bytes())
        self.assertTrue(all(str(call.url).startswith("https://java.example/prod-api/") for call in self.calls))

    def test_business_rejections_role_disabled_and_missing_identity(self):
        cases = [({"code": 401}, self.home), ({**self.info, "roles": []}, self.home),
                 ({**self.info, "user": {"status": "1"}}, self.home), (self.info, {"code": 200, "data": {}})]
        for info, home in cases:
            with self.subTest(info=info, home=home):
                self.info, self.home = info, home
                with self.assertRaises(V2ApiError) as error: self.service.verify("token", force=True)
                self.assertEqual(error.exception.status, 401)

    def test_malformed_http_errors_and_redirects_fail_closed(self):
        for response in [httpx.Response(302,headers={"Location":"https://evil.example"}), httpx.Response(500), httpx.Response(200,text="bad"), httpx.Response(200,json={"code":"200"})]:
            with self.subTest(status=response.status_code):
                self.service.transport = httpx.MockTransport(lambda _:response)
                with self.assertRaises(V2ApiError) as error: self.service.verify("token", force=True)
                self.assertEqual(error.exception.status, 503)

    def test_timeout_is_redacted(self):
        def fail(request): raise httpx.ReadTimeout("sensitive-token", request=request)
        self.service.transport=httpx.MockTransport(fail)
        with self.assertRaises(V2ApiError) as error: self.service.verify("token")
        self.assertEqual(error.exception.status,503)
        self.assertNotIn("sensitive-token", str(error.exception))

    def test_fixed_twenty_minute_window_not_renewed_by_requests(self):
        self.service.verify("token")
        first = self.service._cache[self.service.digest("token")]["until"]
        for _ in range(5): self.service.verify("token")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(first,self.service._cache[self.service.digest("token")]["until"])
        self.expire_cache(); self.service.verify("token")
        self.assertEqual(len(self.calls),4)

    def test_concurrent_verification_coalesces(self):
        original=self.upstream
        def slow(request): time.sleep(.025); return original(request)
        self.service.transport=httpx.MockTransport(slow)
        with ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(lambda _:self.service.verify("token", force=True),range(5)))
        self.assertEqual(len(self.calls),2)

    def test_protection_survives_restart_and_expired_java_until_complete(self):
        result=self.create(); session=result["access_token"]; case=result["case_id"]
        self.service.protect(session,case); self.expire_cache(); self.info={"code":401}
        restarted=JoolunJavaAuth(self.container); restarted.transport=self.service.transport
        self.assertIsNotNone(restarted.authorize(session,"synthetic-java-token",case))
        restarted.complete(session)
        with self.assertRaises(V2ApiError): restarted.authorize(session,"synthetic-java-token",case)

    def test_other_cases_and_general_apis_cannot_use_protection(self):
        result=self.create(); session=result["access_token"]
        self.service.protect(session,result["case_id"]); self.expire_cache(); self.info={"code":401}
        for case in (None,"other-case"):
            with self.assertRaises(V2ApiError): self.service.authorize(session,"synthetic-java-token",case)

    def test_missing_cookie_changed_token_and_disabled_doctor(self):
        result=self.create(); session=result["access_token"]; case=result["case_id"]
        self.service.protect(session,case)
        with self.assertRaises(V2ApiError): self.service.authorize(session,None,case)
        self.info={"code":401}
        with self.assertRaises(V2ApiError): self.service.authorize(session,"changed",case)
        self.assertFalse(self.container.repository.get_joolun_login_binding(session)["protected"])
        doctor=self.container.auth_service.get_doctor_for_session(session)
        self.container.auth_service.update_doctor(doctor.id,enabled=False)
        with self.assertRaises(V2ApiError): self.service.authorize(session,"synthetic-java-token",case)

    def test_eight_hour_deadline_is_not_extended_by_protection(self):
        result=self.create(); session=result["access_token"]
        self.service.protect(session,result["case_id"])
        record=self.container.repository.get_session(session)
        record.expires_at=utc_now()-timedelta(seconds=1)
        self.container.repository.save_session(record)
        with self.assertRaises(V2ApiError): self.service.authorize(session,"synthetic-java-token",result["case_id"])

    def test_account_switch_with_old_cookie_restores_new_doctors_case(self):
        first = self.create()
        self.service.protect(first["access_token"], first["case_id"])
        self.home["data"]["doctorInfo"] = {"id": "second-doctor", "name": "Second doctor"}
        existing = self.service.create(self.payload, "second-java-token")

        response = self.client.post(
            "/api/v2/integrations/joolun/token-sessions",
            json=self.payload.model_dump(),
            headers={"X-FM-Session-BFF": "1", "Authorization": "Bearer second-java-token",
                     "Cookie": "fm_session=" + first["access_token"]},
        )
        self.assertEqual(response.status_code, 201, response.text)
        switched = response.json()
        self.assertEqual(switched["doctor"]["id"], existing["doctor"]["id"])
        self.assertEqual(switched["case_id"], existing["case_id"])
        self.assertNotEqual(switched["case_id"], first["case_id"])
        self.assertIsNone(self.container.auth_service.get_doctor_for_session(first["access_token"]))
        binding = self.container.repository.get_joolun_login_binding(switched["access_token"])
        self.assertFalse(binding["protected"])
        self.assertEqual(binding["token_hash"], self.service.digest("second-java-token"))
        headers = {"Authorization": "Bearer " + switched["access_token"],
                   "Cookie": "Admin-Token=second-java-token"}
        self.assertEqual(self.client.get("/api/v2/cases/" + switched["case_id"], headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/v2/cases/" + first["case_id"], headers=headers).status_code, 403)

    def test_new_token_for_same_doctor_restores_case_without_old_protection(self):
        first = self.create()
        self.service.protect(first["access_token"], first["case_id"])
        calls = len(self.calls)
        restored = self.service.create(self.payload, "renewed-java-token", first["access_token"])
        self.assertEqual(restored["case_id"], first["case_id"])
        self.assertEqual(restored["doctor"]["id"], first["doctor"]["id"])
        self.assertNotEqual(restored["access_token"], first["access_token"])
        self.assertEqual(len(self.calls), calls + 2)
        self.assertFalse(self.container.repository.get_joolun_login_binding(restored["access_token"])["protected"])
        self.assertIsNone(self.container.auth_service.get_doctor_for_session(first["access_token"]))

    def test_invalid_switched_token_cannot_use_previous_protection(self):
        first = self.create()
        self.service.protect(first["access_token"], first["case_id"])
        self.info = {"code": 401}
        with patch.object(self.container.auth_service, "issue_session_for_doctor") as issue:
            with self.assertRaises(V2ApiError) as error:
                self.service.create(self.payload, "invalid-new-token", first["access_token"])
        self.assertEqual(error.exception.status, 401)
        issue.assert_not_called()

    def test_switch_to_disabled_local_doctor_is_rejected(self):
        first = self.create()
        disabled = self.container.auth_service.resolve_external_doctor(
            issuer="joolun", external_doctor_id="disabled-doctor", display_name="Disabled doctor")
        self.container.auth_service.update_doctor(disabled.id, enabled=False)
        self.home["data"]["doctorInfo"] = {"id": "disabled-doctor", "name": "Disabled doctor"}
        with self.assertRaises(V2ApiError) as error:
            self.service.create(self.payload, "disabled-doctor-token", first["access_token"])
        self.assertEqual(error.exception.code, "EMBED_DOCTOR_DISABLED")

    def test_refresh_reuses_protected_session_and_switch_revokes_it(self):
        result=self.create(); session=result["access_token"]
        self.service.protect(session,result["case_id"]); self.expire_cache(); self.info={"code":401}
        resumed=self.service.create(self.payload,"synthetic-java-token",session)
        self.assertEqual(resumed["access_token"],session)
        self.info={"code":200,"roles":["doctor"],"user":{}}
        other=self.payload.model_copy(update={"external_encounter_id":"other", "external_patient_id":"other-patient"})
        self.service.create(other,"synthetic-java-token",session)
        self.assertIsNone(self.container.auth_service.get_doctor_for_session(session))

    def test_recovered_login_keeps_case_and_maps_same_doctor(self):
        result=self.create(); self.container.auth_service.logout(result["access_token"])
        recovered=self.service.create(self.payload,"new-token",result["access_token"])
        self.assertEqual(recovered["case_id"],result["case_id"])

    def test_legacy_endpoints_disabled_and_old_sessions_rejected(self):
        signed=self.fixture.signed_payload(nonce="synthetic-legacy-nonce")
        self.assertEqual(self.client.post("/api/v2/integrations/joolun/embed-sessions",json=signed).status_code,410)
        self.assertEqual(self.client.post("/api/v2/integrations/joolun/embed-tickets:exchange",json={"ticket":"x"*40}).status_code,410)
        doctor=self.container.auth_service.resolve_external_doctor(issuer="joolun",external_doctor_id="old")
        session=self.container.auth_service.issue_session_for_doctor(doctor,lifetime=timedelta(hours=8)).session
        self.assertEqual(self.client.get("/api/v2/cases",headers={"Authorization":"Bearer "+session.id,"Cookie":"Admin-Token=token"}).status_code,401)

    def test_api_integration_and_server_owned_protection(self):
        response=self.client.post("/api/v2/integrations/joolun/token-sessions",json=self.payload.model_dump(),headers={"Authorization":"Bearer synthetic-java-token", "X-FM-Session-BFF":"1"})
        self.assertEqual(response.status_code,201,response.text)
        result=response.json(); headers=self.api_headers(result)
        self.assertEqual(self.client.get("/api/v2/cases/"+result["case_id"],headers=headers).status_code,200)
        # The API starts protection only after actual analysis acceptance.
        with patch("app.api.v2.router.V2WorkflowAdapter.start_analysis",return_value=SimpleNamespace(status="queued",operation_id="operation")):
            # Call the route function directly to avoid replacing the response schema with a fake operation.
            from app.api.v2.router import start_analysis
            request=SimpleNamespace(app=self.fixture.app,state=SimpleNamespace(ai_session_id=result["access_token"]))
            start_analysis(result["case_id"],None,request,SimpleNamespace(headers={}),None)
        self.assertTrue(self.container.repository.get_joolun_login_binding(result["access_token"])["protected"])

    def test_pdf_completion_releases_protection_only_after_successful_send(self):
        result=self.create(); session=result["access_token"]
        self.service.protect(session,result["case_id"]); self.expire_cache(); self.info={"code":401}
        pdf=Path(self.fixture.temp_dir.name)/"synthetic.pdf"; pdf.write_bytes(b"%PDF-1.4\nsynthetic")
        draft=SimpleNamespace(case_id=result["case_id"])
        with patch.object(self.container.repository,"get_draft",return_value=draft), patch.object(self.container.repository,"get_review_decision",return_value=SimpleNamespace(final_status="approved")), patch("app.api.v2.router.V2WorkflowAdapter.ensure_pdf",return_value=(pdf,"synthetic.pdf")):
            response=self.client.get("/api/v2/drafts/draft/report.pdf",headers=self.api_headers(result))
            self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(self.container.repository.get_joolun_login_binding(session)["protected"])
        self.assertEqual(self.client.get("/api/v2/cases/"+result["case_id"],headers=self.api_headers(result)).status_code,401)

    def test_failed_or_unapproved_pdf_does_not_end_protection(self):
        result=self.create(); session=result["access_token"]
        self.service.protect(session,result["case_id"])
        self.service.complete(session,"unapproved-draft")
        self.assertTrue(self.container.repository.get_joolun_login_binding(session)["protected"])
        with patch.object(self.container.repository,"get_draft",return_value=SimpleNamespace(case_id=result["case_id"])), patch("app.api.v2.router.V2WorkflowAdapter.ensure_pdf",side_effect=V2ApiError(status=409,code="REPORT_NOT_READY",title="Not ready",detail="Not ready")):
            response=self.client.get("/api/v2/drafts/draft/report.pdf",headers=self.api_headers(result))
            self.assertEqual(response.status_code,409)
        self.assertTrue(self.container.repository.get_joolun_login_binding(session)["protected"])

    def test_token_session_bearer_is_not_available_to_direct_api_callers(self):
        result=self.create()
        response=self.client.get("/api/v2/integrations/joolun/token-sessions",headers=self.api_headers(result))
        self.assertEqual(response.status_code,404)
        self.assertNotIn(result["access_token"],response.text)

    def test_full_review_approval_sync_and_pdf_continue_after_window(self):
        result=self.create(); session=result["access_token"]; case=result["case_id"]
        headers=self.api_headers(result)
        analysis=workflow_tests.V2WorkflowApiTests._analysis(case)
        self.container.repository.save_case_analysis(analysis)
        draft=workflow_tests.V2WorkflowApiTests._draft(case)
        self.container.repository.save_draft(draft)
        case_record=self.container.case_service.get_case(case)
        case_record.draft_ids.append(draft.id)
        self.container.repository.save_case(case_record)
        # Real route acceptance creates the grant; only the expensive analysis is mocked.
        queued=analysis.model_copy(update={"status":AnalysisStatus.queued})
        with patch.object(self.container.case_analysis_service,"create_analysis",return_value=queued):
            response=self.client.post(f"/api/v2/cases/{case}/analyses",json={"third_party_processing_confirmed":True},headers=headers)
        self.assertEqual(response.status_code,202,response.text)
        self.expire_cache(); self.info={"code":401}; calls=len(self.calls)
        for path in (f"/api/v2/operations/{analysis.id}",f"/api/v2/cases/{case}/analyses/latest",f"/api/v2/drafts/{draft.id}"):
            self.assertEqual(self.client.get(path,headers=headers).status_code,200,path)
        reviewed=analysis.model_copy(update={"revision":analysis.revision+1})
        with patch.object(self.container.case_analysis_service,"review_and_generate",return_value=(reviewed,None,None)):
            response=self.client.post(f"/api/v2/cases/{case}/analyses/{analysis.id}/reviews",json={"expected_revision":analysis.revision},headers=headers)
        self.assertEqual(response.status_code,202,response.text)
        pdf=Path(self.fixture.temp_dir.name)/"full-report.pdf"; pdf.write_bytes(b"%PDF-1.4\nsynthetic")
        review=ReviewDecision(draft_id=draft.id,reviewer_id=result["doctor"]["id"],edits={},final_status="approved",publishable_report="Synthetic",pdf_report_path=str(pdf),pdf_report_filename=pdf.name,audit_log_id="synthetic-audit")
        def approve(*_args, **_kwargs):
            self.container.repository.save_review_decision(review)
            return review
        with patch.object(self.container.review_service,"approve",side_effect=approve):
            response=self.client.post(f"/api/v2/drafts/{draft.id}/approval",json={"expected_revision":draft.revision},headers=headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.get("/api/v2/integrations/joolun/approved-recommendations",headers=headers).status_code,200)
        with patch.object(self.container.review_service,"ensure_pdf",return_value=(pdf,pdf.name)):
            response=self.client.get(f"/api/v2/drafts/{draft.id}/report.pdf",headers=headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(self.calls),calls)
        self.assertEqual(self.client.get(f"/api/v2/cases/{case}",headers=headers).status_code,401)

    def test_internal_native_admin_is_preserved_but_public_native_session_rejected(self):
        doctor=self.container.auth_service.register(username="admin",password="test-password")
        session=self.container.auth_service.issue_session_for_doctor(doctor,lifetime=timedelta(hours=8)).session
        headers={"Authorization":"Bearer "+session.id}
        self.assertEqual(self.client.get("/api/v2/cases",headers=headers).status_code,401)
        self.assertEqual(self.client.get("/api/v2/cases",headers={**headers,"X-FM-Entry":"admin"}).status_code,200)


if __name__ == "__main__": unittest.main()
