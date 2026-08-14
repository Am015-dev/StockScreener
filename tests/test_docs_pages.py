"""/limits and /changelog must render real HTML, not raw markdown source.

Both used to serve their content with mimetype="text/markdown" — every
browser shows that as unstyled plain text, headings and bold and pipe
tables rendered as literal '#', '**' and '|---|'. Confirmed on the live
site before fixing (curl -I showed Content-Type: text/markdown on both
routes). mistune now renders /limits' KNOWN_ISSUES.md into real HTML;
/changelog builds its list directly in the template rather than through
markdown, because a commit subject is uncontrolled text that could itself
contain markdown-special characters.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T = tempfile.mkdtemp(prefix="docs-pages-")
os.environ.update(MARKET_DB=T + "/m.db", JOURNAL_DB=T + "/j.db",
                  SCREENER_CACHE_DB=T + "/c.db", SKIP_WARM="1")
os.environ["PUBLISHED_DIR"] = os.path.join(T, "no_published")
os.makedirs(os.environ["PUBLISHED_DIR"], exist_ok=True)
sys.path.insert(0, str(ROOT))

import app                                                       # noqa: E402

c = app.app.test_client()

# ---- /limits: real HTML, no leaked markdown syntax ----
r = c.get("/limits")
assert r.status_code == 200, r.status_code
assert r.content_type.startswith("text/html"), \
    f"must serve HTML, not {r.content_type!r}"
body = r.data.decode()
assert "<h1>" in body or "<h2>" in body, "headings must render as real tags"
assert "<table>" in body, "the balanced/wide-net comparison tables must render"
assert "<strong>" in body, "bold text must render as a real tag"
assert "<code>" in body, "inline code spans must render as a real tag"
# the exact defect this closes: literal markdown characters visible as text
import re
visible = re.sub(r"<[^>]+>", " ", body)          # strip tags, leave text
assert not re.search(r"^#{1,6}\s", visible, re.M), \
    "a literal '#' heading marker leaked into the rendered text"
assert "|---|" not in visible and "| --- |" not in visible, \
    "a literal markdown table separator leaked into the rendered text"
assert "**" not in visible, "a literal '**' leaked into the rendered text"
print("/limits renders real HTML — headings, tables, bold and code spans "
      "as actual tags, no literal markdown syntax visible")

# content survived the rendering change
assert "0.50" in body and "0.41" in body, \
    "the two permutation-test p-values (release_gate.py's own pin) must still appear"
print("the content the release gate pins (both p-values) survived the fix")

# ---- /changelog: a real list, and markdown-special characters in a
# commit subject must render literally, never be reinterpreted ----
real_run = subprocess.run
tricky_subject = "Rename `_credit_for()` to use __slots__ and *args safely"


def fake_run(cmd, **kw):
    if cmd[:2] == ["git", "log"]:
        class R:
            returncode = 0
            stdout = f"2026-08-14\t{tricky_subject}\n2026-08-13\tAn ordinary commit\n"
            stderr = ""
        return R()
    return real_run(cmd, **kw)


subprocess.run = fake_run
try:
    r = c.get("/changelog")
finally:
    subprocess.run = real_run
assert r.status_code == 200, r.status_code
assert r.content_type.startswith("text/html")
body = r.data.decode()
# Jinja-escaped, so the literal backticks/underscores/asterisks show up
# as themselves in the rendered text rather than being turned into <code>
# or <em>/<strong> by a markdown pass over uncontrolled text
assert "_credit_for()" in body, body
assert "__slots__" in body, body
assert "*args" in body, body
assert "<em>slots</em>" not in body and "<code>_credit_for" not in body, \
    "a commit subject's own markdown-special characters must render as " \
    "literal text, never be reinterpreted as formatting"
print("/changelog renders commit subjects as plain escaped text — a "
      "subject containing its own backticks/underscores/asterisks is "
      "never reinterpreted as markdown formatting")

# ---- /changelog: the git-unavailable path still answers cleanly ----
def failing_run(cmd, **kw):
    if cmd[:2] == ["git", "log"]:
        class R:
            returncode = 1
            stdout = ""
            stderr = "fatal: not a git repository"
        return R()
    return real_run(cmd, **kw)


subprocess.run = failing_run
try:
    r = c.get("/changelog")
finally:
    subprocess.run = real_run
assert r.status_code == 503, r.status_code
assert "unavailable" in r.data.decode().lower()
print("git unavailable: /changelog answers 503 with a plain explanation, "
      "not a crash")

print("\nALL DOCS-PAGES TESTS PASSED")
