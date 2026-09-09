import struct

from video_report_agent.report_image import render_report_image


def test_desktop_full_page_and_cached_image(tmp_path):
    (tmp_path / "report.html").write_text('''<!doctype html><style>
      body {margin:0; height:3200px; background:#fff}
      @media(max-width:900px) {body {height:100px}}
    </style><h1>桌面长图</h1><p style="position:absolute;top:3100px">页面底部</p>''')
    output = render_report_image(tmp_path)
    content = output.read_bytes()
    assert content[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", content[16:24])
    assert width == 920
    assert height >= 3200
    (tmp_path / "report.html").unlink()
    assert render_report_image(tmp_path).read_bytes() == content


def test_old_wide_image_is_regenerated(tmp_path):
    (tmp_path / "report.html").write_text("<html><body>报告</body></html>")
    output = tmp_path / "report.png"
    output.write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 1440, 20)
    )
    content = render_report_image(tmp_path).read_bytes()
    width = struct.unpack(">I", content[16:20])[0]
    assert width == 920
