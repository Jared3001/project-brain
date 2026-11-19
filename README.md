# Project Brain

Ask what a power project's public record says, and get answers that cite the page they came from. The test case is Project Kilby, a gas plant near Pecos, Texas, planned to power a Microsoft data center. The corpus is TCEQ air permit filings, ERCOT interconnection queue reports, and company releases.

Work in progress. Done: corpus, document registry, ingest and full text search, the project record card, and a what changed timeline.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/fetch_corpus.py   # ERCOT reports + TCEQ permit files (~150 MB)
.venv/bin/python -m brain.ingest           # builds data/brain.db
.venv/bin/python -m brain.record           # extracts the project record into the same db
.venv/bin/python -m web.app                # record card at http://localhost:5055, changes at /changes
.venv/bin/python -m brain.search "nominal generation capacity"
.venv/bin/python -m pytest -q
```

OCR of scanned pages uses Tesseract if it is installed.

## Design choices

- **The registry is the source of truth.** `brain/documents.yaml` lists every citable document with its tier (1 regulatory, 2 company, 3 press, 4 analysis), date and URL. `python -m brain.registry` fails if a registered file is missing or a corpus file is unregistered.
- **Pages are real pages.** PDF citations use printed page numbers; nothing is dropped, and pages that stay unreadable after OCR are kept and counted.
- **The index is disposable.** SQLite FTS5, rebuilt from the corpus on every ingest. The corpus is read only, so there is no database server to run.
- **The record is extracted by rules, not generated.** `brain/record.py` reads each fact with a pattern anchored on one document's wording. Before a fact is stored, its quote must be on the cited page and its number must be in its quote. A later filing from the same source becomes history ("560 MW from Feb to Jul 2026"), not a conflict.
- **Conflicts stay conflicts.** `brain/project_schema.yaml` defines the project record. When sources disagree (the public record gives at least five different capacity figures), the record shows all of them with their basis instead of picking one.

Press articles and third party analysis are not redistributed here; `corpus/MANIFEST.md` lists their URLs.
