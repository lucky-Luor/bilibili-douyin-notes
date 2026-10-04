#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes：为笔记时间导航区机械填充深链（模型永不生成 URL）。

v3 = 视频时间索引条目原位填链；v1/v2 = 参考时间戳块生成（冻结路径）。

用法:
    python scripts/note_nav.py <笔记.md> --meta <metadata.json> --txt-dir <转写txt目录>

行为:
    - v3 模式（frontmatter template_version=v3）：正文全面去时间戳，文末
      <details> 的「视频时间索引」由模型写好 NAV 标记对与无链接条目
      `- [mm:ss] 标题`（模型永不生成 URL），本脚本只做机械填链：沿用 ±5s
      容差的分P归属，重写为
      `- [mm:ss](https://www.bilibili.com/video/<bvid>?p=N&t=<分P内相对秒>) 标题`。
      已带链接的条目跳过（幂等）；找不到归属分P → stderr warning 并跳过该行；
      platform=douyin 保持纯文本（属正常，不告警）；标记缺失或条目区为空 →
      stderr warning、不改文件、正常退出 0（存在性由门禁管）；标记不配对 →
      报错退出且不改文件。
    - v1/v2 模式（老笔记兼容，冻结路径）：锚点与文字来源为笔记内的标注行
      （听讲层，跳过代码块）+ 带 (mm:ss) 的 `##` 小节标题（章节起点）；元数据
      提供 platform / bvid / 分P 信息。对每个锚点 [mm:ss]，在逐分P的转写稿中
      找包含该时间戳（±5s 容差）的分P，生成
      https://www.bilibili.com/video/<bvid>?p=<N>&t=<该分P内相对秒>；
      找不到归属分P的锚点跳过并 stderr warning。
      幂等：替换笔记中 <!-- NAV:BEGIN ... --> 与 <!-- NAV:END --> 之间内容；
      标记缺失 → 追加到文件末尾；标记不配对 → 报错退出且不改文件。
      写入用 .tmp → os.replace 原子替换。
    - 两种模式同口径：B站深链 t 均为分P内相对秒而非全片累计秒（U1 语义，
      见 locate_page）；抖音（metadata platform=douyin）输出纯文本行、无链接；
      frontmatter note_type=short 时跳过（short 笔记的时间导航可省）。

输出: stdout 一行 JSON 摘要（ensure_ascii=True）：
    {"status","mode","written","skipped","platform","note_type"}
    （mode：v1/v2 为 "append"/"replace"；v3 为 "v3"）
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
TEMPLATE_VERSION_RE = re.compile(
    r"^template_version\s*:\s*[\"']?(\S+?)[\"']?\s*$", re.MULTILINE)
FENCE_RE = re.compile(r"^\s*```")
TS_RE = re.compile(r"\[(\d{1,3}):(\d{2})\]")

# ── v1/v2 冻结路径（老笔记兼容；v3 不经过）──
# 标注行识别常量：v1/v2 冻结路径专用：识别老笔记的 ★/三词标签标注行；
# v3 笔记不走此机制（见 main_v3）。
# 标签与要点句之间允许有或没有空格（全角括号形式常无空格）。
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

# v3 模式：文末「视频时间索引」的无链接条目（模型只写 `- [mm:ss] 标题`，URL 由
# 本脚本按分P归属机械填入）。`\s+(?!\()` 排除已带链接的条目 `- [mm:ss](url) 标题`
# （幂等跳过）；标题须以非空白开头，与 MARK_LINE_RE 的要点句口径一致。
V3_ENTRY_RE = re.compile(
    r"^(?P<indent>\s*)-\s*\[(?P<ts>\d{1,3}:\d{2})\]\s+(?!\()(?P<title>\S.*?)\s*$")
V3_LINKED_RE = re.compile(r"^\s*-\s*\[\d{1,3}:\d{2}\]\s*\(")

NAV_BEGIN_TEXT = "<!-- NAV:BEGIN 由 scripts/note_nav.py 生成，请勿手工编辑 -->"
NAV_END_TEXT = "<!-- NAV:END -->"
ANCHOR_TOLERANCE = 5  # 锚点归属分P的 ±5s 容差（与检查6 同口径）


# ── v1/v2 冻结路径（老笔记兼容；v3 不经过）──
# （其间 build_page_index / locate_page 为两条路径共用，v3 填链同样调用）
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
    保证结果确定。v1/v2 与 v3 的深链 t 均取该分P内相对秒（U1 语义）。
    U1 待真机实测：B站 `?p=&t=` 相对秒语义，实测前与 BiliNote 实现保持一致。
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
        page, rel = loc
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


def main_v3(note_path: Path, text: str, txt_dir: Path, platform: str,
            bvid: str, note_type: str) -> int:
    """v3 模式：为文末时间索引的无链接条目机械填链（标记对由模型写好，不追加）。

    标记缺失/条目区为空 → stderr warning、不改文件、正常退出 0（存在性由门禁管）；
    标记不配对 → 报错退出且不改文件；条目行原位重写（不排序、不增删行），
    写入沿用 .tmp → os.replace 原子替换。
    """
    lines = text.splitlines()
    begins = [i for i, l in enumerate(lines) if NAV_BEGIN_RE.match(l)]
    ends = [i for i, l in enumerate(lines) if NAV_END_RE.match(l)]
    if not begins and not ends:
        print("warning: v3 笔记未找到 NAV:BEGIN/NAV:END 标记对，跳过填链"
              "（条目存在性由门禁检查）", file=sys.stderr)
        print(json.dumps({"status": "skipped", "reason": "v3 NAV 标记缺失",
                          "mode": "v3", "written": 0, "skipped": 0,
                          "platform": platform,
                          "note_type": note_type or "lecture"},
                         ensure_ascii=True))
        return 0
    if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
        err = (f"NAV 标记不配对（BEGIN×{len(begins)}、END×{len(ends)}，或顺序颠倒），"
               "拒绝修改文件")
        print(f"error: {err}", file=sys.stderr)
        print(json.dumps({"status": "error", "error": err}, ensure_ascii=True))
        return 1
    inner = lines[begins[0] + 1:ends[0]]
    if not any(V3_ENTRY_RE.match(l) or V3_LINKED_RE.match(l) for l in inner):
        print("warning: v3 NAV 条目区为空（无任何时间索引条目），跳过填链",
              file=sys.stderr)
        print(json.dumps({"status": "skipped", "reason": "v3 NAV 条目区为空",
                          "mode": "v3", "written": 0, "skipped": 0,
                          "platform": platform,
                          "note_type": note_type or "lecture"},
                         ensure_ascii=True))
        return 0
    page_index = build_page_index(txt_dir) if platform != "douyin" else {}
    out = list(lines)
    written = skipped = 0
    for offset, line in enumerate(inner):
        m = V3_ENTRY_RE.match(line)
        if not m:
            continue  # 已带链接 / 空行 / 其他内容：原样保留（幂等）
        ts_text, title = m.group("ts"), m.group("title")
        if platform == "douyin":  # 抖音无深链语义：保持纯文本，属正常不告警
            continue
        mm, ss = ts_text.split(":")
        loc = locate_page(page_index, int(mm) * 60 + int(ss))
        if loc is None:
            print(f"warning: v3 条目 [{ts_text}]（{title[:30]}）未找到归属分P，已跳过",
                  file=sys.stderr)
            skipped += 1
            continue
        page, rel = loc
        url = f"https://www.bilibili.com/video/{bvid}?p={page}&t={int(round(rel))}"
        out[begins[0] + 1 + offset] = (
            f"{m.group('indent')}- [{ts_text}]({url}) {title}")
        written += 1
    new_text = "\n".join(out) + ("\n" if text.endswith("\n") else "")
    if new_text != text:
        atomic_write(note_path, new_text)
    print(json.dumps({"status": "ok", "mode": "v3", "written": written,
                      "skipped": skipped, "platform": platform,
                      "note_type": note_type or "lecture"}, ensure_ascii=True))
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="为笔记时间导航区机械填充深链：v3 填「视频时间索引」条目，"
                    "v1/v2 生成「参考时间戳」NAV 块（模型永不生成 URL）")
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
    template_version = ""
    if fm:
        nt = NOTE_TYPE_RE.search(fm.group(1))
        note_type = (nt.group(1) if nt else "").strip().lower()
        tv = TEMPLATE_VERSION_RE.search(fm.group(1))
        template_version = (tv.group(1) if tv else "").strip().lower()
    if note_type == "short":  # short 笔记的时间导航可省，直接跳过
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

    if template_version == "v3":  # v3：条目已由模型写好，仅按分P归属机械填链
        return main_v3(note_path, text, txt_dir, platform, bvid, note_type)

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
