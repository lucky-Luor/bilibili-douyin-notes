# -*- coding: utf-8 -*-
"""bili_screenshot.py 纯函数的离线测试：parse_ts_seconds / auto_anchors / qc_check / ascii_label。

qc_check 用四类合成图（PIL 生成，纯本地）：
    纯白 → bright、白底+少量字 → ok、纯黑 → dark、噪声 → ok。
"""
import hashlib
import json
import random

import pytest
from PIL import Image, ImageDraw

import bili_screenshot


# ---------- parse_ts_seconds ----------

def test_parse_ts_seconds_basic():
    text = "[00:05] 开场\n不是时间行\n[12:34] 讲 BeanFactory\n"
    assert bili_screenshot.parse_ts_seconds(text) == [
        (5, "开场"), (754, "讲 BeanFactory")]


def test_parse_ts_seconds_empty():
    assert bili_screenshot.parse_ts_seconds("没有时间行") == []


def test_parse_ts_seconds_parses_all_lines():
    text = "[05:00] b\n[01:00] a"
    out = bili_screenshot.parse_ts_seconds(text)
    assert sorted(t for t, _ in out) == [60, 300]
    assert dict((t, s) for t, s in out)[60] == "a"


# ---------- auto_anchors ----------

def _lines():
    return [
        (10.0, "今天讲 Spring 的循环依赖问题"),
        (20.0, "循环依赖是面试重点，@Component 注解注册 Bean"),
        (30.0, "循环依赖的三级缓存设计很巧妙"),
        (40.0, "Bean 生命周期分为实例化、属性填充、初始化"),
        (200.0, "最后总结一下 Bean 生命周期的核心流程"),
    ]


def test_auto_anchors_chinese_keywords():
    jobs = bili_screenshot.auto_anchors(_lines())
    assert 0 < len(jobs) <= 3
    for label, keyword, caption, t in jobs:
        assert isinstance(label, str) and label
        assert isinstance(keyword, str) and keyword  # M2: 锚点原文
        assert "出现的画面" in caption
        assert t >= 2.0


def test_auto_anchors_times_spread_and_valid():
    jobs = bili_screenshot.auto_anchors(_lines())
    times = [t for *_, t in jobs]
    assert all(t >= 2.0 for t in times)
    for i in range(len(times)):
        for j in range(i + 1, len(times)):
            assert abs(times[i] - times[j]) >= 15  # 彼此至少错开15秒


def test_auto_anchors_max_n():
    jobs = bili_screenshot.auto_anchors(_lines(), max_n=2)
    assert len(jobs) <= 2


def test_auto_anchors_empty_input():
    assert bili_screenshot.auto_anchors([]) == []


def test_auto_anchors_prefers_repeated_terms():
    lines = [(10.0, "事务"), (20.0, "事务传播"), (30.0, "事务隔离级别")]
    jobs = bili_screenshot.auto_anchors(lines)
    assert jobs and any("事务" in label for label, *_ in jobs)


# ---------- qc_check（四类合成图） ----------

def _solid(color, size=(64, 64)):
    return Image.new("RGB", size, color)


def _white_with_text(size=(120, 120)):
    """白底 + 少量黑色字形（模拟白底 PPT），近白占比 < 98.5%。"""
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    for y in (30, 60, 90):
        d.rectangle([10, y, 110, y + 4], fill=(10, 10, 10))  # 模拟文字行
    return img


def _noise(size=(80, 80), seed=42):
    import numpy as np
    rng = np.random.RandomState(seed)
    arr = rng.randint(0, 256, size=(size[1], size[0]), dtype="uint8")
    return Image.fromarray(arr, mode="L").convert("RGB")


def test_qc_pure_white_is_bright():
    assert bili_screenshot.qc_check(_solid((255, 255, 255))) == "bright"


def test_qc_white_bg_with_text_is_ok():
    assert bili_screenshot.qc_check(_white_with_text()) == "ok"


def test_qc_pure_black_is_dark():
    assert bili_screenshot.qc_check(_solid((0, 0, 0))) == "dark"


def test_qc_noise_is_ok():
    assert bili_screenshot.qc_check(_noise()) == "ok"


def test_qc_tiny_image_is_ok():
    assert bili_screenshot.qc_check(Image.new("RGB", (2, 2), (128, 128, 128))) == "ok"


def test_qc_qc_thresholds_unchanged():
    """QC 常量口径（像素占比法）不得被无意改动。"""
    assert bili_screenshot.QC_DARK_FRAC == 0.97
    assert bili_screenshot.QC_WHITE_FRAC == 0.985
    assert bili_screenshot.QC_BLURRY == 10.0


# ---------- ascii_label（--ascii-names 口径） ----------

def test_ascii_label_ascii_passthrough():
    assert bili_screenshot.ascii_label("spring-bean") == "spring-bean"


def test_ascii_label_chinese_becomes_zh_md5():
    out = bili_screenshot.ascii_label("循环依赖")
    assert out == "zh-" + hashlib.md5("循环依赖".encode("utf-8")).hexdigest()[:6]
    assert out.isascii()


# ---------- M2: manifest keyword 字段 / M9: 孤儿 PNG 清理（main 集成，全 mock） ----------

def _run_main(tmp_path, monkeypatch, capsys, old_manifest=None):
    """mock 网络/解码，跑 main()，返回 (manifest dict, stdout 末行 JSON)。"""
    import PIL.Image

    meta = {"bvid": "BV1xx411c7mD",
            "pages": [{"page": 1, "part": "P1", "cid": 1, "duration": 600}]}
    (tmp_path / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False),
                                            encoding="utf-8")
    txt_dir = tmp_path / "txt"
    txt_dir.mkdir()
    (txt_dir / "01_P1.txt").write_text("[00:10] 循环依赖的三级缓存\n", encoding="utf-8")
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    if old_manifest is not None:
        (img_dir / "manifest.json").write_text(
            json.dumps(old_manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bili_screenshot, "load_anchors",
                        lambda: {1: [("循环依赖", "循环依赖的图示", ["循环依赖"])]})
    monkeypatch.setattr(bili_screenshot, "load_sessdata", lambda: "")
    monkeypatch.setattr(bili_screenshot, "download_video",
                        lambda bvid, cid, dest, cookie="": dest.write_bytes(b"fake") or dest)
    monkeypatch.setattr(bili_screenshot, "grab_frames",
                        lambda video, times: [PIL.Image.new("RGB", (4, 4)) for _ in times])
    monkeypatch.setattr(bili_screenshot, "qc_check", lambda img: "ok")

    bili_screenshot.main([str(tmp_path / "metadata.json"), str(txt_dir), str(img_dir), "1"])
    manifest = json.loads((img_dir / "manifest.json").read_text(encoding="utf-8"))
    out = capsys.readouterr().out
    last = json.loads(out.strip().splitlines()[-1])
    return img_dir, manifest, last


def test_main_manifest_and_stdout_have_keyword(tmp_path, monkeypatch, capsys):
    img_dir, manifest, last = _run_main(tmp_path, monkeypatch, capsys)
    assert manifest["frames"], "应有成功帧"
    for f in manifest["frames"]:
        assert f["keyword"] == "循环依赖"  # M2: manifest 每条 frame 带 keyword
    # stdout 进度行 frames 与 manifest 同构，同样带 keyword
    assert last["frames"] and last["frames"][0]["keyword"] == "循环依赖"


def test_main_keyword_keeps_original_with_ascii_names(tmp_path, monkeypatch, capsys):
    import PIL.Image
    meta = {"bvid": "BV1xx411c7mD",
            "pages": [{"page": 1, "part": "P1", "cid": 1, "duration": 600}]}
    (tmp_path / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False),
                                            encoding="utf-8")
    txt_dir = tmp_path / "txt"
    txt_dir.mkdir()
    (txt_dir / "01_P1.txt").write_text("[00:10] 循环依赖的三级缓存\n", encoding="utf-8")
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    monkeypatch.setattr(bili_screenshot, "load_anchors",
                        lambda: {1: [("循环依赖", "循环依赖的图示", ["循环依赖"])]})
    monkeypatch.setattr(bili_screenshot, "load_sessdata", lambda: "")
    monkeypatch.setattr(bili_screenshot, "download_video",
                        lambda bvid, cid, dest, cookie="": dest.write_bytes(b"fake") or dest)
    monkeypatch.setattr(bili_screenshot, "grab_frames",
                        lambda video, times: [PIL.Image.new("RGB", (4, 4)) for _ in times])
    monkeypatch.setattr(bili_screenshot, "qc_check", lambda img: "ok")
    bili_screenshot.main([str(tmp_path / "metadata.json"), str(txt_dir), str(img_dir),
                          "1", "--ascii-names"])
    manifest = json.loads((img_dir / "manifest.json").read_text(encoding="utf-8"))
    f = manifest["frames"][0]
    assert f["file"].isascii()           # label 转 ASCII 文件名
    assert f["keyword"] == "循环依赖"     # keyword 保留锚点原文


def test_write_manifest_cleans_orphan_pngs_on_overwrite(tmp_path):
    """M9：重跑覆盖页时，该页不再被引用的旧 PNG 被删除；保留页与跨分P不受影响。"""
    old = {"frames": [
        {"file": "p01_keep.png", "page": 1, "t": 5},
        {"file": "p01_orphan.png", "page": 1, "t": 50},
        {"file": "p02_keep.png", "page": 2, "t": 5},
    ], "failed": []}
    for fn in ("p01_keep.png", "p01_orphan.png", "p02_keep.png"):
        (tmp_path / fn).write_bytes(b"png")
    (tmp_path / "manifest.json").write_text(json.dumps(old, ensure_ascii=False),
                                            encoding="utf-8")
    bili_screenshot.write_manifest(tmp_path, [{"file": "p01_keep.png", "page": 1}],
                                   [], touched={1})
    assert (tmp_path / "p01_keep.png").exists()   # 新记录仍引用
    assert not (tmp_path / "p01_orphan.png").exists()  # 被覆盖页的孤儿 → 删除
    assert (tmp_path / "p02_keep.png").exists()   # 跨分P合并逻辑不变
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    files = [f["file"] for f in manifest["frames"]]
    assert "p01_orphan.png" not in files
    assert "p02_keep.png" in files


def test_write_manifest_keeps_files_when_page_untouched(tmp_path):
    (tmp_path / "a.png").write_bytes(b"png")
    old = {"frames": [{"file": "a.png", "page": 3}], "failed": []}
    bili_screenshot.write_manifest(tmp_path, [{"file": "b.png", "page": 1}],
                                   [], touched={1})
    assert (tmp_path / "a.png").exists()
