# Tests

Plain Python asserts, no test framework. Run them from the repo root with the app's venv:

```bash
.venv/bin/python tests/test_search_core.py    # normalisation, obfuscation folding, stemming, NEAR, dictionary expansion,
                                              # version/OCR indexes, place matching. Uses a temp DB and fixtures/.
.venv/bin/python tests/test_entities.py       # identifier extraction: phones, wallets, cards (Luhn), unit numbers, plates
.venv/bin/python tests/test_mediatext_comments.py  # OCR/speech of comment media: which files are read, text tied back to comments
.venv/bin/python tests/test_crawl.py          # crawler hardening against a local fake t.me (crawlfix.py): layout-drift warnings, backoff,
                                              # breaker, limits, media, resume, and what ingest does with runs that went wrong (~1 min)
.venv/bin/python tests/test_crawl_jobs.py     # the same through JobRunner and the API: status from the crawler's phase, resume, stall watchdog,
                                              # recovery after a restart. Starts tgspider and the server as subprocesses (~2 min)
```

`crawlfix.py` is the fake t.me: synthetic pages (no real channel content) and switches for rate limits, 5xx, cut-off pages, changed
markup and bad media. Nothing in these tests touches the network or the real workspace.

`fixtures/dictionary_fixture.db` is a tiny dictionary made by `fixtures/make_fixture.py`, so the search tests do not
depend on the real 50 MB `dictionary.db`.

## Local API tests (not published)

`tests/local/` holds end-to-end API tests that need a populated workspace, and they name the channels in it, so the
folder is git-ignored. Point them at a **copy**, never your real workspace:

```bash
cp -R workspace /tmp/ws-copy
TEST_WORKSPACE=/tmp/ws-copy .venv/bin/python tests/local/test_api.py
```

## UI gates

```bash
cd ui && npm run build && node scripts/coverage.mjs && node scripts/check.mjs
```

`check.mjs` compiles every component. `coverage.mjs` fails if a backend route has no UI surface.
