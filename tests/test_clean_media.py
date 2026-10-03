# -*- coding: utf-8 -*-
"""clean_media.py：媒体缓存清理（dry-run/apply）测试。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import clean_media  # noqa: E402


def _make_outdir(tmp_path: Path, with_meta: bool = True) -> Path:
    outdir = tmp_path / "douyin-notes-x"
    outdir.mkdir()
    if with_meta:
        (outdir / "metadata.json").write_text(json.dumps({
            "platform": "douyin",
            "pages": [{"page": 1, "media_path": "media_p01.mp4"}],
        }, ensure_ascii=False), encoding="utf-8")
        (outdir / "media_p01.mp4").write_bytes(b"v" * 128)
    (outdir / ".audio_01.m4s").write_bytes(b"a" * 16)
    (outdir / ".video_02.m4s").write_bytes(b"v" * 16)
    (outdir / "01_第一课.txt").write_text("笔记正文", encoding="utf-8")
    return outdir


def test_collect_finds_media_and_temp_only(tmp_path):
    outdir = _make_outdir(tmp_path)
    names = {f.name for f in clean_media.collect(outdir)}
    assert names == {"media_p01.mp4", ".audio_01.m4s", ".video_02.m4s"}


def test_collect_without_meta_still_sweeps_temp(tmp_path):
    outdir = _make_outdir(tmp_path, with_meta=False)
    names = {f.name for f in clean_media.collect(outdir)}
    assert names == {".audio_01.m4s", ".video_02.m4s"}


def test_dry_run_does_not_delete(tmp_path, capsys):
    outdir = _make_outdir(tmp_path)
    clean_media.collect(outdir)  # collect 本身无副作用
    assert (outdir / "media_p01.mp4").exists()
    assert (outdir / ".audio_01.m4s").exists()


def test_apply_deletes_media_and_temp_keeps_notes(tmp_path):
    outdir = _make_outdir(tmp_path)
    for f in clean_media.collect(outdir):
        f.unlink()
    assert not (outdir / "media_p01.mp4").exists()
    assert not (outdir / ".audio_01.m4s").exists()
    assert (outdir / "01_第一课.txt").exists()  # 笔记与转写稿不受影响
    assert not list(outdir.glob("*.tmp"))


def test_cli_apply_json_summary(tmp_path, capsys):
    outdir = _make_outdir(tmp_path)
    from io import StringIO
    import contextlib
    sys.argv = ["clean_media.py", str(outdir), "--apply"]
    buf, err = StringIO(), StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        clean_media.main()
    summary = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert summary["status"] == "ok" and summary["mode"] == "apply"
    assert summary["found"] == 3 and summary["deleted"] == 3
