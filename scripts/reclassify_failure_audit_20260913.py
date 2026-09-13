"""Apply the supplied 2026-09-13 audit to its 11 terminal runs only.

Usage: uv run python scripts/reclassify_failure_audit_20260913.py /path/to/runs
Original fields are retained in failure_audit_original; repeat execution is safe.
No missing HTTP/provider codes are inferred from historical generic exceptions.
"""

import argparse
import json
from pathlib import Path

from video_report_agent.pipeline import write_json

AUDITED = {
    **dict.fromkeys(
        ("7b4a2b07", "c39930c1", "c6056540", "cf53bba4", "a27e4281"),
        ("BV1tu4y177hn-p1", "duration"),
    ),
    "39aae247": ("BV12G411X7go-p1", "duration"),
    **dict.fromkeys(("959b1976", "4d552acf", "4985a531", "ae1135ca"), ("BV1VKYE6oEmk-p1", "asr")),
    "d9e83c73": ("BV1a6Yx62EH4-p3", "asr"),
}


def reclassify(root):
    results = []
    for prefix, (video, kind) in AUDITED.items():
        paths = list(root.glob(f"{prefix}*/status.json"))
        if len(paths) != 1:
            results.append((prefix, "missing or ambiguous; unchanged"))
            continue
        path = paths[0]
        status = json.loads(path.read_text())
        metadata = json.loads((path.parent / "input.json").read_text())
        if status.get("failure_audit_source") == "failure-audit-2026-09-13.html":
            results.append((prefix, "already classified"))
            continue
        signature = "yt-dlp failed" if kind == "duration" else "Paraformer submit: HTTPStatusError"
        if (
            status.get("state") != "FAILED"
            or status.get("error") != signature
            or metadata.get("video_id", "").removeprefix("bilibili-") != video
        ):
            results.append((prefix, "evidence mismatch; unchanged"))
            continue
        status["failure_audit_original"] = {
            key: status.get(key) for key in ("error", "error_category", "error_code")
        }
        status["failure_audit_source"] = "failure-audit-2026-09-13.html"
        if kind == "duration":
            status.update(
                error_category="INPUT_REJECTED",
                error_code="VIDEO_DURATION_INVALID",
                error="当前支持的视频最长为 3 小时，请选择不超过 3 小时的视频。",
            )
        else:
            status.update(
                error_category="EXTERNAL_API_FAILURE",
                error_code="ASR_REQUEST_REJECTED",
                error="语音识别服务拒绝了请求；历史日志未保留具体错误码，无法确认是否欠费或限流。",
            )
        write_json(path, status)
        results.append((prefix, "classified"))
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path)
    args = parser.parse_args()
    for prefix, result in reclassify(args.runs):
        print(f"{prefix}: {result}")
