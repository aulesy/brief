"""Manual test script — run from project root: python test_manual.py

Tests real scenarios Brief will face in production, not just the happy path.
Each test prints PASS / FAIL clearly so you can see what's broken at a glance.
"""

import time
from brief import brief, brief_batch
from brief.service import check_existing

PASS = "PASS"
FAIL = "FAIL"


def section(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    print(f"  [{status}] {label}")
    if not condition and detail:
        print(f"         {detail}")


# ── TEST 1: Basic brief at all depths ────────────────────────────

section("TEST 1: Basic brief — all depths")

url = "https://fastapi.tiangolo.com/"
query = "async support"

r0 = brief(url, query, depth=0)
check("depth=0 returns something", bool(r0))
check("depth=0 is short (under 300 chars)", len(r0) < 300, f"got {len(r0)} chars")

r1 = brief(url, query, depth=1)
check("depth=1 returns something", bool(r1))
check("depth=1 is longer than depth=0", len(r1) > len(r0), f"depth=1: {len(r1)}, depth=0: {len(r0)}")

r2 = brief(url, query, depth=2)
check("depth=2 returns something", bool(r2))
check("depth=2 is longer than depth=1", len(r2) > len(r1), f"depth=2: {len(r2)}, depth=1: {len(r1)}")


# ── TEST 2: Caching — same query should be instant ───────────────

section("TEST 2: Cache hit — same query same depth")

start = time.time()
r1_cached = brief(url, query, depth=1)
elapsed = time.time() - start

check("cache hit returns same content", r1_cached == r1)
check("cache hit is fast (under 0.5s)", elapsed < 0.5, f"took {elapsed:.2f}s")


# ── TEST 3: New query reuses extraction ──────────────────────────

section("TEST 3: New query — extraction should not repeat")

new_query = "how to install"
start = time.time()
r_new = brief(url, new_query, depth=1)
elapsed = time.time() - start

check("new query returns something", bool(r_new))
check("new query result differs from first query", r_new != r1)
check("new query is reasonably fast (under 10s)", elapsed < 10, f"took {elapsed:.2f}s — extraction may have repeated")


# ── TEST 4: check_existing shows all queries for a URL ────────

section("TEST 4: check_existing — knows what's been researched")

existing = check_existing(url)
check("check_existing returns something", bool(existing))
check("check_existing mentions async support", "async" in existing.lower(), existing[:200])
check("check_existing mentions how to install", "install" in existing.lower(), existing[:200])


# ── TEST 5: Different content types ──────────────────────────────

section("TEST 5: Content types")

pdf_url = "https://www.w3.org/WAI/WCAG21/wcag21.pdf"
r_pdf = brief(pdf_url, "accessibility guidelines", depth=1)
check("PDF extraction works", bool(r_pdf) and "brief" in r_pdf.lower())

reddit_url = "https://www.reddit.com/r/Python/comments/x3y5zn/what_are_some_underrated_python_libraries/"
r_reddit = brief(reddit_url, "useful python libraries", depth=1)
check("Reddit extraction works", bool(r_reddit) and "brief" in r_reddit.lower())

github_url = "https://github.com/tiangolo/fastapi"
r_github = brief(github_url, "what does this repo do", depth=1)
check("GitHub extraction works", bool(r_github) and "brief" in r_github.lower())


# ── TEST 6: Failure cases ─────────────────────────────────────────

section("TEST 6: Failure cases — graceful handling")

r_404 = brief("https://fastapi.tiangolo.com/this-page-does-not-exist-xyz", "anything", depth=1)
check("404 URL returns error message not crash", bool(r_404) and "404" in r_404.lower(), r_404[:200])

r_bad = brief("https://thisdomaindoesnotexistatall12345.com/", "anything", depth=1)
check("unreachable URL returns error message not crash", bool(r_bad), r_bad[:200])


# ── TEST 7: brief_batch ───────────────────────────────────────────

section("TEST 7: brief_batch — multiple URLs")

urls = [
    "https://fastapi.tiangolo.com/",
    "https://flask.palletsprojects.com/",
    "https://thisdomaindoesnotexistatall12345.com/",  # one bad URL
]

results = brief_batch(urls, query="python web framework", depth=0)
check("brief_batch returns results for all URLs", len(results) == len(urls), f"got {len(results)}")
check("brief_batch good URLs have content", all(bool(r) for r in results[:2]))
check("brief_batch bad URL doesn't kill the batch", bool(results[2]))


# ── TEST 8: depth=0 is not saved ─────────────────────────────────

section("TEST 8: depth=0 — triage only, no file saved")

fresh_url = "https://docs.python.org/3/library/asyncio.html"
r_triage = brief(fresh_url, "event loop", depth=0)
existing_after = check_existing(fresh_url)

check("depth=0 returns a result", bool(r_triage))
check("depth=0 does not save a brief file", "No briefs exist" in existing_after)


# ── SUMMARY ───────────────────────────────────────────────────────

section("DONE")
print("  Check any [FAIL] lines above for issues.")
