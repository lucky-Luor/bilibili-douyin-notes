# -*- coding: utf-8 -*-
"""_hints.py 离线测试：三段式格式、已知错误码的解释、可恢复与不可恢复两种用法。"""
import pytest  # 测试框架

import _hints  # 被测模块


def test_format_hint_three_parts_and_detail():
    # 固定三段顺序；有详情时附第四行并截断
    text = _hints.format_hint("视频已删除", "无法生成笔记", "换一个链接", "x" * 500)  # 带超长详情
    lines = text.split("\n")  # 拆行
    assert lines[:3] == ["原因：视频已删除", "影响：无法生成笔记", "下一步：换一个链接"]  # 三段顺序
    assert lines[3] == "详情：" + "x" * 300  # 详情截断到 300 字符


def test_format_hint_default_impact():
    # 没给影响时用默认文案，保证三段齐全
    assert "影响：" + _hints.DEFAULT_IMPACT in _hints.format_hint("r", "", "n")  # 默认影响


def test_hint_error_is_runtime_error_with_fields():
    # HintError 是 RuntimeError 子类：现有 except Exception 的降级逻辑照常捕获
    e = _hints.HintError("原因A", "影响B", "下一步C", "详情D")  # 构造
    assert isinstance(e, RuntimeError)  # 可被现有逻辑捕获
    assert str(e).startswith("原因：原因A")  # str 即完整提示
    assert e.to_dict() == {"reason": "原因A", "impact": "影响B", "next_step": "下一步C", "detail": "详情D"}  # 字段


def test_hint_exit_raises_systemexit_with_text():
    # hint_exit 走 SystemExit：只打印文案，不打印堆栈
    with pytest.raises(SystemExit) as ei:  # 期望退出
        _hints.hint_exit("找不到 BV 号", "无法开始", "把完整链接发过来")  # 调用
    assert str(ei.value.code).split("\n")[2] == "下一步：把完整链接发过来"  # 退出信息为三段式


def test_explain_http_412_names_platform():
    # 412 识别为风控，原因和下一步都带平台名与可执行建议
    e = _hints.explain_http(412, "bilibili", "这一集跳过")  # B站 412
    assert "B站风控" in e.reason and "家用网络" in e.next_step  # 原因与下一步
    assert e.impact == "这一集跳过" and e.detail == "HTTP 412"  # 影响与默认详情


def test_explain_http_unknown_code():
    # 未收录的状态码也给出可执行的下一步
    e = _hints.explain_http(502, "youtube")  # 未收录
    assert "HTTP 502" in e.reason and "issue" in e.next_step  # 兜底文案


@pytest.mark.parametrize("code,keyword", [(62002, "不可见"), (-404, "不存在"), (-403, "付费"), (-101, "--login")])
def test_explain_bili_code_known(code, keyword):
    # 常见 B站业务码都有对应的原因或下一步
    e = _hints.explain_bili_code(code, "msg")  # 解释
    assert keyword in e.reason + e.next_step  # 命中关键词
    assert e.detail == f"code={code} msg"  # 保留原始码与信息


def test_explain_bili_code_unknown():
    # 未收录的业务码给兜底文案
    e = _hints.explain_bili_code(99999)  # 未收录
    assert "code=99999" in e.reason and e.detail == "code=99999"  # 兜底


def test_explain_douyin_filter_deleted_is_not_signature():
    # 平台返回 filter_detail（作品已删除）时，不能误报为“签名失效”
    data = {"aweme_detail": None, "filter_detail": {"detail_msg": "因作品权限或已被删除，无法观看"}}  # 实测响应
    e = _hints.explain_douyin_filter(data)  # 解释
    assert "作品不可见" in e.reason and "签名" not in e.reason  # 原因正确
    assert e.detail == "因作品权限或已被删除，无法观看"  # 平台原话作为详情


def test_explain_douyin_filter_signature():
    # 没有 filter_detail 时才判断为签名失效
    e = _hints.explain_douyin_filter({"aweme_detail": None})  # 无过滤原因
    assert "签名" in e.reason and "abogus.py" in e.next_step  # 原因与下一步
