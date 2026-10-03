#!/usr/bin/env python3
"""Build the offline documentation pack from docs/*.md.

  python scripts/build_docs.py [--out docs-html] [--pdf]

Writes <out>/index.html, one page per guide, style.css and manual.html (everything in one printable page). With --pdf it also prints
Radio-Remote-Manual.pdf through a headless Chrome/Chromium/Edge when one is found. Needs the "markdown" package (a build-time tool only,
not a runtime dependency of the program).
"""
import argparse
import html
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import markdown
except ImportError:                                                  # pragma: no cover
    sys.exit("pip install markdown   (needed only to build the documentation)")

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

# (file stem, title, one-line description)
SECTIONS = [
    ("Start here", [
        ("INSTALL", "Installation guide", "Flash the Pi, run the installer, first run"),
        ("USER-GUIDE", "User guide", "Operating the radio from a phone, tablet or computer"),
    ]),
    ("Running the system", [
        ("operations", "Operations", "Configuration, services, logs, updating, backup and restore"),
        ("radio-connection", "Radio connection", "USB CAT and audio, the radio's own menu settings"),
        ("04-audio", "Remote audio", "Listening, microphone, levels, troubleshooting"),
        ("tailscale", "Remote access with Tailscale", "Reach the radio from anywhere without opening router ports"),
        ("06-security-remote", "Security and remote access", "Accounts, HTTPS, the safety design"),
        ("troubleshooting", "Troubleshooting", "Symptom by symptom"),
    ]),
    ("Radios", [
        ("08-other-radios", "Other radios", "FTDX10, FTDX101D/MP, FT-710 (experimental)"),
        ("01-capability-matrix", "What each radio supports", "Generated from the radio profiles"),
    ]),
    ("Developer notes", [
        ("05-ui", "User interface notes", "Layouts, meters, behaviour details"),
        ("02-architecture", "Architecture", "How the program is built"),
        ("03-bench-checklist", "Bench checklist", "Tests to run with a real radio"),
        ("bench-results", "Bench results", "What was verified on a real FT-991A"),
        ("07-next-revisions", "Status and roadmap", "What is verified, decided and still open"),
    ]),
]
FLAT = [(s, t, d) for _, items in SECTIONS for (s, t, d) in items]
STEMS = {s for s, _, _ in FLAT}

CSS = """
:root { color-scheme: light dark; --bg:#fff; --fg:#1d2433; --dim:#5b6780; --line:#d9dfeb; --acc:#0a6fb7; --code:#f1f4fa; }
@media (prefers-color-scheme: dark) { :root { --bg:#0e1420; --fg:#e4e9f5; --dim:#97a3bd; --line:#26334d; --acc:#6cb8f0; --code:#18223a; } }
* { box-sizing: border-box; }
body { margin: 0; font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--fg); }
.wrap { max-width: 880px; margin: 0 auto; padding: 24px 18px 80px; }
nav.top { font-size: .9rem; color: var(--dim); margin-bottom: 18px; }
nav.top a { color: var(--acc); text-decoration: none; }
h1 { font-size: 1.9rem; margin: .2em 0 .6em; } h2 { font-size: 1.4rem; margin-top: 1.8em; border-bottom: 1px solid var(--line); padding-bottom: .2em; }
h3 { font-size: 1.12rem; margin-top: 1.4em; }
a { color: var(--acc); }
code { background: var(--code); padding: .1em .35em; border-radius: 4px; font: .9em ui-monospace, Consolas, monospace; }
pre { background: var(--code); padding: 12px 14px; border-radius: 8px; overflow-x: auto; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: .94rem; display: block; overflow-x: auto; }
th, td { border: 1px solid var(--line); padding: 6px 10px; text-align: left; vertical-align: top; }
th { background: var(--code); }
blockquote { border-left: 4px solid var(--acc); margin: 1em 0; padding: .2em 1em; background: var(--code); }
.cards { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); margin: 1em 0 2em; }
.card { border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; text-decoration: none; color: var(--fg); display: block; }
.card:hover { border-color: var(--acc); } .card b { color: var(--acc); display: block; } .card span { color: var(--dim); font-size: .9rem; }
.doc { page-break-before: always; }
@media print { body { font-size: 11pt; } .wrap { max-width: none; padding: 0; } nav.top, .noprint { display: none; } pre, table { page-break-inside: avoid; } a { color: inherit; } }
"""

EXT = ["tables", "fenced_code", "toc", "sane_lists"]


def render(stem: str) -> tuple[str, str]:
    text = (DOCS / f"{stem}.md").read_text(encoding="utf-8")
    md = markdown.Markdown(extensions=EXT, extension_configs={"toc": {"permalink": False}})
    return md.convert(text), md.toc if hasattr(md, "toc") else ""


def fix_links(body: str, single_page: bool) -> str:
    """Turn links to other .md guides into links to the built pages (or to anchors in the one-page manual)."""
    return re.sub(r'href="((?:[^"#:]*/)?([^"#/:]+))\.md(#[^"]*)?"', lambda m: rep_stem(m, single_page), body)


def rep_stem(m, single_page: bool) -> str:
    stem = m.group(2)
    frag = m.group(3) or ""
    if stem not in STEMS:
        return m.group(0)
    return f'href="#doc-{stem}"' if single_page else f'href="{stem}.html{frag}"'


def page(title: str, body: str, back: bool = True) -> str:
    nav = '<nav class="top"><a href="index.html">&larr; Manual contents</a></nav>' if back else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)} - Radio Remote</title><link rel="stylesheet" href="style.css"></head>'
            f'<body><div class="wrap">{nav}{body}</div></body></html>')


def build(out: Path, pdf: bool) -> None:
    if out.exists():
        for p in sorted(out.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
    out.mkdir(parents=True, exist_ok=True)
    (out / "style.css").write_text(CSS, encoding="utf-8")
    parts = []
    for stem, title, _ in FLAT:
        body, _toc = render(stem)
        (out / f"{stem}.html").write_text(page(title, fix_links(body, False)), encoding="utf-8")
        parts.append((stem, title, body))
    cards = []
    for sec, items in SECTIONS:
        cards.append(f"<h2>{html.escape(sec)}</h2><div class='cards'>" + "".join(
            f"<a class='card' href='{s}.html'><b>{html.escape(t)}</b><span>{html.escape(d)}</span></a>" for s, t, d in items) + "</div>")
    index = ("<h1>Radio Remote - manual</h1><p>Web remote control for Yaesu radios on a Raspberry Pi. This manual works offline: it is "
             "installed on the Pi (open <code>/docs/</code> in the app) and shipped inside the installer pack.</p>"
             "<p class='noprint'><a href='manual.html'>Everything on one printable page</a>"
             + (" &middot; <a href='Radio-Remote-Manual.pdf'>PDF</a>" if pdf else "") + "</p>" + "".join(cards))
    (out / "index.html").write_text(page("Manual", index, back=False), encoding="utf-8")
    one = ["<h1>Radio Remote - manual</h1><p class='noprint'>Print this page to PDF for a paper copy.</p><h2>Contents</h2><ol>"]
    one += [f"<li><a href='#doc-{s}'>{html.escape(t)}</a></li>" for s, t, _ in parts] + ["</ol>"]
    for stem, title, body in parts:
        one.append(f"<section class='doc' id='doc-{stem}'>{fix_links(body, True)}</section>")
    (out / "manual.html").write_text(page("Manual (one page)", "".join(one), back=False), encoding="utf-8")
    print(f"wrote {len(parts) + 2} pages to {out}")
    if pdf:
        make_pdf(out)


def make_pdf(out: Path) -> None:
    cands = [os.environ.get("CHROME", ""), shutil.which("chrome") or "", shutil.which("google-chrome") or "",
             shutil.which("chromium") or "", shutil.which("msedge") or "",
             r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
             r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
    exe = next((c for c in cands if c and Path(c).exists()), None)
    if not exe:
        print("no Chrome/Chromium/Edge found: skipping the PDF (open manual.html and print it to PDF instead)")
        return
    target = out / "Radio-Remote-Manual.pdf"
    src = (out / "manual.html").resolve().as_uri()
    subprocess.run([exe, "--headless", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={target}", src],
                   check=False, capture_output=True, timeout=180)
    print("wrote", target, "(%d KB)" % (target.stat().st_size // 1024) if target.exists() else "FAILED")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs-html"))
    ap.add_argument("--pdf", action="store_true")
    a = ap.parse_args()
    build(Path(a.out), a.pdf)
