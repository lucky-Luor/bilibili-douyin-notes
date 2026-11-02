#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语音识别基准：同一批音频，对比不同模型 / 线程数 / 批大小的耗时与文字一致度。

用法:
    python asr_bench.py <音频或视频文件...> [--models small,base] [--threads 0,4,8]
                        [--batch 8] [--lang zh] [--device cpu] [--out 结果.json]

说明:
    - 推理方式与 bili_transcribe.py 一致：int8（CPU）/ float16（CUDA）+ BatchedInferencePipeline + VAD
    - 耗时比 = 转写耗时 ÷ 音频时长，≤ 1 表示比视频本身短；模型加载时间单独记录
    - 一致度 = 与第一个组合（基准）转写文字的相似度（0~1），只作粗略的质量参考，不是准确率
    - threads 为 0 表示交给 CTranslate2 自动决定
"""
import argparse  # 命令行参数解析
import difflib  # 文字相似度
import json  # JSON 输出
import sys  # 退出与输出流
import time  # 计时
from pathlib import Path  # 路径处理

SAMPLE_RATE = 16000  # faster-whisper 解码后的采样率


def _reconfigure_stdout() -> None:  # Windows 管道下统一用 UTF-8 输出
    for stream in (sys.stdout, sys.stderr):  # 标准输出和标准错误都处理
        try:  # 被替换的流可能没有该方法
            stream.reconfigure(encoding="utf-8", errors="replace")  # 改为 UTF-8
        except (AttributeError, ValueError):  # 不支持时保持原样
            pass  # 忽略


def split_list(text: str, cast=str) -> list:  # 把逗号分隔的参数拆成列表
    return [cast(x.strip()) for x in text.split(",") if x.strip()]  # 去空白、去空项并转换类型


def audio_seconds(path: Path) -> float:  # 解码音频求时长（秒）
    from faster_whisper.audio import decode_audio  # 惰性导入
    import _pyav_compat  # faster-whisper 1.2.1 与 PyAV 19 的兼容层
    _pyav_compat.ensure()  # 装上后 decode_audio 才能在新版 PyAV 上运行
    return len(decode_audio(str(path), sampling_rate=SAMPLE_RATE)) / SAMPLE_RATE  # 采样点数 ÷ 采样率


def similarity(a: str, b: str) -> float:  # 两段文字的相似度
    if not a and not b:  # 都为空视为一致
        return 1.0  # 完全一致
    return round(difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), 3)  # 序列相似度，保留三位


def run_combo(path: Path, model_size: str, threads: int, batch: int, lang: str | None, device: str) -> dict:  # 跑一个组合
    from faster_whisper import BatchedInferencePipeline, WhisperModel  # 惰性导入
    import _pyav_compat  # faster-whisper 1.2.1 与 PyAV 19 的兼容层
    _pyav_compat.ensure()  # 转写时内部同样走 decode_audio
    compute = "float16" if device == "cuda" else "int8"  # 与 bili_transcribe.py 的精度选择一致
    t0 = time.monotonic()  # 加载计时起点
    model = WhisperModel(model_size, device=device, compute_type=compute, cpu_threads=threads)  # 加载模型
    load_s = time.monotonic() - t0  # 加载耗时
    pipeline = BatchedInferencePipeline(model=model)  # 批量推理管线
    t1 = time.monotonic()  # 转写计时起点（含解码，与真实使用一致）
    segments, _ = pipeline.transcribe(str(path), language=lang, vad_filter=True, batch_size=batch)  # 转写
    text = "".join(s.text for s in segments)  # segments 是生成器，必须消费完才算转写结束
    asr_s = time.monotonic() - t1  # 转写耗时
    return {"model": model_size, "threads": threads, "batch": batch,  # 组合参数
            "load_s": round(load_s, 1), "asr_s": round(asr_s, 1), "text": text}  # 耗时与文字


def bench(files: list[Path], models: list[str], threads: list[int], batches: list[int],  # 跑全部组合
          lang: str | None, device: str) -> list[dict]:
    rows = []  # 结果行
    for path in files:  # 逐个文件
        dur = audio_seconds(path)  # 音频时长
        baseline = None  # 本文件的基准文字（第一个组合）
        for m in models:  # 模型
            for t in threads:  # 线程数
                for b in batches:  # 批大小
                    print(f"[bench] {path.name} model={m} threads={t} batch={b}", file=sys.stderr, flush=True)  # 进度
                    r = run_combo(path, m, t, b, lang, device)  # 跑组合
                    baseline = r["text"] if baseline is None else baseline  # 第一个组合作为基准
                    r.update({"file": path.name, "duration_s": round(dur, 1),  # 文件与时长
                              "ratio": round(r["asr_s"] / dur, 2) if dur else None,  # 耗时比
                              "similarity": similarity(baseline, r["text"])})  # 与基准的一致度
                    rows.append(r)  # 记录
    return rows  # 返回全部结果


def render_markdown(rows: list[dict]) -> str:  # 生成 Markdown 表
    lines = ["| 文件 | 时长(s) | 模型 | 线程 | 批大小 | 加载(s) | 转写(s) | 耗时比 | 一致度 |",  # 表头
             "|---|---|---|---|---|---|---|---|---|"]  # 分隔行
    for r in rows:  # 每个组合一行
        lines.append(f"| {r['file']} | {r['duration_s']} | {r['model']} | {r['threads']} | {r['batch']} | "
                     f"{r['load_s']} | {r['asr_s']} | {r['ratio']} | {r['similarity']} |")  # 数据行
    return "\n".join(lines)  # 拼成文本


def parse_args(argv=None):  # 解析命令行参数
    ap = argparse.ArgumentParser(description="语音识别耗时基准")  # 参数解析器
    ap.add_argument("files", nargs="+", help="音频或视频文件")  # 输入文件
    ap.add_argument("--models", default="small,base", help="模型列表，逗号分隔")  # 模型
    ap.add_argument("--threads", default="0", help="CPU 线程数列表，0=自动")  # 线程
    ap.add_argument("--batch", default="8", help="批大小列表")  # 批大小
    ap.add_argument("--lang", default="zh", help="语言，auto=自动检测")  # 语言
    ap.add_argument("--device", default="cpu", choices=["cpu", "cuda"], help="推理设备")  # 设备
    ap.add_argument("--out", default="", help="另存 JSON 结果（含转写文字）")  # JSON 输出
    return ap.parse_args(argv)  # 返回解析结果


def main(argv=None) -> int:  # 主流程
    _reconfigure_stdout()  # 统一输出编码
    args = parse_args(argv)  # 解析参数
    files = [Path(f) for f in args.files]  # 转为路径
    missing = [str(f) for f in files if not f.is_file()]  # 不存在的文件
    if missing:  # 有缺失
        raise SystemExit(f"找不到文件: {', '.join(missing)}")  # 人话退出
    rows = bench(files, split_list(args.models), split_list(args.threads, int), split_list(args.batch, int),  # 跑基准
                 None if args.lang == "auto" else args.lang, args.device)  # 语言与设备
    print(render_markdown(rows))  # 输出表格
    if args.out:  # 需要另存
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")  # 写 JSON
    return 0  # 正常退出


if __name__ == "__main__":  # 脚本入口
    sys.exit(main())  # 以主流程返回值作为退出码
