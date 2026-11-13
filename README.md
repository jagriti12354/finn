# FINN: inbox zero for an investor

FINN is a triage queue, not a dashboard. You open it, deal with the few things that matter, and the routine filings stay folded away. When something needs you before you open it, it pings your phone.

- **App (open on a phone):** https://finn-app-pxbu.onrender.com
- **API:** https://finn-api-wrgo.onrender.com/health

The free server sleeps when idle, so the first load after a quiet spell takes up to a minute. The app says so while it waits.

## What it does

**Push: Today.** Every filing and unusual price move in a window you choose (24 hours to 30 days), sorted into three lanes:

| Lane | What goes there | How it reaches you |
|---|---|---|
| Needs you | Verified results, director and C-suite changes, deals, pledges, rating downgrades, big unexplained moves | Telegram, at most 5 a day by default |
| Worth a look | Other material filings | The 8:40 AM digest, and the app |
| Routine | ESOP allotments, newspaper ads, board-meeting notices, compliance paperwork | Folded into "Routine (N)" |

Each card has Dismiss (swipe left, with Undo), Pin and **Dig in**, which opens the research view on that company.

- **Results cards** show Revenue, EBITDA, PBT and PAT, QoQ and YoY, read from the results filing itself. A ✓ appears only after two checks: revenue plus other income must equal total income, and revenue, PBT and PAT must match screener for all three quarters.
- **Management-change cards** show who joined or left, read from the profile and family-relationship fields SEBI requires in the filing. A web search fills gaps and is labelled as coming from the web. A real example: a new Dixon executive director who is the son of the Executive Chairman.
- **Price moves** are measured against Nifty, so a market-wide day doesn't set them off. A move after a filing becomes one card that says when the filing was made. It never claims the filing caused the move. A big move with no filing behind it gets its own "Unexplained move" card.

**Pull: Research, for any NSE ticker.** Primary sources only.

- **Promised vs delivered:** management's guidance from the last five earnings calls, checked against what was then reported.
- **Guidance still open:** promises whose periods haven't been reported yet.
- **Bull vs bear:** every point cites its evidence.
- **Latest numbers, trajectory, balance sheet, cash quality.**

**Watch.** You set what counts as unusual: price move, volume spike and its lookback, pings per day, and which alert types ping.

## Decisions worth explaining

**Checks over trust.** Nothing a model writes reaches a card unverified:
- A promise only counts if its quote is found word for word in the transcript. If the promise has a number, that number must be in management's own words.
- The model never writes an "actual" figure. It points at one in our table by key, and code looks it up.
- Where a target and an actual are both numbers, code decides the verdict, and the app shows which verdicts were calculated and which were judged.
- A bull or bear point is dropped if it cites no evidence, or uses a number that isn't in its evidence.

**Rules before models, and cheap models only where needed.**
- Exchange filings are templated, so rules classify 94.6% of them for free. BSE's own subcategory is checked first.
- Results tables go to Gemini Flash as the one or two pages that matter, not the whole PDF.
- Reasoning steps work on small extracted JSON.
- Transcript work is cached per document, so repeat runs are free.
- On a test of 16 results filings, extraction used about 32,000 input and 8,000 output tokens in total, all on free tiers.

**No RAG.** A research query is one company with a known set of documents. Top-k retrieval can silently miss a promise, which would make "promised vs delivered" wrong with nothing to flag it. Reading each transcript once and keeping structured guidance is more complete, and easier to cite.

**Model fallback.** Free Gemini models are often overloaded, so FINN tries the newest stable Flash models in turn and moves on when one hangs or is retired. Short prompts fall back to Groq. A Groq answer is shown but never cached as final, because it tested fewer promises on real data, so the next run retries Gemini.

## Measured on real data

- **Classification:** over 30 days, 83 priority-1 filings became 40 cards once results filings were grouped per event. That's about 2.4 a day, and 132 of 240 filings were routine.
- **Results extraction:** 16 of 16 filings verified, including 3 scanned PDFs with no usable text.
- **Bugs found by reviewing real output, now fixed:**
  - ITC's revenue included excise duty. Gross growth looked like +43.9% against +3.9% net.
  - A Reliance promise was read from the line before the associates' share of profit.
  - Titan prints revenue only as sub-lines.
  - Trent's year was misread, so quarter dates now come from the filing date, never the model.
  - One "promise" was really an analyst's number that management only agreed with.
- **Infrastructure findings:**
  - BSE's API blocks GitHub Actions, so the filings feed runs on a laptop and publishes to Supabase. The cloud API can still fetch transcript PDFs, so research works without the laptop.
  - Windows Smart App Control blocked pandas and PyMuPDF on the dev laptop. Both were replaced with plain Python and pypdf, and the app is plain HTML and JS with no build step.

## Left out on purpose

- Business snapshot from annual reports. These are 300-page documents, and the next step would be reading them by section headings.
- Sector-relative moves (Nifty only for now).
- Intraday data, voice, multiple users and login.
- A follow-up ping when a result's numbers are verified after the first alert.

## Next

- **An evaluation set, used before every change.** A few hundred filings and transcripts with known answers, re-scored whenever a prompt or model changes, so accuracy is a number rather than a hope. The 16 screener-checked results filings and the real headlines in `tests/` are the start of it.
- **A "this is wrong" button** that sends the item to a review list. Once fixed, the case joins the evaluation set. It never edits a figure directly, because figures come from the filings.
- **A second, different model family checking only the judgement calls,** such as verdicts marked "model's judgement" and web bios. It answers a narrow question ("does this quote support this claim?"), and a disagreement shows the item as unverified rather than silently overwriting it.
- **Relevance learned from Dismiss and Pin, with the user in control.** For example: "You've dismissed 6 of Bajaj Finance's debt allotments. Stop pinging these?" The model doesn't learn from clicks; FINN's ranking and rules do.
- Read annual reports by section: related parties, contingent liabilities, auditor's remarks.
- Compare each stock against its sector index.
- A "what changed since my last visit" view.
- Scoring how management's credibility moves over time.

## Architecture

```
Laptop (Task Scheduler, every 30 min)        Render                       Phone
  BSE filings -> classify -> results     ->   FastAPI  <- Supabase  ->     web app (Today / Research / Watch)
  Yahoo prices -> alerts                      research jobs (any ticker)
  bios, push -> Telegram                      Gemini Flash, Groq fallback
  publish -> Supabase
```

| Folder | What's in it |
|---|---|
| `ingest/` | Filings, classifier, prices, results extraction, bios, push, publish |
| `research/` | Number sections, promised vs delivered, bull vs bear, model fallback |
| `api/` | FastAPI app and the Supabase store |
| `web/` | The mobile app |
| `tests/` | 118 tests |

## Run it

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env          # then add your keys
python -m pytest -q tests
python -m ingest.announcements --days 30
python -m ingest.prices
python -m ingest.enrich
python -m ingest.push --dry-run
uvicorn api.main:app --reload
```

Supabase needs the table in `db/schema.sql`. The laptop schedule runs `scripts/cycle.cmd`.
