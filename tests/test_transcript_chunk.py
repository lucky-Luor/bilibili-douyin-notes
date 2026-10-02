# -*- coding: utf-8 -*-
"""transcript_chunk.py 断块/overlap 的离线测试（含一次子进程 CLI 冒烟，不联网）。"""
import json
import subprocess
import sys
from pathlib import Path

from transcript_chunk import chunk_paragraphs


def test_chunk_breaks_at_budget():
    paras = ["aaaaa", "bbbbb", "ccccc", "ddddd"]  # 每段5字，预算10
    chunks = chunk_paragraphs(paras, budget=10, overlap=0)
    assert chunks == [["aaaaa", "bbbbb"], ["ccccc", "ddddd"]]


def test_chunk_overlap_copies_tail():
    paras = ["aaaaa", "bbbbb", "ccccc", "ddddd"]
    chunks = chunk_paragraphs(paras, budget=10, overlap=1)
    # 第二块开头携带上一块的最后 1 段
    assert chunks[1][0] == "bbbbb"
    assert chunks[1][1] == "ccccc"


def test_chunk_overlap_default_two():
    paras = [chr(97 + i) * 5 for i in range(6)]  # 6 段各5字，预算10
    chunks = chunk_paragraphs(paras, budget=10, overlap=2)
    assert chunks[1][:2] == chunks[0][-2:]


def test_single_oversized_para_own_chunk():
    big = "x" * 50
    paras = [big, "tiny"]
    chunks = chunk_paragraphs(paras, budget=10, overlap=0)
    assert chunks[0] == [big]           # 不截断，独立成块（允许超预算）
    assert chunks[1] == ["tiny"]


def test_no_trailing_overlap_only_chunk():
    """末尾只剩重叠段时不再多出一块。"""
    paras = ["aaaaa", "bbbbb"]  # 恰好一块
    chunks = chunk_paragraphs(paras, budget=10, overlap=2)
    assert chunks == [["aaaaa", "bbbbb"]]


def test_empty_input():
    assert chunk_paragraphs([], budget=10, overlap=2) == []


def test_all_content_preserved():
    paras = ["段落" + str(i) * 3 for i in range(20)]
    budget, overlap = 15, 2
    chunks = chunk_paragraphs(paras, budget, overlap)
    # 每个原始段落至少出现在一个块里（overlap 只会复制，不会丢失）
    joined = ["\n".join(c) for c in chunks]
    for p in paras:
        assert any(p in text for text in joined)


def test_cli_end_to_end(tmp_path):
    src = tmp_path / "transcript.txt"
    src.write_text("\n".join(f"[00:{i:02d}] 第{i}段内容" for i in range(10)),
                   encoding="utf-8")
    outdir = tmp_path / "chunks"
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "transcript_chunk.py"),
         str(src), "--budget", "30", "--overlap", "1", "--outdir", str(outdir)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["n_chunks"] >= 2
    assert len(summary["files"]) == summary["n_chunks"]
    for f in summary["files"]:
        assert Path(f).exists() and Path(f).stat().st_size > 0
        assert not Path(f).with_name(Path(f).name + ".tmp").exists()  # 原子写无残留
