# -*- coding: utf-8 -*-
"""bili_transcribe.py 稳定部分的离线测试（faster-whisper 未安装也不失败）。"""
import json

import pytest

import bili_transcribe


# ---------- safe_name ----------

def test_safe_name():
    assert bili_transcribe.safe_name('第1节:入门?"a"') == "第1节_入门__a_"
    assert len(bili_transcribe.safe_name("x" * 100)) <= 60


# ---------- detect_device：显式指定原样返回（auto 检测不测，依赖硬件） ----------

def test_detect_device_explicit_passthrough():
    assert bili_transcribe.detect_device("cpu") == "cpu"
    assert bili_transcribe.detect_device("cuda") == "cuda"


# ---------- transcribe：backend 参数门禁（不触碰模型，离线） ----------

def test_transcribe_rejects_unknown_backend(tmp_path):
    with pytest.raises(NotImplementedError) as ei:
        bili_transcribe.transcribe(tmp_path / "a.m4s", "tiny", "zh",
                                   device="cpu", backend="not-a-backend")
    assert "not-a-backend" in str(ei.value)


# ---------- model_cached / txt_done：纯文件系统判断 ----------

def test_model_cached_returns_bool():
    assert isinstance(bili_transcribe.model_cached("tiny"), bool)


def test_txt_done_threshold(tmp_path):
    page = {"page": 1, "part": "第一节"}
    f = tmp_path / "01_第一节.txt"
    assert bili_transcribe.txt_done(tmp_path, page) is False
    f.write_text("短", encoding="utf-8")
    assert bili_transcribe.txt_done(tmp_path, page) is False  # <100字节视为残片
    f.write_text("内容" * 100, encoding="utf-8")
    assert bili_transcribe.txt_done(tmp_path, page) is True


# ---------- M6：txt_done 改标记文件（.done） ----------

def test_txt_done_marker_alone_is_enough(tmp_path):
    page = {"page": 1, "part": "第一节"}
    f = tmp_path / "01_第一节.txt"
    f.write_text("短", encoding="utf-8")  # <100字节本应是残片
    bili_transcribe.mark_done(f)
    assert bili_transcribe.txt_done(tmp_path, page) is True  # 有标记即完成


def test_txt_done_missing_marker_missing_txt(tmp_path):
    assert bili_transcribe.txt_done(tmp_path, {"page": 2, "part": "第二节"}) is False


def test_txt_done_backfills_marker_for_legacy_output(tmp_path):
    """兼容旧产物：无标记但 txt >= 100 字节 → 有效并自动补写标记。"""
    page = {"page": 1, "part": "第一节"}
    f = tmp_path / "01_第一节.txt"
    f.write_text("内容" * 100, encoding="utf-8")
    assert not f.with_name(f.name + ".done").exists()
    assert bili_transcribe.txt_done(tmp_path, page) is True
    marker = json.loads(f.with_name(f.name + ".done").read_text(encoding="utf-8"))
    assert marker["size"] == f.stat().st_size
    # 补写后，判定只依赖标记（即使 txt 被清小也视为完成）
    f.write_text("x", encoding="utf-8")
    assert bili_transcribe.txt_done(tmp_path, page) is True


def test_mark_done_writes_valid_json(tmp_path):
    f = tmp_path / "03_第三节.txt"
    f.write_text("[00:10] hi", encoding="utf-8")
    bili_transcribe.mark_done(f)
    marker = f.with_name(f.name + ".done")
    info = json.loads(marker.read_text(encoding="utf-8"))
    assert info["marker_version"] == 1
    assert info["size"] == f.stat().st_size
