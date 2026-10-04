"""Secret-handling helpers. Pure Python: no Streamlit import, no global state.

RULES THIS PROJECT FOLLOWS FOR THE OPENROUTER KEY
  1. The key lives only in the visitor's own Streamlit session (a widget value).
  2. It is passed explicitly to the LLM client; it is never written to os.environ,
     a module global, a file, a log line, the graph state, or any st.cache_* function.
  3. It is flushed from the session after every use (unless the visitor opts to keep it), on EVERY
     code path (success, validation failure, exception), and any error text is redacted before display.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional, Union
from urllib.parse import quote, quote_plus

from securecare.textsafe import clean_line

# Whole token after 'sk-' up to whitespace / quotes / delimiters, so keys containing + / = % . etc.
# do not leak a tail. The lookbehind keeps ordinary words ('risk-based', 'task-force') intact.
_KEY_PATTERN = re.compile(r"(?<![A-Za-z0-9])sk-[^\s\"'`<>(){}\[\]|\\,;]{6,}", re.IGNORECASE)
_STRICT_KEY = re.compile(r"sk-or-[A-Za-z0-9_\-]{16,200}")
MIN_EXACT_SECRET_LEN = 4


def looks_like_openrouter_key(value: str) -> bool:
    """Strict shape check (OpenRouter keys look like 'sk-or-v1-<hex>'): ASCII letters, digits, '_' and '-'
    only, no whitespace or control characters. It does NOT verify the key."""
    return isinstance(value, str) and _STRICT_KEY.fullmatch(value) is not None


def _variants(secret: str) -> list[str]:
    """The secret as it may appear in an error message: raw, repr/JSON-escaped, URL-encoded."""
    forms = {secret, repr(secret)[1:-1], json.dumps(secret)[1:-1], quote(secret, safe=""), quote_plus(secret),
             quote(secret)}
    return sorted((f for f in forms if len(f) >= MIN_EXACT_SECRET_LEN), key=len, reverse=True)


def redact_exact(text: str, secret: Optional[str]) -> str:
    """Mask an EXACT secret value (and its repr / URL-encoded forms), whatever shape the secret has."""
    text = text or ""
    secret = secret.strip() if isinstance(secret, str) else ""
    if len(secret) < MIN_EXACT_SECRET_LEN:
        return text
    for form in _variants(secret):
        text = text.replace(form, "***")
    return text


def redact_secrets(text: str, secrets: Union[None, str, Iterable[Optional[str]]] = None) -> str:
    """Mask anything that looks like an API key before text is shown or stored.

    `secrets` (optional): exact secret value(s) the caller still has in hand; they are removed first,
    in any form (raw, repr, URL-encoded), even if they do not look like 'sk-...' keys."""
    text = "" if text is None else str(text)
    if isinstance(secrets, str):
        secrets = [secrets]
    for secret in secrets or ():
        text = redact_exact(text, secret)
    return _KEY_PATTERN.sub("sk-***", text)


def secret_from_client(client: Any) -> Optional[str]:
    """Best-effort: read the API key out of an LLM client object so error text can be exact-redacted.
    Understands LangChain's SecretStr ('openai_api_key') and plain 'api_key' attributes."""
    for attr in ("openai_api_key", "api_key"):
        value = getattr(client, attr, None)
        if value is None:
            continue
        getter = getattr(value, "get_secret_value", None)
        try:
            value = getter() if callable(getter) else value
        except Exception:  # noqa: BLE001
            continue
        if isinstance(value, str) and value:
            return value
    return None


def describe_exception(exc: BaseException, secret: Optional[str] = None, max_len: int = 250) -> str:
    """'ErrorType: message' with hidden characters removed, any API key redacted (both the exact value the
    caller still holds and anything shaped like a key) and the length capped. Safe to store; the UI adds
    markdown escaping on top (ui/safe.py)."""
    raw = f"{type(exc).__name__}: {exc}"
    text = redact_secrets(clean_line(redact_secrets(raw, secret)), secret)
    return text[:max_len]
