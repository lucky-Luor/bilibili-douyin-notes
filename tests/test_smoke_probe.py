# -*- coding: utf-8 -*-
"""smoke_probe.py 离线测试：探测函数全部打桩，不发任何网络请求。"""
import json  # 构造链接清单与读结果

import pytest  # 测试框架

import smoke_probe  # 被测模块


def _write_urls(tmp_path, data):  # 写一份临时链接清单
    p = tmp_path / "urls.json"  # 清单路径
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")  # UTF-8 落盘
    return p  # 返回路径


def test_load_urls_skips_empty_and_filters_platform(tmp_path):
    # 空链接跳过；指定平台时只返回该平台；兼容字符串与对象两种写法
    p = _write_urls(tmp_path, {"_说明": "x",  # 说明字段应被忽略
                               "bilibili": [{"url": "https://b/1"}, {"url": ""}, "https://b/2"],  # 含空链接与字符串写法
                               "youtube": [{"url": "https://y/1"}]})  # 另一个平台
    assert smoke_probe.load_urls(p, None) == [("bilibili", "https://b/1"),  # 不过滤时按平台固定顺序返回
                                              ("bilibili", "https://b/2"),
                                              ("youtube", "https://y/1")]
    assert smoke_probe.load_urls(p, ["youtube"]) == [("youtube", "https://y/1")]  # 只取 youtube


def test_run_one_success(monkeypatch):
    # 探测函数正常返回时，结果标记成功并带上摘要
    monkeypatch.setitem(smoke_probe.PROBES, "bilibili", lambda url: {"pages": 3})  # 打桩成功
    r = smoke_probe.run_one("bilibili", "https://b/1")  # 执行
    assert r["ok"] is True and r["detail"] == {"pages": 3} and r["error"] == ""  # 成功且无错误


def test_run_one_catches_exception_and_systemexit(monkeypatch):
    # 普通异常与 SystemExit（平台脚本的人话退出）都转成失败记录，不中断后续链接
    def boom(url):  # 抛普通异常
        raise RuntimeError("HTTP Error 412")  # 模拟风控
    def bye(url):  # 抛人话退出
        raise SystemExit("链接缺少 xsec_token")  # 模拟平台脚本的提示
    monkeypatch.setitem(smoke_probe.PROBES, "bilibili", boom)  # 打桩异常
    monkeypatch.setitem(smoke_probe.PROBES, "xiaohongshu", bye)  # 打桩退出
    r1 = smoke_probe.run_one("bilibili", "u")  # 执行异常分支
    r2 = smoke_probe.run_one("xiaohongshu", "u")  # 执行退出分支
    assert not r1["ok"] and "RuntimeError" in r1["error"] and "412" in r1["error"]  # 记录异常类型与信息
    assert not r2["ok"] and r2["error"] == "链接缺少 xsec_token"  # 记录提示文案


def test_run_one_truncates_long_error(monkeypatch):
    # 超长错误信息截断，避免撑爆汇总表
    def long_err(url):  # 抛超长异常
        raise RuntimeError("x" * 1000)  # 1000 个字符
    monkeypatch.setitem(smoke_probe.PROBES, "douyin", long_err)  # 打桩
    assert len(smoke_probe.run_one("douyin", "u")["error"]) == smoke_probe.ERROR_MAX  # 截断到上限


def test_summarize_rates():
    # 按平台统计成功数与成功率
    results = [{"platform": "bilibili", "ok": True}, {"platform": "bilibili", "ok": False},  # B站 1/2
               {"platform": "youtube", "ok": True}]  # YouTube 1/1
    s = smoke_probe.summarize(results)  # 统计
    assert s["bilibili"] == {"total": 2, "ok": 1, "rate": 0.5}  # B站成功率 50%
    assert s["youtube"]["rate"] == 1.0  # YouTube 成功率 100%


def test_render_markdown_escapes_pipes():
    # 明细里的竖线要转义，否则会破坏表格
    results = [{"platform": "youtube", "url": "u", "ok": True, "seconds": 1.0, "error": "",  # 一条成功结果
                "detail": {"title": "a | b"}}]  # 标题里带竖线
    md = smoke_probe.render_markdown(results, smoke_probe.summarize(results))  # 生成表格
    assert "a \\| b" in md and "100%" in md  # 竖线已转义、成功率正确


def test_main_strict_exit_code_and_json(tmp_path, monkeypatch, capsys):
    # --strict 有失败时返回 1；--out 写出完整 JSON
    p = _write_urls(tmp_path, {"bilibili": ["https://b/1"]})  # 一条链接
    monkeypatch.setitem(smoke_probe.PROBES, "bilibili", lambda url: (_ for _ in ()).throw(RuntimeError("x")))  # 打桩失败
    out = tmp_path / "r.json"  # JSON 输出路径
    assert smoke_probe.main(["--urls", str(p), "--strict", "--out", str(out)]) == 1  # 严格模式返回 1
    assert smoke_probe.main(["--urls", str(p)]) == 0  # 非严格模式返回 0
    data = json.loads(out.read_text(encoding="utf-8"))  # 读取 JSON
    assert data["summary"]["bilibili"]["ok"] == 0 and len(data["results"]) == 1  # 内容正确
    assert "| bilibili | 0 / 1 | 0% |" in capsys.readouterr().out  # stdout 有汇总表


def test_main_rejects_unknown_platform(tmp_path):
    # 拼错平台名时给出人话提示
    p = _write_urls(tmp_path, {"bilibili": ["https://b/1"]})  # 一条链接
    with pytest.raises(SystemExit, match="未知平台"):  # 期望人话退出
        smoke_probe.main(["--urls", str(p), "--platform", "bilbili"])  # 拼错的平台名


def test_main_empty_list(tmp_path):
    # 清单里没有可用链接时给出人话提示
    p = _write_urls(tmp_path, {"bilibili": [{"url": ""}]})  # 只有空链接
    with pytest.raises(SystemExit, match="没有可用链接"):  # 期望人话退出
        smoke_probe.main(["--urls", str(p)])  # 执行


def test_default_url_list_is_valid():
    # 仓库自带的链接清单格式正确，且每个平台都有条目
    data = json.loads(smoke_probe.DEFAULT_URLS.read_text(encoding="utf-8"))  # 读取清单
    for platform in smoke_probe.PROBES:  # 四个平台
        assert isinstance(data.get(platform), list) and data[platform]  # 都有非空列表


def test_probe_bilibili_reads_season(monkeypatch):
    # B站探测：合集视频数按 sections 累加；字幕只取一轮
    import bili_fetch  # 被打桩的平台模块
    view = {"data": {"title": "t", "pages": [{"cid": 1}, {"cid": 2}],  # 两个分P
                     "ugc_season": {"id": 9, "sections": [{"episodes": [1, 2]}, {"episodes": [3]}]}}}  # 合集 3 个视频
    monkeypatch.setattr(bili_fetch, "api", lambda url: view)  # 打桩详情接口
    calls = {}  # 记录字幕调用参数
    def fake_subs(bvid, cid, cookie, rounds):  # 打桩字幕接口
        calls.update(cid=cid, rounds=rounds)  # 记录参数
        return [{"x": 1}], True  # 返回一条字幕
    monkeypatch.setattr(bili_fetch, "fetch_subtitles", fake_subs)  # 打桩
    r = smoke_probe.probe_bilibili("BV1Wv411h7kN")  # 执行
    assert r["pages"] == 2 and r["season_id"] == 9 and r["season_episodes"] == 3 and r["subtitle"]  # 摘要正确
    assert calls == {"cid": 1, "rounds": 1}  # 取第 1 个分P、只取一轮


def test_probe_douyin_range_request(monkeypatch):
    # 抖音探测：按原样地址发范围请求，只读 RANGE_BYTES
    import douyin_fetch  # 被打桩的平台模块
    detail = {"desc": "d", "video": {"duration": 19000, "play_addr": {"url_list": ["https://v/playwm/?x"]}}}  # 详情
    monkeypatch.setattr(douyin_fetch, "extract_video_id", lambda u: "1")  # 打桩 ID 解析
    monkeypatch.setattr(douyin_fetch, "fetch_aweme_detail", lambda i: detail)  # 打桩详情接口
    seen = {}  # 记录请求
    class Resp:  # 假响应
        def read(self, n):  # 按长度读取
            return b"\0" * min(n, smoke_probe.RANGE_BYTES)  # 最多返回 RANGE_BYTES
    def fake_http(url, headers=None):  # 打桩 HTTP
        seen.update(url=url, range=headers["Range"])  # 记录地址与范围头
        return Resp()  # 返回假响应
    monkeypatch.setattr(douyin_fetch, "http", fake_http)  # 打桩
    r = smoke_probe.probe_douyin("https://www.douyin.com/video/1")  # 执行
    assert seen["url"] == "https://v/playwm/?x"  # 地址未被改写
    assert seen["range"] == f"bytes=0-{smoke_probe.RANGE_BYTES - 1}"  # 范围头正确
    assert r["range_bytes"] == smoke_probe.RANGE_BYTES and r["duration"] == 19  # 摘要正确
