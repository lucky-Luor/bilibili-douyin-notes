# -*- coding: utf-8 -*-
"""bili_fetch.py 纯函数与字幕筛选逻辑的离线测试（网络一律 mock）。"""
import json
import urllib.request

import pytest

import bili_fetch


# ---------- extract_bvid ----------

def test_extract_bvid_from_url():
    assert bili_fetch.extract_bvid(
        "https://www.bilibili.com/video/BV1xx411c7mD?p=3") == "BV1xx411c7mD"


def test_extract_bvid_plain():
    assert bili_fetch.extract_bvid("BV1AbCdEfGhJ") == "BV1AbCdEfGhJ"


def test_extract_bvid_from_share_text():
    assert bili_fetch.extract_bvid(
        "看看这个【Java】 https://b23.tv/abcdef 不用管后面文字 BV17x411N766") \
        == "BV17x411N766"


def test_extract_bvid_short_link_follows_redirect(monkeypatch):
    class FakeResp:
        def geturl(self):
            return "https://www.bilibili.com/video/BV1xx411c7mD/"
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: FakeResp())
    assert bili_fetch.extract_bvid("https://b23.tv/abcdef") == "BV1xx411c7mD"


def test_extract_bvid_invalid_raises_systemexit():
    with pytest.raises(SystemExit):
        bili_fetch.extract_bvid("https://example.com/no-bv-here")


# ---------- fmt_ts ----------

def test_fmt_ts_basic():
    assert bili_fetch.fmt_ts(0) == "00:00"
    assert bili_fetch.fmt_ts(75) == "01:15"
    assert bili_fetch.fmt_ts(599) == "09:59"
    assert bili_fetch.fmt_ts(600) == "10:00"


# ---------- pick_subtitle ----------

def _sub(lan, ai=0, url="https://example.com/s.json"):
    return {"lan": lan, "ai_type": ai, "subtitle_url": url}


def test_pick_subtitle_prefers_cc_zh_over_ai_zh():
    subs = [_sub("ai-zh", ai=1), _sub("zh-CN")]
    assert bili_fetch.pick_subtitle(subs)["lan"] == "zh-CN"


def test_pick_subtitle_prefers_zh_ai_over_other():
    subs = [_sub("en-US"), _sub("ai-zh", ai=1)]
    assert bili_fetch.pick_subtitle(subs)["lan"] == "ai-zh"


def test_pick_subtitle_falls_back_to_any():
    subs = [_sub("en-US"), _sub("ko-KR")]
    assert bili_fetch.pick_subtitle(subs)["lan"] == "en-US"


def test_pick_subtitle_empty_list():
    assert bili_fetch.pick_subtitle([]) is None


def test_pick_subtitle_tolerates_missing_keys():
    """条目缺 ai_type/lan 等键时不应崩溃（健壮性）。"""
    subs = [{"lan": "zh-CN"}, {"lan": "en-US", "ai_type": 0},
            {"subtitle_url": "https://x"}]
    assert bili_fetch.pick_subtitle(subs)["lan"] == "zh-CN"


# ---------- fetch_subtitles：空 URL 过滤（网络 mock，解包方式兼容 tuple/list） ----------

def test_fetch_subtitles_filters_empty_url(monkeypatch):
    fake = {"data": {"subtitle": {"subtitles": [
        {"lan": "zh-CN", "subtitle_url": ""},
        {"lan": "ai-zh", "ai_type": 1, "subtitle_url": "https://a/b.json"},
    ]}}}
    monkeypatch.setattr(bili_fetch, "api", lambda url, cookie="": fake)
    res = bili_fetch.fetch_subtitles("BV1xx411c7mD", 1, "")
    usable = res[0] if isinstance(res, tuple) else res
    assert len(usable) == 1
    assert usable[0]["lan"] == "ai-zh"


def test_fetch_subtitles_no_usable(monkeypatch):
    fake = {"data": {"subtitle": {"subtitles": [
        {"lan": "zh-CN", "subtitle_url": ""},
    ]}}}
    monkeypatch.setattr(bili_fetch, "api", lambda url, cookie="": fake)
    res = bili_fetch.fetch_subtitles("BV1xx411c7mD", 1, "")
    usable = res[0] if isinstance(res, tuple) else res
    assert usable == []


# ---------- safe_name ----------

def test_safe_name_replaces_illegal_chars():
    assert bili_fetch.safe_name('a/b:c*d?"<>|') == "a_b_c_d_____"
    assert bili_fetch.safe_name("  中文 标题  ") == "中文 标题"


def test_safe_name_truncates():
    assert len(bili_fetch.safe_name("长" * 100)) <= 60


# ---------- note_type_for（规格 §4.4 坑③：metadata.suggested_note_type） ----------

def test_note_type_short_for_short_single_page():
    assert bili_fetch.note_type_for(300, 1) == "short"


def test_note_type_lecture_for_long_or_multi_page():
    assert bili_fetch.note_type_for(900, 3) == "lecture"
    assert bili_fetch.note_type_for(900, 1) == "lecture"   # 长时长单P也是 lecture
    assert bili_fetch.note_type_for(300, 2) == "lecture"   # 短时长多P也是 lecture


def test_note_type_boundary_600s():
    assert bili_fetch.note_type_for(600, 1) == "short"
    assert bili_fetch.note_type_for(601, 1) == "lecture"


def _view_data(duration=None, pages=(300,)):
    data = {"title": "t", "owner": {"name": "u"},
            "pages": [{"page": i + 1, "part": f"p{i + 1}", "cid": i + 1,
                       "duration": d} for i, d in enumerate(pages)]}
    if duration is not None:
        data["duration"] = duration
    return {"data": data}


def test_probe_reports_suggested_note_type_short(monkeypatch, capsys):
    monkeypatch.setattr(bili_fetch, "api", lambda url, cookie="": _view_data(300, (300,)))
    bili_fetch.probe("BV1xx411c7mD")
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["suggested_note_type"] == "short"


def test_probe_reports_suggested_note_type_lecture(monkeypatch, capsys):
    # 多分P：接口未给总时长时取各分P之和（900s），且分P数>1 -> lecture
    monkeypatch.setattr(
        bili_fetch, "api", lambda url, cookie="": _view_data(None, (300, 300, 300)))
    bili_fetch.probe("BV1xx411c7mD")
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["duration"] == 900
    assert out["suggested_note_type"] == "lecture"
