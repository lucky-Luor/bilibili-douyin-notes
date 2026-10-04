# -*- coding: utf-8 -*-
"""bili_fetch.py 纯函数与字幕筛选逻辑的离线测试（网络一律 mock）。"""
import json
import sys
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


# ---------- subtitle_relevant：AI 字幕整体错位交叉校验 ----------
# 实测案例 BV1GKaG6zEtr：接口返回 ai 状态，内容却是无关电影对白

def test_title_tokens_ascii_only():
    assert bili_fetch.title_tokens("MySQL 的 LIKE 为什么这么慢？ElasticSearch 完整教程") \
        == ["MySQL", "LIKE", "ElasticSearch"]


def test_title_tokens_empty_for_pure_cjk():
    assert bili_fetch.title_tokens("精神批发市场与疯传六原则") == []


def test_subtitle_relevant_hit():
    text = "[00:00] 假设你有一张MySQL表，里面存了几百万篇文章"
    assert bili_fetch.subtitle_relevant(
        "MySQL 的 LIKE 为什么这么慢？ElasticSearch 完整教程", text) is True


def test_subtitle_relevant_mismatch_case_insensitive():
    text = "[00:00] 你拥有这么强大的力量，到底是为了什么"
    assert bili_fetch.subtitle_relevant(
        "MySQL 的 LIKE 为什么这么慢？ElasticSearch 完整教程", text) is False


def test_subtitle_relevant_no_ascii_tokens_returns_none():
    """标题无 ASCII 词时无从校验，必须信任字幕而不是误杀。"""
    assert bili_fetch.subtitle_relevant("精神批发市场", "随便什么内容") is None


def test_subtitle_relevant_ignores_cjk_title_words():
    """中文标题词（如"为什么"）与无关对白撞车也不该放行判定——锚点只有 ASCII 词。
    这里验证：字幕不含任何标题 ASCII 词即判 False，即便含标题里的中文词。"""
    text = "你想想啊，到底是为了什么"
    assert bili_fetch.subtitle_relevant("为什么慢？MySQL 实战", text) is False


# ---------- main() 主流程：ai_mismatch 分流（网络全 mock） ----------

def _sub_body():
    return {"body": [{"from": 0, "content": "你拥有这么强大的力量"},
                     {"from": 2, "content": "到底是为了什么"}]}


def _run_main(monkeypatch, tmp_path, title, sub_body, capsys):
    data = {"data": {"aid": 1, "title": title, "owner": {"name": "u"}, "desc": "",
                     "duration": 60,
                     "pages": [{"page": 1, "part": title, "cid": 1, "duration": 60}]}}
    monkeypatch.setattr(bili_fetch, "api", lambda url, cookie="": data)
    monkeypatch.setattr(bili_fetch, "fetch_subtitles",
                        lambda bvid, cid, cookie: (
                            [{"lan": "ai-zh", "ai_type": 1,
                              "subtitle_url": "https://x/s.json"}], True))
    monkeypatch.setattr(bili_fetch, "http_get", lambda url, cookie="": json.dumps(sub_body).encode())
    monkeypatch.setattr(bili_fetch, "load_sessdata", lambda: "")
    monkeypatch.setattr(sys, "argv", ["bili_fetch.py", "BV1xx411c7mD", str(tmp_path)])
    bili_fetch.main()
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_main_flags_ai_mismatch(monkeypatch, tmp_path, capsys):
    out = _run_main(monkeypatch, tmp_path,
                    "MySQL 的 LIKE 为什么这么慢？ElasticSearch 完整教程", _sub_body(),
                    capsys)
    page = out["pages"][0]
    assert page["subtitle_detail"] == "ai_mismatch"
    assert page["subtitle"] is False
    assert "ai字幕存疑" in page["file"]
    assert "hint_mismatch" in out
    # 正式字幕 txt 不得写出（否则转写脚本会误以为已有字幕而跳过 ASR）
    assert not (tmp_path / page["file"].replace("ai字幕存疑", "")).exists()
    assert (tmp_path / page["file"]).exists()
    meta = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert meta["pages"][0]["subtitle_detail"] == "ai_mismatch"


def test_main_writes_txt_for_relevant_ai_subtitle(monkeypatch, tmp_path, capsys):
    body = {"body": [{"from": 0, "content": "假设你有一张MySQL表"},
                     {"from": 2, "content": "几百万行LIKE查询"}]}
    out = _run_main(monkeypatch, tmp_path,
                    "MySQL 的 LIKE 为什么这么慢？ElasticSearch 完整教程", body,
                    capsys)
    page = out["pages"][0]
    assert page["subtitle_detail"] == "ai"
    assert page["subtitle"] is True
    assert (tmp_path / page["file"]).exists()
