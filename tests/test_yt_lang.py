import json  # 解析离线字幕与页面语言
import socket  # 阻止测试意外联网
import sys  # 注入可选依赖桩
import types  # 构造下载器替身
import pytest  # 参数化语言优先级
import yt_fetch as yt  # 被测抓取模块
from test_yt_fetch import _install_fake_transcript_api, _fake_info  # 复用现有离线响应


@pytest.fixture(autouse=True)  # 本文件全部测试禁止配置读取与联网
def offline(monkeypatch):  # 隔离本机配置与外部请求
    monkeypatch.setattr(yt, "load_config", lambda: {})  # 禁止读取配置文件
    def blocked(*args, **kwargs):  # 捕获漏网请求
        raise AssertionError("语言回归禁止联网")  # 给出明确失败原因
    monkeypatch.setattr(socket.socket, "connect", blocked)  # 阻止真实套接字连接


@pytest.mark.parametrize("tracks,expected", [  # 原语言人工中文人工原语言自动及旧兜底
    ([("en", True), ("zh-Hans", False), ("en", False)], ("manual", "en", "en")),  # 原语言人工最高
    ([("ja", True), ("zh-TW", False), ("zh-Hans", False)], ("manual", "zh-Hans", "ja")),  # 中文人工按顺序
    ([("ja", True), ("en", False)], ("auto", "ja", "ja")),  # 原语言自动优先其它人工
    ([("ja", False), ("en", False)], ("manual", "en", None)),  # 无自动时沿用默认策略
    ([("fr", False)], ("manual", "fr", None)),  # 默认语言不可用时保留首条人工兜底
])  # 参数表结束
def test_api_original_language(monkeypatch, tracks, expected):  # 自动策略保留三元返回结构
    _install_fake_transcript_api(monkeypatch, tracks)  # 安装字幕接口桩
    result = {}  # 每次调用独立的结果容器
    got = yt.fetch_via_transcript_api("x" * 11, None, None, result)  # 不显式选择语言
    assert len(got) == 3 and got[1:] == expected[:2]  # 返回类型与实际字幕语言
    assert result == {"audio_lang": expected[2], "subtitle_lang": expected[1]}  # 音频语言不被翻译轨覆盖


def test_api_first_generated_and_explicit_compatibility(monkeypatch):  # 首条自动轨决定音频语言且显式策略独立
    _install_fake_transcript_api(monkeypatch, [("ja", True), ("en", True), ("fr", False), ("en", False)])  # 多个自动语言
    result = {}  # 独立回传对象
    assert yt.fetch_via_transcript_api("x" * 11, ["fr", "en"], None, result)[1:] == ("manual", "fr")  # 用户顺序保持
    assert result["audio_lang"] == "ja"  # 第一条自动轨才是推断依据
    assert yt.fetch_via_transcript_api("x" * 11, ["en", "fr"], None)[1:] == ("manual", "en")  # 旧三参数调用仍有效


def install_ytdlp(monkeypatch):  # 仅模拟读取字幕不接触网络
    calls = []  # 记录实际取用的字幕键
    class Downloader:  # 模拟下载器上下文
        def __init__(self, opts):  # 接收原有下载参数
            pass  # 测试无需解析配置
        def __enter__(self):  # 进入上下文
            return self  # 返回当前替身
        def __exit__(self, *args):  # 退出上下文
            pass  # 无外部资源需要释放
        def urlopen(self, url):  # 观察真正抓取的键
            calls.append(url)  # 保留来源标记
            raw = json.dumps({"events": [{"tStartMs": 0, "segs": [{"utf8": url}]}]}).encode()  # 构造字幕
            return types.SimpleNamespace(read=lambda: raw)  # 返回响应对象
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=Downloader))  # 替换可选依赖
    return calls  # 供断言实际键使用


def tracks(*codes):  # 生成各语言的最小字幕格式
    return {code: [{"ext": "json3", "url": code}] for code in codes}  # 来源键即请求标记


@pytest.mark.parametrize("manual,automatic,language,expected", [  # yt-dlp 与接口采用相同优先级
    (["zh-Hans", "en"], ["en-orig", "zh"], None, ("ytdlp_manual", "en", "en", "en")),  # 原语言人工优先
    (["zh-TW", "zh", "en"], ["ja-orig"], None, ("ytdlp_manual", "zh", "ja", "zh")),  # 中文人工按既定顺序
    (["en"], ["ja-orig", "ja", "en"], None, ("ytdlp_auto", "ja", "ja", "ja-orig")),  # 使用真实原始键抓取
    (["en"], ["ja", "en"], "ja", ("ytdlp_auto", "ja", "ja", "ja")),  # 信息字段也可推断原语言
    (["en", "ja"], ["ja-orig"], "en", ("ytdlp_manual", "ja", "ja", "ja")),  # 原始键优先于信息语言
    (["fr", "en"], [], None, ("ytdlp_manual", "en", None, "en")),  # 无推断保持既有默认策略
])  # 参数表结束
def test_ytdlp_original_language(monkeypatch, manual, automatic, language, expected):  # 下载轨道与记录语言分离
    calls = install_ytdlp(monkeypatch)  # 安装离线下载器
    info = dict(subtitles=tracks(*manual), automatic_captions=tracks(*automatic), language=language)  # 最小元数据
    result = {}  # 局部语言结果
    got = yt.fetch_via_ytdlp(info, None, {}, result)  # 自动策略
    assert got[1:] == expected[:2] and calls == [expected[3]]  # 三元返回与请求键
    assert result == {"audio_lang": expected[2], "subtitle_lang": expected[1]}  # 页面语言规范化


def test_ytdlp_explicit_legacy_and_no_state_leak(monkeypatch):  # 显式顺序与独立调用互不污染
    install_ytdlp(monkeypatch)  # 不执行真正下载
    info = dict(subtitles=tracks("en", "fr"), automatic_captions=tracks("ja-orig"))  # 原语言与人工字幕不同
    result = {}  # 复用字典也不得遗留上一条字幕
    assert yt.fetch_via_ytdlp(info, ["fr", "en"], {}, result)[2] == "fr"  # 用户首选不被原语言覆盖
    assert yt.fetch_via_ytdlp(info, ["en", "fr"], {})[2] == "en"  # 旧三参数顺序保持
    assert yt.fetch_via_ytdlp({}, None, {}, result) is None  # 下一视频没有轨道
    assert result == {"audio_lang": None, "subtitle_lang": None}  # 清理旧语言字段


@pytest.mark.parametrize("explicit,expected", [(None, "en"), ("zh-Hans,en", "zh-Hans")])  # CLI 自动与显式分别覆盖
def test_main_page_languages(tmp_path, monkeypatch, capsys, explicit, expected):  # 页面同时落盘原语言与字幕语言
    monkeypatch.setattr(yt, "fetch_info", lambda *args: _fake_info())  # 禁止元数据联网
    _install_fake_transcript_api(monkeypatch, [("en", True), ("en", False), ("zh-Hans", False)])  # 安装真实选择逻辑所需桩
    argv = ["aircAruvnKk", str(tmp_path)] + (["--lang", explicit] if explicit else [])  # 构造命令行
    yt.main(argv)  # 执行公开入口
    page = json.loads(capsys.readouterr().out)["pages"][0]  # 页面回执
    assert page["audio_lang"] == "en" and page["subtitle_lang"] == expected  # 音频与字幕分别记录
    assert json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))["pages"][0] == page  # 回执与落盘一致
