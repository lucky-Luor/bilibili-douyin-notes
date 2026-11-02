# -*- coding: utf-8 -*-
"""_pyav_compat 离线测试：用假的 faster_whisper.audio 和假 av 模块，验证 metadata_errors 兼容逻辑。"""
import sys  # 注入假模块
import types  # 构造假模块

import pytest  # 测试框架

import _pyav_compat  # 被测模块


def _fake_av(accepts_metadata_errors: bool):  # 构造假 av：新版不认 metadata_errors
    av = types.ModuleType("av")  # 假模块
    av.calls = []  # 记录每次调用的参数
    def open_(file, mode="r", **kwargs):  # 假 open
        av.calls.append(dict(kwargs))  # 记录参数
        if "metadata_errors" in kwargs and not accepts_metadata_errors:  # 新版 PyAV 的行为
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")  # 与真实报错一致
        return f"container:{file}"  # 返回假容器
    av.open = open_  # 挂上 open
    av.audio = "audio-submodule"  # 其他属性应原样转发
    return av  # 返回假模块


@pytest.fixture
def fake_fw(monkeypatch):  # 注入假 faster_whisper.audio
    def install(av):  # 按给定的假 av 安装
        fw = types.ModuleType("faster_whisper")  # 顶层包
        audio = types.ModuleType("faster_whisper.audio")  # audio 子模块
        audio.av = av  # decode_audio 用的全局名
        fw.audio = audio  # 挂到包上
        monkeypatch.setitem(sys.modules, "faster_whisper", fw)  # 注入包
        monkeypatch.setitem(sys.modules, "faster_whisper.audio", audio)  # 注入子模块
        return audio  # 交给测试
    return install  # 返回安装函数


def test_new_pyav_retries_without_metadata_errors(fake_fw):
    # PyAV 19：带参数失败后去掉参数重试成功；其他属性照常可用
    audio = fake_fw(_fake_av(accepts_metadata_errors=False))  # 新版行为
    assert _pyav_compat.ensure() is True  # 本次新装
    assert audio.av.open("a.m4s", mode="r", metadata_errors="ignore") == "container:a.m4s"  # 重试成功
    assert audio.av.calls == [{"metadata_errors": "ignore"}, {}]  # 先带参数，再去掉参数
    assert audio.av.audio == "audio-submodule"  # 属性转发


def test_old_pyav_passes_through_once(fake_fw):
    # 老版 PyAV 支持该参数：只调用一次，参数原样传入
    audio = fake_fw(_fake_av(accepts_metadata_errors=True))  # 老版行为
    _pyav_compat.ensure()  # 安装
    audio.av.open("a.m4s", metadata_errors="ignore")  # 调用
    assert audio.av.calls == [{"metadata_errors": "ignore"}]  # 只调用一次


def test_ensure_is_idempotent_and_other_type_errors_propagate(fake_fw):
    # 重复调用不重复包装；与该参数无关的 TypeError 照常抛出
    av = _fake_av(accepts_metadata_errors=False)  # 新版行为
    def bad_open(*a, **k):  # 其他原因的 TypeError
        raise TypeError("bad file argument")  # 抛错
    av.open = bad_open  # 替换
    audio = fake_fw(av)  # 安装
    assert _pyav_compat.ensure() is True and _pyav_compat.ensure() is False  # 只装一次
    with pytest.raises(TypeError, match="bad file"):  # 不吞掉无关错误
        audio.av.open("a.m4s")  # 调用


def test_ensure_skips_when_module_has_no_av(monkeypatch):
    # faster_whisper.audio 结构不同（没有 av 属性）时不包装、不报错
    fw = types.ModuleType("faster_whisper")  # 顶层包
    audio = types.ModuleType("faster_whisper.audio")  # 没有 av 属性
    fw.audio = audio  # 挂到包上
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)  # 注入包
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", audio)  # 注入子模块
    assert _pyav_compat.ensure() is False  # 跳过
