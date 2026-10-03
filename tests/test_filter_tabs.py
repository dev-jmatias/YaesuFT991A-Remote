from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"


def test_filter_panel_is_tabbed_by_group():
    text = (SRC / "components" / "controls.js").read_text(encoding="utf-8")
    assert 'class="filters tabbed"' in text and "subtabs" in text
    assert "sections[name].hidden = name !== g" in text                 # only the selected group is shown
    assert 'localStorage.getItem(KEY)' in text and "catch" in text      # last tab remembered, storage failure tolerated
    assert "rr.filterTab" in text
