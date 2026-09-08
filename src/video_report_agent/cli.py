"""Local web and real URL pipeline entry points."""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from .pi import PROJECT_ROOT
from .pipeline import create_run, generate
from .web import create_server


def main():
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, default=Path("runs"))
    sub = parser.add_subparsers(dest="command", required=True)
    web = sub.add_parser("web")
    web.add_argument("--port", type=int, default=8765)
    run = sub.add_parser("generate")
    run.add_argument("url")
    run.add_argument("--transcript-mode", choices=["asr-only", "fused"], default="asr-only")
    run.add_argument("--ocr-mode", choices=["off", "auto", "roi", "on"], default="off")
    run.add_argument("--ocr-roi")
    run.add_argument("--subtitle-file", type=Path)
    run.add_argument("--asr-backend", choices=["mlx", "paraformer"])
    run.add_argument("--asr-model")
    run.add_argument("--ocr-backend", choices=["rapidocr"])
    compare = sub.add_parser("compare-asr")
    compare.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "compare-asr":
        from .compare_asr import compare_asr

        output = compare_asr(args.manifest, args.runs)
        print(output)
        return 0 if json.loads((output / "status.json").read_text())["state"] == "COMPLETE" else 1
    if args.command == "generate":
        status = generate(
            create_run(
                args.runs,
                args.url,
                transcript_mode=args.transcript_mode,
                ocr_mode=args.ocr_mode,
                ocr_roi=args.ocr_roi,
                subtitle_file=args.subtitle_file,
                asr_backend=args.asr_backend,
                asr_model=args.asr_model,
                ocr_backend=args.ocr_backend,
            )
        )
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0 if status["state"] == "RENDERED" else 1
    server = create_server(args.runs, args.port)
    print(f"Video Report: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
