"""Static checks on the front-end source (no browser available in the test suite).

Regression: `sheet.onclick = (e) => e.target === sheet && close();` returns `false` for clicks inside the sheet, and an
`onclick` PROPERTY handler returning false cancels the click's default action. Result: every Save button (form submit) and
checkbox in the admin sheet silently did nothing. Found on a real install, fixed in admin.js.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"

# DOM event-handler *properties* (onclick = ...) with an expression body that can evaluate to false.
RISKY = re.compile(r"\.on[a-z]+\s*=\s*(async\s*)?\(?[a-zA-Z_, ]*\)?\s*=>\s*[^{\n]*(&&|\|\|)")


def test_no_expression_bodied_event_properties_that_can_return_false():
    offenders = []
    for path in SRC.rglob("*.js"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if RISKY.search(line):
                offenders.append(f"{path.relative_to(SRC.parent)}:{n}: {line.strip()}")
    assert not offenders, "use a block body or addEventListener so nothing is returned:\n" + "\n".join(offenders)


def test_sheet_backdrop_uses_addeventlistener():
    text = (SRC / "pages" / "admin.js").read_text(encoding="utf-8")
    assert 'sheet.addEventListener("click"' in text
    assert "sheet.onclick" not in text


def test_every_module_imported_by_the_service_worker_shell_exists():
    sw = (SRC.parent / "sw.js").read_text(encoding="utf-8")
    for rel in re.findall(r'"(/src/[^"]+\.js)"', sw):
        assert (SRC.parent / rel.lstrip("/")).exists(), rel
