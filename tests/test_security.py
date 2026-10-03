from securecare.agents.llm import build_llm
from securecare.config import MODEL_OPTIONS, OPENROUTER_BASE_URL
from securecare.formatting import format_inr
from securecare.security import looks_like_openrouter_key, redact_secrets


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
