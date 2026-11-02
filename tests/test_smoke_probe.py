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
    assert s["bilibili"] == {"total": 2, "ok": 1, "link": 0, "rate": 0.5}  # B站成功率 50%
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


def _fake_yt(monkeypatch, info, transcript):  # 打桩 yt_fetch 的各个函数
    import yt_fetch  # 被打桩的平台模块
    monkeypatch.setattr(yt_fetch, "load_config", lambda: {})  # 无配置
    monkeypatch.setattr(yt_fetch, "fetch_info", lambda vid, opts: info)  # 元数据
    monkeypatch.setattr(yt_fetch, "fetch_via_transcript_api", transcript)  # 一级字幕
    monkeypatch.setattr(yt_fetch, "fetch_via_ytdlp", lambda info, langs, opts: None)  # 二级字幕为空


def test_probe_youtube_blocked_stub_is_failure(monkeypatch):
    # 机房 IP 上 yt-dlp 只返回标题、时长为 0、字幕被拦截：必须判为失败（2026-11-02 Actions 实测）
    def blocked(vid, langs, proxy):  # 一级字幕被封
        raise RuntimeError("blocked")  # 与 yt_fetch 的约定一致
    _fake_yt(monkeypatch, {"title": "t", "duration": 0}, blocked)  # 空壳元数据
    with pytest.raises(RuntimeError, match="视为被风控拦截"):  # 期望失败
        smoke_probe.probe_youtube("https://www.youtube.com/watch?v=aircAruvnKk")  # 执行
    r = smoke_probe.run_one("youtube", "https://www.youtube.com/watch?v=aircAruvnKk")  # 走完整流程
    assert r["ok"] is False and "transcript-api: blocked" in r["error"]  # 记录为失败并带原因


def test_probe_youtube_no_subtitle_but_duration_is_ok(monkeypatch):
    # 没字幕但有时长：可以下载音频走语音识别，算成功
    _fake_yt(monkeypatch, {"title": "t", "duration": 600}, lambda vid, langs, proxy: None)  # 有时长、无字幕
    r = smoke_probe.probe_youtube("https://www.youtube.com/watch?v=aircAruvnKk")  # 执行
    assert r["duration"] == 600 and r["subtitle"] is False  # 成功返回摘要


def test_classify_link_vs_platform():
    # 令牌过期、作品已删除算链接失效；风控、接口变动算平台故障
    assert smoke_probe.classify("") == "ok"  # 成功
    assert smoke_probe.classify("原因：笔记页被重定向到登录页或 404：xsec_token 可能已过期") == "link"  # 小红书令牌过期
    assert smoke_probe.classify("HintError: 原因：作品不可见（已删除、设为私密或仅粉丝可见）") == "link"  # 抖音作品删除
    assert smoke_probe.classify("HintError: 原因：请求被B站风控拦截") == "platform"  # 风控


def test_summary_excludes_link_failures_from_rate():
    # 成功率不含链接失效；合计按全部平台算
    results = [{"platform": "xiaohongshu", "ok": True, "category": "ok"},  # 成功
               {"platform": "xiaohongshu", "ok": False, "category": "link"},  # 令牌过期
               {"platform": "douyin", "ok": False, "category": "platform"}]  # 平台故障
    s = smoke_probe.summarize(results)  # 统计
    assert s["xiaohongshu"]["rate"] == 1.0 and s["xiaohongshu"]["link"] == 1  # 不含链接失效
    assert smoke_probe.overall(s) == {"ok": 1, "valid": 2, "link": 1, "rate": 0.5}  # 合计
    md = smoke_probe.render_markdown([dict(r, url="u", seconds=0, error="e", detail={}) for r in results], s)  # 生成报告
    assert "整体成功率 50%" in md and "⚠️ 链接失效" in md and "_说明" in md  # 合计、标记、维护提示


def test_strict_ignores_link_failures_and_report_dir(tmp_path, monkeypatch):
    # --strict 只在平台故障时返回 1；--report-dir 写出同名 .md 与 .json
    p = tmp_path / "urls.json"  # 链接清单
    p.write_text(json.dumps({"xiaohongshu": ["u1"]}), encoding="utf-8")  # 一条小红书
    def expired(url):  # 模拟令牌过期
        raise SystemExit("原因：页面里没有笔记数据：xsec_token 可能已过期")  # 三段式退出
    monkeypatch.setitem(smoke_probe.PROBES, "xiaohongshu", expired)  # 打桩
    rd = tmp_path / "reports"  # 报告目录
    assert smoke_probe.main(["--urls", str(p), "--strict", "--report-dir", str(rd)]) == 0  # 链接失效不算失败
    mds = list(rd.glob("smoke-*.md"))  # 生成的报告
    assert len(mds) == 1 and mds[0].with_suffix(".json").exists()  # .md 与 .json 成对
    data = json.loads(mds[0].with_suffix(".json").read_text(encoding="utf-8"))  # 读 JSON
    assert data["overall"]["link"] == 1 and data["results"][0]["category"] == "link"  # 分类写入
