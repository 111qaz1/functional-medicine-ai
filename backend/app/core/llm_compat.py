from __future__ import annotations


def is_kimi_k2_model(model: str | None) -> bool:
    normalized = (model or "").strip().lower()
    return normalized.startswith("kimi-k2.")


def chat_generation_options(
    *,
    model: str | None,
    temperature: float | None = None,
    thinking_type: str | None = None,
) -> dict[str, object]:
    """Return provider-safe Chat Completions generation parameters.

    Kimi K2.5/K2.6 reject arbitrary temperatures and expose their reasoning
    switch through the top-level ``thinking`` extension. Other compatible
    providers keep the configured temperature and do not receive Kimi-only
    fields.
    """

    if is_kimi_k2_model(model):
        options: dict[str, object] = {}
        if thinking_type in {"enabled", "disabled"}:
            options["thinking"] = {"type": thinking_type}
        return options

    if temperature is None:
        return {}
    return {"temperature": temperature}
