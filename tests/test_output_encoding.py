# -*- coding: utf-8 -*-
"""M3/M4 冒烟测试：GBK 管道下 emoji 摘要行不崩（离线，无网络依赖）。

复现第三轮复检 M3 的失败模式：Agent 用管道调用脚本时 stdout.encoding=gbk，
`print(json.dumps(..., ensure_ascii=False))` 遇 emoji 抛 UnicodeEncodeError，
且发生在全部工作完成之后，Agent 拿不到 JSON 摘要。

这里用 `io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict")`
顶替 sys.stdout，直接调用各脚本真实的 JSON 摘要打印函数（helper 内嵌在
脚本里、main 不便调用，已在脚本内抽出同款 print_json_summary 供 import，
被测逻辑本身未改动），断言：
    1. 不抛异常；
    2. 输出字节可还原，json.loads 后 emoji 原样回来（JSON 转义无损）。

覆盖脚本（各自内含同款 helper，即脚本实际执行的打印路径）：
    - bili_fetch.print_json_summary
    - transcript_chunk.print_json_summary
"""
import io
import json
import sys

import pytest

import bili_fetch
import transcript_chunk

EMOJI_SUMMARY = {
    "title": "Lesson 🎓 1 🔥📌✅",
    "owner": "UP主·名字",
    "pages": [{"page": 1, "part": "第1P 上课啦"}],
}


def _run_under_gbk_pipe(func, *args, **kwargs):
    """把 sys.stdout 换成 GBK(strict) 管道后执行 func，返回 (返回值, 输出字节)。"""
    buf = io.BytesIO()
    fake = io.TextIOWrapper(buf, encoding="gbk", errors="strict")
    old = sys.stdout
    try:
        sys.stdout = fake
        ret = func(*args, **kwargs)
        fake.flush()
    finally:
        sys.stdout = old
    return ret, buf.getvalue()


@pytest.mark.parametrize(
    "printer",
    [bili_fetch.print_json_summary, transcript_chunk.print_json_summary],
    ids=["bili_fetch", "transcript_chunk"])
def test_json_summary_survives_gbk_pipe(printer):
    """ensure_ascii=True 的摘要行在 GBK 管道下不崩，emoji 经转义无损还原。"""
    _, data = _run_under_gbk_pipe(printer, EMOJI_SUMMARY)
    assert data  # 走到这里本身即未抛 UnicodeEncodeError
    parsed = json.loads(data.decode("utf-8"))
    assert parsed == EMOJI_SUMMARY  # emoji 照样还原


def test_gbk_pipe_reproduces_m3_failure_mode():
    """对照组：确认该 GBK 管道确实能复现 M3——ensure_ascii=False + emoji 必崩。"""
    with pytest.raises(UnicodeEncodeError):
        _run_under_gbk_pipe(lambda obj: print(json.dumps(obj, ensure_ascii=False)),
                            EMOJI_SUMMARY)


def test_reconfigure_stdout_is_safe():
    """M3 防线一：_reconfigure_stdout 对不支持 reconfigure 的 stdout 不抛异常。"""
    class _NoReconfigure:
        pass

    old = sys.stdout
    try:
        sys.stdout = _NoReconfigure()
        bili_fetch._reconfigure_stdout()
        transcript_chunk._reconfigure_stdout()
    finally:
        sys.stdout = old
