# Project Brain

Ask what a power project's public record says, and get an answer that cites the page it came from. When the documents don't say, it says so.

**Live:** https://web-production-3de901ea.up.railway.app

The test case is Project Kilby, a gas plant near Pecos, Texas, planned to power a Microsoft data center. The corpus is public: the TCEQ air permit file (1,100 pages including the draft permit and correspondence), 14 monthly ERCOT interconnection queue reports, company releases, and press. It covers 40 documents and 1,448 pages.

## What it does

- **Record card** (`/`): the project's key facts (capacity, turbines, battery, queue entries, permits, emissions), each with its page, quote and status. Sources that disagree are shown as a conflict, never resolved silently.
- **What changed** (`/changes`): every value that moved between filings of the same source, dated and cited on both sides. For example, the battery was cut from 560 to 330 MW between the June and July 2026 ERCOT reports.
- **Ask** (`/ask`): a plain English question gets a few sentences, each citing pages by number. The cited page opens beside the answer with the quote highlighted.
- **Eval** (`/eval`): the test set, every run, and every question's answer.

## How an answer is made

1. **Retrieve.** If the question touches a record field, start from the pages that field's facts were read from, then add SQLite full text search results. Near identical monthly reports are capped so they can't crowd out the filings.
2. **Answer.** Claude (Opus 5.5) returns JSON: sentences, each with page ids and a verbatim quote per citation, or "not in the documents."
3. **Check, in code.** Every cited page must be one it was given, every quote must be on its page, and every number and date in a sentence must appear on a cited page. Any failure withholds the whole answer and says why. The model's output is never trusted on its own.
4. **Log.** Every question is stored with its outcome, tokens and cost.

## Results

Graded in code, no model grading another model. The scores are reported separately because a system can look accurate by refusing too much, or look helpful by guessing.

| Run | Questions | Answer accuracy | Citation accuracy | Refusals | False refusals | Cost per question |
|---|---|---|---|---|---|---|
| Baseline | 42 tuning | 81% | 96% | 10/10 | 13% | $0.046 |
| After fixes (v2) | 42 tuning | 94% | 97% | 10/10 | 3% | $0.046 |
| Held out | 21 new | 17/17 | 17/17 | 4/4 | 0/17 | $0.042 |

- The fixes in v2 came from reading the tuning set's failures, so 94% is optimistic. The held out questions were written afterwards and committed before they ran; they are the honest number, though mostly single fact lookups.
- Every question the documents can't answer was declined in every run, including the decoys: carbon capture cost per MWh in the permit file, $10 billion in tax revenue, a different plant in the same county.
- One run per question, so differences of a few points between runs are noise.

## Design choices

- **The registry is the source of truth.** `brain/documents.yaml` lists every citable document with its tier (1 regulatory, 2 company, 3 press, 4 analysis), date and URL, and validation fails if a file is missing or unregistered.
- **Pages are real pages.** PDF citations use printed page numbers. Scanned pages go through OCR, and pages that stay unreadable are kept and counted, not dropped.
- **The record is extracted by rules, not generated.** Each fact is read with a pattern anchored on one document's wording, and its quote is checked against its page before it is stored.
- **Conflicts stay conflicts.** The record shows 2,869 MW (TCEQ) and about 2.67 GW (Chevron) side by side, because no source explains the gap.
- **The index is disposable.** It is rebuilt from the corpus on every ingest, and the 4.7 MB SQLite file ships with the app, so there is no database server.
- **Public spend is capped.** Repeat questions come from cache, new ones are limited per visitor per hour, and there is a daily budget.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=..." > .env           # only needed for Ask and the eval
.venv/bin/python scripts/fetch_corpus.py      # ERCOT reports and TCEQ permit files, about 150 MB
.venv/bin/python -m brain.ingest              # builds data/brain.db (OCR uses Tesseract if installed)
.venv/bin/python -m brain.record              # extracts the project record
.venv/bin/python -m web.app                   # http://localhost:5055
.venv/bin/python -m pytest -q
.venv/bin/python -m eval.run --check          # verifies eval labels against the corpus, free
.venv/bin/python -m eval.run                  # full eval, about $2
```

## Layout

```
brain/      registry, ingest, record extraction, search, answer
web/        Flask app and templates
eval/       test sets (cases.yaml, heldout.yaml), grader and runner, export for /eval
corpus/     the primary sources that can be redistributed, plus MANIFEST.md
scripts/    fetch_corpus.py for the large primary files
```

## Sources and limits

- Everything here is public: TCEQ filings and notices, ERCOT reports, company releases, and pages recovered from the Internet Archive. `corpus/MANIFEST.md` lists each source and what it shows.
- Press articles and third party analysis are not redistributed in this repo. The app quotes single lines from them and links to the original.
- Private email addresses in the permit correspondence are masked in the page viewer.
- This is a reading of the public record, not a statement about the developer's internal plans or numbers.

## License

MIT for the code. The documents in `corpus/` belong to their publishers.
