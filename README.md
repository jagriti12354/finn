# FINN — Financial Investment Agent (ENAM build challenge)

See PLAN.md for the plan and architecture.

## Run (Windows, from this folder)
```
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q tests
python -m ingest.announcements --verify     # each code should print the right company name
python -m ingest.announcements --days 30    # real feed -> data/announcements.json
python -m ingest.prices                     # EOD + alerts -> data/prices.json, data/price_alerts.json
python -m ingest.enrich                     # results numbers, checked -> data/results.json (needs GEMINI_API_KEY in .env)
python -m ingest.push --dry-run             # what Telegram would receive; drop --dry-run to send (needs TELEGRAM_BOT_TOKEN in .env)
python -m ingest.push --digest              # the morning digest
```
