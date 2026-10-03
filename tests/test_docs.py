"""Documentation that is generated or must stay consistent with the program."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gen_capability_doc as gen  # noqa: E402


def test_capability_page_is_up_to_date():
    assert gen.main(["--check"]) == 0, "run: python tools/gen_capability_doc.py"


def test_capability_page_says_what_it_should():
    text = gen.render()
    assert "FT-991A" in text and "FTDX10" in text and "FT-710" in text
    assert "REQUIRES VERIFICATION" not in text and "I will" not in text         # no first-person design-phase text
    row = next(line for line in text.splitlines() if line.startswith("| PTT (hold to transmit)"))
    assert row.split("|")[2].strip() == "works" and "from the manual" in row        # tested on the FT-991A, written from the manual for the rest
    tune = next(line for line in text.splitlines() if line.startswith("| TUNE"))
    assert tune.split("|")[2].strip() == "works"
    quick = next(line for line in text.splitlines() if line.startswith("| Quick split"))
    assert "untested" in quick.split("|")[2]


def test_every_manual_page_exists_and_is_listed():
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_docs
    for stem, title, desc in build_docs.FLAT:
        assert (ROOT / "docs" / f"{stem}.md").exists(), stem
        assert title and desc
    listed = {s for s, _, _ in build_docs.FLAT}
    on_disk = {p.stem for p in (ROOT / "docs").glob("*.md")}
    assert on_disk == listed, f"docs not in the manual: {on_disk - listed}; listed but missing: {listed - on_disk}"


def test_links_between_documents_point_at_real_files():
    bad = []
    for md in [ROOT / "README.md", ROOT / "image" / "README.md", *(ROOT / "docs").glob("*.md")]:
        for target in re.findall(r"\]\(((?!https?:|#|mailto:)[^)\s]+)\)", md.read_text(encoding="utf-8")):
            path = target.split("#")[0]
            if path and not (md.parent / path).resolve().exists():
                bad.append(f"{md.relative_to(ROOT)} -> {target}")
    assert not bad, bad
