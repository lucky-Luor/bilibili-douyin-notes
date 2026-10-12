# -*- coding: utf-8 -*-
"""xhs_fetch.py 的离线测试（网络一律 mock）。"""
import io  # 构造假响应体
import json  # 解析 stdout 摘要与 metadata
import urllib.error  # 构造 HTTPError

import pytest  # 测试框架

import xhs_fetch  # 被测模块

NID = "6a963ece000000001203cfb4"  # 测试用笔记 ID（24 位十六进制）
TOKEN = "CBuTd92n-vpxCfqg2zLAOZ5TEtjfEN5QhrtRWvvtkySv0="  # 解码后的 xsec_token
SHARE = (f"https://www.xiaohongshu.com/discovery/item/{NID}?app_platform=android"  # 真实分享链接形态
         "&xsec_source=app_share&type=video"
         "&xsec_token=CBuTd92n-vpxCfqg2zLAOZ5TEtjfEN5QhrtRWvvtkySv0%3D&xhsshare=WeixinSession")


def video_note(**over):
    """构造手机页结构的视频笔记对象。"""
    note = {"type": "video", "title": "末日 / 养殖场?", "desc": "#助眠",  # 基础字段
            "user": {"nickName": "不一莫"},  # 手机页作者字段为 nickName
            "video": {"capa": {"duration": 1979},  # 时长
                      "consumer": {"originVideoKey": "spectrum/abc"},  # 原片 key
                      "media": {"stream": {  # 转码流
                          "h265": [{"height": 720, "masterUrl": "http://cdn/h265_720.mp4"}],  # h265
                          "h264": [{"height": 480, "masterUrl": "http://cdn/h264_480.mp4"},  # h264 低清
                                   {"height": 720, "masterUrl": "", "backupUrls": ["http://cdn/h264_720_bak.mp4"]}],  # 只有备用地址
                      }}}}
    note.update(over)  # 覆盖字段
    return note  # 返回


def page_html(state: dict) -> str:
    """把状态对象包装成页面 HTML（模拟 JS 字面量 undefined）。"""
    body = json.dumps(state, ensure_ascii=False).replace('"__UNDEF__"', "undefined")  # 注入 undefined
    return f"<html><script>window.__INITIAL_STATE__={body}</script></html>"  # 页面片段


class FakeResp:
    """模拟 urllib 响应：支持 read / geturl / close。"""

    def __init__(self, body: bytes = b"", url: str = ""):
        self._buf, self._url = io.BytesIO(body), url  # 响应体与最终地址

    def read(self, n=-1):
        return self._buf.read(n)  # 分块读取

    def geturl(self):
        return self._url  # 跟随跳转后的地址

    def close(self):
        pass  # 无需释放


# ---------- 链接解析 ----------

def test_resolve_link_keeps_token_from_share_text():
    nid, token, source = xhs_fetch.resolve_link(f"复制这段 {SHARE} 打开小红书")  # 夹在分享文本中
    assert (nid, token, source) == (NID, TOKEN, "app_share")  # token 已 URL 解码


def test_resolve_link_explore_url():
    nid, _, source = xhs_fetch.resolve_link(  # explore 形态、无 xsec_source
        f"https://www.xiaohongshu.com/explore/{NID}?xsec_token=abc")
    assert nid == NID and source == "app_share"  # 来源缺省为 app_share


def test_resolve_link_short_link_follows_redirect(monkeypatch):
    monkeypatch.setattr(xhs_fetch, "http", lambda url, cookie="": FakeResp(url=SHARE))  # 短链 302 到完整链接
    assert xhs_fetch.resolve_link("http://xhslink.com/a/AbCd123 复制打开")[0] == NID  # 解析出 ID


def test_resolve_link_without_token_exits():
    with pytest.raises(SystemExit, match="xsec_token"):  # 引导复制完整分享链接
        xhs_fetch.resolve_link(f"https://www.xiaohongshu.com/explore/{NID}")  # 只有 ID


def test_resolve_link_invalid_exits():
    with pytest.raises(SystemExit):  # 非小红书链接
        xhs_fetch.resolve_link("https://example.com/x")  # 无法解析


def test_note_page_url_drops_tracking_params():
    url = xhs_fetch.note_page_url(NID, TOKEN, "app_share")  # 重建地址
    assert url == (f"https://www.xiaohongshu.com/discovery/item/{NID}"  # 手机页路径
                   "?xsec_token=CBuTd92n-vpxCfqg2zLAOZ5TEtjfEN5QhrtRWvvtkySv0%3D&xsec_source=app_share")  # 只留必需参数


# ---------- 页面解析 ----------

def test_parse_state_handles_undefined_and_mobile_structure():
    state = {"noteData": {"data": {"noteData": video_note(cover="__UNDEF__")}}}  # 手机页结构 + undefined
    note = xhs_fetch.extract_note(xhs_fetch.parse_state(page_html(state)), NID)  # 解析
    assert note["type"] == "video" and note["cover"] is None  # undefined 变 None


def test_extract_note_desktop_structure():
    state = {"note": {"noteDetailMap": {NID: {"note": {"type": "video", "title": "t"}}}}}  # 桌面页结构
    assert xhs_fetch.extract_note(state, NID)["title"] == "t"  # 按 ID 取到


def test_parse_state_missing_raises():
    with pytest.raises(RuntimeError):  # 页面没有状态脚本
        xhs_fetch.parse_state("<html>captcha</html>")  # 被拦截的页面


def test_check_is_video_rejects_image_note():
    with pytest.raises(SystemExit, match="图文笔记"):  # 图文笔记人话退出
        xhs_fetch.check_is_video({"type": "normal", "imageList": [{}]})  # 图文


# ---------- 下载地址选择 ----------

def test_pick_video_url_prefers_h264_highest_with_backup():
    url, kind = xhs_fetch.pick_video_url(video_note())  # 默认策略
    assert url == "https://cdn/h264_720_bak.mp4"  # h264 最高清、备用地址兜底、升级 https
    assert kind == "stream.h264 720p"  # 来源说明


def test_pick_video_url_origin_flag():
    url, _ = xhs_fetch.pick_video_url(video_note(), origin=True)  # 要求原片
    assert url == "https://sns-video-bd.xhscdn.com/spectrum/abc"  # 拼接原片地址


def test_pick_video_url_falls_back():
    note = video_note()  # 基础笔记
    note["video"]["media"]["stream"] = {"h265": [{"height": 720, "masterUrl": "http://cdn/h265.mp4"}]}  # 只有 h265
    assert xhs_fetch.pick_video_url(note)[0] == "https://cdn/h265.mp4"  # 退到 h265
    note["video"]["media"]["stream"] = {}  # 没有转码流
    assert xhs_fetch.pick_video_url(note)[0].endswith("/spectrum/abc")  # 退到原片
    note["video"]["consumer"] = {}  # 原片也没有
    with pytest.raises(RuntimeError):  # 无地址报错
        xhs_fetch.pick_video_url(note)  # 选择


# ---------- 抓取的人话错误 ----------

def test_fetch_note_captcha(monkeypatch):
    def captcha(url, cookie=""):  # 模拟风控验证码
        raise urllib.error.HTTPError(url, 461, "captcha", {}, None)  # 461
    monkeypatch.setattr(xhs_fetch, "http", captcha)  # 注入
    with pytest.raises(SystemExit, match="风控"):  # 人话提示
        xhs_fetch.fetch_note(NID, TOKEN, "app_share", "")  # 抓取


def test_fetch_note_redirect_to_login(monkeypatch):
    monkeypatch.setattr(xhs_fetch, "http",  # 模拟被跳登录页
                        lambda url, cookie="": FakeResp(b"<html></html>", "https://www.xiaohongshu.com/login?x=1"))
    with pytest.raises(SystemExit, match="重新复制分享链接"):  # 引导重新分享
        xhs_fetch.fetch_note(NID, TOKEN, "app_share", "")  # 抓取


def test_fetch_note_empty_state(monkeypatch):
    html = page_html({"noteData": {"data": {}}}).encode()  # 有状态但没有笔记
    monkeypatch.setattr(xhs_fetch, "http", lambda url, cookie="": FakeResp(html, url))  # 注入
    with pytest.raises(SystemExit, match="xsec_token"):  # 令牌过期提示
        xhs_fetch.fetch_note(NID, TOKEN, "app_share", "")  # 抓取


# ---------- main 端到端（全部 mock） ----------

def test_main_writes_metadata_and_downloads(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(xhs_fetch, "load_cookie", lambda: "")  # 不读真实配置
    monkeypatch.setattr(xhs_fetch, "fetch_note", lambda *a: video_note())  # 笔记 mock
    got = {}  # 记录下载参数

    def fake_dl(url, dest, cookie=""):  # 假下载
        got["url"] = url  # 记录地址
        dest.write_bytes(b"0" * (200 * 1024))  # 写一个足够大的文件
        return 200 * 1024  # 返回字节数

    monkeypatch.setattr(xhs_fetch, "download", fake_dl)  # 注入
    xhs_fetch.main([SHARE, str(tmp_path)])  # 运行
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])  # 摘要
    assert summary["platform"] == "xiaohongshu" and summary["video_id"] == NID  # 平台与 ID
    assert got["url"] == "https://cdn/h264_720_bak.mp4"  # 默认下载 h264 流
    meta = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))  # 读 metadata
    page = meta["pages"][0]  # 唯一分P
    assert meta["owner"] == "不一莫" and meta["duration"] == 1979  # 作者与时长
    assert meta["suggested_note_type"] == "lecture"  # >600s → lecture
    assert page["media_path"] == "media_p01.mp4" and page["subtitle"] is False  # 交给 ASR
    assert page["part"] == "末日 _ 养殖场_"  # 文件名清洗


def test_main_reuses_cached_video(tmp_path, monkeypatch, capsys):
    (tmp_path / "media_p01.mp4").write_bytes(b"0" * (2 * 1024 * 1024))  # 已有 2MB 缓存
    monkeypatch.setattr(xhs_fetch, "load_cookie", lambda: "")  # 不读真实配置
    monkeypatch.setattr(xhs_fetch, "fetch_note", lambda *a: video_note(duration=None))  # 笔记 mock

    def no_dl(*a, **k):  # 不应发生下载
        raise AssertionError("不应重复下载")  # 断言失败

    monkeypatch.setattr(xhs_fetch, "download", no_dl)  # 注入
    xhs_fetch.main([SHARE, str(tmp_path)])  # 运行
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["video_id"] == NID  # 正常输出
