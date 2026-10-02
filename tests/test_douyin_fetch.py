# -*- coding: utf-8 -*-
"""douyin_fetch.py 纯函数的离线测试（网络一律 mock；gmssl 缺失时由 conftest 打桩）。"""
import urllib.request

import pytest

import douyin_fetch


# ---------- extract_video_id ----------

def test_extract_video_id_from_full_url():
    assert douyin_fetch.extract_video_id(
        "https://www.douyin.com/video/7301234567890123456") == "7301234567890123456"


def test_extract_video_id_from_share_text(monkeypatch):
    text = "8.99 Kfx:/ 复制打开抖音 https://v.douyin.com/iRNBho6/ 看视频"

    class FakeResp:
        def geturl(self):
            return "https://www.douyin.com/video/7301234567890123456?x=y"

    class FakeOpener:
        def open(self, req, timeout=30):
            return FakeResp()

    monkeypatch.setattr(douyin_fetch, "_OPENER", FakeOpener())
    assert douyin_fetch.extract_video_id(text) == "7301234567890123456"


def test_extract_video_id_plain_digits():
    assert douyin_fetch.extract_video_id("7301234567890123456") == "7301234567890123456"


def test_extract_video_id_invalid_raises():
    with pytest.raises(SystemExit):
        douyin_fetch.extract_video_id("https://example.com/nothing")


# ---------- encode_pairs（签名预映像序列化，与 JS URLSearchParams 对齐） ----------

def test_encode_pairs_basic():
    assert douyin_fetch.encode_pairs([("a", "1"), ("b", "2")]) == "a=1&b=2"


def test_encode_pairs_escapes_space_and_keeps_safe_chars():
    assert douyin_fetch.encode_pairs([("k", "x y")]) == "k=x%20y"
    assert douyin_fetch.encode_pairs([("k", "a*b-c._d")]) == "k=a*b-c._d"


# ---------- gen_verify_fp ----------

def test_gen_verify_fp_shape():
    fp = douyin_fetch.gen_verify_fp()
    assert fp.startswith("verify_")
    parts = fp.split("_")
    assert len(parts) == 7            # verify_<b36时间戳>_<36字符含4个下划线>
    body = "_".join(parts[2:])
    assert len(body) == 36 and body.count("_") == 4
    assert body[14] == "4"  # uuid4 形状


# ---------- pick_video_url ----------

def _detail(play_urls=None, dl_urls=None):
    video = {}
    if play_urls is not None:
        video["play_addr"] = {"url_list": play_urls}
    if dl_urls is not None:
        video["download_addr"] = {"url_list": dl_urls}
    return {"video": video}


def test_pick_video_url_prefers_no_watermark():
    url, kind = douyin_fetch.pick_video_url(
        _detail(play_urls=["https://aweme.snssdk.com/aweme/v1/playwm/?x"],
                dl_urls=["https://x/download"]))
    assert "/playwm/" not in url and "/play/" in url
    assert "no watermark" in kind


def test_pick_video_url_falls_back_to_download_addr():
    url, kind = douyin_fetch.pick_video_url(
        _detail(dl_urls=["https://x/download"]))
    assert url == "https://x/download"
    assert "watermarked" in kind


def test_pick_video_url_none_raises():
    with pytest.raises(RuntimeError):
        douyin_fetch.pick_video_url(_detail())


# ---------- safe_name ----------

def test_safe_name():
    assert douyin_fetch.safe_name("标题/带*非法") == "标题_带_非法"
