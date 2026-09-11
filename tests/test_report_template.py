from pathlib import Path

TEMPLATE = (
    Path(__file__).parents[1]
    / "src/video_report_agent/skills/video-report/assets/report-template.html"
)


def test_video_description_placeholder_reuses_source_details_contract():
    template = TEMPLATE.read_text()

    assert "{{VIDEO_DESCRIPTION}}" in template
    assert "<details><summary>来源与处理说明</summary>{{SOURCES}}</details>" in template
    assert (
        ".video-description-text{white-space:pre-wrap;overflow-wrap:anywhere;"
        "word-break:break-word}"
    ) in template


def test_footer_keeps_report_label_left_and_domain_right():
    template = TEMPLATE.read_text()

    assert "footer{display:flex;align-items:baseline;justify-content:space-between;" in template
    assert (
        "<footer><span>精读 · 本地阅读报告</span>"
        "<span>vreport.tri4t.xyz</span></footer>"
    ) in template
