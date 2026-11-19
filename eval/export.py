"""Bundle eval results into data/eval.json for the web app's /eval page.

  python -m eval.export

Reads the committed run outputs under .claude/hillclimb/ (results.jsonl,
summary_scores.json, change.md) plus the answer text from local traces, and
writes one file the deployed app can read without the eval folders.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from brain.registry import ROOT

RUNS = [
    ("answer", "baseline", "Baseline", "tuning", "Before any tuning."),
    ("answer", "v1", "v1", "tuning", None),
    ("answer", "v2", "v2", "tuning", None),
    ("answer-heldout", "baseline", "Held out", "heldout", "21 new questions written after v2 and committed before they ran."),
]


def first_line(path: Path) -> str | None:
    if not path.exists():
        return None
    return next((l.strip() for l in path.read_text().splitlines() if l.strip() and not l.startswith("#")), None)


def answer_text(trace: Path) -> str:
    if not trace.exists():
        return ""
    turns = json.loads(trace.read_text())
    text = next((t["content"] for t in turns if t["role"] == "assistant"), "")
    text = text.split("\n\n")[0]
    if text.startswith("["):   # drop the "[answered] " status prefix the trace carries
        text = text.split("] ", 1)[-1]
    return text[:900]


def main() -> None:
    cases = {}
    for name, split in (("cases.yaml", "tuning"), ("heldout.yaml", "heldout")):
        for c in yaml.safe_load((ROOT / "eval" / name).read_text())["cases"]:
            cases[c["id"]] = {"id": c["id"], "kind": c["kind"], "split": split, "question": c["question"],
                              "expect": " and ".join(" / ".join(g) for g in c.get("facts", [])) or "not in the documents",
                              "trap": c.get("trap", "")}
    runs = []
    for flow, variant, label, split, note in RUNS:
        d = ROOT / ".claude" / "hillclimb" / flow / variant
        rows = [json.loads(l) for l in (d / "results.jsonl").read_text().splitlines() if l.strip()]
        per_case = {
            r["prompt_id"]: {
                "status": r["answer_status"], "grade": r["grade"], "cost_usd": r["cost_usd"],
                "latency_s": r["latency_s"], "answer": answer_text(d / "traces" / f"{r['prompt_id']}_rep0.json"),
            }
            for r in rows
        }
        runs.append({
            "id": f"{flow}/{variant}", "label": label, "split": split,
            "note": note or first_line(d / "change.md") or "",
            "summary": json.loads((d / "summary_scores.json").read_text()),
            "cases": per_case,
        })
    out = ROOT / "data" / "eval.json"
    out.write_text(json.dumps({"cases": cases, "runs": runs}, indent=1))
    print(f"wrote {out.relative_to(ROOT)}: {len(cases)} cases, {len(runs)} runs")


if __name__ == "__main__":
    main()
