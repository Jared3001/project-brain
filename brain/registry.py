"""Load and validate the document registry and the project schema.

Validation fails loudly on drift: a registered file that is missing, a corpus
file that is neither registered nor excluded, a schema field that names a
source kind no document has.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "brain" / "documents.yaml"
SCHEMA = ROOT / "brain" / "project_schema.yaml"

KINDS = {
    "permit_application",
    "permit_notice",
    "queue_report",
    "status_snapshot",
    "company_release",
    "press",
    "analysis",
}
INGESTIBLE = {".pdf", ".txt", ".xlsx", ".html", ".csv"}
# Full text of these is not committed (copyright); a fresh clone may lack them.
OPTIONAL_KINDS = {"press", "analysis"}


@dataclass
class Document:
    id: str
    title: str
    path: Path
    kind: str
    tier: int
    publisher: str
    published: dt.date
    url: str
    notes: str = ""

    @property
    def rel(self) -> str:
        return str(self.path.relative_to(ROOT))


@dataclass
class Registry:
    documents: list[Document]
    excluded: list[dict] = field(default_factory=list)

    def by_id(self, doc_id: str) -> Document:
        for d in self.documents:
            if d.id == doc_id:
                return d
        raise KeyError(doc_id)


def _date(value) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


def _expand_glob(entry: dict) -> list[Document]:
    """One registry entry with `glob` becomes one Document per matching file.

    ERCOT GIS files are named by publish date (2026-11-02); the report covers
    the previous month (October 2026). Wayback snapshots are named by capture
    timestamp (tceq-pending_20260107075632.html).
    """
    docs = []
    for path in sorted(ROOT.glob(entry["glob"])):
        name = path.name
        m = re.match(r"(\d{4}-\d{2}-\d{2})_", name)
        if m:
            published = dt.date.fromisoformat(m.group(1))
        else:
            m = re.search(r"_(\d{8})\d*\.", name)
            if not m:
                raise ValueError(f"cannot date {name}")
            published = dt.datetime.strptime(m.group(1), "%Y%m%d").date()
        report_month = (published.replace(day=1) - dt.timedelta(days=1))
        title = entry["title"].format(
            month=report_month.strftime("%B %Y"),
            date=published.isoformat(),
        )
        suffix = report_month.strftime("%Y-%m") if "{month}" in entry["title"] else published.isoformat()
        docs.append(
            Document(
                id=f"{entry['id_prefix']}-{suffix}",
                title=title,
                path=path,
                kind=entry["kind"],
                tier=int(entry["tier"]),
                publisher=entry["publisher"],
                published=published,
                url=entry["url"],
                notes=entry.get("notes", ""),
            )
        )
    if not docs:
        raise ValueError(f"glob matched nothing: {entry['glob']}")
    return docs


def load_registry(path: Path = REGISTRY) -> Registry:
    raw = yaml.safe_load(path.read_text())
    docs: list[Document] = []
    for entry in raw["documents"]:
        if "glob" in entry:
            docs.extend(_expand_glob(entry))
            continue
        docs.append(
            Document(
                id=entry["id"],
                title=entry["title"],
                path=ROOT / entry["path"],
                kind=entry["kind"],
                tier=int(entry["tier"]),
                publisher=entry["publisher"],
                published=_date(entry["published"]),
                url=entry["url"],
                notes=entry.get("notes", ""),
            )
        )
    return Registry(docs, raw.get("excluded", []))


def validate(reg: Registry, schema_path: Path = SCHEMA, check_coverage: bool = True) -> list[str]:
    """Return a list of problems; empty means the registry is sound."""
    problems: list[str] = []
    seen: set[str] = set()
    for d in reg.documents:
        if d.id in seen:
            problems.append(f"duplicate id {d.id}")
        seen.add(d.id)
        if d.kind not in KINDS:
            problems.append(f"{d.id}: unknown kind {d.kind}")
        if d.tier not in (1, 2, 3, 4):
            problems.append(f"{d.id}: tier must be 1..4")
        if not d.path.exists() and d.kind not in OPTIONAL_KINDS:
            problems.append(f"{d.id}: missing file {d.rel}")
        if not d.url.startswith("https://"):
            problems.append(f"{d.id}: url must be https")

    schema = yaml.safe_load(schema_path.read_text())
    kinds_present = {d.kind for d in reg.documents}
    for name, spec in schema["fields"].items():
        for kind in spec.get("sources", []):
            if kind not in KINDS:
                problems.append(f"schema {name}: unknown source kind {kind}")
            elif kind not in kinds_present:
                problems.append(f"schema {name}: no document of kind {kind}")

    if check_coverage:
        registered = {d.rel for d in reg.documents}
        patterns = [e["path"] for e in reg.excluded]
        for path in sorted((ROOT / "corpus").rglob("*")):
            if not path.is_file() or path.suffix not in INGESTIBLE:
                continue
            rel = str(path.relative_to(ROOT))
            if rel in registered or any(fnmatch.fnmatch(rel, p) for p in patterns):
                continue
            if path.suffix == ".html" and path.with_suffix(".txt").exists():
                continue  # raw HTML kept for provenance next to its extracted text
            if path.name.startswith("0-READ THIS FIRST"):
                continue  # TCEQ portfolio instructions page
            problems.append(f"unregistered corpus file: {rel}")
    return problems


if __name__ == "__main__":
    reg = load_registry()
    issues = validate(reg)
    print(f"{len(reg.documents)} documents registered")
    for p in issues:
        print("PROBLEM:", p)
    raise SystemExit(1 if issues else 0)
