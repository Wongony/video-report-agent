import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from video_report_agent.migration import import_run
from video_report_agent.pipeline import create_run, write_json
from video_report_agent.reuse import cache_key, reuse_asr, reuse_transcript
from video_report_agent.storage import StaleExecution, Store
from video_report_agent.transcript_foundation import FUSION_VERSION, NORMALIZER_VERSION

URL = "BV1aTtb6uE7d"


def test_caller_connection_rolls_back_all_core_records(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    with pytest.raises(RuntimeError), store.transaction() as db:
        run = create_run(tmp_path, URL, database_path=store.path, db=db, export=False)
        assert store.run(run.name, db=db)["input"]["video_id"].endswith("p1")
        assert not run.exists()
        raise RuntimeError("after task insertion")
    with store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 0


def test_single_run_claim_and_cache_producer_fencing(tmp_path):
    runs = [create_run(tmp_path, URL) for _ in range(3)]
    store = Store(tmp_path / "state.sqlite3", initialize=False)
    with ThreadPoolExecutor(max_workers=8) as pool:

        def claim(_):
            try:
                return store.claim_run(runs[0].name)
            except StaleExecution:
                return None

        claims = list(pool.map(claim, range(8)))
    assert sum(value is not None for value in claims) == 1
    executions = [
        next(value for value in claims if value),
        store.claim_run(runs[1].name),
        store.claim_run(runs[2].name),
    ]
    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(
            pool.map(
                lambda pair: store.claim_cache("asr", "shared", pair[0].name, pair[1]),
                zip(runs, executions),
            )
        )
    assert [status for status, _ in outcomes].count("PRODUCE") == 1
    producer = [status for status, _ in outcomes].index("PRODUCE")
    owner, execution = runs[producer].name, executions[producer]
    with store.transaction() as db:
        db.execute("UPDATE runs SET supervisor_pid=? WHERE id=?", (os.getpid(), owner))
    assert store.reconcile_producer(owner, execution) is False
    assert store.cache("asr", "shared")["state"] == "PROCESSING"
    with store.transaction() as db:
        db.execute("UPDATE runs SET supervisor_pid=NULL WHERE id=?", (owner,))
    assert store.reconcile_producer(owner, execution)
    target = (producer + 1) % 3
    assert store.claim_cache("asr", "shared", runs[target].name, executions[target])[0] == "PRODUCE"
    with pytest.raises(StaleExecution):
        store.ready_cache("asr", "shared", owner, execution, {"old": True})
    store.ready_cache("asr", "shared", runs[target].name, executions[target], {"new": True})
    assert json.loads(store.cache("asr", "shared")["payload_json"]) == {"new": True}


@pytest.mark.parametrize(
    "field,value",
    [
        ("video_id", "bilibili-BV1aTtb6uE7d-p2"),
        ("asr_model", "other"),
        ("asr_parameters", {"other": True}),
        ("ocr_mode", "auto"),
        ("ocr_backend", "other"),
        ("ocr_roi", [1, 2, 3, 4]),
    ],
)
def test_cache_identity_settings_and_report_mode(tmp_path, field, value):
    run = create_run(tmp_path, URL)
    store = Store(tmp_path / "state.sqlite3", initialize=False)
    metadata = store.run(run.name)["input"]
    key = cache_key("transcript", metadata)
    assert cache_key("transcript", {**metadata, "report_mode": "brief"}) == key
    assert cache_key("transcript", {**metadata, field: value}) != key
    subtitle = {**metadata, "subtitle_file": "subtitle.srt"}
    assert cache_key("transcript", subtitle, {"subtitle.srt": "A"}) != cache_key(
        "transcript", subtitle, {"subtitle.srt": "B"}
    )


def test_processing_versions_and_downloaded_subtitles_change_identity(tmp_path, monkeypatch):
    from video_report_agent import reuse

    run = create_run(tmp_path, URL, transcript_mode="fused")
    metadata = Store(tmp_path / "state.sqlite3", initialize=False).run(run.name)["input"]
    original = cache_key("transcript", metadata, {"download/source.zh.vtt": "caption A"})
    assert original != cache_key("transcript", metadata, {"download/source.zh.vtt": "caption B"})
    monkeypatch.setattr(reuse, "NORMALIZER_VERSION", "future-normalizer")
    assert original != cache_key("transcript", metadata, {"download/source.zh.vtt": "caption A"})


def test_exited_worker_cache_recovers_while_service_supervisor_is_alive(tmp_path):
    run = create_run(tmp_path, URL)
    store = Store(tmp_path / "state.sqlite3", initialize=False)
    execution = store.claim_run(run.name)
    store.supervise(run.name, execution)
    store.claim_cache("asr", "exited", run.name, execution)
    store.result(run.name, execution, {"state": "FAILED"}, exited=True)
    assert store.reconcile_producer(run.name, execution)
    assert store.cache("asr", "exited")["state"] == "FAILED"


def test_explicit_import_text_reuse_survives_missing_exports(tmp_path):
    old = create_run(tmp_path, URL)
    metadata = json.loads((old / "input.json").read_text())
    metadata.update(title="Fixture", attribution="Bilibili")
    write_json(old / "input.json", metadata)
    asr = {"segments": [{"ordinal": 0, "start_ms": 0, "end_ms": 1000, "text": "hello"}]}
    write_json(old / "asr.json", asr)
    (old / "canonical-transcript.jsonl").write_text(
        json.dumps(
            {
                "unit_id": "unit-000001",
                "start_ms": 0,
                "end_ms": 1000,
                "canonical_text": "hello",
            }
        )
        + "\n"
    )
    (old / "source-text-events.jsonl").write_text("{}\n")
    (old / "accepted-multi-character-replacements.jsonl").write_text("{}\n")
    (old / "subtitle-ocr-events.jsonl").write_text("{}\n")
    (old / "subtitle-ocr-frame-candidates.jsonl").write_text("{}\n")
    write_json(
        old / "transcript-manifest.json",
        {
            "status": "READY",
            "video_id": metadata["video_id"],
            "transcript_mode": "asr-only",
            "normalizer_version": NORMALIZER_VERSION,
            "fusion_version": FUSION_VERSION,
            "canonical_unit_count": 1,
            "artifacts": {
                "canonical_transcript": "canonical-transcript.jsonl",
                "source_text_events": "source-text-events.jsonl",
                "transcript_manifest": "transcript-manifest.json",
                "accepted_multi_character_replacements": (
                    "accepted-multi-character-replacements.jsonl"
                ),
                "subtitle_ocr_events": "subtitle-ocr-events.jsonl",
                "subtitle_ocr_frame_candidates": "subtitle-ocr-frame-candidates.jsonl",
            },
        },
    )
    write_json(old / "status.json", {"state": "FAILED"})
    store = Store(tmp_path / "state.sqlite3", initialize=False)
    assert import_run(store, old)
    assert not import_run(store, old)
    for path in old.glob("*.json*"):
        path.unlink()
    new = create_run(tmp_path, URL, report_mode="brief")
    assert reuse_transcript(new, store.run(new.name)["input"]) == old.name
    assert "hello" in (new / "transcript.md").read_text()
    for name in (
        "accepted-multi-character-replacements.jsonl",
        "subtitle-ocr-events.jsonl",
        "subtitle-ocr-frame-candidates.jsonl",
    ):
        assert (new / name).read_text() == "{}\n"
    (new / "asr.json").unlink()
    assert reuse_asr(new, store.run(new.name)["input"]) == old.name
    assert json.loads((new / "asr.json").read_text()) == asr


def test_recovery_terminates_and_confirms_real_orphan_before_reclaim(tmp_path):
    import subprocess
    import sys

    run = create_run(tmp_path, URL)
    store = Store(tmp_path / "state.sqlite3", initialize=False)
    execution = store.claim_run(run.name)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", str(run), execution],
        start_new_session=True,
    )
    try:
        store.worker(run.name, execution, process.pid)
        store.claim_cache("asr", "orphan", run.name, execution)
        assert store.reconcile_producer(run.name, execution)
        process.wait(timeout=3)
        assert store.cache("asr", "orphan")["state"] == "FAILED"
        later = create_run(tmp_path, URL)
        new_execution = store.claim_run(later.name)
        assert store.claim_cache("asr", "orphan", later.name, new_execution)[0] == "PRODUCE"
        with pytest.raises(StaleExecution):
            store.ready_cache("asr", "orphan", run.name, execution, {"late": True})
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


@pytest.mark.parametrize("expired_media", [False, True])
def test_concurrent_pipeline_waits_and_calls_download_asr_once(
    tmp_path, monkeypatch, expired_media
):
    import threading
    import time
    from types import SimpleNamespace

    from video_report_agent import pipeline

    runs = [
        create_run(tmp_path, URL, **({"asr_model": f"whisper-config-{i}"} if expired_media else {}))
        for i in range(2)
    ]
    store = Store(tmp_path / "state.sqlite3", initialize=False)
    entered, release = threading.Event(), threading.Event()
    calls = []
    waiting = threading.Event()
    if expired_media:
        old = create_run(tmp_path, URL)
        execution = store.claim_run(old.name)
        info_body = json.dumps({"title": "Fixture", "uploader": "Fixture", "duration": 1})
        store.document(old.name, "download/source.info.json", info_body)
        store.artifact(old.name, "download/source.m4a", old / "download/source.m4a", "media")
        key = cache_key("download", store.run(old.name)["input"])
        store.claim_cache("download", key, old.name, execution)
        store.ready_cache(
            "download",
            key,
            old.name,
            execution,
            {"media": str(old / "download/source.m4a"), "documents": {}},
        )
        store.result(old.name, execution, {"state": "FAILED"}, exited=True)
        store.finalize(old.name, execution, {"state": "FAILED"})
        first_misses = threading.Barrier(2)
        original_reuse, original_claim = pipeline.reuse_download, Store.claim_cache
        seen = set()

        def reuse(source, run, **options):
            result = original_reuse(source, run, **options)
            if run.name not in seen:
                seen.add(run.name)
                assert result == (None, None)
                first_misses.wait(timeout=3)
            return result

        def claim(self, kind, *args):
            result = original_claim(self, kind, *args)
            if kind == "download" and result[0] == "WAIT":
                waiting.set()
            return result

        monkeypatch.setattr(pipeline, "reuse_download", reuse)
        monkeypatch.setattr(Store, "claim_cache", claim)

    def download(source, run, **options):
        calls.append("download")
        if expired_media:
            assert waiting.wait(3)
        (run / "download").mkdir()
        media, info = run / "download/source.m4a", run / "download/source.info.json"
        media.write_bytes(b"fixture")
        write_json(info, {"title": "Fixture", "uploader": "Fixture", "duration": 1})
        return SimpleNamespace(
            media_path=media, title="Fixture", uploader="Fixture", attribution="Bilibili"
        )

    def asr(*args, **options):
        calls.append("asr")
        entered.set()
        if not expired_media:
            assert release.wait(3)
        payload = {"segments": [{"ordinal": 0, "start_ms": 0, "end_ms": 1000, "text": "source"}]}
        return SimpleNamespace(to_dict=lambda: payload, segments=payload["segments"])

    def build(*args, **options):
        run = options["artifact_root"]
        unit = SimpleNamespace(unit_id="U001", start_ms=0, end_ms=1000, canonical_text="source")
        (run / "canonical-transcript.jsonl").write_text(json.dumps(vars(unit)) + "\n")
        write_json(
            run / "transcript-manifest.json",
            {
                "status": "READY",
                "video_id": store.run(run.name)["input"]["video_id"],
                "transcript_mode": "asr-only",
                "normalizer_version": NORMALIZER_VERSION,
                "fusion_version": FUSION_VERSION,
                "canonical_unit_count": 1,
                "artifacts": {"canonical_transcript": "canonical-transcript.jsonl"},
            },
        )
        return SimpleNamespace(canonical_units=[unit]), {}, {}

    async def report(run):
        calls.append("report")
        (run / "report.html").write_text("<html><body>fixture</body></html>")

    monkeypatch.setattr(pipeline, "download_bilibili_video", download)
    monkeypatch.setattr(
        pipeline,
        "extract_audio",
        lambda media, output: SimpleNamespace(path=output, probe=SimpleNamespace(duration_ms=1000)),
    )
    monkeypatch.setattr(pipeline, "transcribe_audio", asr)
    monkeypatch.setattr(pipeline, "build_transcript", build)
    monkeypatch.setattr(
        pipeline,
        "PiRunner",
        lambda **options: SimpleNamespace(run=report, provider="fixture", model="fixture"),
    )
    monkeypatch.setattr(pipeline, "render_report_image", lambda run: None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(pipeline.generate, runs[0])
        if not expired_media:
            assert entered.wait(3)
        second = pool.submit(pipeline.generate, runs[1])
        deadline = time.monotonic() + 3
        while store.run(runs[1].name)["execution_id"] is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert store.run(runs[1].name)["execution_id"]
        release.set()
        results = [first.result(timeout=5), second.result(timeout=5)]
        assert all(result["state"] == "RENDERED" for result in results), results
    assert calls.count("download") == 1
    assert calls.count("asr") == (2 if expired_media else 1)
    assert calls.count("report") == 2
    if expired_media:
        assert waiting.is_set()
        producer = store.cache("download", key)["producer_run"]
        waiter = next(run for run in runs if run.name != producer)
        assert store.run(waiter.name)["status"]["download_reused_from"] == producer
        with store.read() as db:
            assert (
                db.execute(
                    "SELECT source_run FROM run_cache_uses WHERE run_id=? AND kind='download'",
                    (waiter.name,),
                ).fetchone()[0]
                == producer
            )
    with store.read() as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM execution_events WHERE run_id=?", (runs[0].name,)
            ).fetchone()[0]
            > 0
        )


@pytest.mark.parametrize("stop", ["cancel", "deadline", "stale"])
def test_repeated_unavailable_media_reclaims_with_execution_checks(tmp_path, monkeypatch, stop):
    from video_report_agent import pipeline
    from video_report_agent.reuse import acquire_cache

    old = create_run(tmp_path, URL)
    store = Store(tmp_path / "state.sqlite3", initialize=False)
    execution = store.claim_run(old.name)
    key = cache_key("download", store.run(old.name)["input"])
    store.claim_cache("download", key, old.name, execution)
    store.ready_cache("download", key, old.name, execution, {"documents": {}})
    run = create_run(tmp_path, URL, asr_model="whisper-other-config")
    attempts = []

    def acquire(store, kind, key, run, execution_id):
        if kind == "download":
            attempts.append(kind)
            if len(attempts) == 3:
                if stop == "cancel":
                    (run / "cancel.requested").touch()
                elif stop == "deadline":
                    monkeypatch.setenv("VIDEO_REPORT_RUN_DEADLINE", "0")
                else:
                    execution_id = "stale-execution"
        return acquire_cache(store, kind, key, run, execution_id)

    monkeypatch.setattr(pipeline, "acquire_cache", acquire)
    monkeypatch.setattr(pipeline, "reuse_download", lambda *a, **k: (None, None))
    # Another producer can replace READY between invalidation and reclaim.
    monkeypatch.setattr(Store, "invalidate_media", lambda *a, **k: None)
    monkeypatch.setattr(
        pipeline, "download_bilibili_video", lambda *a, **k: pytest.fail("download without PRODUCE")
    )
    result = pipeline.generate(run)
    assert result["state"] == "FAILED"
    assert len(attempts) == 3
    assert {"cancel": "cancelled", "deadline": "deadline", "stale": "execution"}[stop] in result[
        "error"
    ].lower()
