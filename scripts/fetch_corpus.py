"""Download the large primary sources that are not committed to git.

  python scripts/fetch_corpus.py

ERCOT monthly GIS reports (Sep 2025 to Oct 2026) and the two TCEQ permit
portfolios, whose embedded files are unpacked with PyMuPDF. Files already
present are skipped. Press and third party analysis are not redistributed;
see corpus/MANIFEST.md for their URLs.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (project-brain corpus fetch)"}

ERCOT_LIST = "https://www.ercot.com/misapp/servlets/IceDocListJsonWS?reportTypeId=15933"
ERCOT_DOC = "https://www.ercot.com/misdownload/servlets/mirDownload?doclookupId={}"
ERCOT_FROM, ERCOT_TO = "2025-10-01", "2026-11-30"  # publish dates

TCEQ = "https://www.tceq.texas.gov/assets/public/permitting/air/reports/applications/{}"
PORTFOLIOS = {
    "399548-ac.pdf": "corpus/tceq/application/2025-10-17_admin-complete",
    "399548-tc.pdf": "corpus/tceq/application/2026-08_technically-complete",
}


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r:
        return r.read()


def fetch_ercot() -> None:
    out = ROOT / "corpus/ercot"
    out.mkdir(parents=True, exist_ok=True)
    docs = json.loads(get(ERCOT_LIST))["ListDocsByRptTypeRes"]["DocumentList"]
    for item in docs:
        d = item["Document"]
        date = d["PublishDate"][:10]
        if not d["FriendlyName"].startswith("GIS_Report") or not (ERCOT_FROM <= date <= ERCOT_TO):
            continue
        dest = out / f"{date}_ercot_gis_report.xlsx"
        if dest.exists():
            continue
        print("ERCOT", dest.name)
        dest.write_bytes(get(ERCOT_DOC.format(d["DocID"])))


def fetch_tceq() -> None:
    for name, folder in PORTFOLIOS.items():
        out = ROOT / folder
        if out.exists() and any(out.glob("1-*.pdf")):
            continue
        out.mkdir(parents=True, exist_ok=True)
        print("TCEQ", name, "(large, a few minutes)")
        with pymupdf.open(stream=get(TCEQ.format(name)), filetype="pdf") as portfolio:
            for i in range(portfolio.embfile_count()):
                info = portfolio.embfile_info(i)
                (out / info["filename"]).write_bytes(portfolio.embfile_get(i))


if __name__ == "__main__":
    fetch_ercot()
    fetch_tceq()
    print("done")
