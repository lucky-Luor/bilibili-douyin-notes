# -*- coding: utf-8 -*-
"""asr_bench.py 离线测试：faster-whisper 全部打桩，不加载真实模型。"""
import json  # 读 JSON 结果
import sys  # 注入假模块
import types  # 构造假模块

import pytest  # 测试框架

import asr_bench  # 被测模块


def test_split_list_casts_and_strips():
    # 逗号分隔参数：去空白、去空项、按类型转换
    assert asr_bench.split_list(" small, base ,") == ["small", "base"]  # 字符串列表
    assert asr_bench.split_list("0,6,12", int) == [0, 6, 12]  # 整数列表


def test_similarity_bounds():
    # 相似度：完全相同为 1，都为空为 1，完全不同接近 0
    assert asr_bench.similarity("梯度下降", "梯度下降") == 1.0  # 相同
    assert asr_bench.similarity("", "") == 1.0  # 都为空
    assert asr_bench.similarity("abc", "xyz") == 0.0  # 完全不同


@pytest.fixture
def fake_whisper(monkeypatch):
    # 注入假的 faster_whisper：记录构造参数，转写结果随模型名变化
    created = []  # 记录每次构造模型的参数

    class WhisperModel:  # 假模型
        def __init__(self, size, device, compute_type, cpu_threads):  # 记录参数
            self.size = size  # 模型名
            created.append({"size": size, "device": device, "compute_type": compute_type,  # 保存构造参数
                            "cpu_threads": cpu_threads})

    class BatchedInferencePipeline:  # 假批量管线
        def __init__(self, model):  # 保存模型
            self.model = model  # 引用
        def transcribe(self, path, language, vad_filter, batch_size):  # 返回生成器，模拟真实接口
            assert vad_filter is True  # 与 bili_transcribe.py 一致开启 VAD
            text = "梯度下降" if self.model.size == "small" else "梯度下将"  # 不同模型给不同文字
            return (types.SimpleNamespace(text=c) for c in text), None  # 逐字片段

    fw = types.ModuleType("faster_whisper")  # 假包
    fw.WhisperModel = WhisperModel  # 挂模型类
    fw.BatchedInferencePipeline = BatchedInferencePipeline  # 挂管线类
    audio = types.ModuleType("faster_whisper.audio")  # 假子模块
    audio.decode_audio = lambda path, sampling_rate: [0.0] * (sampling_rate * 10)  # 固定 10 秒音频
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)  # 注入包
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", audio)  # 注入子模块
    return created  # 返回构造记录


def test_bench_ratio_and_similarity(tmp_path, fake_whisper, monkeypatch):
    # 每个组合一行；第一个组合为基准（一致度 1），耗时比 = 转写耗时 ÷ 时长
    f = tmp_path / "a.m4s"  # 假音频
    f.write_bytes(b"x")  # 写入内容
    ticks = iter(range(0, 1000, 5))  # 每次取时间前进 5 秒
    monkeypatch.setattr(asr_bench.time, "monotonic", lambda: next(ticks))  # 固定计时
    rows = asr_bench.bench([f], ["small", "base"], [0, 6], [8], "zh", "cpu")  # 跑 2×2 组合
    assert len(rows) == 4  # 四个组合
    assert rows[0]["similarity"] == 1.0 and rows[0]["duration_s"] == 10.0  # 基准一致度为 1、时长 10 秒
    assert rows[0]["asr_s"] == 5 and rows[0]["ratio"] == 0.5  # 转写 5 秒 → 耗时比 0.5
    assert rows[2]["model"] == "base" and rows[2]["similarity"] < 1.0  # base 的文字与基准不同
    assert [c["cpu_threads"] for c in fake_whisper] == [0, 6, 0, 6]  # 线程数逐个传入
    assert all(c["compute_type"] == "int8" for c in fake_whisper)  # CPU 用 int8


def test_cuda_uses_float16(tmp_path, fake_whisper):
    # CUDA 设备与 bili_transcribe.py 一致使用 float16
    f = tmp_path / "a.m4s"  # 假音频
    f.write_bytes(b"x")  # 写入内容
    asr_bench.bench([f], ["small"], [0], [8], "zh", "cuda")  # 跑一个组合
    assert fake_whisper[0]["compute_type"] == "float16"  # 精度正确


def test_main_outputs_table_and_json(tmp_path, fake_whisper, capsys):
    # main：stdout 输出表格，--out 写出含文字的 JSON
    f = tmp_path / "a.m4s"  # 假音频
    f.write_bytes(b"x")  # 写入内容
    out = tmp_path / "r.json"  # JSON 输出路径
    assert asr_bench.main([str(f), "--models", "small", "--out", str(out)]) == 0  # 正常退出
    assert "| a.m4s | 10.0 | small |" in capsys.readouterr().out  # 表格行
    assert json.loads(out.read_text(encoding="utf-8"))[0]["text"] == "梯度下降"  # JSON 含转写文字


def test_main_missing_file(tmp_path):
    # 文件不存在时人话退出
    with pytest.raises(SystemExit, match="找不到文件"):  # 期望退出
        asr_bench.main([str(tmp_path / "nope.m4s")])  # 不存在的文件
