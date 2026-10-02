#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes 长转写稿分块工具（map-reduce 前置步骤）。

用法:
    python transcript_chunk.py <转写.txt> [--budget 15000] [--overlap 2] [--outdir <目录>]

输入: 带 `[mm:ss] 内容` 时间行的转写稿，每行视为一个段落。
分块规则:
    - 按字符预算累计，累计到 budget 即断块；断块回退到段落边界（不切断任何一行）；
    - 断块时把上一块最后 overlap 个段落复制到下一块开头，保证上下文连续；
    - 单段超过 budget 时独立成块（允许超预算，不截断内容）。

输出:
    <outdir>/<原文件名>.chunk-01.txt ... （outdir 默认为源文件同目录的 chunks/，
    每个文件原子写入：先写 .tmp 再 os.replace 落盘，避免半截文件）
    stdout 打印 JSON 摘要: {"n_chunks": N, "files": [...], "chunks": [{"file","chars"}...]}
    （chars 为该块内容字符数，不含换行）
"""
import argparse
import json
import os
from pathlib import Path


def chunk_paragraphs(paras: list[str], budget: int, overlap: int) -> list[list[str]]:
    """按字符预算把段落列表切块，块间复制末尾 overlap 个段落作重叠。"""
    chunks: list[list[str]] = []
    cur: list[str] = []
    cur_chars = 0
    new_since_close = 0  # 距上次断块新增的段落数（防止末尾只剩重叠段时多出一块）
    for p in paras:
        cur.append(p)
        cur_chars += len(p)
        new_since_close += 1
        if cur_chars >= budget:
            chunks.append(cur)
            tail = cur[-overlap:] if overlap > 0 else []
            cur = list(tail)
            cur_chars = sum(len(x) for x in cur)
            new_since_close = 0
    if cur and new_since_close > 0:
        chunks.append(cur)
    return chunks


def main():
    ap = argparse.ArgumentParser(description="长转写稿分块（map-reduce 用）")
    ap.add_argument("src", help="转写稿 .txt 路径（每行一段，[mm:ss] 内容 格式）")
    ap.add_argument("--budget", type=int, default=15000, help="每块字符预算（默认15000）")
    ap.add_argument("--overlap", type=int, default=2, help="块尾重叠段落数（默认2）")
    ap.add_argument("--outdir", help="输出目录（默认源文件同目录下的 chunks/）")
    args = ap.parse_args()

    src = Path(args.src)
    if not src.exists():
        print(json.dumps({"error": f"文件不存在: {src}"}, ensure_ascii=False))
        raise SystemExit(1)
    paras = [l.rstrip("\n") for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not paras:
        print(json.dumps({"error": "转写稿为空"}, ensure_ascii=False))
        raise SystemExit(1)

    chunks = chunk_paragraphs(paras, args.budget, args.overlap)

    outdir = Path(args.outdir) if args.outdir else src.parent / "chunks"
    outdir.mkdir(parents=True, exist_ok=True)
    summary = []
    for i, chunk in enumerate(chunks, 1):
        out = outdir / f"{src.stem}.chunk-{i:02d}.txt"
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_text("\n".join(chunk) + "\n", encoding="utf-8")
        os.replace(tmp, out)  # 原子写：.tmp → os.replace，避免中断留下半截文件
        summary.append({"file": str(out), "chars": sum(len(x) for x in chunk)})

    print(json.dumps({"n_chunks": len(chunks),
                      "files": [s["file"] for s in summary],
                      "chunks": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
