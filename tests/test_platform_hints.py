# -*- coding: utf-8 -*-
"""各平台脚本的失败提示离线测试：每处报错都要有“原因 / 影响 / 下一步”三段（错误分支清单 U4–U6、P1–P10、G1）。"""
import io  # 构造假的 HTTP 响应
import json  # 构造接口返回
import urllib.error  # HTTPError / URLError

import pytest  # 测试框架

import _hints  # HintError / run_cli
import bili_fetch  # B站元数据与字幕
import bili_screenshot  # B站截图
import bili_transcribe  # B站转写
import douyin_fetch  # 抖音
import xhs_fetch  # 小红书
import yt_fetch  # YouTube


def three(text):  # 断言文案三段齐全，且不是英文堆栈
    assert "原因：" in text and "影响：" in text and "下一步：" in text, text  # 三段
    assert "Traceback" not in text  # 没有堆栈


def exit_text(ei):  # 取 SystemExit 的退出文案
    return str(ei.value.code)  # SystemExit 的 code 即文案


class _Resp(io.BytesIO):  # 可用作 with 语句、带 headers 的假响应
    headers = {}  # douyin get_ttwid 会读 Set-Cookie
    def __enter__(self):  # 进入上下文
        return self  # 返回自身
    def __exit__(self, *a):  # 退出上下文
        return False  # 不吞异常


# ---------- 公共：run_cli ----------

def test_run_cli_turns_hint_error_into_clean_exit():
    # 顶层 HintError 只以三段文案退出，不带堆栈
    def boom():  # 模拟主流程失败
        raise _hints.HintError("r", "i", "n")  # 三段式异常
    with pytest.raises(SystemExit) as ei:  # 期望干净退出
        _hints.run_cli(boom)  # 运行
    three(exit_text(ei))  # 三段齐全
    assert ei.value.__cause__ is None  # 不串联原始异常


# ---------- B站：U4、G1、P4、P5、P6 ----------

def test_bili_api_code_explained(monkeypatch):
    # U4：view 接口 62002 → “稿件不可见”，不再是 RuntimeError: 接口失败 code=…
    monkeypatch.setattr(bili_fetch, "http_get", lambda url, cookie="": json.dumps({"code": 62002, "message": "稿件不可见"}).encode())  # 假接口
    with pytest.raises(_hints.HintError) as ei:  # 期望三段式异常
        bili_fetch.api("https://api.bilibili.com/x/web-interface/view?bvid=BV1xx411c7mD")  # 请求
    three(str(ei.value))  # 三段齐全
    assert "已删除、被下架或设为私密" in ei.value.reason  # 固定解释


def test_bili_extract_bvid_invalid_has_next_step():
    # G1：解析不到 BV 号时告诉用户发什么
    with pytest.raises(SystemExit) as ei:  # 期望干净退出
        bili_fetch.extract_bvid("https://example.com/none")  # 无 BV 号
    three(exit_text(ei))  # 三段齐全


@pytest.mark.parametrize("mod,fn", [(bili_transcribe, "download_audio"), (bili_screenshot, "download_video")])
def test_bili_playurl_code_and_missing_dash(mod, fn, monkeypatch, tmp_path):
    # P4 / P5：playurl 业务码非 0；P6：缺 DASH 流 → 都给三段式提示
    monkeypatch.setattr(mod, "http_get", lambda url, cookie="": json.dumps({"code": -403, "message": "x"}).encode())  # 权限不足
    with pytest.raises(_hints.HintError) as ei:  # 期望三段式异常
        getattr(mod, fn)("BV1xx411c7mD", 1, tmp_path / "a.m4s")  # 下载
    three(str(ei.value))  # 三段齐全
    monkeypatch.setattr(mod, "http_get", lambda url, cookie="": json.dumps({"code": 0, "data": {}}).encode())  # 没有 dash
    with pytest.raises(_hints.HintError) as ei:  # 期望三段式异常
        getattr(mod, fn)("BV1xx411c7mD", 1, tmp_path / "a.m4s")  # 下载
    assert "--login" in ei.value.next_step  # 引导登录


# ---------- 抖音：G1、P2、U5、P1、P8、G2 ----------

def test_douyin_extract_invalid():
    # G1：解析不到作品 ID
    with pytest.raises(SystemExit) as ei:  # 期望干净退出
        douyin_fetch.extract_video_id("随便一段没有链接的文字")  # 无链接
    three(exit_text(ei))  # 三段齐全


def test_douyin_ttwid_failure_is_human(monkeypatch):
    # P2：不再说“ttwid 注册失败”，说“游客身份获取失败”
    monkeypatch.setattr(douyin_fetch, "http", lambda *a, **k: _Resp(b"{}"))  # 没有 ttwid
    with pytest.raises(_hints.HintError) as ei:  # 期望三段式异常
        douyin_fetch.get_ttwid()  # 获取游客身份
    assert "游客身份" in ei.value.reason  # 人话原因


def _detail_with(monkeypatch, behaviour):  # 让详情接口按 behaviour 返回或抛错，返回调用计数
    box = {"n": 0}  # 调用次数
    monkeypatch.setattr(douyin_fetch, "get_ttwid", lambda: "t")  # 跳过 ttwid
    monkeypatch.setattr(douyin_fetch, "ABogus", lambda: type("B", (), {"get_value": lambda self, p: "x"})())  # CI 无 gmssl，签名打桩
    monkeypatch.setattr(douyin_fetch.time, "sleep", lambda s: None)  # 不等待
    def fake(url, data=None, headers=None, method=None):  # 假请求
        box["n"] += 1  # 计数
        return behaviour()  # 返回或抛错
    monkeypatch.setattr(douyin_fetch, "http", fake)  # 注入
    return box  # 交给测试检查


def test_douyin_deleted_not_reported_as_signature(monkeypatch):
    # P1：作品已删除（有 filter_detail）不再误报“签名失效”
    _detail_with(monkeypatch, lambda: _Resp(json.dumps({"filter_detail": {"detail_msg": "因作品权限或已被删除，无法观看"}}).encode()))  # 已删除
    with pytest.raises(_hints.HintError) as ei:  # 期望三段式异常
        douyin_fetch.fetch_aweme_detail("7301234567890123456")  # 请求详情
    assert "作品不可见" in ei.value.reason and "签名" not in ei.value.reason  # 原因正确


def test_douyin_4xx_no_retry(monkeypatch):
    # U5：403 不重试，直接三段式提示
    def deny():  # 模拟 403
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b""))  # 抛错
    box = _detail_with(monkeypatch, deny)  # 注入
    with pytest.raises(_hints.HintError, match="拒绝访问"):  # 期望 403 的解释
        douyin_fetch.fetch_aweme_detail("7301234567890123456")  # 请求详情
    assert box["n"] == 1  # 只请求一次


def test_douyin_network_retry_then_hint(monkeypatch):
    # U5：网络错误重试 3 次后给“连不上抖音”
    def down():  # 模拟超时
        raise urllib.error.URLError("timed out")  # 抛错
    box = _detail_with(monkeypatch, down)  # 注入
    with pytest.raises(_hints.HintError) as ei:  # 期望三段式异常
        douyin_fetch.fetch_aweme_detail("7301234567890123456")  # 请求详情
    assert box["n"] == 3 and "连不上抖音" in ei.value.reason  # 次数与原因


def test_douyin_no_video_url_and_album():
    # P8：没有下载地址；G2：图集帖
    with pytest.raises(_hints.HintError) as ei:  # 期望三段式异常
        douyin_fetch.pick_video_url({"video": {}})  # 没有地址
    three(str(ei.value))  # 三段齐全
    with pytest.raises(SystemExit) as ei:  # 期望干净退出
        douyin_fetch.check_not_album({"aweme_type": 68, "video": {}})  # 图集
    three(exit_text(ei))  # 三段齐全


# ---------- 小红书：P7、P8、U6、G3 ----------

def test_xhs_no_state_and_no_video_url():
    # P7：页面没有数据块；P8：没有下载地址
    with pytest.raises(_hints.HintError) as ei:  # 期望三段式异常
        xhs_fetch.parse_state("<html></html>")  # 空页面
    three(str(ei.value))  # 三段齐全
    with pytest.raises(_hints.HintError) as ei:  # 期望三段式异常
        xhs_fetch.pick_video_url({"video": {}})  # 没有地址
    three(str(ei.value))  # 三段齐全


def test_xhs_other_http_error_explained(monkeypatch):
    # U6：非风控类 HTTP 错误不再原样抛出
    def err(url, cookie=""):  # 模拟 404
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))  # 抛错
    monkeypatch.setattr(xhs_fetch, "http", err)  # 注入
    with pytest.raises(_hints.HintError) as ei:  # 期望三段式异常
        xhs_fetch.fetch_note("6ae36d62000000001e000000", "tok", "pc_search", "")  # 请求笔记
    three(str(ei.value))  # 三段齐全


def test_xhs_missing_token_three_parts():
    # G3：缺 xsec_token
    with pytest.raises(SystemExit) as ei:  # 期望干净退出
        xhs_fetch.resolve_link("https://www.xiaohongshu.com/explore/6ae36d62000000001e000000")  # 无令牌
    three(exit_text(ei))  # 三段齐全


# ---------- YouTube：G1、G4、P10 ----------

def test_yt_extract_invalid_and_live():
    # G1：解析不到 ID；G4：直播中
    with pytest.raises(SystemExit) as ei:  # 期望干净退出
        yt_fetch.extract_video_id("not a link")  # 无 ID
    three(exit_text(ei))  # 三段齐全
    with pytest.raises(SystemExit) as ei:  # 期望干净退出
        yt_fetch.check_not_live({"live_status": "is_live"})  # 直播中
    three(exit_text(ei))  # 三段齐全


def test_yt_metadata_failure_non_bot_has_next_step(tmp_path, monkeypatch):
    # P10：非风控类元数据失败也给下一步（代理 / 视频是否公开）
    def fail(vid, o):  # 模拟视频不可用
        raise Exception("Video unavailable")  # yt-dlp 的典型报错
    monkeypatch.setattr(yt_fetch, "fetch_info", fail)  # 注入
    with pytest.raises(SystemExit) as ei:  # 期望干净退出
        yt_fetch.main(["aircAruvnKk", str(tmp_path)])  # 运行
    text = exit_text(ei)  # 退出文案
    three(text)  # 三段齐全
    assert "代理" in text and "Video unavailable" in text  # 下一步与原始错误


@pytest.mark.parametrize("mod", [douyin_fetch, xhs_fetch])
def test_video_download_403_explained(mod, monkeypatch, tmp_path):
    # P12：视频下载地址过期（403）不再原样抛出
    def deny(*a, **k):  # 模拟 403
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b""))  # 抛错
    monkeypatch.setattr(mod, "http", deny)  # 注入
    with pytest.raises(_hints.HintError) as ei:  # 期望三段式异常
        mod.download("https://example.com/v.mp4", tmp_path / "media_p01.mp4")  # 下载
    three(str(ei.value))  # 三段齐全
    assert "过期" in ei.value.detail  # 详情说明地址可能过期
