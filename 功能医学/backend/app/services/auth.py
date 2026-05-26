from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.domain.models import DoctorAccount, DoctorRole, SessionRecord
from app.repositories.in_memory import LocalRepository


SESSION_DAYS = 14
PASSWORD_ITERATIONS = 210_000


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class AuthSession:
    doctor: DoctorAccount
    session: SessionRecord


class AuthService:
    def __init__(self, repository: LocalRepository) -> None:
        self.repository = repository

    def register(self, *, username: str, password: str, display_name: str | None = None) -> DoctorAccount:
        normalized_username = self._normalize_username(username)
        if not normalized_username:
            raise ValueError("请输入医生账号。")
        if len(password or "") < 6:
            raise ValueError("密码至少需要 6 位。")
        if self.repository.get_doctor_by_username(normalized_username):
            raise ValueError("该医生账号已存在，请直接登录。")

        role = DoctorRole.admin if self.repository.count_doctors() == 0 else DoctorRole.doctor
        doctor = DoctorAccount(
            id=f"doctor_{uuid.uuid4().hex[:12]}",
            username=normalized_username,
            display_name=(display_name or "").strip() or normalized_username,
            password_hash=self._hash_password(password),
            role=role,
            enabled=True,
        )
        return self.repository.save_doctor(doctor)

    def login(self, *, username: str, password: str) -> AuthSession:
        normalized_username = self._normalize_username(username)
        doctor = self.repository.get_doctor_by_username(normalized_username)
        if not doctor or not self._verify_password(password, doctor.password_hash):
            raise ValueError("账号或密码不正确。")
        if not doctor.enabled:
            raise ValueError("该医生账号已停用。")

        session = SessionRecord(
            id=f"sess_{secrets.token_urlsafe(32)}",
            doctor_id=doctor.id,
            expires_at=utc_now() + timedelta(days=SESSION_DAYS),
        )
        self.repository.save_session(session)
        return AuthSession(doctor=doctor, session=session)

    def get_doctor_for_session(self, session_id: str | None) -> DoctorAccount | None:
        if not session_id:
            return None
        session = self.repository.get_session(session_id)
        if not session:
            return None
        if session.expires_at <= utc_now():
            self.repository.delete_session(session.id)
            return None
        doctor = self.repository.get_doctor(session.doctor_id)
        if not doctor or not doctor.enabled:
            self.repository.delete_session(session.id)
            return None
        return doctor

    def logout(self, session_id: str | None) -> None:
        if session_id:
            self.repository.delete_session(session_id)

    def cleanup_expired_sessions(self) -> None:
        self.repository.delete_expired_sessions(utc_now().isoformat())

    def _hash_password(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
        return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"

    def _verify_password(self, password: str, password_hash: str) -> bool:
        try:
            algorithm, iterations, salt_hex, digest_hex = password_hash.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            expected = bytes.fromhex(digest_hex)
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                bytes.fromhex(salt_hex),
                int(iterations),
            )
            return hmac.compare_digest(digest, expected)
        except (TypeError, ValueError):
            return False

    def _normalize_username(self, username: str) -> str:
        return (username or "").strip().lower()
