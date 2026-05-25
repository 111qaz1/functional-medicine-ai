from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.bootstrap import build_container
from app.core.settings import AppSettings
from app.domain.models import (
    ClinicianRule,
    ClinicianRuleAction,
    ProductRule,
    Questionnaire,
    RuleScope,
    WorkspaceScope,
)
from app.main import create_app


class AuthWorkspaceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
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
            report_reference_path=root / "0316测试报告1.pdf",
        )
        self.container = build_container(settings)
        self.app = create_app()
        self.app.state.container = self.container
        self.client_a = TestClient(self.app)
        self.client_b = TestClient(self.app)
        self.public_client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client_a.close()
        self.client_b.close()
        self.public_client.close()
        self.temp_dir.cleanup()

    def _register(self, client: TestClient, username: str):
        response = client.post(
            "/auth/register",
            json={"username": username, "password": "secret123", "display_name": username.upper()},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["doctor"]

    def test_doctor_workspaces_are_isolated_but_public_workspace_is_open(self) -> None:
        doctor_a = self._register(self.client_a, "doctor-a")
        self._register(self.client_b, "doctor-b")

        private_case = self.client_a.post(
            "/cases",
            json={"customer_name": "A的客户", "workspace_scope": "doctor", "analysis_mode": "llm_primary"},
        )
        self.assertEqual(private_case.status_code, 200, private_case.text)
        case_id = private_case.json()["case"]["id"]
        self.assertEqual(private_case.json()["case"]["owner_doctor_id"], doctor_a["id"])

        public_case = self.public_client.post(
            "/cases",
            json={"customer_name": "公共客户", "workspace_scope": "public", "analysis_mode": "llm_primary"},
        )
        self.assertEqual(public_case.status_code, 200, public_case.text)

        self.assertEqual(self.client_a.get("/cases", params={"workspace": "doctor"}).json()["cases"][0]["id"], case_id)
        self.assertEqual(self.client_b.get(f"/cases/{case_id}").status_code, 403)
        self.assertEqual(self.public_client.get(f"/cases/{case_id}").status_code, 403)

        public_list = self.public_client.get("/cases", params={"workspace": "public"})
        self.assertEqual(public_list.status_code, 200, public_list.text)
        self.assertEqual(public_list.json()["cases"][0]["customer_name"], "公共客户")

    def test_rule_scope_controls_matching_for_future_reports(self) -> None:
        doctor_a = self.container.auth_service.register(username="doctor-a", password="secret123")
        doctor_b = self.container.auth_service.register(username="doctor-b", password="secret123")
        self.container.repository.save_product(
            ProductRule(
                sku_id="sku_scope_public_support",
                display_name="公共规则测试营养素",
                category="test",
                source_refs=["manual:test"],
                formula_summary="验证公共规则会对所有医生生效。",
                core_ingredients=["测试成分"],
                candidate_use_cases=["医生规则"],
                contraindications=[],
                enabled=True,
                indications=[],
                exclusions=[],
                dosage_rule="每日 1 粒。",
                interaction_rule=[],
                warning_text=[],
                lifestyle_tags=[],
                priority=1,
            )
        )
        self.container.repository.save_product(
            ProductRule(
                sku_id="sku_scope_private_support",
                display_name="私人规则测试营养素",
                category="test",
                source_refs=["manual:test"],
                formula_summary="验证私人规则只对所属医生生效。",
                core_ingredients=["测试成分"],
                candidate_use_cases=["医生规则"],
                contraindications=[],
                enabled=True,
                indications=[],
                exclusions=[],
                dosage_rule="每日 1 粒。",
                interaction_rule=[],
                warning_text=[],
                lifestyle_tags=[],
                priority=1,
            )
        )
        self.container.repository.save_clinician_rule(
            ClinicianRule(
                id="rule_public_scope",
                title="公共规则命中疲劳",
                instruction_text="以后遇到疲劳病例加入公共规则测试营养素。",
                created_by="doctor-a",
                created_by_doctor_id=doctor_a.id,
                scope=RuleScope.public,
                action=ClinicianRuleAction.boost,
                target_sku_ids=["sku_scope_public_support"],
                trigger_symptoms=["fatigue_scope"],
            )
        )
        self.container.repository.save_clinician_rule(
            ClinicianRule(
                id="rule_private_scope",
                title="私人规则命中疲劳",
                instruction_text="以后遇到疲劳病例加入私人规则测试营养素。",
                created_by="doctor-a",
                created_by_doctor_id=doctor_a.id,
                scope=RuleScope.private,
                owner_doctor_id=doctor_a.id,
                action=ClinicianRuleAction.boost,
                target_sku_ids=["sku_scope_private_support"],
                trigger_symptoms=["fatigue_scope"],
            )
        )

        case_a = self.container.case_service.create_case(
            customer_name="A医生后续病例",
            consultant_id="doctor-a",
            notes=None,
            consent=None,
            workspace_scope=WorkspaceScope.doctor,
            owner_doctor_id=doctor_a.id,
        )
        case_b = self.container.case_service.create_case(
            customer_name="B医生后续病例",
            consultant_id="doctor-b",
            notes=None,
            consent=None,
            workspace_scope=WorkspaceScope.doctor,
            owner_doctor_id=doctor_b.id,
        )
        questionnaire = Questionnaire(sex="unknown", symptoms=["fatigue_scope"])
        self.container.case_service.submit_questionnaire(case_a.id, questionnaire)
        self.container.case_service.submit_questionnaire(case_b.id, questionnaire)

        matched_a = self.container.assistant_rule_service.match_rules_for_case(
            self.container.case_service.get_case(case_a.id)
        )
        matched_b = self.container.assistant_rule_service.match_rules_for_case(
            self.container.case_service.get_case(case_b.id)
        )

        self.assertEqual({rule.id for rule in matched_a}, {"rule_public_scope", "rule_private_scope"})
        self.assertEqual({rule.id for rule in matched_b}, {"rule_public_scope"})

    def test_anonymous_user_cannot_create_rule_from_public_workspace(self) -> None:
        case = self.public_client.post(
            "/cases",
            json={"customer_name": "公共匿名病例", "workspace_scope": "public", "analysis_mode": "llm_primary"},
        )
        self.assertEqual(case.status_code, 200, case.text)

        response = self.public_client.post(
            "/assistant/rules/from-case",
            json={
                "case_id": case.json()["case"]["id"],
                "instruction_text": "以后遇到类似病例加入 sku_vitamin_d3_k。",
                "scope": "public",
            },
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.container.repository.list_clinician_rules(), [])

    def test_parsing_review_manual_indicator_is_persisted_and_displayed(self) -> None:
        created = self.public_client.post(
            "/cases",
            json={"customer_name": "人工补录病例", "workspace_scope": "public", "analysis_mode": "llm_primary"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        case_id = created.json()["case"]["id"]

        response = self.public_client.put(
            f"/cases/{case_id}/parsing-review",
            json={
                "reviewer_id": "reviewer-01",
                "files": [],
                "normalized_lab_items": [],
                "manual_indicators": [
                    {
                        "indicator_name": "脂肪肝",
                        "result_text": "总检提示",
                        "status": "positive",
                        "evidence_text": "总检汇总分析第3项",
                    }
                ],
                "missing_fields": [],
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["case"]["manual_indicators"][0]["indicator_name"], "脂肪肝")
        self.assertEqual(payload["display_indicators"][0]["indicator_name"], "脂肪肝")
        self.assertEqual(payload["display_indicators"][0]["source_span"]["snippet"], "总检汇总分析第3项")


if __name__ == "__main__":
    unittest.main()
