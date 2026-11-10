# FINN — 3-day build plan (deadline 15 Nov 2026)

**Brief in one line:** a mobile-first agent that *filters* the firehose: Push (digest + triggered alerts) and Pull (primary-source research on any NSE ticker). Grading is on judgment, product sense and architecture, not completeness. **A demo that doesn't run is the worst outcome.**

## Point of view (the pitch)
**"Inbox zero for an investor."** FINN is a triage queue, not a dashboard. You open it, clear 3–6 cards that matter, and the other ~90% of filings are collapsed under "routine (37)". Every card has three actions: **Dismiss · Pin · Dig in**. *Dig in* jumps from a pushed event straight into the Pull view, already scoped to that question (e.g. a results card opens "promised vs delivered").
- Alerts come through **Telegram** (a real phone push for almost no setup cost). The **PWA** handles the digest and research.
- On every research claim, the source shows as a chip: *(AR FY26 p.47)* or *(Q2 call)*. This is what proves the "primary sources only" discipline.

## Architecture
```
GitHub Actions cron (or laptop) ──► ingest/*.py ──► data/*.json + Supabase
   prices.py  (Yahoo chart EOD, 16:30 IST)       │
   announcements.py (BSE API, every 30 min)      ├─► Telegram bot (triggered alerts)
   enrich.py  (results PDF → numbers, mgmt bio)  │
                                                 ▼
FastAPI on Render/HF Spaces  ◄── PWA (Next.js on Vercel)
   /digest?window=24h   /research/{symbol}  (any ticker, cached per doc hash)
```
**Model routing (cost story):**
| Step | Model | Why |
|---|---|---|
| Classify filings | Rules first (₹0), Gemini Flash-Lite for the leftovers | Filings are templated; measure the rule hit-rate |
| Results PDF → 4 numbers × 3 periods | Gemini Flash (JSON schema) | SEBI results format already prints current Q, prior Q and year-ago Q columns, so one filing gives QoQ and YoY. Cross-check against screener's quarterly table and show ✓ verified |
| Mgmt-change bio | The filing itself first (SEBI LODR Reg 30 appointment disclosures include a brief profile), then grounded search for LinkedIn-style background | Cheapest correct source first |
| Per-transcript guidance extraction | Flash, chunked, cached by doc hash | Bulk extraction work |
| Promised-vs-delivered, bull/bear | One call on the newest free Flash model over the *extracted JSON*, not raw PDFs | Pro models are reported as paid-only; a small input fits free limits |

Everything runs on the Gemini free tier (no billing set up). Log tokens per call and show them in the app, e.g. "this digest used 4,200 tokens, ₹0". That's a quantified efficiency claim, not an asserted one.

## Day-by-day
**Day 1 (Tue 10 / Wed 11): plumbing + Push backend**
- [x] Portfolio (18 stocks, 3 sizes, 14 sectors) + alert thresholds config
- [x] `prices.py`: EOD fetch + volume ≥2× 20-day avg / ±X% alerts (tested)
- [x] `announcements.py`: BSE feed → rule classifier → P1/P2/P3 priority (tested)
- [ ] Run locally: `--verify` the codes, then a 30-day pull to tune rules on real headlines
- [ ] `enrich.py`: results PDF → Revenue/EBITDA/PBT/PAT QoQ/YoY; mgmt-change profile; one-liners for P2
- [ ] Telegram bot sends P1 + price alerts
**Day 2 (Thu 12): Pull engine**
- [ ] `screener.py`: any ticker → tables (P&L, BS, CF, ratios, quarters) + AR/concall links
- [ ] PDF → text (pypdf; PyMuPDF is blocked by Smart App Control on the dev laptop) → chunk → per-doc extraction, cached
- [ ] Build 5 of the 8 sections well: Snapshot, Trajectory, Cash quality, **Promised vs Delivered (4 quarters)**, Bull/Bear. Balance sheet + guidance get folded in, narrative-vs-numbers *is* the hero
- [ ] FastAPI endpoints + test on a ticker outside the demo set
**Day 3 (Fri 13): frontend + ship**
- [ ] PWA: Today (triage cards) · Watch (thresholds) · Research (ticker → sections) · cost footer
- [ ] Deploy (Vercel + Render), test on a real phone, GitHub Actions cron
**Buffer (Sat 14):** writeup, export the build history (this chat + others), record a 60-second screen capture, send to assessment@enam.com, subject "IIT Delhi – AI Engineer - 2027"

## Deliberately left out (say so in the writeup)
Intraday ticks, auth/multi-user, voice, NSE scraping (BSE gives the same coverage), all 8 research sections at equal depth.

## Risks
- BSE blocks GitHub Actions (HTTP 403, tested 10 Nov; Yahoo and screener pass) → the BSE feed runs on the laptop and publishes its output, so the deployed app doesn't depend on the laptop being on
- screener PDFs are sometimes scanned → skip with a visible "unreadable source" note rather than hallucinating
- Results PDFs: in a 16-company test, 2 had no usable text and 4 more had broken digits in their text layer (e.g. "20,21 1"). Don't regex numbers from text; send the P&L pages to the model as PDF, and keep the screener ✓ as the check
- Free-tier rate limits and overloaded models → caching by document hash makes re-runs free, models fall back newest-first, and the demo tickers are pre-computed so the demo doesn't depend on Gemini being up
