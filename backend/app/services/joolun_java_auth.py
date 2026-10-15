"""Partner login verification. Only token digests and scoped grants are persisted."""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import weakref
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlsplit

import httpx

from app.api.v2.problems import V2ApiError
from app.repositories.in_memory import PatientCaseBindingError


def utc_now():
    return datetime.now(timezone.utc)


def reject(status=401, code="JOOLUN_LOGIN_REQUIRED"):
    raise V2ApiError(status=status, code=code, title="Partner login verification failed",
                     detail="请返回甲方平台确认登录后重新加载 AI。" if status != 503 else "登录核验服务暂不可用，请稍后重试；病例进度已保留。")


class JoolunJavaAuth:
    def __init__(self, container):
        self.container = container
        self.repository = container.repository
        self.settings = container.settings
        self._cache = OrderedDict()
        self._lock = threading.RLock()
        self._inflight = weakref.WeakValueDictionary()
        self.transport = None  # Tests inject an httpx transport; never changes the configured host.

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def verify(self, token, *, force=False):
        if not token or len(token) > 8192:
            reject()
        key = self.digest(token)
        requested = utc_now()
        with self._lock:
            lock = self._inflight.get(key)
            if lock is None:
                lock = threading.RLock()
                self._inflight[key] = lock
        with lock:
            now = utc_now()
            with self._lock:
                cached = self._cache.get(key)
            if cached and now < cached["until"] and (not force or cached["checked"] >= requested):
                return cached["identity"]
            base = self.settings.joolun_java_api_base_url or ""
            try:
                url = urlsplit(base)
            except ValueError:
                reject(503, "JOOLUN_NOT_CONFIGURED")
            if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password or url.query or url.fragment:
                reject(503, "JOOLUN_NOT_CONFIGURED")
            try:
                with httpx.Client(timeout=5.0, follow_redirects=False, transport=self.transport) as client:
                    results = []
                    for path in ("/getInfo", "/admin/api/doctor/home"):
                        response = client.get(base + path, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
                        if response.status_code in {401, 403}:
                            with self._lock:
                                self._cache.pop(key, None)
                            reject()
                        if response.status_code != 200:
                            reject(503)
                        data = response.json()
                        if not isinstance(data, dict):
                            reject(503)
                        if data.get("code") in {401, 403}:
                            with self._lock:
                                self._cache.pop(key, None)
                            reject()
                        if type(data.get("code")) is not int or data["code"] != 200:
                            reject(503)
                        results.append(data)
            except (httpx.HTTPError, ValueError, TypeError):
                reject(503)
            info, home = results
            user = info.get("user")
            doctor = (home.get("data") or {}).get("doctorInfo") if isinstance(home.get("data"), dict) else None
            if not isinstance(user, dict) or not isinstance(info.get("roles"), list) or "doctor" not in info["roles"]:
                reject()
            if user.get("status") not in (None, "0", 0) or user.get("delFlag") not in (None, "0", 0):
                reject()
            if not isinstance(doctor, dict) or type(doctor.get("id")) not in (str, int) or str(doctor["id"]).strip() in {"", "0"} or not isinstance(doctor.get("name"), str) or not doctor["name"].strip():
                reject()
            identity = {"id": str(doctor["id"]).strip(), "name": doctor["name"].strip()}
            checked = utc_now()
            with self._lock:
                self._cache[key] = {"identity": identity, "checked": checked, "until": checked + timedelta(minutes=20)}
                self._cache.move_to_end(key)
                while len(self._cache) > 2048:
                    self._cache.popitem(last=False)
            return identity

    def create(self, payload, token, previous_session=None):
        if not self.settings.joolun_embed_enabled or self.settings.joolun_auth_mode != "java_token":
            reject(503, "JOOLUN_NOT_CONFIGURED")
        if payload.parent_origin not in self.settings.joolun_embed_allowed_parent_origins:
            reject(403, "EMBED_PARENT_ORIGIN_REJECTED")
        if not token:
            reject()
        issuer = self.settings.joolun_issuer
        if not issuer or not self.settings.joolun_embed_base_url:
            reject(503, "JOOLUN_NOT_CONFIGURED")
        previous_context = None
        previous_binding = None
        if previous_session:
            context = self.repository.get_embed_session_context(previous_session, utc_now().isoformat())
            binding = self.repository.get_joolun_login_binding(previous_session)
            if (context and binding and context["issuer"] == issuer and
                    binding["patient_id"] == payload.external_patient_id and
                    self.digest(token) == binding["token_hash"] and
                    self.container.auth_service.get_doctor_for_session(previous_session)):
                doctor = self.authorize(previous_session, token, context["case_id"])
                previous_context, previous_binding = context, binding
        if previous_context:
            identity = {"id": previous_binding["external_doctor_id"], "name": doctor.display_name}
        else:
            # A new Java credential must establish its own identity and session;
            # never authorize it against the previous account's case binding.
            identity = self.verify(token, force=True)
            try:
                doctor = self.container.auth_service.resolve_external_doctor(issuer=issuer, external_doctor_id=identity["id"], display_name=identity["name"])
            except ValueError:
                reject(403, "EMBED_DOCTOR_DISABLED")
        try:
            mapping = self.repository.resolve_patient_case(
                issuer=issuer, external_doctor_id=identity["id"], external_patient_id=payload.external_patient_id,
                external_encounter_id=payload.external_encounter_id, doctor_id=doctor.id,
                patient_name=payload.patient_name, doctor_name=doctor.display_name)
        except PatientCaseBindingError as exc:
            raise V2ApiError(status=409, code=str(exc), title="Patient case association requires review",
                             detail="患者旧病例关联不一致，请核对原 issuer、医生归属及病例映射；未创建替代病例。") from exc
        except (sqlite3.Error, ValueError, OSError) as exc:
            raise V2ApiError(status=503, code="PATIENT_CASE_LOOKUP_FAILED", title="Case recovery unavailable",
                             detail="病例恢复暂不可用，请稍后重试；未创建替代病例。") from exc
        case_id = mapping["case_id"]
        if previous_context:
            if previous_context["case_id"] == case_id:
                previous_binding = self.repository.get_joolun_login_binding(previous_session)
                previous_binding["read_only"] = self.repository.case_has_approved_report(case_id)
                self.repository.save_joolun_login_binding(previous_binding)
                return self.response(previous_session, doctor)
            # A protected old blank session cannot lend its exemption to the restored case.
            verified = self.verify(token, force=True)
            if verified["id"] != identity["id"]:
                reject()
        session = self.container.auth_service.issue_session_for_doctor(doctor, lifetime=timedelta(hours=8)).session
        self.repository.save_embed_session_context({"session_id": session.id, "issuer": issuer,
            "external_encounter_id": mapping["external_encounter_id"], "parent_origin": payload.parent_origin,
            "case_id": case_id, "expires_at": session.expires_at.isoformat()})
        self.repository.save_joolun_login_binding({"session_id": session.id, "token_hash": self.digest(token),
            "doctor_id": doctor.id, "external_doctor_id": identity["id"], "case_id": case_id,
            "patient_id": payload.external_patient_id, "protected": False,
            "read_only": self.repository.case_has_approved_report(case_id)})
        if previous_session:
            self.container.auth_service.logout(previous_session)
        return self.response(session.id, doctor)

    def response(self, session_id, doctor):
        session = self.repository.get_session(session_id)
        context = self.repository.get_embed_session_context(session_id, utc_now().isoformat())
        binding = self.repository.get_joolun_login_binding(session_id)
        return {"external_encounter_id": context["external_encounter_id"], "read_only": bool(binding and binding.get("read_only")),
                "access_token": session_id, "case_id": context["case_id"], "expires_at": session.expires_at,
                "embed_url": f'{self.settings.joolun_embed_base_url}/integration/embed/cases/{context["case_id"]}',
                "doctor": {"id": doctor.id, "username": doctor.username, "display_name": doctor.display_name,
                           "role": "doctor", "enabled": doctor.enabled}}

    def authorize(self, session_id, token, case_id=None):
        doctor = self.container.auth_service.get_doctor_for_session(session_id)
        binding = self.repository.get_joolun_login_binding(session_id) if session_id else None
        if not doctor or not binding or not token:
            reject()
        token = unquote(token)
        same_token = self.digest(token) == binding["token_hash"]
        if not same_token:
            binding["protected"] = False
            self.repository.save_joolun_login_binding(binding)
        if not (same_token and binding["protected"] and case_id == binding["case_id"]):
            identity = self.verify(token)
            if identity["id"] != binding["external_doctor_id"]:
                reject()
            if not same_token:
                binding.update(token_hash=self.digest(token), protected=False)
                self.repository.save_joolun_login_binding(binding)
        if doctor.id != binding["doctor_id"]:
            reject()
        return doctor

    def protect(self, session_id, case_id):
        binding = self.repository.get_joolun_login_binding(session_id)
        if binding and binding["case_id"] == case_id:
            binding["protected"] = True
            self.repository.save_joolun_login_binding(binding)

    def complete(self, session_id, draft_id=None):
        if draft_id:
            review = self.repository.get_review_decision(draft_id)
            if review is None or getattr(review.final_status, "value", review.final_status) != "approved":
                return
        binding = self.repository.get_joolun_login_binding(session_id)
        if binding:
            binding["protected"] = False
            self.repository.save_joolun_login_binding(binding)

    def request_case(self, request, session_id):
        params = request.path_params
        if params.get("case_id"):
            return params["case_id"]
        if params.get("draft_id"):
            draft = self.repository.get_draft(params["draft_id"])
            return draft.case_id if draft else None
        if params.get("operation_id"):
            analysis = self.repository.get_case_analysis(params["operation_id"])
            return analysis.case_id if analysis else None
        if request.url.path.rsplit("/", 1)[-1] in {"token-sessions", "embed-context", "approved-recommendations"}:
            context = self.repository.get_embed_session_context(session_id, utc_now().isoformat())
            return context["case_id"] if context else None
        return None
