# -*- coding: utf-8 -*-
"""S1/S3：扫码登录的 Cookie 解析与配置写入、转写降级链的纯函数测试。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import bili_fetch  # noqa: E402
import bili_transcribe  # noqa: E402


# ---- S1: --login ----

def test_cookies_from_set_cookie():
    raw = ["SESSDATA=abc%2Cdef; Path=/; HttpOnly; SameSite=Lax",
           "bili_jct=xyz123; Path=/", "invalid-no-equals"]
    cookies = bili_fetch._cookies_from_set_cookie(raw)
    assert cookies["SESSDATA"] == "abc%2Cdef"
    assert cookies["bili_jct"] == "xyz123"
    assert "invalid-no-equals" in cookies  # 容错：无=的行取整行作键


def test_save_sessdata_preserves_other_keys(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"SESSDATA": "old", "asr_model": "base"}),
                   encoding="utf-8")
    monkeypatch.setattr(bili_fetch, "CONFIG", cfg)
    bili_fetch._save_sessdata("new-value")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["SESSDATA"] == "new-value"
    assert data["asr_model"] == "base"  # 其他配置保留
    assert not list(tmp_path.glob("*.tmp"))  # 原子写无残留


def test_save_sessdata_missing_file_creates(tmp_path, monkeypatch):
    monkeypatch.setattr(bili_fetch, "CONFIG", tmp_path / "config.json")
    bili_fetch._save_sessdata("v1")
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))["SESSDATA"] == "v1"


# ---- S3: 转写降级链 ----

def test_model_chain_large_falls_to_small():
    chain = bili_transcribe.resolve_model_chain("large-v3")
    assert chain == ["large-v3", "medium", "small"]


def test_model_chain_single_level_unchanged():
    assert bili_transcribe.resolve_model_chain("small") == ["small"]
    assert bili_transcribe.resolve_model_chain("base") == ["base"]  # 未登记的模型不降级


def test_build_prompt_zh_with_terms():
    prompt = bili_transcribe._build_prompt("zh", ["@Component", "Starter"])
    assert prompt and "简体中文" in prompt and "常听术语" in prompt
    assert "@Component" in prompt and "Starter" in prompt


def test_build_prompt_no_lang_no_terms_is_none():
    assert bili_transcribe._build_prompt(None, None) is None


def test_build_prompt_terms_capped_at_20():
    prompt = bili_transcribe._build_prompt("zh", [f"term{i}" for i in range(30)])
    assert "term19" in prompt and "term25" not in prompt


def test_cpu_threads_config_override(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"asr_cpu_threads": 3}), encoding="utf-8")
    monkeypatch.setattr(bili_transcribe, "CONFIG", cfg)
    assert bili_transcribe._cpu_threads() == 3
    cfg.write_text("{}", encoding="utf-8")
    assert 1 <= bili_transcribe._cpu_threads() <= 8  # 自动档有上限


def test_transcribe_backend_gate_unchanged(tmp_path):
    import pytest
    with pytest.raises(NotImplementedError):
        bili_transcribe.transcribe(tmp_path / "x.m4a", "small", "zh", backend="whisper.cpp")


# ---------- _render_qr：PNG 为主、ASCII 兜底（2026-10-04 修订） ----------

def test_render_qr_png_primary(tmp_path, monkeypatch):
    """主路径：PNG 写到当前工作目录，返回路径字符串（Agent 直接把路径给用户）。"""
    pytest.importorskip("qrcode", reason="CI 轻量依赖无 qrcode，本地开发环境真实跑")
    monkeypatch.chdir(tmp_path)
    out = bili_fetch._render_qr("https://bilibili.com/login?qr=test")
    assert out is not None
    assert out.endswith("bilibili-login-qr.png")
    assert Path(out).exists() and Path(out).stat().st_size > 0


def test_render_qr_falls_back_to_ascii_when_png_fails(tmp_path, monkeypatch):
    """cwd 与临时目录都写不进 PNG 时退回终端 ASCII，不直接失败。"""
    pytest.importorskip("qrcode", reason="CI 轻量依赖无 qrcode，本地开发环境真实跑")
    monkeypatch.chdir(tmp_path)
    import qrcode

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(qrcode, "make", boom)
    out = bili_fetch._render_qr("https://bilibili.com/login?qr=test")
    assert out == "ascii"
