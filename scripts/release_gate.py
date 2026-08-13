"""Audit the DEPLOYED site in a real browser, and fail loudly if it lies.

Why this exists: every serious defect this project shipped was invisible
to curl and obvious in a browser. The page that hid itself behind
display:none served perfectly valid HTML; the JS that painted a stale
credit standing under a fresh ticker returned 200; "deployed" was
claimed while Render was still building the previous push. The pattern
was always the same — the code was checked, the build was not.

So this reads the build. It waits until the site reports it is running
the expected commit, renders the pages at phone width in headless
Chromium, exercises the check flow by pressing the actual button, and
asserts the properties that have actually broken before. It knows
nothing about intentions; a claim of "fixed" that this script does not
confirm is a claim, not a fix.

Usage:
    python scripts/release_gate.py --url https://... [--commit SHA]

Exit 0 only if every check passes. Each failure prints what a reader
would have seen.
"""
import argparse
import json
import re
import sys
import time
import urllib.request

FAILS: list[str] = []
PASSES: list[str] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    if ok:
        PASSES.append(name)
        print(f"  ok   {name}")
    else:
        FAILS.append(f"{name}{' — ' + detail if detail else ''}")
        print(f"  FAIL {name}{' — ' + detail if detail else ''}")


def get_json(url: str, timeout: float = 60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def wait_for_build(base: str, commit: str | None, minutes: float) -> dict:
    """Poll /published until the running build is the expected commit.

    Render deploys take minutes after a push, and free instances cold
    start; auditing before the new build is live audits the OLD build
    and calls it the new one — which is precisely the mistake this
    script exists to prevent.
    """
    deadline = time.time() + minutes * 60
    last = {}
    while time.time() < deadline:
        try:
            last = get_json(f"{base}/published")
            build = last.get("build")
            if not commit or (build and commit.startswith(build[:12])
                              or (build or "").startswith(commit[:12])):
                return last
            print(f"  live build {str(build)[:12]}, want {commit[:12]} — waiting")
        except Exception as e:
            print(f"  site not answering yet ({type(e).__name__}) — waiting")
        time.sleep(30)
    return last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--commit", default=None,
                    help="git SHA the deploy should be running")
    ap.add_argument("--wait-minutes", type=float, default=12)
    a = ap.parse_args()
    base = a.url.rstrip("/")

    print(f"waiting for {base} to run {a.commit or '(any build)'}")
    pub = wait_for_build(base, a.commit, a.wait_minutes)
    if a.commit:
        build = str(pub.get("build") or "")
        check(bool(build) and (build.startswith(a.commit[:12])
                               or a.commit.startswith(build[:12])),
              "the expected commit is what is actually running",
              f"live={build[:12] or 'unknown'} want={a.commit[:12]}")
        if FAILS:
            # everything after this would audit the wrong build
            print("\nGATE FAILED — the deploy never arrived")
            return 1

    # A deploy restarts the instance, and the books load in background
    # threads over the following minute or two. Sampling them exactly once
    # at boot failed the first live gate run on "credit: 0" while the
    # browser checks thirty seconds later found the book loaded and the
    # reports rendering. Empty-at-boot is warm-up; empty after a grace
    # period is an outage.
    loaded = pub.get("loaded") or {}
    grace = time.time() + 240
    while time.time() < grace and (loaded.get("prices", 0) <= 100
                                   or loaded.get("credit", 0) < 5):
        print(f"  books still warming ({loaded}) — waiting")
        time.sleep(20)
        try:
            loaded = (get_json(f"{base}/published") or {}).get("loaded") or {}
        except Exception:
            pass
    check(loaded.get("prices", 0) > 100, "price book is loaded",
          str(loaded))
    check(loaded.get("credit", 0) >= 5, "credit book is loaded", str(loaded))

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 390, "height": 844})
        js_errors: list[str] = []
        pg.on("pageerror", lambda e: js_errors.append(str(e)))

        # ---- the front page, as a first-time phone visitor ----
        pg.goto(base + "/", wait_until="load", timeout=90000)
        pg.wait_for_timeout(1500)
        body = pg.inner_text("body")

        check(pg.locator("#main").is_visible(),
              "the page content is visible without answering anything")
        check(pg.locator("#ckTicker").is_visible(),
              "the pre-trade check is on screen")
        check(pg.locator("#crTicker").is_visible(),
              "the credit lookup is on screen")
        check("Closest to the setup today" not in body,
              "no trade is recommended")
        # The refutation lives inside the collapsed pattern section, so a
        # reader meets it exactly when they meet the list it refutes. The
        # gate does what the reader does: checks the summary line is
        # visible, opens it, and checks the sentence is actually there —
        # inner_text of a closed <details> is empty, which is also why an
        # HTML grep is not evidence of anything here.
        check("no recommendation" in body,
              "the pattern section says up front it recommends nothing")
        try:
            pg.locator("details summary").first.click()
            pg.wait_for_timeout(300)
            check("no better than random entry" in pg.inner_text("details"),
                  "opening the pattern list reveals its refutation")
        except Exception as e:
            check(False, "opening the pattern list reveals its refutation",
                  type(e).__name__)
        n_credit = pg.locator('a[href^="/credit/"]').count()
        check(n_credit >= 4, "credit reports are one click away",
              f"{n_credit} links")
        for junk in ("undefined", "NaN", "[object", "{{"):
            check(junk not in body, f"no leaked '{junk}' on the page")

        # ---- the check flow, by pressing the actual button ----
        pg.fill("#ckTicker", "AAPL")
        pg.click("#ckGo")
        try:
            pg.wait_for_function(
                "() => { const o = document.getElementById('ckOut');"
                " return o && o.innerText.length > 80"
                " && !o.innerText.includes('Checking'); }",
                timeout=45000)
            out = pg.inner_text("#ckOut")
            check(True, "the check answers")
            check(not re.search(r"undefined|NaN|\[object", out),
                  "the check output is clean", out[:120])
        except Exception as e:
            check(False, "the check answers", type(e).__name__)

        # ---- a credit report with data behind it ----
        tick = None
        try:
            book = get_json(pub["base"] + "/credit.json")
            tick = next(t for t, r in book.items()
                        if isinstance(r, dict) and r.get("dd") is not None)
        except Exception:
            pass
        if tick:
            pg.goto(f"{base}/credit/{tick}", wait_until="load", timeout=90000)
            rep = pg.inner_text("body")
            check("standard deviations" in rep,
                  f"/credit/{tick} renders a measured report")
            check(pg.locator("svg polyline").count() >= 1,
                  "the report draws its history")
            check("{{" not in rep, "no unrendered template on the report")

        # ---- the record pages ----
        pg.goto(base + "/full", wait_until="load", timeout=90000)
        check("tested against random entry and failed"
              in pg.inner_text("body"),
              "/full states the falsification before its table")
        pg.goto(base + "/limits", wait_until="load", timeout=90000)
        lim = pg.inner_text("body")
        check("0.50" in lim and "0.41" in lim,
              "/limits carries both permutation results")

        # ---- the decision page ----
        # This is the one page the whole site exists to have. Everything
        # else measures; this decides, and if it is wrong it is wrong in
        # the direction of somebody losing money.
        pg.goto(base + "/today", wait_until="load", timeout=90000)
        td = pg.inner_text("body")
        check("{{" not in td, "no unrendered template on /today")
        cards = pg.locator(".card.pick").count()
        check(cards <= 5, "/today shows at most five names", f"showed {cards}")
        if cards:
            # a name without a stop and a share count is not a plan, it is
            # a tip — which is the thing this product refuses to be
            check(pg.locator("dl.plan dt", has_text="Stop").count() == cards,
                  "every name carries a stop")
            check(pg.locator("dl.plan dt", has_text="Size").count() == cards,
                  "every name carries a share count")
            check(pg.locator("dl.plan dt", has_text="Wrong if").count() == cards,
                  "every name says what would make it wrong")
        # A price target implies a forecast that was measured here and not
        # found. It must not creep back in through a template edit.
        check("target" not in td.lower(),
              "/today prints no price target anywhere")
        check("Risk 1% of your account" in td,
              "/today carries the position-sizing rule")
        over = pg.evaluate("() => document.documentElement.scrollWidth - "
                           "document.documentElement.clientWidth")
        check(over <= 0, "/today does not scroll sideways on a phone",
              f"{over}px of overflow")

        # ---- the pattern sweep ----
        # The credit report shipped once with no route to it from anywhere
        # a reader would look, and nobody found it for weeks. A page that
        # cannot be reached is a page that does not exist.
        pg.goto(base + "/", wait_until="load", timeout=90000)
        link = pg.locator('a[href="/patterns"]').first
        check(link.count() > 0 and link.is_visible(),
              "the front page links to the pattern sweep, visibly")
        pg.goto(base + "/patterns", wait_until="load", timeout=90000)
        pat = pg.inner_text("body")
        check("{{" not in pat, "no unrendered template on /patterns")
        check("falsified rule" in pat or "Not measured yet" in pat,
              "/patterns shows the known-worthless control, or says it has "
              "not run yet")
        if "Not measured yet" not in pat:
            # the failures are the point: a page that only lists winners
            # is the thing this whole module exists to not be
            check("no better than random" in pat or "too rare" in pat,
                  "/patterns publishes what failed, not only what survived")
            check("volatility bucket" in pat,
                  "/patterns states that the null is volatility-matched")
        over = pg.evaluate("() => document.documentElement.scrollWidth - "
                           "document.documentElement.clientWidth")
        check(over <= 0, "/patterns does not scroll sideways on a phone",
              f"{over}px of overflow")

        check(not js_errors, "no JavaScript errors anywhere",
              "; ".join(js_errors[:3]))
        b.close()

    print(f"\n{len(PASSES)} passed, {len(FAILS)} failed")
    if FAILS:
        print("GATE FAILED:")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("GATE PASSED — the deployed build does what the code claims")
    return 0


if __name__ == "__main__":
    sys.exit(main())
