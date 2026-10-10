"""Explicit offline import of legacy run materials; never used by online queries."""

import json
import time
from pathlib import Path

from .reuse import cache_key, valid_asr_payload, validated_transcript
from .storage import TERMINAL, TEXT_FILES, Store, encode


def read_json(path):
    try:
        payload = json.loads(path.read_text())
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def inventory(root):
    result = {"runs": [], "invalid": []}
    for run in sorted(Path(root).iterdir()):
        if not run.is_dir() or not (run / "status.json").is_file():
            continue
        status = read_json(run / "status.json")
        if status.get("run_id", run.name) != run.name or status.get("state") == "COMPLETE":
            continue
        if not status:
            result["invalid"].append(str(run / "status.json"))
            continue
        result["runs"].append(
            {
                "id": run.name,
                "state": status.get("state"),
                "has_input": bool(read_json(run / "input.json")),
                "has_report": (run / "report.html").is_file(),
            }
        )
    return result


def import_run(store: Store, source: Path, *, workspace=None):
    """Keep original IDs and unknown facts; cache only structurally valid text."""
    source = source.resolve()
    workspace = Path(workspace or source).resolve()
    marker = str(source / "status.json")
    with store.read() as db:
        if db.execute("SELECT 1 FROM import_sources WHERE source=?", (marker,)).fetchone():
            return False
        existing = db.execute(
            "SELECT managed,execution_id,finalized FROM runs WHERE id=?", (source.name,)
        ).fetchone()
        if existing and any(existing):
            return False  # Database-owned execution facts must not be replaced by exports.
    metadata = read_json(source / "input.json")
    status = read_json(source / "status.json")
    status.setdefault("run_id", source.name)
    names = list(TEXT_FILES)
    if metadata.get("subtitle_file"):
        names.append(metadata["subtitle_file"])
    names.extend(
        str(path.relative_to(source))
        for path in (source / "download").glob("source.*")
        if path.suffix in {".srt", ".vtt", ".ass"}
    )
    documents = {name: (source / name).read_text() for name in names if (source / name).is_file()}
    validated = validated_transcript(source, metadata) if metadata.get("video_id") else None
    if validated and "transcript.md" not in documents:
        _, units = validated
        documents["transcript.md"] = "\n".join(
            [
                f"# {metadata.get('title', '')}",
                metadata.get("attribution", ""),
                "",
                *[
                    f"[{u['unit_id']} | {u['start_ms'] / 1000:.3f}–{u['end_ms'] / 1000:.3f}s] "
                    f"{u['canonical_text']}"
                    for u in units
                ],
            ]
        )
    with store.transaction() as db:
        if not db.execute("SELECT 1 FROM runs WHERE id=?", (source.name,)).fetchone():
            store.register(workspace, metadata, status, db=db)
        else:
            # Explicit importer is also useful for assembling isolated legacy fixtures.
            db.execute(
                "UPDATE runs SET input_json=?,status_json=?,workspace=? WHERE id=?",
                (encode(metadata), encode(status), str(workspace), source.name),
            )
        terminal = status.get("state") in TERMINAL
        execution = "historical:" + source.name
        db.execute(
            "UPDATE runs SET execution_id=?,execution_state=?,finalized=?,result_json=?,"
            "result_execution_id=? WHERE id=?",
            (
                execution if terminal else None,
                "EXITED" if terminal else "QUEUED",
                int(terminal),
                encode(status) if terminal else None,
                execution if terminal else None,
                source.name,
            ),
        )
        db.execute(
            "UPDATE runs SET updated_at=? WHERE id=?",
            ((source / "status.json").stat().st_mtime, source.name),
        )
        for name, body in documents.items():
            store.document(source.name, name, body, db=db)
        if (source / "report.html").is_file():
            db.execute(
                "INSERT INTO reports VALUES (?,?,NULL,?,?) ON CONFLICT(run_id) DO NOTHING",
                (
                    source.name,
                    source.name,
                    (source / "report.html").read_text(),
                    status.get("finished_at"),
                ),
            )
        media = [
            path
            for path in (source / "download").glob("source.*")
            if path.suffix
            in {
                ".mp4",
                ".m4a",
                ".webm",
                ".opus",
                ".mp3",
                ".ogg",
                ".aac",
                ".flac",
                ".wav",
                ".mkv",
            }
            and path.is_file()
            and path.stat().st_size
        ]
        for path in [
            *media,
            source / "audio.wav",
            source / "report.png",
            *[p for p in (source / "assets").rglob("*") if p.is_file()],
        ]:
            if path.is_file():
                name = str(path.relative_to(source))
                store.artifact(
                    source.name,
                    name,
                    workspace / name,
                    "media" if path in media or name == "audio.wav" else "image",
                    db=db,
                )
        if metadata.get("asr_backend") and metadata.get("video_id"):
            asr = read_json(source / "asr.json")
            for kind, valid, payload in (
                ("asr", valid_asr_payload(asr), {"asr": asr}),
                ("transcript", bool(validated), {"documents": documents, "metadata": metadata}),
                (
                    "download",
                    bool(media) and bool(documents.get("download/source.info.json")),
                    {
                        "media": str(workspace / media[0].relative_to(source)),
                        "documents": {
                            name: body
                            for name, body in documents.items()
                            if name.startswith("download/")
                        },
                    }
                    if media
                    else {},
                ),
            ):
                if not valid:
                    continue
                key = cache_key(kind, metadata, documents)
                db.execute(
                    "INSERT INTO cache_entries VALUES (?,?,'READY',?,?,NULL,?,NULL,?) "
                    "ON CONFLICT(kind,cache_key) DO NOTHING",
                    (kind, key, source.name, execution, encode(payload), time.time()),
                )
        db.execute(
            "INSERT INTO import_sources VALUES (?,'run',?,?)",
            (marker, encode({"input": metadata, "status": status}), time.time()),
        )
    return True
