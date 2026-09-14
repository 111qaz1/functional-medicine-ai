from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from app.domain.models import WorkspaceScope


SIGNATURE_MAX_SKEW = timedelta(minutes=5)
TICKET_LIFETIME = timedelta(seconds=60)
EMBED_SESSION_LIFETIME = timedelta(hours=8)


class JoolunEmbedError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JoolunEmbedService:
    def __init__(self, container) -> None:
        self.container = container
        self.settings = container.settings
        self.repository = container.repository

    @staticmethod
    def canonical_payload(payload) -> str:
        return "\n".join(
            [
                payload.issuer.strip().lower(),
                payload.external_doctor_id.strip(),
                (payload.doctor_name or "").strip(),
                payload.external_patient_id.strip(),
                payload.external_encounter_id.strip(),
                payload.patient_name.strip(),
                payload.parent_origin.strip().rstrip("/"),
                str(payload.timestamp),
                payload.nonce.strip(),
            ]
        )

    def create_embed_session(self, payload) -> dict[str, object]:
        self._require_enabled()
        self._verify_request(payload)

        issuer = payload.issuer.strip().lower()
        external_doctor_id = payload.external_doctor_id.strip()
        external_patient_id = payload.external_patient_id.strip()
        external_encounter_id = payload.external_encounter_id.strip()
        parent_origin = payload.parent_origin.strip().rstrip("/")
        now = utc_now()

        nonce_claimed = self.repository.claim_embed_nonce(
            issuer=issuer,
            nonce=payload.nonce.strip(),
            expires_at=(now + SIGNATURE_MAX_SKEW).isoformat(),
            now_iso=now.isoformat(),
        )
        if not nonce_claimed:
            raise JoolunEmbedError("EMBED_NONCE_REPLAYED", "The embed request nonce has already been used.")

        try:
            doctor = self.container.auth_service.resolve_external_doctor(
                issuer=issuer,
                external_doctor_id=external_doctor_id,
                display_name=payload.doctor_name,
            )
        except ValueError as exc:
            raise JoolunEmbedError("EMBED_DOCTOR_DISABLED", str(exc)) from exc

        mapping = self.repository.get_external_case_mapping(
            issuer=issuer,
            external_doctor_id=external_doctor_id,
            external_encounter_id=external_encounter_id,
        )
        if mapping:
            if mapping["doctor_id"] != doctor.id or mapping["external_patient_id"] != external_patient_id:
                raise JoolunEmbedError(
                    "EMBED_ENCOUNTER_IDENTITY_CONFLICT",
                    "The encounter is already mapped to a different doctor or patient.",
                )
            case = self.container.case_service.get_case(mapping["case_id"])
        else:
            case = self.container.case_service.create_case(
                customer_name=payload.patient_name.strip(),
                consultant_id=doctor.display_name,
                notes=None,
                consent=None,
                workspace_scope=WorkspaceScope.doctor,
                owner_doctor_id=doctor.id,
            )
            mapping = self.repository.save_external_case_mapping(
                {
                    "issuer": issuer,
                    "external_doctor_id": external_doctor_id,
                    "external_encounter_id": external_encounter_id,
                    "external_patient_id": external_patient_id,
                    "doctor_id": doctor.id,
                    "case_id": case.id,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
            )
            if mapping["case_id"] != case.id:
                case = self.container.case_service.get_case(mapping["case_id"])

        raw_ticket = secrets.token_urlsafe(32)
        expires_at = now + TICKET_LIFETIME
        self.repository.save_embed_ticket(
            {
                "token_hash": self._ticket_hash(raw_ticket),
                "issuer": issuer,
                "external_doctor_id": external_doctor_id,
                "external_patient_id": external_patient_id,
                "external_encounter_id": external_encounter_id,
                "parent_origin": parent_origin,
                "doctor_id": doctor.id,
                "case_id": case.id,
                "expires_at": expires_at.isoformat(),
            }
        )
        return {
            "embed_url": f"{self.settings.joolun_embed_base_url}/integration/embed/start?ticket={quote(raw_ticket)}",
            "case_id": case.id,
            "expires_at": expires_at,
        }

    def exchange_ticket(self, raw_ticket: str) -> dict[str, object]:
        self._require_enabled()
        now = utc_now()
        ticket = self.repository.consume_embed_ticket(
            token_hash=self._ticket_hash(raw_ticket.strip()),
            consumed_at=now.isoformat(),
        )
        if ticket is None:
            raise JoolunEmbedError("EMBED_TICKET_INVALID", "The embed ticket is invalid, expired, or already used.")

        doctor = self.repository.get_doctor(ticket["doctor_id"])
        if doctor is None or not doctor.enabled:
            raise JoolunEmbedError("EMBED_DOCTOR_DISABLED", "The external doctor identity is disabled.")
        session = self.container.auth_service.issue_session_for_doctor(
            doctor,
            lifetime=EMBED_SESSION_LIFETIME,
        )
        self.repository.save_embed_session_context(
            {
                "session_id": session.session.id,
                "issuer": ticket["issuer"],
                "external_encounter_id": ticket["external_encounter_id"],
                "parent_origin": ticket["parent_origin"],
                "case_id": ticket["case_id"],
                "expires_at": session.session.expires_at.isoformat(),
            }
        )
        return {
            "session_id": session.session.id,
            "case_id": ticket["case_id"],
            "expires_at": session.session.expires_at,
        }

    def get_context(self, session_id: str) -> dict[str, str]:
        context = self.repository.get_embed_session_context(session_id, utc_now().isoformat())
        if context is None:
            raise JoolunEmbedError("EMBED_SESSION_REQUIRED", "A valid embed session is required.")
        return context

    def approved_recommendations(self, session_id: str) -> dict[str, object]:
        context = self.get_context(session_id)
        case = self.container.case_service.get_case(context["case_id"])
        if not case.draft_ids:
            raise JoolunEmbedError("EMBED_APPROVED_DRAFT_NOT_FOUND", "No approved draft exists for this encounter.")
        draft = self.repository.get_draft(case.draft_ids[-1])
        review = self.repository.get_review_decision(draft.id) if draft else None
        if draft is None or review is None or getattr(review.final_status, "value", review.final_status) != "approved":
            raise JoolunEmbedError("EMBED_APPROVED_DRAFT_NOT_FOUND", "No approved draft exists for this encounter.")

        effective = self.container.review_service._draft_with_filtered_recommendations(draft, review.edits)
        mappings = self._load_sku_mappings()
        items: list[dict[str, object]] = []
        unmapped: list[dict[str, str]] = []
        for recommendation in effective.recommended_skus:
            mapping = mappings.get(recommendation.sku_id)
            if mapping is None or mapping.get("status") != "mapped" or not mapping.get("enabled", True):
                unmapped.append(
                    {
                        "sku_id": recommendation.sku_id,
                        "display_name": recommendation.display_name,
                        "reason": str((mapping or {}).get("reason") or "mapping_not_configured"),
                    }
                )
                continue
            dose = self._convert_approved_dose(recommendation)
            items.append(
                {
                    "sku_id": recommendation.sku_id,
                    "display_name": recommendation.display_name,
                    "goods_id": str(mapping["goods_id"]),
                    "spec_id": None if mapping.get("spec_id") is None else str(mapping["spec_id"]),
                    "product_type": int(mapping.get("product_type", 0)),
                    "unit": dose["unit"],
                    "dosage_option_id": recommendation.dosage_option_id,
                    "dosage_text": recommendation.dosage,
                    "dose_status": dose["status"],
                    "quantity": dose["quantity"],
                    "breakfast_dose": dose["breakfast_dose"],
                    "lunch_dose": dose["lunch_dose"],
                    "dinner_dose": dose["dinner_dose"],
                    "reason": recommendation.reason,
                }
            )
        return {
            "version": 2,
            "external_encounter_id": context["external_encounter_id"],
            "case_id": case.id,
            "draft_id": draft.id,
            "revision": draft.revision,
            "items": items,
            "unmapped_skus": unmapped,
        }

    @staticmethod
    def _regimen_value(regimen, field: str) -> float | None:
        raw = getattr(regimen, field, None) if regimen is not None else None
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _positive_integer(value: float | None) -> int | None:
        if value is None or value <= 0 or not float(value).is_integer():
            return None
        return int(value)

    @classmethod
    def _convert_approved_dose(cls, recommendation) -> dict[str, object]:
        regimen = recommendation.dosage_regimen
        unit = str(getattr(regimen, "unit", None) or "粒")
        single_min = cls._regimen_value(regimen, "single_dose_min")
        single_max = cls._regimen_value(regimen, "single_dose_max")
        frequency_min = cls._regimen_value(regimen, "daily_frequency_min")
        frequency_max = cls._regimen_value(regimen, "daily_frequency_max")
        daily_max = cls._regimen_value(regimen, "daily_max")
        dosage_text = str(recommendation.dosage or "")

        quantity: int | None = None
        if re.search(r"每日\s*1\s*[-–—至]\s*2\s*粒", dosage_text):
            quantity = 2
        elif (
            single_min is not None
            and single_max == single_min
            and frequency_min is not None
            and frequency_max == frequency_min
        ):
            quantity = cls._positive_integer(single_max * frequency_max)
        if quantity is None and single_min == single_max and cls._positive_integer(daily_max) is not None:
            quantity = cls._positive_integer(daily_max)
        if quantity is None:
            daily_cap = cls._positive_integer(daily_max)
            per_dose_ceiling = (
                cls._positive_integer(single_max * frequency_max)
                if single_max is not None and frequency_max is not None
                else None
            )
            if daily_cap is not None and per_dose_ceiling is not None:
                quantity = min(daily_cap, per_dose_ceiling)

        if quantity is None:
            return {
                "status": "pending",
                "unit": unit,
                "quantity": None,
                "breakfast_dose": None,
                "lunch_dose": None,
                "dinner_dose": None,
            }

        timing = list(getattr(regimen, "timing", None) or [])
        breakfast, lunch, dinner = cls._allocate_meals(quantity, timing)
        return {
            "status": "resolved",
            "unit": unit,
            "quantity": quantity,
            "breakfast_dose": breakfast,
            "lunch_dose": lunch,
            "dinner_dose": dinner,
        }

    @staticmethod
    def _allocate_meals(quantity: int, timing: list[str]) -> tuple[int, int, int]:
        timing_set = set(timing)
        if "早晚餐后" in timing_set:
            breakfast = (quantity + 1) // 2
            return breakfast, 0, quantity - breakfast
        if timing_set.intersection({"早餐后", "早晨"}):
            return quantity, 0, 0
        if "午餐后" in timing_set:
            return 0, quantity, 0
        if timing_set.intersection({"晚餐后", "晚上", "睡前"}):
            return 0, 0, quantity
        if quantity == 1:
            return 1, 0, 0
        if quantity == 2:
            return 1, 0, 1
        if quantity == 3:
            return 1, 1, 1
        base, remainder = divmod(quantity, 3)
        return base + (1 if remainder >= 1 else 0), base + (1 if remainder >= 2 else 0), base

    def _verify_request(self, payload) -> None:
        now = utc_now()
        try:
            timestamp = datetime.fromtimestamp(payload.timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise JoolunEmbedError("EMBED_SIGNATURE_EXPIRED", "The embed request timestamp is invalid.") from exc
        if abs(now - timestamp) > SIGNATURE_MAX_SKEW:
            raise JoolunEmbedError("EMBED_SIGNATURE_EXPIRED", "The embed request timestamp is outside the allowed window.")
        parent_origin = payload.parent_origin.strip().rstrip("/")
        if parent_origin not in self.settings.joolun_embed_allowed_parent_origins:
            raise JoolunEmbedError("EMBED_PARENT_ORIGIN_REJECTED", "The parent page origin is not allowed.")
        expected = hmac.new(
            self.settings.joolun_embed_shared_secret.encode("utf-8"),
            self.canonical_payload(payload).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        signature = payload.signature.strip().lower()
        if signature.startswith("sha256="):
            signature = signature.split("=", 1)[1]
        if not hmac.compare_digest(expected, signature):
            raise JoolunEmbedError("EMBED_SIGNATURE_INVALID", "The embed request signature is invalid.")

    def _require_enabled(self) -> None:
        if not self.settings.joolun_embed_enabled:
            raise JoolunEmbedError("EMBED_NOT_CONFIGURED", "The Joolun embed integration is disabled.")
        if not self.settings.joolun_embed_shared_secret or not self.settings.joolun_embed_base_url:
            raise JoolunEmbedError("EMBED_NOT_CONFIGURED", "The Joolun embed integration is not fully configured.")

    @staticmethod
    def _ticket_hash(raw_ticket: str) -> str:
        return hashlib.sha256(raw_ticket.encode("utf-8")).hexdigest()

    def _load_sku_mappings(self) -> dict[str, dict[str, object]]:
        path = self.settings.joolun_sku_mapping_path
        if path is None or not Path(path).exists():
            return {}
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise JoolunEmbedError("EMBED_SKU_MAPPING_INVALID", "The SKU mapping configuration is invalid.") from exc
        rows = payload.get("mappings", []) if isinstance(payload, dict) else []
        if not isinstance(payload, dict) or payload.get("version") != 2 or not isinstance(rows, list):
            raise JoolunEmbedError("EMBED_SKU_MAPPING_INVALID", "The SKU mapping configuration must use version 2.")
        mappings: dict[str, dict[str, object]] = {}
        mapped_pairs: set[tuple[str, str]] = set()
        for row in rows:
            if not isinstance(row, dict) or not str(row.get("sku_id") or "").strip():
                raise JoolunEmbedError("EMBED_SKU_MAPPING_INVALID", "A SKU mapping is missing sku_id.")
            sku_id = str(row["sku_id"]).strip()
            if sku_id in mappings:
                raise JoolunEmbedError("EMBED_SKU_MAPPING_INVALID", "The SKU mapping contains a duplicate sku_id.")
            status = str(row.get("status") or "")
            if status not in {"mapped", "unmapped"}:
                raise JoolunEmbedError("EMBED_SKU_MAPPING_INVALID", "A SKU mapping has an invalid status.")
            if status == "mapped":
                goods_id = str(row.get("goods_id") or "").strip()
                spec_id = str(row.get("spec_id") or "").strip()
                if not goods_id or (int(row.get("product_type", 0)) != 6 and not spec_id):
                    raise JoolunEmbedError("EMBED_SKU_MAPPING_INVALID", "A mapped SKU is missing goods_id or spec_id.")
                pair = (goods_id, spec_id)
                if row.get("enabled", True) and pair in mapped_pairs:
                    raise JoolunEmbedError("EMBED_SKU_MAPPING_INVALID", "Two enabled SKUs map to the same product specification.")
                mapped_pairs.add(pair)
            elif not str(row.get("reason") or "").strip():
                raise JoolunEmbedError("EMBED_SKU_MAPPING_INVALID", "An unmapped SKU is missing its reason.")
            mappings[sku_id] = row
        return mappings
