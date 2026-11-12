"""Retrieval evaluation: does search_passages return the page that answers a question?

Usage: otto-eval evals/retrieval.yaml [--limit 10]
Each entry has a `question` and `expect: {document_id: [pages]}`; a result
counts when its document and page match any expected page. Page 0 stands for
the document's card.
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import yaml


def first_hit(results: list[dict], expect: dict[int, list[int]]) -> int | None:
    for rank, result in enumerate(results, start=1):
        if (result["page"] or 0) in expect.get(result["document_id"], []):
            return rank
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Score OTTO's passage search against expected pages.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--limit", type=int, default=10, help="results requested per question")
    args = parser.parse_args()
    logging.disable(logging.WARNING)

    from . import server

    cases = yaml.safe_load(args.file.read_text())
    server.index().sync(server.client())  # evaluate the current archive
    ranks, page_ranks, durations = [], [], []
    print(f"{'rank':>4}  {'ms':>5}  question  ->  top result")
    for case in cases:
        expect = {int(doc): pages for doc, pages in case["expect"].items()}
        started = time.monotonic()
        results = server.search_passages(case["question"], limit=args.limit)["results"]
        durations.append(time.monotonic() - started)
        rank = first_hit(results, expect)
        ranks.append(rank)
        page_ranks.append(first_hit([r for r in results if r["page"]], expect))
        top = "nothing"
        if results:
            top = f"#{results[0]['document_id']} " + (f"p{results[0]['page']}" if results[0]["page"] else "card")
        print(f"{rank or '-':>4}  {durations[-1] * 1000:5.0f}  {case['question'][:60]}  ->  {top}")

    n = len(ranks)
    print(f"\n{n} questions, median {sorted(durations)[n // 2] * 1000:.0f} ms")
    print(f"  with cards:  {scores(ranks)}")
    print(f"  pages only:  {scores(page_ranks)}  (cards ignored: are the right pages still found?)")


def scores(ranks: list[int | None]) -> str:
    n = len(ranks)
    hit = lambda k: sum(1 for r in ranks if r and r <= k) / n
    mrr = sum(1 / r for r in ranks if r) / n
    return f"hit@1 {hit(1):.0%}  hit@3 {hit(3):.0%}  hit@5 {hit(5):.0%}  MRR {mrr:.2f}"
