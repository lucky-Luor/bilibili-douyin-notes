#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes 第五步：机械生成「参考时间戳」深链块（模型永不生成 URL）。

用法:
    python scripts/note_nav.py <笔记.md> --meta <metadata.json> --txt-dir <转写txt目录>

行为（规格 §1.1 / §3.5 / §6）:
    - 锚点与文字来源：笔记内的标注行（听讲层，跳过代码块）+ 带 (mm:ss) 的
      `##` 小节标题（章节起点）；元数据提供 platform / bvid / 分P 信息。
    - B站深链（U1 语义）：对每个锚点 [mm:ss]，在逐分P的转写稿中找包含该
      时间戳（±5s 容差）的分P，生成
      https://www.bilibili.com/video/<bvid>?p=<N>&t=<该分P内相对秒>
      （与 BiliNote 生产实现一致：t 是分P内相对秒而非全片累计秒，待人工实测
      确认）；找不到归属分P的锚点跳过并 stderr warning。
    - 抖音（metadata platform=douyin）：输出纯文本行，无链接。
    - 幂等：替换笔记中 <!-- NAV:BEGIN ... --> 与 <!-- NAV:END --> 之间内容；
      标记缺失 → 追加到文件末尾；标记不配对 → 报错退出且不改文件。
      写入用 .tmp → os.replace 原子替换。
    - frontmatter note_type=short 时跳过（short 的参考时间戳可省，§1.1/§2.2）。

输出: stdout 一行 JSON 摘要（ensure_ascii=True）：
    {"status","mode","written","skipped","platform","note_type"}
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

NAV_BEGIN_RE = re.compile(r"^\s*<!--\s*NAV:BEGIN\b.*?-->\s*$")
NAV_END_RE = re.compile(r"^\s*<!--\s*NAV:END\b.*?-->\s*$")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
NOTE_TYPE_RE = re.compile(r"^note_type\s*:\s*[\"']?(\S+?)[\"']?\s*$", re.MULTILINE)
FENCE_RE = re.compile(r"^\s*```")
TS_RE = re.compile(r"\[(\d{1,3}):(\d{2})\]")

# 标注行识别常量：同时兼容 v1 ★ 符号 与 v2 三词文字标签（**加粗** / 全角括号）。
# 标签与要点句之间允许有或没有空格（全角括号形式常无空格）。
# v2 切换为唯一语法时，只需修改本常量。
NAV_LABELS = r"核心必考|重点掌握|了解即可"
MARK_LINE_RE = re.compile(
    r"^\s*[-*]\s+(?:"
    r"(?P<star>★{1,3})"
    r"|\*\*(?P<tag_bold>" + NAV_LABELS + r")\*\*"
    r"|[（(](?P<tag_paren>" + NAV_LABELS + r")[）)]"
    r")\s*(?P<point>\S.*?)(?:\s+(?P<ts>\[\d{1,3}:\d{2}\]))?\s*$"
)
# 小节标题（带 (mm:ss) 或 （mm:ss））→ 章节起点
SECTION_TS_RE = re.compile(
    r"^##\s+(?P<title>.+?)\s*[（(](?P<ts>\d{1,3}:\d{2})[）)]\s*$")
# 转写稿文件名页码前缀（bili_fetch/transcribe 契约：<页码2位>_<分P名>.txt）
TXT_PAGE_RE = re.compile(r"^(\d{1,3})_")

NAV_BEGIN_TEXT = "<!-- NAV:BEGIN 由 scripts/note_nav.py 生成，请勿手工编辑 -->"
NAV_END_TEXT = "<!-- NAV:END -->"
ANCHOR_TOLERANCE = 5  # 锚点归属分P的 ±5s 容差（与检查6 同口径）


def collect_entries(text: str) -> list[dict]:
    """从笔记收集带时间戳的导航条目（标注行 + 带时间的小节标题）。

    跳过代码块；无 [mm:ss] 锚点的标注行不收集（无法生成深链）。
    返回 [{"sec", "label", "text", "order"}]，order 为原始行号（稳定排序用）。
    """
    entries: list[dict] = []
    in_fence = False
    for i, line in enumerate(text.splitlines()):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = MARK_LINE_RE.match(line)
        if m:
            ts = m.group("ts")
            if not ts:
                continue
            mm, ss = ts[1:-1].split(":")
            entries.append({
                "sec": int(mm) * 60 + int(ss),
                "label": m.group("star") or m.group("tag_bold")
                         or m.group("tag_paren") or "",
                "text": m.group("point").strip(),
                "order": i})
            continue
        m = SECTION_TS_RE.match(line)
        if m:
            mm, ss = m.group("ts").split(":")
            entries.append({"sec": int(mm) * 60 + int(ss), "label": "",
                            "text": m.group("title").strip(), "order": i})
    return entries


def build_page_index(txt_dir: Path) -> dict[int, list[int]]:
    """{页码: [该分P内各时间行的相对秒]}，来自 <页码>_<分P名>.txt。"""
    idx: dict[int, list[int]] = {}
    for f in sorted(txt_dir.glob("*.txt")):
        m = TXT_PAGE_RE.match(f.name)
        if not m:
            continue
        times = []
        for line in f.read_text(encoding="utf-8").splitlines():
            tm = re.match(r"\[(\d{1,3}):(\d{2})\]", line)
            if tm:
                times.append(int(tm.group(1)) * 60 + int(tm.group(2)))
        idx[int(m.group(1))] = times
    return idx


def locate_page(page_index: dict[int, list[int]], sec: int):
    """返回 (页码, 该分P内相对秒)；找不到归属分P返回 None。

    各分P时间戳均从 0 起，同一秒可能命中多个分P——按页码升序取首个，
    保证结果确定。
    """
    for page in sorted(page_index):
        for t in page_index[page]:
            if abs(t - sec) <= ANCHOR_TOLERANCE:
                return page, t
    return None


def fmt_ts(sec: int) -> str:
    return f"{sec // 60:02d}:{sec % 60:02d}"


def render_lines(entries: list[dict], platform: str, bvid: str,
                 page_index: dict[int, list[int]]) -> tuple[list[str], int]:
    """渲染导航行，按时间排序；返回 (行列表, 跳过数)。"""
    lines: list[str] = []
    skipped = 0
    for e in sorted(entries, key=lambda x: (x["sec"], x["order"])):
        ts = fmt_ts(e["sec"])
        text = f"{e['label']}：{e['text']}" if e["label"] else e["text"]
        if platform == "douyin":
            lines.append(f"- [{ts}] {text}")
            continue
        loc = locate_page(page_index, e["sec"])
        if loc is None:
            print(f"warning: 锚点 [{ts}]（{e['text'][:30]}）未找到归属分P，已跳过",
                  file=sys.stderr)
            skipped += 1
            continue
        page, rel = loc  # U1: t 用该分P内相对秒（与 BiliNote 一致，待人工实测确认）
        url = f"https://www.bilibili.com/video/{bvid}?p={page}&t={int(round(rel))}"
        lines.append(f"- [{ts}]({url}) {text}")
    return lines, skipped


def block_lines(nav_lines: list[str]) -> list[str]:
    """NAV 标记之间的内容（含「参考时间戳」标题）。"""
    return ["## 参考时间戳", ""] + nav_lines


def splice(text: str, nav_lines: list[str]) -> tuple[str, str]:
    """按标记状态拼装新文本；返回 (新文本, "append"|"replace")。

    标记不配对（BEGIN/END 数量不等、多个、或顺序颠倒）抛 ValueError。
    """
    lines = text.splitlines()
    begins = [i for i, l in enumerate(lines) if NAV_BEGIN_RE.match(l)]
    ends = [i for i, l in enumerate(lines) if NAV_END_RE.match(l)]
    if not begins and not ends:  # 标记缺失 → 追加到文件末尾
        block = "\n".join([NAV_BEGIN_TEXT] + block_lines(nav_lines) + [NAV_END_TEXT])
        return text.rstrip("\n") + "\n\n" + block + "\n", "append"
    if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
        raise ValueError(
            f"NAV 标记不配对（BEGIN×{len(begins)}、END×{len(ends)}，或顺序颠倒），"
            "拒绝修改文件")
    b, e = begins[0], ends[0]
    out = lines[:b + 1] + block_lines(nav_lines) + lines[e:]
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(out) + suffix, "replace"


def atomic_write(path: Path, content: str):
    """先写 .tmp 再 os.replace，避免中断留下半个文件。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="生成/刷新笔记的「参考时间戳」NAV 深链块")
    ap.add_argument("note", help="笔记 .md 路径")
    ap.add_argument("--meta", required=True, help="metadata.json 路径")
    ap.add_argument("--txt-dir", required=True, help="转写稿 .txt 所在目录")
    args = ap.parse_args(argv)

    note_path = Path(args.note)
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    txt_dir = Path(args.txt_dir)
    text = note_path.read_text(encoding="utf-8")

    fm = FRONTMATTER_RE.match(text)
    note_type = ""
    if fm:
        nt = NOTE_TYPE_RE.search(fm.group(1))
        note_type = (nt.group(1) if nt else "").strip().lower()
    if note_type == "short":  # §1.1/§2.2：short 的参考时间戳可省，直接跳过
        print(json.dumps({"status": "skipped", "reason": "note_type=short",
                          "written": 0, "skipped": 0}, ensure_ascii=True))
        return 0

    platform = (meta.get("platform") or "bilibili").lower()
    bvid = meta.get("bvid") or meta.get("video_id") or ""
    if platform != "douyin" and not bvid:
        print(json.dumps({"status": "error",
                          "error": "metadata 缺少 bvid/video_id，无法生成深链"},
                         ensure_ascii=True))
        return 1

    entries = collect_entries(text)
    page_index = build_page_index(txt_dir) if platform != "douyin" else {}
    nav_lines, skipped = render_lines(entries, platform, bvid, page_index)
    try:
        new_text, mode = splice(text, nav_lines)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(json.dumps({"status": "error", "error": str(exc)},
                         ensure_ascii=True))
        return 1
    if new_text != text:
        atomic_write(note_path, new_text)
    print(json.dumps({"status": "ok", "mode": mode, "written": len(nav_lines),
                      "skipped": skipped, "platform": platform,
                      "note_type": note_type or "lecture"}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
