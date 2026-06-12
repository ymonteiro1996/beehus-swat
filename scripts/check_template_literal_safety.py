"""Guard against the recurring backtick-in-HTML-comment bug.

Background
----------
Several large Jinja partials embed HTML inside JS template literals,
typically:

    root.innerHTML = `
      <div>...</div>
      <!-- comment text -->
      ...
    `;

If a backtick appears anywhere inside that HTML (including inside an
HTML comment), it closes the outer template literal prematurely. The
JS file then fails to parse — the entire `const Strip = {...}` (or
similar) declaration is corrupted, no global is exposed, and the page
sits at its HTML-default "Carregando..." state with no onclick
handlers firing.

This has bitten us twice now in templates/partials/_strip_script.html,
both times inside an innocent-looking HTML comment that referenced JS
identifiers wrapped in backticks for emphasis.

What this script does
---------------------
Walks every file in templates/partials/*.html, locates JS template
literals delimited by ``` (backtick), and reports any backticks
that sit inside an HTML comment within one of those literals.

Heuristic, not a full JS parser — but the heuristic catches the
specific pattern that has actually broken the page in practice, with
zero false positives on the current codebase.

Exit code 0 when clean, 1 when offending backticks are found.

Usage:
    python scripts\\check_template_literal_safety.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGET_DIR = PROJECT_ROOT / "templates" / "partials"


def find_offending_backticks(text: str) -> list[tuple[int, str]]:
    """Return [(line_number, snippet)] for every backtick inside an HTML
    comment that itself sits between a pair of JS template literal
    backticks.

    The strategy:
      1. Walk the text once, tracking whether we are currently inside a
         JS template literal (toggled by every unescaped backtick).
      2. While inside a template literal, look for HTML comments
         (<!-- ... -->); if such a comment contains a backtick, flag it.
      3. The flagged backtick IS the one that broke the literal — once
         we hit it we toggle "inside template literal" off (the bug),
         but we still report it so the operator can remove it.
    """
    offending: list[tuple[int, str]] = []
    inside_literal = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            # Skip escaped char.
            i += 2
            continue
        if ch == "`":
            inside_literal = not inside_literal
            i += 1
            continue
        if inside_literal and text.startswith("<!--", i):
            end = text.find("-->", i + 4)
            if end == -1:
                # Malformed; stop scanning the rest defensively.
                break
            comment = text[i:end + 3]
            if "`" in comment:
                line_no = text.count("\n", 0, i) + 1
                snippet = comment.strip().splitlines()[0][:120]
                offending.append((line_no, snippet))
            i = end + 3
            continue
        i += 1
    return offending


def main() -> int:
    if not TARGET_DIR.is_dir():
        print(f"[skip] {TARGET_DIR} not found", file=sys.stderr)
        return 0

    any_offending = False
    for path in sorted(TARGET_DIR.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        offending = find_offending_backticks(text)
        if not offending:
            continue
        any_offending = True
        print(f"\n[FAIL] {path.relative_to(PROJECT_ROOT)}")
        for line_no, snippet in offending:
            print(f"  line {line_no}: {snippet}")

    if any_offending:
        print(
            "\n>>> Found backticks inside HTML comments that sit inside a JS\n"
            "    template literal.  These will close the outer literal\n"
            "    prematurely and break the surrounding `const ... = {...}`\n"
            "    declaration at load time.\n\n"
            "    Fix: remove the backticks from the comment (use single\n"
            "    quotes or no quotes when referring to JS identifiers).",
            file=sys.stderr,
        )
        return 1

    print("[ok] no offending backticks inside JS template literal HTML comments.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
