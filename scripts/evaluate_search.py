"""Evaluate a saved/API task against the independent known-paper fixture.

Usage: python scripts/evaluate_search.py TASK_ID [--output outputs/evaluation.json]
This reads results only. Benchmark titles are never sent to retrieval queries.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def matches(paper: dict, expected: dict) -> bool:
    identifier = expected["arxiv_id"]
    if any(identifier in str(paper.get(key, "")) for key in ("paper_id", "url", "pdf_url")):
        return True

    def normalize(value):
        return re.sub(r"[^a-z0-9]", "", str(value).lower())

    return normalize(paper.get("title", "")) == normalize(expected["title"])


def evaluate(task: dict, fixture: dict) -> dict:
    screening = (task.get("results") or {}).get("screening") or {}
    observed = [
        paper
        for group in ("included", "supporting", "excluded", "pending")
        for paper in screening.get(group, [])
    ]
    retained = task.get("papers") or []
    available = retained + screening.get("supporting", [])
    eligible = [
        paper
        for paper in fixture["papers"]
        if paper["expected_decision"] == "retain" and paper["year"] >= task["year_from"]
    ]
    details = [
        {
            "title": expected["title"],
            "url": expected["url"],
            "retrieved": any(matches(p, expected) for p in observed) if screening else None,
            "retained": any(matches(p, expected) for p in retained),
            "available_including_supporting": any(matches(p, expected) for p in available),
            "decisions": [p.get("screening_status") for p in observed if matches(p, expected)],
        }
        for expected in eligible
    ]
    negatives = [
        expected
        for expected in fixture["papers"]
        if expected["expected_decision"] != "retain" and expected["year"] >= task["year_from"]
    ]
    return {
        "task_id": task["id"],
        "status": task["status"],
        "year_from": task["year_from"],
        "quick_mode": task["quick_mode"],
        "eligible_known_papers": len(eligible),
        "known_papers_retrieved": sum(bool(p["retrieved"]) for p in details) if screening else None,
        "known_papers_retained": sum(p["retained"] for p in details),
        "known_papers_available_including_supporting": sum(
            p["available_including_supporting"] for p in details
        ),
        "total_retained": len(retained),
        "screening_counts": {
            group: len(screening.get(group, []))
            for group in ("included", "supporting", "excluded", "pending")
        },
        "known_negative_retained": [
            p["title"] for p in negatives if any(matches(item, p) for item in retained)
        ],
        "details": details,
        "limitations": "Known-item recall on a small purposive sample; not exhaustive recall or a precision estimate. Historical tasks without screening audit have unknown retrieved recall.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", args.task_id):
        parser.error("Invalid task id")
    with urllib.request.urlopen(
        f"http://127.0.0.1:8000/api/tasks/{args.task_id}", timeout=15
    ) as response:
        task = json.load(response)
    fixture = json.loads((ROOT / "tests/fixtures/retrieval_benchmark.json").read_text())
    result = evaluate(task, fixture)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
