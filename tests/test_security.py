import json
from urllib.parse import quote, quote_plus

from securecare.agents.llm import build_llm
from securecare.config import LLM_MAX_TOKENS, MODEL_OPTIONS, OPENROUTER_BASE_URL
from securecare.formatting import format_inr
from securecare.security import (
    describe_exception, looks_like_openrouter_key, redact_exact, redact_secrets, secret_from_client,
)


def test_key_shape_check():
    assert looks_like_openrouter_key("sk-or-v1-" + "a" * 30)
    assert not looks_like_openrouter_key("sk-" + "a" * 30)   # a plain OpenAI-style key is not accepted
    assert not looks_like_openrouter_key("hello") and not looks_like_openrouter_key("")


def test_redaction():
    assert redact_secrets("bad key sk-or-v1-abcdef123456789 used") == "bad key sk-*** used"


def test_inr_format():
    assert format_inr(127500) == "₹1,27,500"
    assert format_inr(999) == "₹999"
    assert format_inr(10000000) == "₹1,00,00,000"


def test_llm_client_targets_openrouter():
    llm = build_llm("sk-or-v1-" + "a" * 30, MODEL_OPTIONS[0])      # builds the client; no network call
    assert llm.openai_api_base == OPENROUTER_BASE_URL
    assert llm.model_name == MODEL_OPTIONS[0] and "/" in llm.model_name   # OpenRouter "vendor/model" slug


# ---------------------------------------------------------------- S-6: strict key shape
import pytest

FAKE_KEY = "sk-or-v1-" + "A1b2C3d4" * 4


@pytest.mark.parametrize("value", [
    FAKE_KEY, "sk-or-v1-" + "a" * 64, "sk-or-v1-" + "a" * 30, "sk-or-" + "A_b-" * 8,
])
def test_realistic_keys_are_accepted(value):
    assert looks_like_openrouter_key(value)


@pytest.mark.parametrize("value", [
    "", "hello", "sk-or-v1", "sk-or-short", "sk-" + "a" * 30, " " + FAKE_KEY, FAKE_KEY + " ", FAKE_KEY + "\n",
    "sk-or-v1-" + "a" * 16 + "\tb" + "c" * 16,                       # tab inside
    "sk-or-v1-" + "a" * 16 + "\x00" + "c" * 16,                      # control char
    "sk-or-v1-" + "a" * 16 + "é" + "c" * 16,                         # non-ASCII
    "sk-or-v1-" + "a" * 16 + "​" + "c" * 16,                    # zero-width
    "sk-or-v1-" + "a" * 16 + " " + "c" * 16,                         # space inside
    "sk-or-v1-" + "a" * 16 + "+/=" + "c" * 16,                       # base64-ish characters are not valid here
    "sk-or-" + "a" * 201,                                            # too long
    "SK-OR-v1-" + "a" * 30, None, 12345,
])
def test_malformed_keys_are_rejected(value):
    assert not looks_like_openrouter_key(value)


# ---------------------------------------------------------------- S-6: robust redaction
@pytest.mark.parametrize("secret", [
    "sk-or-v1-abc+def/ghi=jkl==", "sk-proj-AbC_dEf-123.456", "SK-OR-V1-ABCDEF123456", "sk-or-v1-%2Fabc%3D",
])
def test_odd_character_keys_do_not_leak_a_tail(secret):
    text = f"Incorrect API key provided: {secret}. You can find your key at settings"
    out = redact_secrets(text)
    assert "sk-***" in out
    assert secret[8:] not in out and "abc+def" not in out and "ghi=" not in out


def test_redaction_does_not_mangle_ordinary_words():
    text = "A risk-assessment, task-management and disk-partitioning approach, plus ask-me"
    assert redact_secrets(text) == text


def test_redaction_needs_a_boundary_before_sk():
    assert redact_secrets("key=sk-or-v1-abcdef123456") == "key=sk-***"
    assert redact_secrets('{"key":"sk-or-v1-abcdef123456"}') == '{"key":"sk-***"}'
    assert redact_secrets("(sk-or-v1-abcdef123456)") == "(sk-***)"


def test_exact_secret_redaction_covers_non_sk_formats_and_encodings():
    secret = "AIza Sy/Odd+Key=value?1"                      # nothing like 'sk-...'
    msg = (f"auth failed for {secret}; repr={secret!r}; url=https://x/?k={quote(secret, safe='')}; "
           f"plus=?k={quote_plus(secret)}; json={json.dumps(secret)}")
    out = redact_secrets(msg, secret)
    assert "Sy/Odd" not in out and "Sy%2FOdd" not in out and "Sy+Odd" not in out
    assert redact_exact("my secret is hunter2hunter2", "hunter2hunter2") == "my secret is ***"
    assert redact_exact("short", "ab") == "short"             # too short to be a secret: leave text alone
    assert redact_secrets("nothing here", None) == "nothing here"


def test_describe_exception_redacts_exact_and_shaped_keys_and_caps_length():
    secret = "plain-secret-without-prefix-123"
    exc = RuntimeError(f"bad {secret} and sk-or-v1-{'z' * 40} " + "x" * 1000)
    text = describe_exception(exc, secret, max_len=120)
    assert secret not in text and "sk-or-v1" not in text and text.startswith("RuntimeError: bad ***")
    assert len(text) <= 120
    assert "​" not in describe_exception(RuntimeError("a​b\x00c"))


def test_secret_from_client_reads_langchain_secretstr_and_plain_attributes():
    llm = build_llm(FAKE_KEY, MODEL_OPTIONS[0])
    assert secret_from_client(llm) == FAKE_KEY

    class Holder:
        api_key = "plain-key-value"
    assert secret_from_client(Holder()) == "plain-key-value" and secret_from_client(object()) is None


def test_llm_client_has_an_output_token_cap():
    llm = build_llm(FAKE_KEY, MODEL_OPTIONS[0])
    assert llm.max_tokens == LLM_MAX_TOKENS and 0 < LLM_MAX_TOKENS <= 800
