import os

VALID_PROVIDERS = ("openai", "ollama")


def _build(name):
    if name == "openai":
        from .openai_provider import OpenAIDiarizedProvider
        return OpenAIDiarizedProvider()

    if name == "ollama":
        from .ollama_provider import OllamaChunkedProvider
        return OllamaChunkedProvider()

    raise ValueError(
        f"Unknown STT_PROVIDER: {name}. "
        f"Valid: {', '.join(VALID_PROVIDERS)}"
    )


def resolve_stt_chain():
    """
    Ordered provider chain.

    STT_PROVIDER=openai|ollama  -> forced, single provider
    unset (auto)                -> openai first when a key exists,
                                   ollama always as fallback
    """
    explicit = (os.getenv("STT_PROVIDER") or "").strip().lower()

    if explicit:
        return [explicit]

    if os.getenv("OPENAI_API_KEY"):
        return ["openai", "ollama"]

    return ["ollama"]


def transcribe_with_fallback(audio_path: str):
    """
    Try each provider in order; return
    (segments, provider_name) from the first success.
    """
    errors = []

    for name in dict.fromkeys(resolve_stt_chain()):
        try:
            provider = _build(name)

            print(f"[STT] Using provider: {name}")

            return provider.transcribe(audio_path), name

        except Exception as e:
            print(f"[STT] Provider '{name}' failed: {e!r}")

            errors.append(f"{name}: {e!r}")

    raise RuntimeError(
        "All STT providers failed -> " + "; ".join(errors)
    )
