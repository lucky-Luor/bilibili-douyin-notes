#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-notes 第二步（ASR兜底）：视频无官方字幕时，下载音频并用 faster-whisper 转写。

用法:
    python bili_transcribe.py <metadata.json路径> [--page 页码] [--model 模型] [--lang 语言]

说明:
    - 页码对应 metadata.json 中 pages[].page（1开始）；不传则转写所有尚无文本的分P
    - 音频通过B站 playurl API 直接下载（64kbps，无需 ffmpeg/yt-dlp）
    - 模型默认 small；首次运行会从 HF（已默认走 hf-mirror.com）下载模型
      tiny≈75MB / base≈145MB / small≈480MB / medium≈1.5GB，CPU 用 int8
    - 输出: <输出目录>/<页码>_<分P名>.txt（[mm:ss] 时间戳格式）
    - stdout 每处理完一个分P输出一行 JSON 进度
"""
import argparse
import json
import os
import re
import sys
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


def http_get(url: str, cookie: str = "") -> bytes:
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    if cookie:
        headers["Cookie"] = f"SESSDATA={cookie}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()[:60]


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


def transcribe(audio_path: Path, model_size: str, lang: str | None) -> str:
    if model_cached(model_size):
        os.environ["HF_HUB_OFFLINE"] = "1"
    from faster_whisper import WhisperModel
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    # initial_prompt 引导 whisper 输出简体中文，避免转成繁体
    prompt = "以下是普通话的简体中文转写。" if lang == "zh" else None
    segments, info = model.transcribe(str(audio_path), language=lang,
                                      initial_prompt=prompt, vad_filter=True)
    lines = [f"[{int(s.start) // 60:02d}:{int(s.start) % 60:02d}] {s.text.strip()}"
             for s in segments if s.text.strip()]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("meta", help="bili_fetch.py 生成的 metadata.json 路径")
    ap.add_argument("--page", type=int, default=0, help="只处理第N个分P（1开始），默认处理所有")
    ap.add_argument("--model", default="small",
                    help="faster-whisper 模型: tiny/base/small/medium/large-v3（默认small）")
    ap.add_argument("--lang", default="zh", help="语言，zh=中文，auto=自动检测（默认zh）")
    args = ap.parse_args()

    meta_path = Path(args.meta)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    outdir = meta_path.parent
    cookie = load_sessdata()
    lang = None if args.lang == "auto" else args.lang

    # 已有字幕文本的分P视为完成，跳过
    def done(p) -> bool:
        f = outdir / f"{p['page']:02d}_{safe_name(p['part'])}.txt"
        return f.exists() and f.stat().st_size > 0

    targets = [p for p in meta["pages"]
               if (args.page == 0 or p["page"] == args.page) and not done(p)]
    if not targets:
        print(json.dumps({"status": "nothing_to_do"}, ensure_ascii=False))
        return

    for p in targets:
        out_file = outdir / f"{p['page']:02d}_{safe_name(p['part'])}.txt"
        audio = outdir / f".audio_{p['page']:02d}.m4s"
        try:
            download_audio(meta["bvid"], p["cid"], audio, cookie)
            text = transcribe(audio, args.model, lang)
            out_file.write_text(text, encoding="utf-8")
            print(json.dumps({"page": p["page"], "part": p["part"],
                              "file": str(out_file), "chars": len(text),
                              "status": "ok"}, ensure_ascii=False), flush=True)
        except Exception as e:
            print(json.dumps({"page": p["page"], "part": p["part"],
                              "status": "error", "error": str(e)},
                             ensure_ascii=False), flush=True)
        finally:
            audio.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
