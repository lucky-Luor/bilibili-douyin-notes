# -*- coding: utf-8 -*-
"""_net.py 离线测试：4xx 不重试直接给三段式提示，网络错误和 5xx 重试，单集失败记录结构化字段。"""
import io  # 构造假的 HTTP 错误响应体
import json  # 解析进度 JSON
import urllib.error  # HTTPError / URLError

import pytest  # 测试框架

import _hints  # failure_record
import _net  # 被测模块

URL = "https://api.bilibili.com/x/test"  # 测试用地址（urlopen 已被替换，不会真正请求）


def _http_error(code):  # 构造指定状态码的 HTTPError
    return urllib.error.HTTPError("https://api.bilibili.com/x", code, "err", {}, io.BytesIO(b""))  # 假响应


class _Resp(io.BytesIO):  # 可用作 with 语句的假响应
    def __enter__(self):  # 进入上下文
        return self  # 返回自身
    def __exit__(self, *a):  # 退出上下文
        return False  # 不吞异常


@pytest.fixture
def calls(monkeypatch):  # 记录 urlopen 调用次数，并去掉退避等待
    box = {"n": 0, "plan": []}  # n=调用次数，plan=每次调用的结果（异常或字节）
    def fake_urlopen(req, timeout):  # 按 plan 依次返回
        box["n"] += 1  # 计数
        item = box["plan"][min(box["n"], len(box["plan"])) - 1]  # 取本次结果，超出则重复最后一个
        if isinstance(item, Exception):  # 计划抛异常
            raise item  # 抛出
        return _Resp(item)  # 正常返回
    monkeypatch.setattr(_net.urllib.request, "urlopen", fake_urlopen)  # 替换真实请求
    monkeypatch.setattr(_net.time, "sleep", lambda s: None)  # 不真正等待
    return box  # 交给测试设置 plan


def test_4xx_no_retry_and_three_part_hint(calls):
    # 412 风控：只请求一次，抛 HintError，三段都在，不是英文堆栈
    calls["plan"] = [_http_error(412)]  # 第一次就 412
    with pytest.raises(_hints.HintError) as ei:  # 期望三段式异常
        _net.http_get(URL, {}, platform="bilibili", impact="无法获取视频信息")  # 发请求
    assert calls["n"] == 1  # 4xx 不重试
    text = str(ei.value)  # 用户看到的文案
    assert "原因：请求被B站风控拦截" in text and "影响：无法获取视频信息" in text and "下一步：" in text  # 三段齐全
    assert "Precondition" not in text  # 不暴露英文原始错误


def test_network_error_retries_then_hint(calls):
    # 超时：重试 3 次后给“连不上”提示
    calls["plan"] = [urllib.error.URLError(TimeoutError("timed out"))]  # 每次都超时
    with pytest.raises(_hints.HintError) as ei:  # 期望三段式异常
        _net.http_get(URL, {}, retries=3, impact="x")  # 发请求
    assert calls["n"] == 3  # 用满重试次数
    assert "连不上B站" in ei.value.reason and ei.value.next_step == _net.NETWORK_NEXT_STEP  # 原因与下一步


def test_5xx_retries_then_succeeds(calls):
    # 502 是服务器临时故障，重试后成功
    calls["plan"] = [_http_error(502), b"ok"]  # 第一次 502，第二次成功
    assert _net.http_get(URL, {}) == b"ok"  # 拿到响应体
    assert calls["n"] == 2  # 重试了一次


def test_5xx_exhausted_explains_status(calls):
    # 5xx 一直失败：按状态码解释，而不是报“连不上”
    calls["plan"] = [_http_error(503)]  # 一直 503
    with pytest.raises(_hints.HintError) as ei:  # 期望三段式异常
        _net.http_get(URL, {}, retries=2)  # 发请求
    assert calls["n"] == 2 and "HTTP 503" in ei.value.reason  # 次数与原因


def test_download_to_writes_file_and_maps_4xx(calls, tmp_path):
    # 流式下载：成功写文件；403 转成三段式提示
    calls["plan"] = [b"abc"]  # 成功返回 3 字节
    dest = tmp_path / "a.m4s"  # 目标文件
    assert _net.download_to(URL, {}, dest) == dest and dest.read_bytes() == b"abc"  # 写入成功
    calls["n"], calls["plan"] = 0, [_http_error(403)]  # 重置为 403
    with pytest.raises(_hints.HintError, match="拒绝访问"):  # 期望 403 的解释
        _net.download_to(URL, {}, tmp_path / "b.m4s")  # 发请求


def test_bili_headers_with_and_without_cookie():
    # 有 SESSDATA 才带 Cookie
    assert "Cookie" not in _net.bili_headers("")  # 未登录
    assert _net.bili_headers("abc")["Cookie"] == "SESSDATA=abc"  # 已登录


def test_failure_record_hint_and_plain_exception():
    # HintError 沿用三段字段；普通异常取首行原文并给通用下一步；两者都保留 error 字段
    rec = _hints.failure_record(_hints.HintError("r", "抛出处影响", "n"), "默认影响", "cmd")  # 三段式异常
    assert rec["reason"] == "r" and rec["impact"] == "抛出处影响" and rec["next_step"] == "n；补跑：cmd"  # 字段
    assert rec["error"].startswith("原因：r") and rec["rerun"] == "cmd"  # 兼容字段与补跑命令
    rec = _hints.failure_record(ValueError("坏数据\n第二行"), "默认影响")  # 普通异常
    assert rec["reason"] == "坏数据" and rec["impact"] == "默认影响" and "issue" in rec["next_step"]  # 首行与通用建议


def test_transcribe_page_error_has_hint_and_rerun(tmp_path, monkeypatch, capsys):
    # 转写单集下载被 412 拦截：进度 JSON 带三段字段和只补这一集的命令，并继续执行
    import bili_transcribe  # 被测脚本
    meta = {"bvid": "BV1xx411c7mD", "pages": [{"page": 1, "part": "P1", "cid": 1, "duration": 60}]}  # 单分P
    mp = tmp_path / "metadata.json"  # metadata 路径
    mp.write_text(json.dumps(meta), encoding="utf-8")  # 写入
    def boom(*a, **k):  # 模拟下载音频时被风控
        raise _hints.explain_http(412, "bilibili", "这一集无法下载音频，不能语音识别")  # 三段式异常
    monkeypatch.setattr(bili_transcribe, "download_audio", boom)  # 替换下载
    monkeypatch.setattr(bili_transcribe, "load_sessdata", lambda: "")  # 无登录态
    monkeypatch.setattr("sys.argv", ["bili_transcribe.py", str(mp)])  # main 从命令行读参数
    bili_transcribe.main()  # 运行
    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])  # 最后一行进度
    assert last["status"] == "error" and "风控" in last["reason"]  # 原因说清
    assert last["rerun"].endswith(f'"{mp}" --page 1')  # 只补跑这一集


def test_screenshot_page_error_has_hint_and_rerun(tmp_path, monkeypatch, capsys):
    # 截图单集下载视频被 412 拦截：进度 JSON 带三段字段，补跑命令只含这一集
    import bili_screenshot  # 被测脚本
    meta = {"bvid": "BV1xx411c7mD", "pages": [{"page": 2, "part": "P2", "cid": 1, "duration": 600}]}  # 单分P
    mp = tmp_path / "metadata.json"  # metadata 路径
    mp.write_text(json.dumps(meta), encoding="utf-8")  # 写入
    (tmp_path / "02_P2.txt").write_text("[00:10] 循环依赖的三级缓存\n", encoding="utf-8")  # 转写稿
    img_dir = tmp_path / "images"  # 截图目录
    monkeypatch.setattr(bili_screenshot, "load_anchors",  # 固定一个锚点
                        lambda vid=None: {2: [("循环依赖", "图示", ["循环依赖"])]})
    monkeypatch.setattr(bili_screenshot, "load_sessdata", lambda: "")  # 无登录态
    def boom(*a, **k):  # 模拟下载视频时被风控
        raise _hints.explain_http(412, "bilibili", "这一集没有截图，笔记照常生成")  # 三段式异常
    monkeypatch.setattr(bili_screenshot, "download_video", boom)  # 替换下载
    bili_screenshot.main([str(mp), str(tmp_path), str(img_dir)])  # 运行
    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])  # 最后一行进度
    assert last["status"] == "error" and last["impact"] == "这一集没有截图，笔记照常生成"  # 影响说清
    assert last["rerun"].endswith(f'"{img_dir}" 2')  # 只补跑第 2 集
