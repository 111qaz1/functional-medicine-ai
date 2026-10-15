from app.services.joolun_java_auth import reject


def authorize_request(request, session_id, doctor):
    container = request.app.state.container
    if container.settings.joolun_auth_mode == "legacy_hmac":
        return doctor
    if request.headers.get("X-FM-Entry") == "admin" and not doctor.username.startswith(f"external:{container.settings.joolun_issuer}:"):
        return doctor
    if not doctor.username.startswith("external:"):
        if request.headers.get("X-FM-Entry") != "admin":
            reject()
        return doctor
    service = container.joolun_java_auth
    case_id = service.request_case(request, session_id)
    trusted = service.authorize(session_id, request.cookies.get("Admin-Token"), case_id)
    binding = container.repository.get_joolun_login_binding(session_id)
    if case_id and case_id != binding["case_id"]:
        reject(403, "EMBED_CASE_SCOPE_REJECTED")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and (
        not case_id or binding.get("read_only") or container.repository.case_has_approved_report(case_id)
    ):
        from app.api.v2.problems import V2ApiError
        raise V2ApiError(status=403, code="EMBED_CASE_READ_ONLY", title="Case is read-only",
                         detail="已批准病例仅可查看和下载，不能修改原资料或分析。")
    return trusted
