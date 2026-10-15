"""Focused restoration tests; fixtures contain no real patient data."""
from __future__ import annotations

import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from unittest.mock import patch

from backend.tests import test_joolun_java_auth as auth_tests
from backend.tests import test_api_v2_workflow as workflow_tests
from app.api.v2.problems import V2ApiError
from app.domain.models import WorkspaceScope, CaseStatus
from app.repositories.in_memory import LocalRepository
from app.services.joolun_java_auth import JoolunJavaAuth, utc_now


class PatientCaseTests(unittest.TestCase):
    setUp = auth_tests.JavaLoginTests.setUp
    upstream = auth_tests.JavaLoginTests.upstream
    create = auth_tests.JavaLoginTests.create
    api_headers = auth_tests.JavaLoginTests.api_headers

    def counts(self):
        with closing(self.container.repository._connect()) as db, db:
            return tuple(db.execute("SELECT count(*) FROM " + table).fetchone()[0]
                         for table in ("cases", "external_case_mappings", "external_patient_cases"))

    def legacy(self, encounter, *, content=False, issuer=None, age=0, approved=False):
        issuer = issuer or self.container.settings.joolun_issuer
        doctor = self.container.auth_service.resolve_external_doctor(
            issuer=issuer, external_doctor_id=str(self.home["data"]["doctorInfo"]["id"]), display_name="Synthetic")
        case = self.container.case_service.create_case(customer_name="Synthetic", consultant_id=None,
            notes=None, consent=None, workspace_scope=WorkspaceScope.doctor, owner_doctor_id=doctor.id)
        case.clinical_summary_text = "Synthetic saved summary" if content else None
        case.updated_at = utc_now() - timedelta(days=age)
        if approved:
            case.status = CaseStatus.approved
        self.container.repository.save_case(case)
        self.container.repository.save_external_case_mapping(dict(issuer=issuer,
            external_doctor_id=str(self.home["data"]["doctorInfo"]["id"]),
            external_patient_id=self.payload.external_patient_id, external_encounter_id=encounter,
            doctor_id=doctor.id, case_id=case.id, created_at=case.created_at.isoformat(), updated_at=case.updated_at.isoformat()))
        return case

    def test_new_uuid_new_browser_and_process_restart_restore_same_case(self):
        first = self.create()
        service = JoolunJavaAuth(self.container)
        service.transport = self.service.transport
        for encounter in ("new-browser", "after-prescription-submission"):
            result = service.create(self.payload.model_copy(update={"external_encounter_id":encounter}), "synthetic-java-token")
            self.assertEqual(result["case_id"], first["case_id"])
            self.assertEqual(result["external_encounter_id"], "encounter")
        self.assertEqual(self.counts(), (1,1,1))
        self.assertFalse(first["read_only"])

    def test_old_content_beats_new_blank_and_does_not_merge_or_delete(self):
        old = self.legacy("old-content", content=True, age=3)
        blank = self.legacy("new-blank")
        result = self.create()
        self.assertEqual(result["case_id"], old.id)
        self.assertEqual(result["external_encounter_id"], "old-content")
        self.assertIsNotNone(self.container.repository.get_case(blank.id))
        self.assertEqual(self.counts(), (2,2,1))
        self.assertEqual(self.container.repository.get_case(old.id).clinical_summary_text, "Synthetic saved summary")

    def test_analysis_only_is_meaningful_and_latest_content_wins(self):
        self.legacy("older-content", content=True, age=5)
        analyzed = self.legacy("analyzed", age=2)
        self.container.repository.save_case_analysis(workflow_tests.V2WorkflowApiTests._analysis(analyzed.id))
        self.legacy("latest-blank")
        self.assertEqual(self.create()["case_id"], analyzed.id)

    def test_all_blank_reuses_latest_blank(self):
        self.legacy("older", age=1)
        latest = self.legacy("latest")
        self.assertEqual(self.create()["case_id"], latest.id)
        self.assertEqual(self.counts(), (2,2,1))

    def test_existing_binding_stays_stable_even_if_old_other_record_changes(self):
        first = self.legacy("first", content=True)
        other = self.legacy("other", content=True, age=3)
        self.assertEqual(self.create()["case_id"], first.id)
        other.updated_at = utc_now() + timedelta(days=1)
        self.container.repository.save_case(other)
        self.assertEqual(self.create()["case_id"], first.id)

    def test_doctors_and_patients_are_isolated_with_long_string_ids(self):
        first = self.create()
        second = self.service.create(self.payload.model_copy(update={"external_patient_id":"2099664879912943619", "external_encounter_id":"patient-two"}), "synthetic-java-token")
        self.home["data"]["doctorInfo"]["id"] = "2099664879912943620"
        third = self.service.create(self.payload, "another-doctor-token")
        self.assertEqual(len({first["case_id"],second["case_id"],third["case_id"]}),3)
        self.assertEqual(self.counts(), (3,3,3))
        response = self.client.get("/api/v2/cases/"+first["case_id"],headers=self.api_headers(second))
        self.assertEqual(response.status_code,403)

    def test_old_issuer_and_broken_owner_fail_without_new_cases(self):
        self.legacy("old", issuer="previous-platform")
        before = self.counts()
        with self.assertRaises(V2ApiError) as error:
            self.create()
        self.assertEqual(error.exception.code,"PATIENT_CASE_ISSUER_CONFLICT")
        self.assertEqual(self.counts(),before)

    def test_broken_mapping_and_database_error_never_create_fallback(self):
        case = self.legacy("broken", content=True)
        with closing(self.container.repository._connect()) as db, db:
            db.execute("UPDATE cases SET owner_doctor_id='wrong-owner' WHERE id=?",(case.id,))
        before = self.counts()
        with self.assertRaises(V2ApiError) as error:
            self.create()
        self.assertEqual(error.exception.status,409)
        with patch.object(self.container.repository,"resolve_patient_case",side_effect=sqlite3.OperationalError("private diagnostic")):
            with self.assertRaises(V2ApiError) as error:
                self.create()
        self.assertEqual(error.exception.status,503)
        self.assertNotIn("private diagnostic",error.exception.detail)
        self.assertEqual(self.counts(),before)

    def test_atomic_creation_rolls_back_on_binding_write_failure(self):
        class BrokenConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if sql.startswith("INSERT OR IGNORE INTO external_patient_cases"):
                    raise sqlite3.OperationalError("synthetic disk failure")
                return super().execute(sql,parameters)
        def connect():
            db=sqlite3.connect(self.container.repository.database_path,factory=BrokenConnection)
            db.row_factory=sqlite3.Row
            return db
        before=self.counts()
        with patch.object(self.container.repository,"_connect",side_effect=connect):
            with self.assertRaises(V2ApiError) as error:
                self.create()
        self.assertEqual(error.exception.status,503)
        self.assertEqual(self.counts(),before)

    def test_concurrent_different_connections_create_one_case_and_one_audit(self):
        doctor=self.container.auth_service.resolve_external_doctor(issuer="joolun", external_doctor_id=str(self.home["data"]["doctorInfo"]["id"]),display_name="Synthetic")
        repos=[LocalRepository(self.container.repository.database_path) for _ in range(5)]
        def resolve(i):
            return repos[i].resolve_patient_case(issuer="joolun",external_doctor_id=str(self.home["data"]["doctorInfo"]["id"]),
                external_patient_id=self.payload.external_patient_id,external_encounter_id="concurrent-"+str(i),
                doctor_id=doctor.id,patient_name="Synthetic",doctor_name="Synthetic")["case_id"]
        with ThreadPoolExecutor(max_workers=5) as pool:
            ids=list(pool.map(resolve,range(5)))
        self.assertEqual(len(set(ids)),1)
        self.assertEqual(self.counts(),(1,1,1))
        self.assertEqual(len(self.container.repository.list_audit_logs(ids[0])),1)

    def test_reopened_approved_is_read_only_in_session_context_and_write_routes(self):
        case=self.legacy("approved",content=True,approved=True)
        result=self.create()
        self.assertTrue(result["read_only"])
        headers=self.api_headers(result)
        context=self.client.get("/api/v2/integrations/joolun/embed-context",headers=headers)
        self.assertEqual(context.status_code,200,context.text)
        self.assertTrue(context.json()["read_only"])
        self.assertEqual(self.client.get("/api/v2/cases/"+case.id,headers=headers).status_code,200)
        for method,path,body in (("put",f"/api/v2/cases/{case.id}/clinical-summary",{"clinical_summary":"changed"}),
                                 ("post",f"/api/v2/cases/{case.id}/analyses",{"third_party_processing_confirmed":True}),
                                 ("delete",f"/api/v2/cases/{case.id}/attachments/file",None)):
            response=getattr(self.client,method)(path,headers=headers,**({"json":body} if body else {}))
            self.assertEqual(response.status_code,403,response.text)
        self.assertEqual(self.container.repository.get_case(case.id).clinical_summary_text,"Synthetic saved summary")

    def test_active_session_cannot_modify_after_approval_and_refresh_keeps_expiry(self):
        result=self.create()
        case=self.container.repository.get_case(result["case_id"])
        case.status=CaseStatus.approved
        self.container.repository.save_case(case)
        response=self.client.post(f"/api/v2/cases/{case.id}/analyses",json={"third_party_processing_confirmed":True},headers=self.api_headers(result))
        self.assertEqual(response.status_code,403,response.text)
        restored=self.service.create(self.payload.model_copy(update={"external_encounter_id":"changed"}),"synthetic-java-token",result["access_token"])
        self.assertTrue(restored["read_only"])
        self.assertEqual(restored["access_token"],result["access_token"])
        self.assertEqual(restored["expires_at"],result["expires_at"])


if __name__ == "__main__":
    unittest.main()
