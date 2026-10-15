from __future__ import annotations

import hashlib
import hmac
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.api.v2.joolun_integration import router as joolun_router
from app.core.bootstrap import build_container
from app.core.settings import AppSettings
from app.domain.models import DraftRecommendationItem, DosageOptionSummary
from app.services.joolun_embed import JoolunEmbedService


class JoolunEmbedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        knowledge_root = root / "knowledge"
        knowledge_root.mkdir(parents=True)
        mapping_path = root / "joolun_sku_mapping.json"
        mapping_path.write_text('{"version": 2, "mappings": []}', encoding="utf-8")
        settings = AppSettings(
            project_root=root,
            data_dir=Path(__file__).resolve().parents[1] / "app" / "data",
            runtime_dir=root / ".runtime",
            upload_dir=root / ".runtime" / "uploads",
            report_export_dir=root / ".runtime" / "reports",
            sqlite_path=root / ".runtime" / "test.sqlite3",
            knowledge_root=knowledge_root,
            report_reference_path=root / "report-reference.pdf",
            joolun_embed_enabled=True,
            joolun_auth_mode="legacy_hmac",
            joolun_embed_shared_secret="embed-test-secret",
            joolun_embed_base_url="http://localhost:3000",
            joolun_embed_allowed_parent_origins=("http://localhost:7600",),
            joolun_sku_mapping_path=mapping_path,
        )
        self.container = build_container(settings)
        self.app = FastAPI()
        self.app.state.container = self.container
        self.app.include_router(joolun_router)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.container.case_analysis_service.shutdown()
        self.temp_dir.cleanup()

    def signed_payload(
        self,
        *,
        nonce: str,
        encounter_id: str = "encounter-001",
        patient_id: str = "patient-001",
    ) -> dict:
        payload = {
            "issuer": "joolun",
            "external_doctor_id": "doctor-001",
            "doctor_name": "Synthetic doctor",
            "external_patient_id": patient_id,
            "external_encounter_id": encounter_id,
            "patient_name": "Synthetic patient",
            "parent_origin": "http://localhost:7600",
            "timestamp": int(time.time()),
            "nonce": nonce,
        }
        canonical = "\n".join(
            [
                payload["issuer"],
                payload["external_doctor_id"],
                payload["doctor_name"],
                payload["external_patient_id"],
                payload["external_encounter_id"],
                payload["patient_name"],
                payload["parent_origin"],
                str(payload["timestamp"]),
                payload["nonce"],
            ]
        )
        payload["signature"] = hmac.new(
            b"embed-test-secret",
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return payload

    def test_same_encounter_restores_case_and_ticket_is_single_use(self) -> None:
        first = self.client.post(
            "/api/v2/integrations/joolun/embed-sessions",
            json=self.signed_payload(nonce="nonce-embed-test-0001"),
        )
        self.assertEqual(first.status_code, 201, first.text)
        second = self.client.post(
            "/api/v2/integrations/joolun/embed-sessions",
            json=self.signed_payload(nonce="nonce-embed-test-0002"),
        )
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(first.json()["case_id"], second.json()["case_id"])

        another_encounter = self.client.post(
            "/api/v2/integrations/joolun/embed-sessions",
            json=self.signed_payload(nonce="nonce-embed-test-0006", encounter_id="encounter-002"),
        )
        self.assertEqual(another_encounter.status_code, 201, another_encounter.text)
        self.assertNotEqual(first.json()["case_id"], another_encounter.json()["case_id"])

        ticket = first.json()["embed_url"].split("ticket=", 1)[1]
        exchanged = self.client.post(
            "/api/v2/integrations/joolun/embed-tickets:exchange",
            json={"ticket": ticket},
        )
        self.assertEqual(exchanged.status_code, 200, exchanged.text)
        token = exchanged.json()["access_token"]
        context = self.client.get(
            "/api/v2/integrations/joolun/embed-context",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(context.status_code, 200, context.text)
        self.assertEqual(context.json()["case_id"], first.json()["case_id"])

        replay = self.client.post(
            "/api/v2/integrations/joolun/embed-tickets:exchange",
            json={"ticket": ticket},
        )
        self.assertEqual(replay.status_code, 401, replay.text)
        self.assertEqual(replay.json()["code"], "EMBED_TICKET_INVALID")

    def test_encounter_identity_conflict_and_disabled_doctor_are_rejected(self) -> None:
        created = self.client.post(
            "/api/v2/integrations/joolun/embed-sessions",
            json=self.signed_payload(nonce="nonce-embed-test-0007"),
        )
        self.assertEqual(created.status_code, 201, created.text)
        conflict = self.client.post(
            "/api/v2/integrations/joolun/embed-sessions",
            json=self.signed_payload(nonce="nonce-embed-test-0008", patient_id="patient-002"),
        )
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["code"], "EMBED_ENCOUNTER_IDENTITY_CONFLICT")

        doctor = self.container.auth_service.resolve_external_doctor(
            issuer="joolun",
            external_doctor_id="doctor-001",
            display_name="Synthetic doctor",
        )
        self.container.auth_service.update_doctor(doctor.id, enabled=False)
        disabled = self.client.post(
            "/api/v2/integrations/joolun/embed-sessions",
            json=self.signed_payload(nonce="nonce-embed-test-0009", encounter_id="encounter-003"),
        )
        self.assertEqual(disabled.status_code, 403, disabled.text)
        self.assertEqual(disabled.json()["code"], "EMBED_DOCTOR_DISABLED")

    def test_nonce_replay_and_wrong_parent_origin_are_rejected(self) -> None:
        payload = self.signed_payload(nonce="nonce-embed-test-0003")
        first = self.client.post("/api/v2/integrations/joolun/embed-sessions", json=payload)
        self.assertEqual(first.status_code, 201, first.text)
        replay = self.client.post("/api/v2/integrations/joolun/embed-sessions", json=payload)
        self.assertEqual(replay.status_code, 409, replay.text)
        self.assertEqual(replay.json()["code"], "EMBED_NONCE_REPLAYED")

        wrong_origin = self.signed_payload(nonce="nonce-embed-test-0004")
        wrong_origin["parent_origin"] = "https://untrusted.example"
        canonical = "\n".join(
            [
                wrong_origin["issuer"], wrong_origin["external_doctor_id"], wrong_origin["doctor_name"],
                wrong_origin["external_patient_id"], wrong_origin["external_encounter_id"],
                wrong_origin["patient_name"], wrong_origin["parent_origin"], str(wrong_origin["timestamp"]),
                wrong_origin["nonce"],
            ]
        )
        wrong_origin["signature"] = hmac.new(
            b"embed-test-secret", canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        rejected = self.client.post("/api/v2/integrations/joolun/embed-sessions", json=wrong_origin)
        self.assertEqual(rejected.status_code, 403, rejected.text)
        self.assertEqual(rejected.json()["code"], "EMBED_PARENT_ORIGIN_REJECTED")

    def test_expired_signature_is_rejected(self) -> None:
        payload = self.signed_payload(nonce="nonce-embed-test-0005")
        payload["timestamp"] = int(time.time()) - 301
        canonical = "\n".join(
            [
                payload["issuer"], payload["external_doctor_id"], payload["doctor_name"],
                payload["external_patient_id"], payload["external_encounter_id"], payload["patient_name"],
                payload["parent_origin"], str(payload["timestamp"]), payload["nonce"],
            ]
        )
        payload["signature"] = hmac.new(
            b"embed-test-secret", canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        response = self.client.post("/api/v2/integrations/joolun/embed-sessions", json=payload)
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["code"], "EMBED_SIGNATURE_EXPIRED")

    def test_committed_catalog_maps_confirmed_zinc_and_super_anti_inflammatory(self) -> None:
        payload = json.loads(
            (Path(__file__).resolve().parents[1] / "app" / "data" / "joolun_sku_mapping.json").read_text(
                encoding="utf-8"
            )
        )
        mappings = {item["sku_id"]: item for item in payload["mappings"]}
        self.assertEqual(payload["version"], 2)
        self.assertEqual(len(mappings), 31)
        self.assertEqual(mappings["sku_zinc_complex"]["goods_id"], "2091816225546137602")
        self.assertEqual(mappings["sku_zinc_complex"]["spec_id"], "2091817634534486017")
        anti_inflammatory = mappings["sku_super_anti_inflammatory"]
        self.assertEqual(anti_inflammatory["status"], "mapped")
        self.assertIs(anti_inflammatory["enabled"], True)
        self.assertEqual(anti_inflammatory["goods_id"], "2099664879912943617")
        self.assertEqual(anti_inflammatory["spec_id"], "2099665381761417218")
        self.assertEqual(anti_inflammatory["product_type"], 1)
        pairs = [(item["goods_id"], item["spec_id"]) for item in mappings.values()
                 if item["status"] == "mapped" and item["enabled"]]
        self.assertEqual(len(pairs), len(set(pairs)))
        self.assertTrue(all(isinstance(value, str) for pair in pairs for value in pair))
        service = JoolunEmbedService(SimpleNamespace(
            settings=SimpleNamespace(joolun_sku_mapping_path=
                Path(__file__).resolve().parents[1] / "app/data/joolun_sku_mapping.json"),
            repository=None,
        ))
        self.assertEqual(service._load_sku_mappings(), mappings)

    def enzyme_recommendation(self, option_suffix: str) -> DraftRecommendationItem:
        catalog = json.loads((Path(__file__).resolve().parents[1] /
            "app/data/product_dosage_mapping.json").read_text(encoding="utf-8-sig"))
        product = next(row for row in catalog["products"] if row["sku_id"] == "sku_digestive_enzymes")
        options = [DosageOptionSummary.model_validate({key: row[key] for key in
            DosageOptionSummary.model_fields if key in row}) for row in product["dose_options"]]
        option = next(row for row in options if row.option_id.endswith(option_suffix))
        return DraftRecommendationItem(
            sku_id=product["sku_id"], display_name="综合消化酶", reason="专项测试",
            dosage=option.display_text, dosage_option_id=option.option_id,
            dosage_regimen=option.regimen, dosage_options=options,
        )

    def test_only_two_confirmed_per_meal_enzyme_options_resolve_three(self) -> None:
        for suffix in ("dose_f13b6d46fa", "dose_a8089f5dd9"):
            with self.subTest(option=suffix):
                recommendation = self.enzyme_recommendation(suffix)
                original = recommendation.model_dump()
                dose = JoolunEmbedService._convert_approved_dose(recommendation)
                self.assertEqual(dose, {"status": "resolved", "unit": "粒", "quantity": 3,
                    "breakfast_dose": 1, "lunch_dose": 1, "dinner_dose": 1})
                self.assertEqual(recommendation.model_dump(), original)

    def test_enzyme_explicit_daily_options_keep_approved_amount(self) -> None:
        for suffix, expected in (("dose_16377b841f", 1), ("dose_218f118981", 2), ("dose_54fd3a651d", 2)):
            with self.subTest(option=suffix):
                dose = JoolunEmbedService._convert_approved_dose(self.enzyme_recommendation(suffix))
                self.assertEqual(dose["quantity"], expected)
                self.assertEqual(sum(dose[key] for key in ("breakfast_dose", "lunch_dose", "dinner_dose")), expected)

    def test_effective_daily_wording_and_regimen_override_per_meal_default(self) -> None:
        recommendation = self.enzyme_recommendation("dose_a8089f5dd9")
        for quantity in (1, 2, 4):
            with self.subTest(quantity=quantity):
                effective = recommendation.model_copy(update={"dosage": f"医生复核剂量；每日{quantity}粒，随餐服用"})
                self.assertEqual(JoolunEmbedService._convert_approved_dose(effective)["quantity"], quantity)
        effective = recommendation.model_copy(update={"dosage_regimen": recommendation.dosage_regimen.model_copy(
            update={"daily_frequency_min": 2, "daily_frequency_max": 2, "daily_max": 2})})
        self.assertEqual(JoolunEmbedService._convert_approved_dose(effective)["quantity"], 2)

    def test_other_per_dose_products_and_unknown_enzyme_options_stay_pending(self) -> None:
        original = self.enzyme_recommendation("dose_a8089f5dd9")
        for update in ({"sku_id": "sku_other_product"}, {"dosage_option_id": "sku_digestive_enzymes:unknown"},
                       {"dosage": "隔日每次1粒"}, {"dosage": "每日两次，每次1粒"},
                       {"dosage": "每日0粒"}, {"dosage": "每日1.5粒"}):
            with self.subTest(update=update):
                dose = JoolunEmbedService._convert_approved_dose(original.model_copy(update=update))
                self.assertEqual(dose["status"], "pending")
                self.assertTrue(all(dose[key] is None for key in
                    ("quantity", "breakfast_dose", "lunch_dose", "dinner_dose")))
        strengthened = self.enzyme_recommendation("dose_218f118981").model_copy(update={"dosage": "每日1.5粒"})
        self.assertEqual(JoolunEmbedService._convert_approved_dose(strengthened)["status"], "pending")

    def test_approved_payload_preserves_original_text_and_applies_doctor_selected_option(self) -> None:
        class Draft(SimpleNamespace):
            def model_copy(self, *, update):
                return Draft(**(vars(self) | update))

        recommendation = self.enzyme_recommendation("dose_a8089f5dd9")
        anti_inflammatory = DraftRecommendationItem(sku_id="sku_super_anti_inflammatory",
            display_name="超强抗炎", reason="专项测试", dosage="每日2粒", dosage_option_id="daily-2",
            dosage_regimen=recommendation.dosage_regimen.model_copy(update={
                "daily_frequency_min": 2, "daily_frequency_max": 2, "daily_max": 2}))
        draft = Draft(id="draft-test", revision=2, recommended_skus=[recommendation, anti_inflammatory])
        review = SimpleNamespace(final_status="approved", edits={})
        service = JoolunEmbedService(SimpleNamespace(
            settings=SimpleNamespace(joolun_sku_mapping_path=Path(__file__).resolve().parents[1] /
                "app/data/joolun_sku_mapping.json"),
            repository=SimpleNamespace(
                get_embed_session_context=lambda *_: {"case_id": "case-test", "external_encounter_id": "encounter-test"},
                get_draft=lambda _: draft, get_review_decision=lambda _: review),
            case_service=SimpleNamespace(get_case=lambda _: SimpleNamespace(id="case-test", draft_ids=[draft.id])),
            review_service=self.container.review_service,
        ))
        approved = service.approved_recommendations("session-test")
        self.assertEqual(approved["version"], 2)
        self.assertEqual(approved["items"][0]["quantity"], 3)
        self.assertEqual(approved["items"][0]["dosage_text"], recommendation.dosage)
        self.assertEqual(approved["unmapped_skus"], [])
        self.assertEqual(approved["items"][1]["spec_id"], "2099665381761417218")
        for suffix, expected in (("dose_16377b841f", 1), ("dose_218f118981", 2)):
            selected = self.enzyme_recommendation(suffix)
            review.edits = {"dosage_overrides": {recommendation.sku_id: {
                "option_id": selected.dosage_option_id, "note": "医生选档"}}}
            item = service.approved_recommendations("session-test")["items"][0]
            self.assertEqual(item["quantity"], expected)
            self.assertEqual(item["dosage_text"], selected.dosage)
            self.assertEqual(item["dosage_option_id"], selected.dosage_option_id)
        self.assertEqual(draft.recommended_skus[0], recommendation)

    def test_daily_one_to_two_uses_upper_bound_and_unrepresentable_dose_is_pending(self) -> None:
        recommendation = SimpleNamespace(
            dosage="每日 1–2 粒，随餐服用。",
            dosage_regimen=SimpleNamespace(
                unit="粒",
                single_dose_min=1,
                single_dose_max=2,
                daily_frequency_min=1,
                daily_frequency_max=1,
                daily_max=2,
                timing=["随餐"],
            ),
        )
        resolved = JoolunEmbedService._convert_approved_dose(recommendation)
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["quantity"], 2)
        self.assertEqual(
            (resolved["breakfast_dose"], resolved["lunch_dose"], resolved["dinner_dose"]),
            (1, 0, 1),
        )

        recommendation.dosage = "隔日服用"
        recommendation.dosage_regimen = SimpleNamespace(unit="粒", timing=[], weekly_frequency_min=3)
        pending = JoolunEmbedService._convert_approved_dose(recommendation)
        self.assertEqual(pending["status"], "pending")
        self.assertIsNone(pending["quantity"])

    def test_range_regimen_with_daily_cap_resolves_upper_bound(self) -> None:
        recommendation = SimpleNamespace(
            dosage="每日2 粒，分 1-2 次服用（早餐 1 粒 + 晚餐 1 粒），随餐服用，连续服用 3 个月后可减至每日 1 粒维持。",
            dosage_regimen=SimpleNamespace(
                unit="粒",
                single_dose_min=1,
                single_dose_max=2,
                daily_frequency_min=1,
                daily_frequency_max=2,
                daily_max=2,
                timing=["随餐"],
            ),
        )
        resolved = JoolunEmbedService._convert_approved_dose(recommendation)
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(resolved["quantity"], 2)
        self.assertEqual(
            (resolved["breakfast_dose"], resolved["lunch_dose"], resolved["dinner_dose"]),
            (1, 0, 1),
        )


if __name__ == "__main__":
    unittest.main()
