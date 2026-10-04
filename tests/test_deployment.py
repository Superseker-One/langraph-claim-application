"""S-7 / S-11: deployment config and supply-chain files, plus the owner's branding."""
import re
import tomllib
from pathlib import Path

from securecare.config import APP_TITLE

ROOT = Path(__file__).resolve().parent.parent


def test_streamlit_config_hides_error_details_and_keeps_xsrf_protection():
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["client"]["showErrorDetails"] == "none"
    assert config["server"]["enableXsrfProtection"] is True
    assert config["server"]["headless"] is True and config["client"]["toolbarMode"] == "minimal"


def test_devcontainer_does_not_disable_cors_or_xsrf_and_does_not_install_unpinned_streamlit():
    text = (ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8")
    assert "enableXsrfProtection" not in text and "enableCORS" not in text
    assert "install --user streamlit" not in text and "pip3 install streamlit" not in text
    assert "pip3 install --user -r requirements.txt" in text                 # requirements.txt governs
    assert '"server": "streamlit run app.py"' in text


def test_requirements_declare_every_direct_import():
    names = {re.split(r"[<>=!~ ]", line)[0].lower().replace("_", "-")
             for line in (ROOT / "requirements.txt").read_text().splitlines() if line.strip()}
    assert {"streamlit", "langgraph", "langchain-core", "langchain-openai", "pydantic", "typing-extensions"} <= names


def test_lock_file_pins_exact_versions_for_the_runtime_packages_only():
    lines = [l for l in (ROOT / "requirements-lock.txt").read_text().splitlines() if l and not l.startswith("#")]
    assert lines and all(re.fullmatch(r"[A-Za-z0-9_.\-]+==[0-9][A-Za-z0-9_.\-+!]*", l) for l in lines), lines
    pinned = {l.split("==")[0].lower().replace("_", "-") for l in lines}
    assert {"streamlit", "langgraph", "langchain-openai", "pydantic", "typing-extensions"} <= pinned
    assert not ({"pytest", "pluggy", "iniconfig"} & pinned)


def test_readme_documents_python_version_lock_file_and_known_limitations():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "3.14.4" in readme and "requirements-lock.txt" in readme
    assert "## Known limitations" in readme and "## Security hardening" in readme
    for phrase in ("policy-number enumeration", "ledger", "self-declared", "mlc", "mock policy store"):
        assert phrase in readme.lower(), phrase
    assert "guaranteed" not in readme.split("## Security hardening")[1].split("\n## ")[0].lower()   # no overclaiming


def test_branding_is_preserved():
    assert APP_TITLE == "SekureCare Claims Assistant by Supersek Labs"
    assert "SekureCare" in (ROOT / "securecare" / "agents" / "communicator.py").read_text(encoding="utf-8")
