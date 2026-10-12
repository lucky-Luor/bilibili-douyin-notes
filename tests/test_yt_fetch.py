# -*- coding: utf-8 -*-
"""yt_fetch.py 的离线测试（网络一律 mock，不依赖 yt-dlp / youtube-transcript-api 是否安装）。"""
import json  # 解析 stdout 摘要与 metadata
import sys  # 注入假模块
import types  # 构造假模块

import pytest  # 测试框架

import yt_fetch  # 被测模块


# ---------- extract_video_id ----------

@pytest.mark.parametrize("text", [  # 各种链接形态都应解析出同一个 ID
    "https://www.youtube.com/watch?v=aircAruvnKk",  # 标准 watch 链接
    "https://www.youtube.com/watch?list=PL1&v=aircAruvnKk&t=30s",  # v 不在首位
    "https://youtu.be/aircAruvnKk?si=abc",  # 短链
    "https://www.youtube.com/shorts/aircAruvnKk",  # Shorts
    "https://www.youtube.com/live/aircAruvnKk",  # 直播回放
    "https://www.youtube.com/embed/aircAruvnKk",  # 嵌入链接
    "看这个 https://youtu.be/aircAruvnKk 很好",  # 夹在分享文本中
    "aircAruvnKk",  # 纯 ID
])
def test_extract_video_id_variants(text):
    assert yt_fetch.extract_video_id(text) == "aircAruvnKk"  # 统一归一为 11 位 ID


def test_extract_video_id_invalid_raises():
    with pytest.raises(SystemExit):  # 无法解析时人话退出
        yt_fetch.extract_video_id("https://example.com/nothing")  # 非 YouTube 链接


# ---------- 代理优先级 ----------

def test_resolve_proxy_priority(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://env:1")  # 环境变量最低优先级
    assert yt_fetch.resolve_proxy("http://cli:1", {"youtube_proxy": "http://cfg:1"}) == "http://cli:1"  # 命令行最高
    assert yt_fetch.resolve_proxy(None, {"youtube_proxy": "http://cfg:1"}) == "http://cfg:1"  # 其次配置文件
    assert yt_fetch.resolve_proxy(None, {}) == "http://env:1"  # 最后环境变量


def test_ydl_base_opts_cookies_and_proxy():
    opts = yt_fetch.ydl_base_opts("http://p:1", "c.txt", "chrome")  # 三项都给
    assert opts["proxy"] == "http://p:1"  # 代理传入
    assert opts["cookiefile"] == "c.txt"  # cookie 文件传入
    assert opts["cookiesfrombrowser"] == ("chrome",)  # 浏览器 cookie 为元组
    assert opts["noprogress"] is True  # 不打印进度条，保护 stdout 摘要


# ---------- 字幕处理纯函数 ----------

def test_pick_lang_exact_then_prefix():
    assert yt_fetch.pick_lang(["en", "zh-Hans"], ["zh-Hans", "en"]) == "zh-Hans"  # 精确匹配优先
    assert yt_fetch.pick_lang(["zh-Hant-TW"], ["zh-Hans", "en"]) == "zh-Hant-TW"  # 主语言前缀匹配
    assert yt_fetch.pick_lang(["fr"], ["zh", "en"]) is None  # 都不匹配


def test_dedupe_rolling_removes_repeats():
    segs = [{"start": 0.0, "text": "hello"},  # 第一句
            {"start": 1.0, "text": "hello"},  # 完全重复
            {"start": 2.0, "text": "hello world"},  # 前缀重复
            {"start": 3.0, "text": ""},  # 空行
            {"start": 4.0, "text": "next  line\n"}]  # 新句（含多余空白）
    out = yt_fetch.dedupe_rolling(segs)  # 去重
    assert out == [{"start": 0.0, "text": "hello"},  # 保留首句
                   {"start": 2.0, "text": "world"},  # 只保留新增尾巴
                   {"start": 4.0, "text": "next line"}]  # 空白被压缩


def test_parse_json3_and_txt_format():
    data = {"events": [{"tStartMs": 0},  # 无 segs 的窗口事件
                       {"tStartMs": 4500, "segs": [{"utf8": "This is "}, {"utf8": "a three"}]},  # 多分片
                       {"tStartMs": 6000, "segs": [{"utf8": "\n"}]},  # 只有换行
                       {"tStartMs": 65200, "segs": [{"utf8": "next"}]}]}  # 超过一分钟
    segs = yt_fetch.parse_json3(data)  # 解析
    assert segs == [{"start": 4.5, "text": "This is a three"},  # 毫秒转秒、分片拼接
                    {"start": 65.2, "text": "next"}]  # 空事件被跳过
    assert yt_fetch.segments_to_txt(segs) == "[00:04] This is a three\n[01:05] next"  # [mm:ss] 格式


def test_check_not_live():
    yt_fetch.check_not_live({"live_status": "was_live"})  # 直播回放可以处理
    with pytest.raises(SystemExit):  # 直播中退出
        yt_fetch.check_not_live({"live_status": "is_live"})  # 正在直播


# ---------- 一级：youtube-transcript-api（假模块） ----------

def _install_fake_transcript_api(monkeypatch, tracks=None, error=None):
    """注入假 youtube_transcript_api：tracks 为 [(语言, 是否自动)]，error 为要抛的异常类名。"""
    mod = types.ModuleType("youtube_transcript_api")  # 假顶层模块
    proxies = types.ModuleType("youtube_transcript_api.proxies")  # 假代理子模块
    proxies.GenericProxyConfig = lambda **kw: kw  # 代理配置直接返回参数

    class Snip:  # 模拟 FetchedTranscriptSnippet 数据类（按属性读）
        def __init__(self, start, text):
            self.start, self.text = start, text  # 属性赋值

    class Track:  # 模拟单条字幕轨道
        def __init__(self, code, gen):
            self.language_code, self.is_generated = code, gen  # 语言与是否自动

    class TList(list):  # 模拟 TranscriptList
        def find_transcript(self, codes):
            t = next(x for x in self if x.language_code == codes[0])  # 找到对应轨道
            return types.SimpleNamespace(fetch=lambda: [Snip(1.2, f"{t.language_code}-text")])  # 返回假片段

    class Api:  # 模拟 YouTubeTranscriptApi
        def __init__(self, **kw):
            self.kw = kw  # 记录参数

        def list(self, vid):
            if error:  # 需要抛错
                raise type(error, (Exception,), {})("boom")  # 按类名构造异常
            return TList(Track(c, g) for c, g in tracks or [])  # 返回轨道列表

    mod.YouTubeTranscriptApi = Api  # 挂到假模块
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", mod)  # 注入顶层
    monkeypatch.setitem(sys.modules, "youtube_transcript_api.proxies", proxies)  # 注入子模块


def test_transcript_api_prefers_manual(monkeypatch):
    _install_fake_transcript_api(monkeypatch, [("zh-Hans", True), ("en", False)])  # 中文自动 + 英文人工
    segs, kind, lang = yt_fetch.fetch_via_transcript_api("x" * 11, ["zh-Hans", "en"], None)  # 拉取
    assert (kind, lang) == ("manual", "en")  # 人工字幕优先于语言优先级
    assert segs == [{"start": 1.2, "text": "en-text"}]  # 按属性读出片段


def test_transcript_api_auto_then_original(monkeypatch):
    _install_fake_transcript_api(monkeypatch, [("ja", True)])  # 只有日语自动字幕
    _, kind, lang = yt_fetch.fetch_via_transcript_api("x" * 11, ["zh", "en"], "http://p:1")  # 带代理
    assert (kind, lang) == ("auto", "ja")  # 不在优先级里时取原语言


def test_transcript_api_blocked_raises(monkeypatch):
    _install_fake_transcript_api(monkeypatch, error="IpBlocked")  # 模拟被封 IP
    with pytest.raises(RuntimeError, match="blocked"):  # 通知调用方回退
        yt_fetch.fetch_via_transcript_api("x" * 11, ["en"], None)  # 拉取


def test_transcript_api_disabled_returns_none(monkeypatch):
    _install_fake_transcript_api(monkeypatch, error="TranscriptsDisabled")  # 视频关闭了字幕
    assert yt_fetch.fetch_via_transcript_api("x" * 11, ["en"], None) is None  # 交下一级


# ---------- main 端到端（全部 mock） ----------

def _fake_info(**over):
    """构造假的 yt-dlp 元数据。"""
    info = {"title": "Neural / Net?", "uploader": "3B1B", "duration": 1120,  # 基础字段
            "description": "desc", "live_status": "not_live"}  # 简介与直播状态
    info.update(over)  # 覆盖字段
    return info  # 返回


def test_main_with_subtitles(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(yt_fetch, "fetch_info", lambda vid, o: _fake_info())  # 元数据 mock
    monkeypatch.setattr(yt_fetch, "fetch_via_transcript_api",  # 一级返回自动字幕（含滚动重复）
                        lambda vid, l, p: ([{"start": 0, "text": "a"}, {"start": 1, "text": "a b"}], "auto", "en"))
    yt_fetch.main(["https://youtu.be/aircAruvnKk", str(tmp_path)])  # 运行
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])  # 最后一行摘要
    page = summary["pages"][0]  # 唯一分P
    assert summary["platform"] == "youtube" and summary["video_id"] == "aircAruvnKk"  # 平台与 ID
    assert page["subtitle_detail"] == "auto" and page["subtitle"] is True  # 字幕状态
    txt = tmp_path / "01_Neural _ Net_.txt"  # 文件名经 safe_name 清洗
    assert txt.read_text(encoding="utf-8") == "[00:00] a\n[00:01] b"  # 自动字幕已去重
    assert (tmp_path / "01_Neural _ Net_.txt.done").exists()  # 完成标记供 bili_transcribe 跳过
    meta = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))  # 读 metadata
    assert meta["suggested_note_type"] == "lecture" and "media_path" not in meta["pages"][0]  # 有字幕不下载


def test_main_blocked_falls_back_to_ytdlp(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(yt_fetch, "fetch_info", lambda vid, o: _fake_info())  # 元数据 mock

    def blocked(*a):  # 一级被封
        raise RuntimeError("blocked")  # 抛出

    monkeypatch.setattr(yt_fetch, "fetch_via_transcript_api", blocked)  # 注入
    monkeypatch.setattr(yt_fetch, "fetch_via_ytdlp",  # 二级成功
                        lambda info, l, o: ([{"start": 2, "text": "hi"}], "ytdlp_manual", "en"))
    yt_fetch.main(["aircAruvnKk", str(tmp_path)])  # 运行
    page = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["pages"][0]  # 摘要分P
    assert page["subtitle_detail"] == "ytdlp_manual"  # 回退到 yt-dlp


def test_main_no_subtitle_with_download(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(yt_fetch, "fetch_info", lambda vid, o: _fake_info(duration=300))  # 短视频
    monkeypatch.setattr(yt_fetch, "fetch_via_transcript_api", lambda *a: None)  # 一级无字幕
    monkeypatch.setattr(yt_fetch, "fetch_via_ytdlp", lambda *a: None)  # 二级无字幕
    calls = []  # 记录下载调用

    def fake_dl(vid, outdir, opts):  # 假下载
        calls.append(vid)  # 记录
        return outdir / "media_p01.m4a"  # 返回音频路径

    monkeypatch.setattr(yt_fetch, "download_audio", fake_dl)  # 注入
    yt_fetch.main(["aircAruvnKk", str(tmp_path), "--download"])  # 带 --download 运行
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])  # 摘要
    page = summary["pages"][0]  # 分P
    assert calls == ["aircAruvnKk"]  # 发生了下载
    assert page["subtitle_detail"] == "none" and page["media_path"] == "media_p01.m4a"  # 交给 ASR
    assert summary["suggested_note_type"] == "short"  # ≤600s 单分P → short


def test_main_no_subtitle_without_download_hints(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(yt_fetch, "fetch_info", lambda vid, o: _fake_info())  # 元数据 mock
    monkeypatch.setattr(yt_fetch, "fetch_via_transcript_api", lambda *a: None)  # 一级无字幕
    monkeypatch.setattr(yt_fetch, "fetch_via_ytdlp", lambda *a: None)  # 二级无字幕
    yt_fetch.main(["aircAruvnKk", str(tmp_path)])  # 不带 --download
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])  # 摘要
    assert "--download" in summary["hint"]  # 引导加 --download 走 ASR


def test_main_bot_check_hint(tmp_path, monkeypatch):
    def bot(vid, o):  # 模拟 YouTube 风控
        raise Exception("Sign in to confirm you're not a bot")  # yt-dlp 的典型报错

    monkeypatch.setattr(yt_fetch, "fetch_info", bot)  # 注入
    with pytest.raises(SystemExit, match="cookies"):  # 人话提示里引导配置 cookie
        yt_fetch.main(["aircAruvnKk", str(tmp_path)])  # 运行


def test_segments_to_txt_collapses_newlines():
    segs = [{"start": 3, "text": "第一行\n第二行"}, {"start": 5, "text": " \n"}]  # 带换行与空片段
    assert yt_fetch.segments_to_txt(segs) == "[00:03] 第一行 第二行"  # 不产生无时间戳孤行
