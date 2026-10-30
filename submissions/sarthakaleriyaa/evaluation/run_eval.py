"""Reproducible DocuMind evaluation.

Runs every question in dataset.json through the same code the API uses:
extract_pages -> chunk_pages -> embeddings -> cosine top-k + distance threshold
-> rag.assemble_answer (prompt, refusal and citation logic). The only
substitution is the vector store: chunks are held in memory and ranked with
the same cosine distance pgvector computes, so the evaluation needs no
database. (The pgvector SQL path is covered by backend/tests/test_postgres_integration.py.)

Usage (from the submission folder):
    python evaluation/run_eval.py --label baseline
    python evaluation/run_eval.py --label chunk400 --chunk-size 400 --overlap 60
    python evaluation/run_eval.py --compare evaluation/results/baseline.json evaluation/results/chunk400.json

--provider local (default) is offline, deterministic and free. --provider gemini
sends the documents and questions to Google's API and may cost money, so it
also requires --allow-remote.
"""

import argparse
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

# The app modules need these at import; nothing here connects to a database.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://eval:eval@127.0.0.1:1/eval")
os.environ.setdefault("JWT_SECRET_KEY", "evaluation-only")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--provider", choices=["local", "gemini"], default="local")
    parser.add_argument("--allow-remote", action="store_true",
                        help="required with --provider gemini (sends data to Google)")
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument("--overlap", type=int, default=150)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-distance", type=float, default=None,
                        help="default: the provider's default threshold")
    parser.add_argument("--compare", nargs=2, metavar=("BASELINE", "VARIANT"))
    return parser.parse_args()


def normalise(text: str) -> str:
    return " ".join(text.lower().split())


def cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return 1.0 - dot / norm if norm else 1.0


def contains_any(text: str, needles: list[str]) -> list[str]:
    lowered = text.lower()
    return [n for n in needles if n.lower() in lowered]


def run(args: argparse.Namespace) -> dict:
    if args.provider == "gemini" and not args.allow_remote:
        sys.exit("--provider gemini sends documents to Google's API; add --allow-remote to confirm.")

    os.environ["AI_PROVIDER"] = args.provider
    if args.provider == "local":
        os.environ["DOCUMIND_DISABLE_DOTENV"] = "1"

    from app.services import rag
    from app.services.document_processor import chunk_pages, extract_pages

    dataset = json.loads((HERE / "dataset.json").read_text(encoding="utf-8"))
    payloads = dataset["injection_payloads"]
    client = rag.get_ai_client()
    _, default_distance = rag.retrieval_settings()
    max_distance = args.max_distance if args.max_distance is not None else default_distance

    # Ingest: identical extraction and chunking to the worker.
    store = []
    for path in sorted((HERE / "documents").iterdir()):
        chunks = chunk_pages(extract_pages(str(path)), args.chunk_size, args.overlap)
        vectors = rag.embed_texts(client, [text for _, text in chunks], "RETRIEVAL_DOCUMENT")
        for index, ((page, text), vector) in enumerate(zip(chunks, vectors, strict=True)):
            store.append({"filename": path.name, "page": page, "chunk_index": index,
                          "content": text, "vector": vector})

    records = []
    for item in dataset["questions"]:
        started = time.perf_counter()
        query_vector = rag.embed_texts(client, [item["question"]], "RETRIEVAL_QUERY")[0]
        ranked = sorted(store, key=lambda c: cosine_distance(query_vector, c["vector"]))
        candidates = ranked[: args.top_k]
        sources = [
            {"source_id": f"S{i}", "document_id": 0, "filename": c["filename"],
             "chunk_index": c["chunk_index"], "page_number": c["page"],
             "content": c["content"], "score": round(1 - cosine_distance(query_vector, c["vector"]), 4)}
            for i, c in enumerate(
                (c for c in candidates if cosine_distance(query_vector, c["vector"]) <= max_distance),
                start=1,
            )
        ]
        result = rag.assemble_answer(client, item["question"], sources, user_id=0)
        latency_ms = (time.perf_counter() - started) * 1000

        record = {
            "id": item["id"], "category": item["category"], "answerable": item["answerable"],
            "question": item["question"], "answer": result["answer"], "refused": result["refused"],
            "latency_ms": round(latency_ms, 1),
            "retrieved": [{"filename": s["filename"], "page": s["page_number"], "score": s["score"]} for s in sources],
            "cited": [{"filename": c["filename"], "page": c["page_number"]} for c in result["citations"]],
            "input_tokens": result["input_tokens"], "output_tokens": result["output_tokens"],
        }

        leaked = contains_any(result["answer"], item.get("must_not_include", []) + payloads)
        record["leaked_payloads"] = leaked

        if item["answerable"]:
            def is_evidence(chunk, item=item):
                return (chunk["filename"] == item["expected_document"]
                        and normalise(item["evidence"]) in normalise(chunk["content"]))

            record["retrieval_hit"] = any(
                is_evidence(c) for c in candidates
                if cosine_distance(query_vector, c["vector"]) <= max_distance
            )
            record["recall_at_k"] = any(is_evidence(c) for c in candidates)
            missing = [s for s in item["must_include"] if s.lower() not in result["answer"].lower()]
            record["correct"] = not result["refused"] and not missing and not leaked
            record["citation_ok"] = any(
                c["filename"] == item["expected_document"]
                and (item.get("expected_page") is None or c["page"] == item["expected_page"])
                for c in record["cited"]
            )
        else:
            record["correct"] = result["refused"] and not leaked
        records.append(record)

    answerable = [r for r in records if r["answerable"]]
    unanswerable = [r for r in records if not r["answerable"]]
    latencies = [r["latency_ms"] for r in records]

    def rate(rows, key):
        return round(sum(1 for r in rows if r[key]) / len(rows), 3) if rows else None

    summary = {
        "label": args.label,
        "config": {"provider": args.provider, "chunk_size": args.chunk_size, "overlap": args.overlap,
                   "top_k": args.top_k, "max_distance": max_distance,
                   "generation_model": rag.GENERATION_MODEL if args.provider == "gemini" else "local-extractive",
                   "embedding_model": rag.EMBEDDING_MODEL if args.provider == "gemini" else "local-hash-768"},
        "questions": len(records), "answerable": len(answerable), "unanswerable": len(unanswerable),
        "chunks_indexed": len(store),
        "retrieval_hit_rate": rate(answerable, "retrieval_hit"),
        "recall_at_k_before_threshold": rate(answerable, "recall_at_k"),
        "answer_correctness": rate(answerable, "correct"),
        "citation_accuracy": rate(answerable, "citation_ok"),
        "false_refusal_rate": rate(answerable, "refused"),
        "correct_refusal_rate": rate(unanswerable, "correct"),
        "injection_leaks": sum(1 for r in records if r["leaked_payloads"]),
        "latency_ms_avg": round(statistics.mean(latencies), 1),
        "latency_ms_p95": round(sorted(latencies)[max(0, math.ceil(0.95 * len(latencies)) - 1)], 1),
    }
    return {"summary": summary, "records": records}


METRICS = [
    ("retrieval_hit_rate", "Retrieval hit rate (answerable)"),
    ("recall_at_k_before_threshold", "Evidence in top-k before threshold"),
    ("answer_correctness", "Answer correctness (answerable)"),
    ("citation_accuracy", "Citation accuracy (doc + page)"),
    ("false_refusal_rate", "False refusal rate (answerable)"),
    ("correct_refusal_rate", "Correct refusal rate (unanswerable)"),
    ("injection_leaks", "Answers containing injection payloads"),
    ("chunks_indexed", "Chunks indexed"),
    ("latency_ms_avg", "Avg latency (ms)"),
    ("latency_ms_p95", "p95 latency (ms)"),
]


def print_summary(summary: dict) -> None:
    print(f"\n## {summary['label']}  {json.dumps(summary['config'])}")
    print(f"{summary['questions']} questions ({summary['answerable']} answerable, "
          f"{summary['unanswerable']} unanswerable)\n")
    print("| Metric | Value |\n|---|---|")
    for key, name in METRICS:
        print(f"| {name} | {summary[key]} |")


def compare(baseline_path: str, variant_path: str) -> None:
    base = json.loads(Path(baseline_path).read_text(encoding="utf-8"))["summary"]
    var = json.loads(Path(variant_path).read_text(encoding="utf-8"))["summary"]
    changed = {k: (base["config"][k], var["config"][k])
               for k in base["config"] if base["config"][k] != var["config"][k]}
    print(f"Changed: {changed}\n")
    print(f"| Metric | {base['label']} | {var['label']} |\n|---|---|---|")
    for key, name in METRICS:
        print(f"| {name} | {base[key]} | {var[key]} |")


def main() -> None:
    args = parse_args()
    if args.compare:
        compare(*args.compare)
        return

    result = run(args)
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{args.label}.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print_summary(result["summary"])

    failures = [r for r in result["records"] if not r["correct"]]
    if failures:
        print("\nIncorrect:")
        for r in failures:
            print(f"- {r['id']} ({r['category']}): {r['answer'][:110]!r}")
    print(f"\nWrote {out_path.relative_to(HERE.parent)}")


if __name__ == "__main__":
    main()
