#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes：为笔记时间导航区机械填充深链（模型永不生成 URL）。

v4 = 文末「## 视频时间索引」节内 `+ [mm:ss] 标题` 条目原位填链（无 frontmatter 或 --template v4）；
v3 = NAV 标记内的视频时间索引条目原位填链（frontmatter template_version=v3）。
v1 / v2 的「参考时间戳」生成路径已移除，frontmatter 写其他版本时报错退出。

用法:
    python scripts/note_nav.py <笔记.md> --meta <metadata.json> --txt-dir <转写txt目录>

行为:
    - v4 模式（无 frontmatter 或 --template v4，语雀直导版式，无 NAV 标记）：
      定位文末「## 视频时间索引」节，把节内无链接条目 `+ [mm:ss] 标题`
      原位重写为 `+ [mm:ss](URL) 标题`（列表符号原样保留，其余行不动）；
      note_type 取 --note-type > metadata.json 的 suggested_note_type > lecture，
      short 跳过；找不到该节或节内无条目 → stderr warning、不改文件、退出 0；
      分P归属、平台深链、幂等与原子写入同 v3。
    - v3 模式（frontmatter template_version=v3）：正文全面去时间戳，文末
      <details> 的「视频时间索引」由模型写好 NAV 标记对与无链接条目
      `- [mm:ss] 标题`（模型永不生成 URL），本脚本只做机械填链：沿用 ±5s
      容差的分P归属，重写为
      `- [mm:ss](https://www.bilibili.com/video/<bvid>?p=N&t=<分P内相对秒>) 标题`。
      已带链接的条目跳过（幂等）；找不到归属分P → stderr warning 并跳过该行；
      platform=douyin 保持纯文本（属正常，不告警）；标记缺失或条目区为空 →
      stderr warning、不改文件、正常退出 0（存在性由门禁管）；标记不配对 →
      报错退出且不改文件。
    - 两种模式同口径：B站深链 t 均为分P内相对秒而非全片累计秒（U1 语义，
      见 locate_page）；YouTube（platform=youtube，单分P）生成
      https://www.youtube.com/watch?v=<video_id>&t=<秒>s；
      抖音 / 小红书（platform=douyin / xiaohongshu）输出纯文本行、无链接；
      note_type=short 时跳过（short 笔记的时间导航可省）。

输出: stdout 一行 JSON 摘要（ensure_ascii=True）：
    {"status","mode","written","skipped","platform","note_type"}
    （mode：v4 为 "v4"；v3 为 "v3"）
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
TS_RE = re.compile(r"\[(\d{1,3}):(\d{2})\]")

# 转写稿文件名页码前缀（bili_fetch/transcribe 契约：<页码2位>_<分P名>.txt）
TXT_PAGE_RE = re.compile(r"^(\d{1,3})_")

# v3 模式：文末「视频时间索引」的无链接条目（模型只写 `- [mm:ss] 标题`，URL 由
# 本脚本按分P归属机械填入）。`\s+(?!\()` 排除已带链接的条目 `- [mm:ss](url) 标题`
# （幂等跳过）；标题须以非空白开头。
V3_ENTRY_RE = re.compile(
    r"^(?P<indent>\s*)-\s*\[(?P<ts>\d{1,3}:\d{2})\]\s+(?!\()(?P<title>\S.*?)\s*$")
V3_LINKED_RE = re.compile(r"^\s*-\s*\[\d{1,3}:\d{2}\]\s*\(")
# v4 模式：时间索引节内的无链接条目 `+ [mm:ss] 标题`（列表符号兼容 - / *，原样保留）；
# `(?!\()` 排除已带链接的条目，保证幂等
V4_ENTRY_RE = re.compile(
    r"^(?P<indent>\s*)(?P<bullet>[-*+])\s*\[(?P<ts>\d{1,3}:\d{2})\]\s+(?!\()(?P<title>\S.*?)\s*$")
V4_INDEX_HEAD_RE = re.compile(r"^##\s+.*视频时间索引")  # v4 时间索引节标题
H2_LINE_RE = re.compile(r"^##\s+")  # 任意二级标题（确定节的结束位置）

ANCHOR_TOLERANCE = 5  # 条目归属分P的 ±5s 容差（与门禁同口径）


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


def locate_page(page_index: dict[int, list[int]], sec: int,
                only_page: int | None = None):
    """返回 (页码, 该分P内相对秒)；找不到归属分P返回 None。

    各分P时间戳均从 0 起，同一秒可能命中多个分P——按页码升序取首个，
    保证结果确定。v3 与 v4 的深链 t 均取该分P内相对秒（U1 语义）。
    U1 待真机实测：B站 `?p=&t=` 相对秒语义，实测前与 BiliNote 实现保持一致。
    only_page：指定笔记只覆盖单个分P时把定位限制在该分P内（每选集一份
    笔记的场景；早分P的同秒时间行会抢先命中导致深链跳错选集）。
    """
    pages = sorted(page_index)
    if only_page is not None:
        pages = [p for p in pages if p == only_page]
    for page in pages:
        for t in page_index[page]:
            if abs(t - sec) <= ANCHOR_TOLERANCE:
                return page, t
    return None


# 无深链语义的平台：时间索引保持纯文本（网页端不支持按秒跳转）
NO_LINK_PLATFORMS = ("douyin", "xiaohongshu")


def deep_link(platform: str, vid: str, page: int, rel: float) -> str:
    """按平台生成深链：YouTube 为单分P的 watch?v=<id>&t=<秒>s，其余为 B站 ?p=N&t=S。"""
    if platform == "youtube":  # YouTube 没有分P概念，只带秒数
        return f"https://www.youtube.com/watch?v={vid}&t={int(round(rel))}s"  # t 参数带 s 后缀
    return f"https://www.bilibili.com/video/{vid}?p={page}&t={int(round(rel))}"  # B站原有格式


def atomic_write(path: Path, content: str):
    """先写 .tmp 再 os.replace，避免中断留下半个文件。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def main_v3(note_path: Path, text: str, txt_dir: Path, platform: str,
            bvid: str, note_type: str, only_page: int | None = None) -> int:
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
    page_index = build_page_index(txt_dir) if platform not in NO_LINK_PLATFORMS else {}  # 无深链平台不建索引
    out = list(lines)
    written = skipped = 0
    for offset, line in enumerate(inner):
        m = V3_ENTRY_RE.match(line)
        if not m:
            continue  # 已带链接 / 空行 / 其他内容：原样保留（幂等）
        ts_text, title = m.group("ts"), m.group("title")
        if platform in NO_LINK_PLATFORMS:  # 抖音 / 小红书无深链语义：保持纯文本，属正常不告警
            continue
        mm, ss = ts_text.split(":")
        loc = locate_page(page_index, int(mm) * 60 + int(ss), only_page)
        if loc is None:
            print(f"warning: v3 条目 [{ts_text}]（{title[:30]}）未找到归属分P，已跳过",
                  file=sys.stderr)
            skipped += 1
            continue
        page, rel = loc
        url = deep_link(platform, bvid, page, rel)  # 按平台生成深链（B站 / YouTube）
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


def main_v4(note_path: Path, text: str, txt_dir: Path, platform: str,
            bvid: str, only_page: int | None = None) -> int:
    """v4 模式：为「## 视频时间索引」节内的无链接条目原位填链（无 NAV 标记）。

    找不到该节 / 节内无待填条目 → stderr warning、不改文件、退出 0（存在性由门禁管）；
    条目行原位重写（不排序、不增删行），写入沿用 .tmp → os.replace 原子替换。
    """
    lines = text.splitlines()  # 全文按行
    head = next((i for i, l in enumerate(lines) if V4_INDEX_HEAD_RE.match(l)), None)  # 索引节标题行
    summary = {"mode": "v4", "platform": platform, "note_type": "lecture"}  # JSON 摘要公共字段
    if head is None:  # 没有时间索引节
        print("warning: v4 笔记未找到「## 视频时间索引」小节，跳过填链"
              "（条目存在性由门禁检查）", file=sys.stderr)  # 提示
        print(json.dumps({"status": "skipped", "reason": "v4 时间索引小节缺失",
                          "written": 0, "skipped": 0, **summary}, ensure_ascii=True))  # 摘要
        return 0  # 正常退出
    end = next((j for j in range(head + 1, len(lines)) if H2_LINE_RE.match(lines[j])),
               len(lines))  # 节结束位置（下一个 ## 或文末）
    page_index = build_page_index(txt_dir) if platform not in NO_LINK_PLATFORMS else {}  # 无深链平台不建索引
    out = list(lines)  # 输出副本
    written = skipped = 0  # 计数
    for i in range(head + 1, end):  # 只处理节内行
        m = V4_ENTRY_RE.match(lines[i])  # 无链接条目
        if not m:  # 已带链接 / 空行 / 其他内容
            continue  # 原样保留（幂等）
        if platform in NO_LINK_PLATFORMS:  # 抖音 / 小红书无深链语义
            continue  # 保持纯文本，属正常不告警
        ts_text, title = m.group("ts"), m.group("title")  # 时刻与标题
        mm, ss = ts_text.split(":")  # 拆分分秒
        loc = locate_page(page_index, int(mm) * 60 + int(ss), only_page)  # 归属分P
        if loc is None:  # 找不到归属
            print(f"warning: v4 条目 [{ts_text}]（{title[:30]}）未找到归属分P，已跳过",
                  file=sys.stderr)  # 提示
            skipped += 1  # 计数
            continue  # 跳过该行
        url = deep_link(platform, bvid, loc[0], loc[1])  # 按平台生成深链
        out[i] = f"{m.group('indent')}{m.group('bullet')} [{ts_text}]({url}) {title}"  # 原位重写
        written += 1  # 计数
    new_text = "\n".join(out) + ("\n" if text.endswith("\n") else "")  # 保留末尾换行
    if new_text != text:  # 有变化才写
        atomic_write(note_path, new_text)  # 原子写入
    print(json.dumps({"status": "ok", "written": written, "skipped": skipped, **summary},
                     ensure_ascii=True))  # 摘要
    return 0  # 正常退出


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="为笔记「视频时间索引」条目机械填充深链（v4 / v3，模型永不生成 URL）")
    ap.add_argument("note", help="笔记 .md 路径")
    ap.add_argument("--meta", required=True, help="metadata.json 路径")
    ap.add_argument("--txt-dir", required=True, help="转写稿 .txt 所在目录")
    ap.add_argument("--page", type=int, default=None,
                    help="单分P笔记场景：限定深链只归属到该分P页码"
                         "（每选集一份笔记时必填，否则早分P同秒时间行抢先命中）")
    ap.add_argument("--template", choices=["v4"], default=None,
                    help="模板版本：v4（语雀直导版式）；省略时无 frontmatter 也按 v4 处理")  # 显式指定 v4
    ap.add_argument("--note-type", choices=["lecture", "short"], default=None,
                    help="仅 v4：笔记类型；省略取 metadata.json 的 suggested_note_type")  # v4 的 note_type 来源
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
    is_v4 = args.template == "v4" or fm is None  # 显式 v4 或无 frontmatter → v4
    if not is_v4 and template_version != "v3":  # frontmatter 写了不支持的版本
        err = (f"不支持的模板版本「{template_version or '缺失'}」：v1 / v2 已移除，"
               "仅支持 v4（无 frontmatter）与 v3")  # 报错信息
        print(f"error: {err}", file=sys.stderr)  # stderr 提示
        print(json.dumps({"status": "error", "error": err}, ensure_ascii=True))  # JSON 摘要
        return 1  # 不改文件
    if is_v4:  # v4 无 frontmatter：note_type 取参数 > metadata 建议值 > lecture
        note_type = (args.note_type or meta.get("suggested_note_type") or "lecture").lower()  # 笔记类型
    if note_type == "short":  # short 笔记的时间导航可省，直接跳过
        print(json.dumps({"status": "skipped", "reason": "note_type=short",
                          "written": 0, "skipped": 0}, ensure_ascii=True))
        return 0

    platform = (meta.get("platform") or "bilibili").lower()
    bvid = meta.get("bvid") or meta.get("video_id") or ""
    if platform not in NO_LINK_PLATFORMS and not bvid:  # 有深链语义的平台必须有视频 ID
        print(json.dumps({"status": "error",
                          "error": "metadata 缺少 bvid/video_id，无法生成深链"},
                         ensure_ascii=True))
        return 1

    if is_v4:  # v4：时间索引节内条目原位填链
        return main_v4(note_path, text, txt_dir, platform, bvid, only_page=args.page)  # 交给 v4
    return main_v3(note_path, text, txt_dir, platform, bvid, note_type,
                   only_page=args.page)  # v3：NAV 标记内条目按分P归属机械填链

if __name__ == "__main__":
    sys.exit(main())
