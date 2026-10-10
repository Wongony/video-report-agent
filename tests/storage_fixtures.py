"""Explicit legacy import for old isolated file fixtures, before exercising SQL paths."""

from video_report_agent.migration import import_run
from video_report_agent.storage import Store


def import_fixtures(root):
    store = Store(root / "state.sqlite3")
    for run in root.iterdir():
        if run.is_dir() and (run / "status.json").exists():
            import_run(store, run)
    return store
