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
        "<footer><span>视频 · 本地阅读报告</span>"
        "<span>vreport.tri4t.xyz</span></footer>"
    ) in template


def test_template_keeps_paper_white_and_canvas_light_gray():
    template = TEMPLATE.read_text()

    assert "--paper:#fff;--canvas:#f2f5f7;" in template


def test_bar_component_pins_block_display_on_fill():
    """柱图组件必须自带 display:block。

    历史故障：模型自写 `.bar-fill{height:100%}` 挂在 <span> 上，行内元素的
    width/height 不生效，getBoundingClientRect() 返回 0×0，整条柱子渲染成空白。
    """
    template = TEMPLATE.read_text()

    assert ".bars{" in template
    assert ".bar-track{" in template
    assert ".bar-fill{display:block;height:100%;" in template
    assert ".bar-value{" in template
