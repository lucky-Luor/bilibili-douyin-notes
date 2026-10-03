#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes 第二步（ASR兜底）：视频无官方字幕时，下载音频并用 faster-whisper 转写。

用法:
    python bili_transcribe.py <metadata.json路径> [--page 页码] [--model 模型] [--lang 语言] [--device 设备]

说明:
    - 页码对应 metadata.json 中 pages[].page（1开始）；不传则转写所有尚无文本的分P
    - 音频通过B站 playurl API 直接下载（64kbps，无需 ffmpeg/yt-dlp）
    - 兼容抖音 metadata（douyin_fetch.py 生成）：分P带 media_path 时直接用本地
      视频文件（faster-whisper 经 PyAV 可直接解码 mp4 音轨，无需抽轨）
    - 模型默认 small；首次运行会从 HF（已默认走 hf-mirror.com）下载模型
      tiny≈75MB / base≈145MB / small≈480MB / medium≈1.5GB，CPU 用 int8
    - 设备自动检测：有 NVIDIA GPU（CUDA 可用）时用 cuda+float16，否则 cpu+int8；
      可用 --device cpu/cuda 强制指定
    - 输出: <输出目录>/<页码>_<分P名>.txt（[mm:ss] 时间戳格式），
      另写完成标记 <txt路径>.done（M6，转写成功后落盘）
    - stdout 每处理完一个分P输出一行 JSON 进度
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# 国内网络默认走 HF 镜像；必须在 import huggingface 相关模块之前设置
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CONFIG = Path(__file__).resolve().parent.parent / "config.json"


def load_sessdata() -> str:
    try:
        return (json.loads(CONFIG.read_text(encoding="utf-8")).get("SESSDATA") or "").strip()
    except Exception:
        return ""


def http_get(url: str, cookie: str = "", retries: int = 3) -> bytes:
    """带重试的 GET：网络类错误按 1s/2s 退避重试，与 bili_fetch 侧对称。"""
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    if cookie:
        headers["Cookie"] = f"SESSDATA={cookie}"
    last_exc = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_exc = e
            if attempt < retries - 1:
                time.sleep(attempt + 1)
    raise last_exc


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()[:60]


def atomic_write_text(path: Path, text: str):
    """先写 .tmp 临时文件再 os.replace，避免中断留下半个文件。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def download_audio(bvid: str, cid: int, dest: Path, cookie: str = "") -> Path:
    """从 playurl 的 DASH 流中取最低码率音频（64k），存为 m4s（fMP4，PyAV 可直接解码）。"""
    url = f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&fnval=16"
    data = json.loads(http_get(url, cookie).decode("utf-8"))
    if data.get("code") != 0:
        raise RuntimeError(f"playurl 接口失败: code={data.get('code')} msg={data.get('message')}")
    audios = data["data"]["dash"]["audio"]
    best = min(audios, key=lambda a: a["bandwidth"])  # 做笔记无需高音质，越小越快
    audio_url = best["baseUrl"] or best["base_url"]
    req = urllib.request.Request(audio_url, headers={
        "User-Agent": UA, "Referer": "https://www.bilibili.com/",
        **({"Cookie": f"SESSDATA={cookie}"} if cookie else {})})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)
    return dest


def model_cached(model_size: str) -> bool:
    """模型已在本地 HF 缓存中时强制离线，避免网络问题导致失败。"""
    name = f"models--Systran--faster-whisper-{model_size}"
    bases = [Path.home() / ".cache" / "huggingface" / "hub"]
    if os.environ.get("HF_HOME"):
        bases.append(Path(os.environ["HF_HOME"]) / "hub")
    return any((b / name).exists() for b in bases)


def detect_device(device: str) -> str:
    """device=auto 时检测 CUDA 是否可用；否则原样返回。"""
    if device != "auto":
        return device
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


_MODEL_CHAIN = {"large-v3": ["large-v3", "medium", "small"],
                "large": ["large", "medium", "small"],
                "medium": ["medium", "small"]}
_MODEL_STATE = {}  # 进程内缓存：多分P只加载一次模型


def resolve_model_chain(model_size: str) -> list[str]:
    """模型降级链：高阶模型加载失败时逐级落到 small。"""
    return list(_MODEL_CHAIN.get(model_size, [model_size]))


def _cfg(key: str, default: str) -> str:
    try:
        return (json.loads(CONFIG.read_text(encoding="utf-8")).get(key) or default)
    except Exception:
        return default


def _cpu_threads() -> int:
    try:
        n = int(json.loads(CONFIG.read_text(encoding="utf-8")).get("asr_cpu_threads") or 0)
        if n > 0:
            return n
    except Exception:
        pass
    return min(os.cpu_count() or 4, 8)


def _build_prompt(lang: str | None, terms: list[str] | None) -> str | None:
    """initial_prompt：语言引导 + 用户术语偏好（词表正确词，转写时就写对）。"""
    parts = []
    if lang == "zh":
        parts.append("以下是普通话的简体中文转写。")
    if terms:
        parts.append("常听术语：" + "、".join(terms[:20]))
    return " ".join(parts) or None


def load_model(model_size: str, device: str = "auto") -> dict:
    """加载模型（降级链 + 进程内缓存）。返回状态 dict，含
    model_used/device_used/batched/fallback_reasons——每次落级都记录原因，绝不静默。"""
    key = (model_size, device)
    if _MODEL_STATE.get("key") == key:
        return _MODEL_STATE
    if model_cached(model_size):
        os.environ["HF_HUB_OFFLINE"] = "1"
    from faster_whisper import WhisperModel
    meta = {"model_used": model_size, "device_used": detect_device(device),
            "fallback_reasons": []}
    model = None
    for i, size in enumerate(resolve_model_chain(model_size)):
        try:
            if meta["device_used"] == "cuda":
                try:
                    model = WhisperModel(size, device="cuda", compute_type="float16",
                                         cpu_threads=_cpu_threads())
                except Exception as e:
                    meta["fallback_reasons"].append(f"cuda 加载失败({e})，回退 cpu int8")
                    meta["device_used"] = "cpu"
                    model = WhisperModel(size, device="cpu", compute_type="int8",
                                         cpu_threads=_cpu_threads())
            else:
                model = WhisperModel(size, device="cpu", compute_type="int8",
                                     cpu_threads=_cpu_threads())
            meta["model_used"] = size
            if i:
                meta["fallback_reasons"].append(f"模型 {model_size} 加载失败，降级为 {size}")
            break
        except Exception as e:
            meta["fallback_reasons"].append(f"{size} 加载失败: {e}")
            if size == "small":
                raise
    # 批量推理：可用则用（CPU 上约 1.5~2x），导入/构建失败回退逐段
    try:
        from faster_whisper import BatchedInferencePipeline
        pipeline = BatchedInferencePipeline(model=model)

        def transcriber(audio_path, language, prompt):
            return pipeline.transcribe(str(audio_path), language=language,
                                       initial_prompt=prompt, vad_filter=True,
                                       batch_size=8)
        batched = True
    except Exception as e:
        meta["fallback_reasons"].append(f"批量推理不可用({e})，回退逐段转写")

        def transcriber(audio_path, language, prompt):
            return model.transcribe(str(audio_path), language=language,
                                    initial_prompt=prompt, vad_filter=True)
        batched = False
    meta.update({"key": key, "model": model, "transcriber": transcriber,
                 "batched": batched})
    _MODEL_STATE.clear()
    _MODEL_STATE.update(meta)
    return _MODEL_STATE


def transcribe(audio_path: Path, model_size: str, lang: str | None,
               device: str = "auto", backend: str = "faster-whisper",
               terms: list[str] | None = None) -> str:
    if backend != "faster-whisper":
        raise NotImplementedError(
            f"暂不支持的 ASR 后端: {backend}（当前仅实现 faster-whisper）")
    state = load_model(model_size, device)
    prompt = _build_prompt(lang, terms)
    try:
        segments, info = state["transcriber"](audio_path, lang, prompt)
    except Exception:
        if not state["batched"]:
            raise
        # 批量推理运行期失败 → 回退逐段（"没有就用下级代替"）
        state["fallback_reasons"].append("批量推理运行期失败，本次回退逐段转写")
        state["batched"] = False
        segments, info = state["model"].transcribe(str(audio_path), language=lang,
                                                   initial_prompt=prompt,
                                                   vad_filter=True)
    lines = [f"[{int(s.start) // 60:02d}:{int(s.start) % 60:02d}] {s.text.strip()}"
             for s in segments if s.text.strip()]
    return "\n".join(lines)


def mark_done(txt_path: Path):
    """转写成功后写标记文件 <txt路径>.done（JSON 元信息：大小/完成时间/版本）。

    标记文件与 txt 同目录、原子写入；M6 后完成判据以标记文件为准，
    避免「txt 足够大但其实是上次中断残片」的误判。
    """
    info = {"size": txt_path.stat().st_size, "done_at": round(time.time(), 3),
            "marker_version": 1}
    atomic_write_text(txt_path.with_name(txt_path.name + ".done"),
                      json.dumps(info, ensure_ascii=True))


def txt_done(outdir: Path, page: dict) -> bool:
    """转写完成判据（M6）：标记文件 <txt路径>.done 存在即视为完成。

    兼容旧产物：无标记但 txt >= 100 字节视为有效（100 字节阈值逻辑只留在
    此兼容分支），并自动补写标记；更小的 txt 视为上次中断的残片，需重转。
    """
    f = outdir / f"{page['page']:02d}_{safe_name(page['part'])}.txt"
    if f.with_name(f.name + ".done").exists():
        return True
    if f.exists() and f.stat().st_size >= 100:  # 旧产物兼容分支：补写标记
        try:
            mark_done(f)
        except OSError:
            pass
        return True
    return False


def main():
    try:  # Agent 经管道调用时 stdout 可能是 gbk，emoji 标题会炸在最后一行 print
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("meta", help="bili_fetch.py 生成的 metadata.json 路径")
    ap.add_argument("--page", type=int, default=0, help="只处理第N个分P（1开始），默认处理所有")
    ap.add_argument("--model", default=None,
                    help="faster-whisper 模型: tiny/base/small/medium/large-v3"
                         "（默认取 config.json asr_model，再默认 small；高阶模型加载"
                         "失败自动降一档直到 small）")
    ap.add_argument("--lang", default="zh", help="语言，zh=中文，auto=自动检测（默认zh）")
    ap.add_argument("--device", default=None, choices=["auto", "cpu", "cuda"],
                    help="推理设备：auto=自动检测CUDA（默认取 config.json asr_device）；"
                         "cuda 加载失败自动回退 cpu int8")
    args = ap.parse_args()

    model_size = args.model or _cfg("asr_model", "small")
    device_pref = args.device or _cfg("asr_device", "auto")
    try:  # S4：用户术语偏好注入 initial_prompt（转写时就写对）
        from apply_glossary import top_terms
        terms = top_terms(limit=20)
    except Exception:
        terms = None

    meta_path = Path(args.meta)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    outdir = meta_path.parent
    cookie = load_sessdata()
    lang = None if args.lang == "auto" else args.lang

    # 已有字幕文本（>=100 字节）的分P视为完成，跳过
    targets = [p for p in meta["pages"]
               if (args.page == 0 or p["page"] == args.page) and not txt_done(outdir, p)]
    if not targets:
        print(json.dumps({"status": "nothing_to_do"}, ensure_ascii=True))
        return

    for p in targets:
        out_file = outdir / f"{p['page']:02d}_{safe_name(p['part'])}.txt"
        audio = outdir / f".audio_{p['page']:02d}.m4s"
        try:
            if p.get("media_path"):  # 抖音等平台：视频已在本地
                audio = outdir / p["media_path"]
            else:
                download_audio(meta["bvid"], p["cid"], audio, cookie)
            text = transcribe(audio, model_size, lang, device_pref, terms=terms)
            state = _MODEL_STATE  # load_model 已在 transcribe 内缓存
            atomic_write_text(out_file, text)
            mark_done(out_file)  # M6：写 <txt>.done 完成标记
            if state.get("fallback_reasons"):
                for reason in state["fallback_reasons"]:
                    print(f"警告: {reason}", file=sys.stderr)  # 落级可审计，不静默
            print(json.dumps({"page": p["page"], "part": p["part"],
                              "file": str(out_file), "chars": len(text),
                              "model_used": state.get("model_used"),
                              "device_used": state.get("device_used"),
                              "batched": state.get("batched"),
                              "terms_injected": bool(terms),
                              "fallback_reasons": state.get("fallback_reasons", []),
                              "status": "ok"}, ensure_ascii=True), flush=True)
        except Exception as e:
            print(json.dumps({"page": p["page"], "part": p["part"],
                              "status": "error", "error": str(e)},
                             ensure_ascii=True), flush=True)
        finally:
            if str(audio) == str(outdir / f".audio_{p['page']:02d}.m4s"):
                audio.unlink(missing_ok=True)  # 仅清理临时下载的音频，保留平台视频缓存


if __name__ == "__main__":
    main()
