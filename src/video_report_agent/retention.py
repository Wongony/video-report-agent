"""Prune successful runs' media while retaining reports and source text."""

import json
import logging
import os
import time
from pathlib import Path


def cleanup_media(root: Path) -> None:
    """Zero disables each limit; when both are set, either limit expires media."""
    try:
        keep = int(os.getenv("MEDIA_KEEP_LAST", "20"))
        days = int(os.getenv("MEDIA_MAX_AGE_DAYS", "0"))
        if keep < 0 or days < 0:
            raise ValueError("media retention limits must be non-negative")
        completed = []
        for run in root.iterdir():
            if not run.is_dir() or run.is_symlink():
                continue
            status = run / "status.json"
            try:
                if json.loads(status.read_text()).get("state") == "RENDERED":
                    completed.append((status.stat().st_mtime, run))
            except (OSError, ValueError, AttributeError):
                continue
        cutoff = time.time() - days * 86400
        for index, (finished, run) in enumerate(sorted(completed, reverse=True)):
            if not ((keep and index >= keep) or (days and finished < cutoff)):
                continue
            # Keep subtitles, metadata, report assets and all diagnostic text.
            paths = [run / "audio.wav"]
            download = run / "download"
            if not download.is_symlink():
                paths.extend(download.glob("source*.mp4"))
            for path in paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logging.exception("Could not remove expired media: %s", path)
    except (OSError, ValueError):
        # A housekeeping failure must not change a report's successful status.
        logging.exception("Media cleanup failed")
