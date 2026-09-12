"""Fill report content that is owned by the local report pipeline."""

from __future__ import annotations

import html
import json
from pathlib import Path

VIDEO_DESCRIPTION_PLACEHOLDER = "{{VIDEO_DESCRIPTION}}"
VIDEO_DESCRIPTION_START = "<!-- VIDEO_DESCRIPTION_START -->"
VIDEO_DESCRIPTION_END = "<!-- VIDEO_DESCRIPTION_END -->"


def _read_video_description(run_dir: Path) -> str | None:
    metadata_path = run_dir / "download" / "source.info.json"
    if not metadata_path.is_file():
        return None

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {metadata_path}") from exc

    if not isinstance(metadata, dict):
        return None
    description = metadata.get("description")
    if not isinstance(description, str) or not description.strip():
        return None
    return description


def _boundary_span(report_html: str) -> tuple[int, int] | None:
    start_count = report_html.count(VIDEO_DESCRIPTION_START)
    end_count = report_html.count(VIDEO_DESCRIPTION_END)
    if start_count != end_count:
        raise ValueError("Malformed video description boundaries: start/end markers do not match")
    if start_count > 1:
        raise ValueError("Duplicate video description boundaries")
    if start_count == 0:
        return None

    start = report_html.index(VIDEO_DESCRIPTION_START)
    end = report_html.index(VIDEO_DESCRIPTION_END)
    if end < start:
        raise ValueError("Malformed video description boundaries: end marker precedes start marker")
    return start, end + len(VIDEO_DESCRIPTION_END)


def _render_video_description(description: str) -> str:
    escaped = html.escape(description)
    return (
        f"{VIDEO_DESCRIPTION_START}\n"
        "<details><summary>视频简介（展开）</summary>"
        f'<div class="video-description-text">{escaped}</div></details>\n'
        f"{VIDEO_DESCRIPTION_END}"
    )


def fill_video_description(report_path: Path, run_dir: Path) -> None:
    """Fill or remove the template's video-description region in ``report_path``.

    The source metadata is always read from ``run_dir``.  The generated region is
    delimited by comments so a later inspection can apply the same transformation
    to a copy without accumulating another disclosure block.
    """

    report_path = Path(report_path)
    run_dir = Path(run_dir)
    report_html = report_path.read_text(encoding="utf-8")
    boundary = _boundary_span(report_html)

    # A placeholder inside an already-filled description is user content.  Count
    # placeholders only outside the owned region so filling remains idempotent.
    placeholder_html = report_html
    if boundary is not None:
        placeholder_html = report_html[: boundary[0]] + report_html[boundary[1] :]
    placeholder_count = placeholder_html.count(VIDEO_DESCRIPTION_PLACEHOLDER)
    if placeholder_count > 1:
        raise ValueError("Duplicate {{VIDEO_DESCRIPTION}} placeholders")
    if boundary is not None and placeholder_count:
        raise ValueError("Video description has both a boundary and a placeholder")

    description = _read_video_description(run_dir)
    if description is None:
        if boundary is not None:
            report_html = report_html[: boundary[0]] + report_html[boundary[1] :]
        elif placeholder_count:
            report_html = report_html.replace(VIDEO_DESCRIPTION_PLACEHOLDER, "")
        else:
            return
    elif boundary is not None:
        report_html = (
            report_html[: boundary[0]]
            + _render_video_description(description)
            + report_html[boundary[1] :]
        )
    elif placeholder_count == 1:
        report_html = report_html.replace(
            VIDEO_DESCRIPTION_PLACEHOLDER, _render_video_description(description)
        )
    else:
        raise ValueError(
            "Non-empty video description requires one {{VIDEO_DESCRIPTION}} placeholder "
            "or a complete boundary"
        )

    report_path.write_text(report_html, encoding="utf-8")
