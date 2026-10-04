"""Supersek brand layer: theme palette, logo/footer, branded graph, and the 'constants only' rule."""
import inspect
import tomllib
from pathlib import Path

from streamlit.testing.v1 import AppTest

from securecare.graph import build_claim_graph
from securecare.graph.visualize import graph_to_dot
from securecare.ui import brand

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app.py")


def _theme() -> dict:
    return tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text())["theme"]


def test_theme_uses_the_supersek_palette():
    t = _theme()
    assert t["base"] == "dark"
    assert t["backgroundColor"].upper() == "#1F2223"            # Dark Background
    assert t["secondaryBackgroundColor"].upper() == "#393E40"   # Dark Surface
    assert t["textColor"].upper() == "#DCDAD7"                  # Light Text
    assert t["primaryColor"].upper() == "#7874EA"               # Mid Purple (visible on dark)
    assert t["font"].startswith("Inter:") and "wght@400;500;600;700;800" in t["font"]
    assert t["codeFont"].startswith("JetBrains Mono:")


def test_brand_constants_match_the_template():
    assert (brand.PRIMARY_PURPLE, brand.MID_PURPLE) == ("#221CD2", "#7874EA")
    assert (brand.DARK_BG, brand.DARK_SURFACE) == ("#1F2223", "#393E40")
    assert (brand.LIGHT_TEXT, brand.MUTED_TEXT, brand.SUBTLE_TEXT) == ("#DCDAD7", "#A7A29A", "#8A8379")
    assert brand.BRAND_DOMAIN == "supersek.com"


def test_signature_gradient_and_uppercase_headings_are_in_the_css():
    css = brand._CSS
    assert "linear-gradient(90deg, #DCDAD7, #7874EA, #221CD2, #7874EA, #DCDAD7)" in css
    assert "text-transform: uppercase" in css and "letter-spacing: 0.05em" in css
    assert "font-weight: 800" in css and "letter-spacing: -0.01em" in css          # the logo


def test_app_shows_logo_title_and_footer_without_errors():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception and not at.warning
    html = " ".join(m.value for m in at.markdown)
    assert "ss-logo" in html and "SUPERSEK" in html and "supersek.com" in html
    assert "deep-dive" not in html.lower()                         # the consulting tagline is not shown
    assert any("SekureCare" in t.value for t in at.title)


def test_raw_html_is_confined_to_the_brand_module_and_takes_no_input():
    offenders = [str(p.relative_to(ROOT)) for p in (ROOT / "securecare").rglob("*.py")
                 if "unsafe_allow_html" in p.read_text() and p.name != "brand.py"]
    offenders += [name for name in ("app.py",) if "unsafe_allow_html" in (ROOT / name).read_text()]
    assert offenders == [], f"raw HTML outside ui/brand.py: {offenders}"
    for fn in (brand.apply_brand, brand.render_brand_header, brand.render_brand_footer):
        assert not inspect.signature(fn).parameters, f"{fn.__name__} must not accept (user) input"


def test_workflow_graph_is_drawn_in_brand_colours():
    dot = graph_to_dot(build_claim_graph(None))
    for colour in ("#221CD2", "#7874EA", "#393E40", "#DCDAD7"):
        assert colour in dot
    assert "#EEF3FF" not in dot                                  # the old light-blue fill is gone


def test_favicon_exists_and_is_a_small_png():
    from PIL import Image
    icon = ROOT / "assets" / "favicon.png"
    assert icon.is_file()
    with Image.open(icon) as img:
        assert img.format == "PNG" and max(img.size) <= 256


def test_consulting_tagline_is_nowhere_in_the_app_source():
    for path in [ROOT / "app.py", *(ROOT / "securecare").rglob("*.py"), ROOT / ".streamlit" / "config.toml"]:
        assert "deep-dive security consulting" not in path.read_text().lower(), path
