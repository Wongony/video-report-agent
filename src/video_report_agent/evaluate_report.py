"""Small report-only A/B experiment using fixed local transcripts, no ASR."""

import argparse
import asyncio
import json
import shutil
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .inspect_report import inspect_report
from .pi import PROJECT_ROOT, SKILL, PiRunner
from .usage import call_costs


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def review_trace(run: Path):
    attempts, images, errors = 0, 0, 0
    path = run / "pi.events.jsonl"
    if path.is_file():
        for line in path.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue  # A terminated RPC process may leave a partial last event.
            if event.get("toolName") != "inspect_report":
                continue
            if event.get("type") == "tool_execution_start":
                attempts += 1
            if event.get("type") == "tool_execution_end":
                errors += int(bool(event.get("isError")))
                images += event.get("result", {}).get("details", {}).get("imagesReturned", 0)
    return {"attempts": attempts, "errors": errors, "images_returned": images}


def evaluate(manifest: Path, output: Path, *, timeout: float = 600, repeats: int = 1):
    spec = json.loads(manifest.read_text())
    output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(manifest, output / "manifest.json")
    shutil.copytree(SKILL, output / "skill-snapshot")
    runtime = output / "runtime-snapshot"
    runtime.mkdir()
    for name in ("pi.py", "report_content.py", "report_image.py", "inspect_report.py",
                 "report_inspect.ts", "evaluate_report.py"):
        shutil.copy2(Path(__file__).with_name(name), runtime / name)
    rows = []
    for repeat in range(repeats):
        for index, case in enumerate(spec["cases"]):
            # Alternate execution order to reduce a systematic warm-cache/order advantage.
            variants = [False, True] if (index + repeat) % 2 == 0 else [True, False]
            for review in variants:
                variant = "review" if review else "baseline"
                run = output / f"{case['id']}-{repeat + 1}-{variant}"
                run.mkdir()
                material = manifest.parent / case["material"]
                shutil.copy2(material / "transcript.md", run / "transcript.md")
                shutil.copy2(material / "input.json", run / "input.json")
                if (material / "download/source.info.json").is_file():
                    shutil.copytree(material / "download", run / "download")
                runner = PiRunner(review=review, timeout=timeout)
                row = {"case": case["id"], "repeat": repeat + 1, "variant": variant,
                       "run": run.name, "provider": runner.provider, "model": runner.model,
                       "thinking": runner.thinking, "timeout_seconds": timeout,
                       "status": "running", "human_review": "pending"}
                print(f"START {run.name}", flush=True)
                started = time.monotonic()
                try:
                    asyncio.run(runner.run(run))
                    row["generation_seconds"] = round(time.monotonic() - started, 3)
                    result = inspect_report(run)
                    counts = Counter(f["kind"] for f in result.get("findings", []))
                    row.update(status="complete", inspection_status=result["status"],
                               findings=dict(counts))
                except Exception as exc:
                    row.update(status="failed", error=f"{type(exc).__name__}: {exc}",
                               generation_seconds=round(time.monotonic() - started, 3))
                reviews = sorted((run / "inspection").glob("review-*/result.json"))
                row["inspection_calls"] = len(reviews)
                row["inspection_trace"] = review_trace(run)
                row["review_checks"] = [json.loads(p.read_text()) for p in reviews]
                row["feedback_changed_report"] = None
                row["final_matches_last_inspection"] = None
                if reviews and (run / "report.html").is_file():
                    final = (run / "report.html").read_bytes()
                    row["feedback_changed_report"] = (
                        reviews[0].with_name("report.html").read_bytes() != final)
                    row["final_matches_last_inspection"] = (
                        reviews[-1].with_name("report.html").read_bytes() == final)
                row["review_compliance"] = (
                    "not_applicable" if not review else
                    "checked_current_report" if reviews
                    and row["final_matches_last_inspection"]
                    and row["review_checks"][-1]["status"] == "checked" else
                    "missing_or_failed_or_stale_inspection"
                )
                row["usage"] = call_costs(run, {"transcript_reused_from": case["id"]}, None)
                rows.append(row)
                write_json(run / "evaluation.json", row)
                write_json(output / "results.json", rows)
                print(f"END {run.name}: {row['status']} ({row['generation_seconds']}s)", flush=True)
    lines = ["# Report inspection A/B", "",
             "Fixed local transcripts; no download or ASR. No automatic semantic/aesthetic score.",
             "Human review is pending. Findings count elements/candidates, not unique defects.",
             "", "| Case | Variant | State | Seconds | Inspection calls | Findings |",
             "|---|---|---|---:|---:|---|"]
    for row in rows:
        lines.append(f"| {row['case']} | {row['variant']} | {row['status']} | "
                     f"{row['generation_seconds']} | {row['inspection_calls']} | "
                     f"{json.dumps(row.get('findings'), ensure_ascii=False)} |")
    (output / "summary.md").write_text("\n".join(lines) + "\n")
    return rows


def main():
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        default=PROJECT_ROOT / "evals/report-review/manifest.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--repeats", type=int, choices=range(1, 4), default=1)
    args = parser.parse_args()
    output = args.output or PROJECT_ROOT / "runs" / (
        "report-eval-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    rows = evaluate(args.manifest.resolve(), output.resolve(),
                    timeout=args.timeout, repeats=args.repeats)
    print(output)
    return 0 if all(r["status"] == "complete" and r["inspection_status"] == "checked"
                    for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
