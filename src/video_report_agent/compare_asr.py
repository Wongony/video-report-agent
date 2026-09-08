"""Fixed-material, fresh ASR-only comparison; no report model or fusion."""

import argparse
import difflib
import json
import subprocess
import uuid
from pathlib import Path

from .asr import transcribe_audio
from .audio import probe_audio
from .media_config import resolve_media_config
from .paraformer import CloudAsrError
from .pipeline import write_json
from .transcript import build_transcript


def compare_asr(manifest_path: Path, runs: Path) -> Path:
    manifest = json.loads(manifest_path.read_text())
    run = runs.resolve() / ("asr-compare-" + uuid.uuid4().hex)
    run.mkdir(parents=True)
    configs, unavailable = {}, {}
    # Resolve both explicitly; ASR_MODEL must not leak across providers.
    from .asr import MlxWhisperBackend
    from .paraformer import ParaformerBackend

    for name, model in (
        ("mlx", MlxWhisperBackend.default_model),
        ("paraformer", ParaformerBackend.default_model),
    ):
        try:
            configs[name] = resolve_media_config(name, model, "rapidocr")
        except ValueError as exc:
            unavailable[name] = str(exc)
    write_json(
        run / "input.json",
        {
            "kind": "asr-comparison",
            "manifest": manifest,
            "configs": configs,
            "unavailable": unavailable,
            "transcript_mode": "asr-only",
            "ocr_mode": "off",
        },
    )
    summary = []
    for clip in manifest["clips"]:
        clip_id = clip["id"]
        if Path(clip_id).name != clip_id or clip_id in {".", ".."}:
            raise ValueError("Clip id must be a simple directory name")
        root = run / clip_id
        root.mkdir()
        source = Path(clip["source_path"]).expanduser()
        if not source.is_absolute():
            source = (manifest_path.resolve().parent / source).resolve()
        offset = clip["start_ms"]
        duration = clip["duration_ms"]
        if offset < 0 or duration <= 0:
            raise ValueError("Clip range must have non-negative start and positive duration")
        audio = root / "audio.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-i",
                str(source),
                "-ss",
                str(offset / 1000),
                "-t",
                str(duration / 1000),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(audio),
            ],
            check=True,
            capture_output=True,
        )
        actual_duration = probe_audio(audio).duration_ms
        entry = {
            "id": clip_id,
            "source_path": str(source),
            "source_offset_ms": offset,
            "audio_duration_ms": actual_duration,
            "results": {},
            "review_status": "待人工核听；文本差异不等于错误",
            "cer": None,
        }
        texts = {}
        for name in ("mlx", "paraformer"):
            output = root / name
            output.mkdir()
            if name in unavailable:
                entry["results"][name] = {"state": "NOT_RUN", "reason": unavailable[name]}
                continue
            config = configs[name]
            write_json(output / "config.json", config)
            try:
                result = transcribe_audio(
                    audio,
                    model=config["asr_model"],
                    backend=name,
                    base_url=config["asr_base_url"],
                    parameters=config["asr_parameters"],
                )
                write_json(output / "asr.json", result.to_dict())
                build, _, _ = build_transcript(
                    argparse.Namespace(transcript_mode="asr-only", ocr_mode="off"),
                    source_path=audio,
                    artifact_root=output,
                    asr_path=output / "asr.json",
                    source_duration_ms=actual_duration,
                    manifest={"video_id": clip_id},
                )
                texts[name] = "\n".join(u.canonical_text for u in build.canonical_units)
                (output / "transcript.txt").write_text(texts[name])
                write_json(
                    output / "source-mapping.json",
                    {
                        "timestamp_semantics": "asr timestamps are relative to audio.wav",
                        "source_offset_ms": offset,
                        "segments": [
                            {
                                "ordinal": s.ordinal,
                                "source_start_ms": offset + s.start_ms,
                                "source_end_ms": offset + s.end_ms,
                            }
                            for s in result.segments
                        ],
                    },
                )
                terms = clip.get("review_terms", [])
                entry["results"][name] = {
                    "state": "READY",
                    "elapsed_ms": result.elapsed_ms,
                    "rtf": round(result.elapsed_ms / actual_duration, 4),
                    "timings": result.timings,
                    "usage": result.usage,
                    "segment_count": len(result.segments),
                    "first_start_ms": result.segments[0].start_ms,
                    "last_end_ms": result.segments[-1].end_ms,
                    "review_term_occurrences": {t: texts[name].count(t) for t in terms},
                    "cost": {
                        "estimate": None,
                        "bill": None,
                        "note": "No verified unit price or bill supplied; usage retained",
                    },
                }
            except Exception as exc:
                error = (
                    exc.to_dict()
                    if isinstance(exc, CloudAsrError)
                    else {"stage": "asr_or_canonical", "detail": type(exc).__name__}
                )
                write_json(output / "error.json", error)
                entry["results"][name] = {"state": "FAILED", "error": error}
            write_json(run / "comparison.json", {"clips": [*summary, entry]})
        if len(texts) == 2:
            diff = "\n".join(
                difflib.unified_diff(
                    texts["mlx"].splitlines(),
                    texts["paraformer"].splitlines(),
                    fromfile="mlx",
                    tofile="paraformer",
                    lineterm="",
                )
            )
            (root / "text.diff").write_text(diff)
        summary.append(entry)
        write_json(run / "comparison.json", {"clips": summary})
    lines = [
        "# ASR 对照",
        "",
        "ASR-only / OCR off；每个 backend 每段新跑一次。",
        "时间戳相对 audio.wav；source-mapping.json 单独映射原视频位置。",
        "没有人工真值，不计算 CER。术语出现次数与文本差异不代表准确率。",
        "",
        "|材料|Backend|状态|耗时 ms|RTF|",
        "|---|---|---|---:|---:|",
    ]
    for entry in summary:
        for name, result in entry["results"].items():
            lines.append(
                f"|{entry['id']}|{name}|{result['state']}|"
                f"{result.get('elapsed_ms', '—')}|{result.get('rtf', '—')}|"
            )
    lines += [
        "",
        "人工核听：逐段检查开头、中间、结尾的语音与时间边界；核对术语疑点。",
        "费用：comparison.json 保留服务计量；缺少核实单价时不推算费用。",
    ]
    (run / "comparison.md").write_text("\n".join(lines))
    complete = all(r["state"] == "READY" for c in summary for r in c["results"].values())
    write_json(
        run / "status.json",
        {"state": "COMPLETE" if complete else "INCOMPLETE", "human_review": "PENDING"},
    )
    return run
