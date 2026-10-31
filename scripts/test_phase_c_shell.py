# -*- coding: utf-8 -*-
"""Phase C static smoke: shell markup + assets present."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
html = (ROOT / "web" / "index.html").read_text(encoding="utf-8", errors="replace")
css = (ROOT / "web" / "css" / "style.css").read_text(encoding="utf-8", errors="replace")
js = (ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8", errors="replace")

checks = [
    ("home view", 'id="view-home"' in html),
    ("workspace view", 'id="view-workspace"' in html),
    ("top nav", 'class="top-nav"' in html),
    ("side rail", 'class="side-rail"' in html),
    ("module grid", 'id="moduleGrid"' in html),
    ("quick prompts", 'id="chatQuickPrompts"' in html),
    ("theme toggle", 'id="themeToggleBtn"' in html),
    ("reef css", "theme-reef" in css or "--rail:" in css),
    ("router js", "initHubShell" in js),
    ("hash gene route", "#/gene/" in js),
    ("md local links", "md-a-local" in js or "#/" in js),
    ("closed shell", "<!-- /shell -->" in html or "/shell-main" in html),
]

fails = []
for name, ok in checks:
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        fails.append(name)
print(f"\nSUMMARY pass={len(checks)-len(fails)} fail={len(fails)}")
raise SystemExit(1 if fails else 0)
