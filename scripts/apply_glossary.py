#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes ASR 术语校正工具（报告式，可审计，不静默替换）。

用法:
    python apply_glossary.py <文件.md|.txt> [更多文件...] [--glossary <词表路径>] [--apply]

词表:
    默认 <skill目录>/references/asr-glossary.json（JSON，键 "map"：错法→正确写法）。
    也接受 .md 词表：每行一条「错法（正确词）」或「错法→正确写法」，# 开头视为注释。
    正确词统一取 split("（")[0]（括号内仅是语境说明，如「权限（Reactive权限例子语境）」
    只替换为「权限」）。

模式:
    默认 --dry-run：只输出报告行 `[mm:ss] "错法" -> 正确词 | 上下文±20字`，不改任何文件。
    --apply：先把原文件备份为 <文件>.bak，再原子写（.tmp → os.replace）完成替换。
    设计原则：替换是否成立依赖语境（如「全线」→「权限」仅在 Reactive 权限例子语境成立），
    必须先看 dry-run 报告逐条确认，再决定是否 --apply；本工具不做任何静默改写。

匹配规则:
    词表键按长度降序合并进同一个正则交替（长键优先，如「购语言」先于「购语」），
    单趟扫描、大小写不敏感；替换产生的文本不会被再次匹配（幂等，可安全往返）。

输出: 每条命中一行报告 + 末尾一行 JSON 摘要 {"file","hits","replaced","backup"}。
正常 exit 0；文件/词表读取失败 exit 1。
"""
import argparse
import bisect
import json
import os
import re
import sys
from pathlib import Path

TS_RE = re.compile(r"\[(\d{1,2}:\d{2})\]")
MD_PAREN_RE = re.compile(r"[-*\d.、\s]*([^\s→（(]+)\s*[（(]([^）)]+)[）)]")
MD_ARROW_RE = re.compile(r"[-*\s]*([^\s→]+)\s*→\s*(\S+)")


def load_glossary(path: Path) -> dict[str, str]:
    """加载词表为 {错法: 正确词}；正确词取 split("（")[0]（主词，不含括号内说明）。"""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        mapping = data.get("map", {}) if isinstance(data, dict) else {}
        if not isinstance(mapping, dict):
            raise ValueError(f"词表 {path} 缺少 'map' 对象")
        raw = {str(k): str(v) for k, v in mapping.items()}
    else:
        raw = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("|"):
                continue
            m = MD_PAREN_RE.match(line) or MD_ARROW_RE.match(line)
            if m:
                raw[m.group(1)] = m.group(2)
    glossary = {}
    for wrong, right in raw.items():
        main = right.split("（")[0].split("(")[0].strip()
        if wrong and main:
            glossary[wrong] = main
    return glossary


def build_pattern(glossary: dict[str, str]) -> re.Pattern:
    """词表键按长度降序进同一个正则交替（长键优先），单趟扫描、大小写不敏感。"""
    keys = sorted(glossary, key=len, reverse=True)
    if not keys:
        raise ValueError("词表为空")
    return re.compile("|".join(re.escape(k) for k in keys), re.IGNORECASE)


def build_ts_index(text: str) -> tuple[list[int], list[str]]:
    """[mm:ss] 时间戳位置索引，供二分查找每个命中左侧最近的时间戳。"""
    positions, stamps = [], []
    for m in TS_RE.finditer(text):
        positions.append(m.start())
        stamps.append(m.group(1))
    return positions, stamps


def nearest_ts(positions: list[int], stamps: list[str], pos: int) -> str:
    """命中位置左侧最近的 [mm:ss]；前方没有时间戳时返回占位符。"""
    i = bisect.bisect_right(positions, pos) - 1
    return stamps[i] if i >= 0 else "--:--"


def scan_text(text: str, glossary: dict[str, str],
              pat: re.Pattern) -> tuple[list[dict], str]:
    """扫描全部命中。返回 (报告条目, 替换后的文本)。"""
    repl = {k.lower(): v for k, v in glossary.items()}
    positions, stamps = build_ts_index(text)
    hits = []
    for m in pat.finditer(text):
        wrong, right = m.group(0), repl.get(m.group(0).lower(), "")
        s, e = m.span()
        ctx = text[max(0, s - 20):s] + "【" + wrong + "】" + text[e:e + 20]
        hits.append({"ts": nearest_ts(positions, stamps, s),
                     "wrong": wrong, "right": right,
                     "context": ctx.replace("\n", " ").replace("\r", " ")})
    new_text = pat.sub(lambda m: repl[m.group(0).lower()], text)
    return hits, new_text


def process_file(path: Path, glossary: dict[str, str], pat: re.Pattern,
                 apply: bool) -> dict:
    """处理单个文件：打印报告行；--apply 时先 .bak 备份再原子写替换。"""
    text = path.read_text(encoding="utf-8")
    hits, new_text = scan_text(text, glossary, pat)
    for h in hits:
        print(f'[{h["ts"]}] "{h["wrong"]}" -> {h["right"]} | …{h["context"]}…')
    backup = None
    if apply and hits:
        backup = str(path) + ".bak"
        Path(backup).write_text(text, encoding="utf-8")  # 备份原文（替换前）
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, path)  # 原子写：.tmp → os.replace
    return {"file": str(path), "hits": len(hits),
            "replaced": len(hits) if apply else 0, "backup": backup}


def main():
    ap = argparse.ArgumentParser(
        description="ASR 术语校正（报告式，可审计；--apply 才落盘）",
        epilog="示例: python apply_glossary.py 转写.txt --dry-run   # 先看报告\n"
               "      python apply_glossary.py 转写.txt --apply          # 确认后替换")
    ap.add_argument("files", nargs="+", help="要校正的 .md / .txt 文件（可多个）")
    ap.add_argument("--glossary", help="词表路径（默认 <skill目录>/references/asr-glossary.json）")
    ap.add_argument("--apply", action="store_true",
                    help="实际替换（先写 .bak 备份，再原子写）；缺省为 dry-run 只报告")
    args = ap.parse_args()

    glossary_path = (Path(args.glossary) if args.glossary
                     else Path(__file__).resolve().parent.parent / "references" / "asr-glossary.json")
    try:
        glossary = load_glossary(glossary_path)
        pat = build_pattern(glossary)
    except (OSError, ValueError) as e:
        print(json.dumps({"error": f"词表加载失败: {e}"}, ensure_ascii=False))
        sys.exit(1)

    summaries = []
    for f in args.files:
        path = Path(f)
        if not path.exists():
            print(json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False))
            sys.exit(1)
        summaries.append(process_file(path, glossary, pat, args.apply))
    print(json.dumps({"glossary": str(glossary_path), "mode": "apply" if args.apply else "dry-run",
                      "results": summaries}, ensure_ascii=False))


if __name__ == "__main__":
    main()
