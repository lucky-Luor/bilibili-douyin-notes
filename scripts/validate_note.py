#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes 笔记质量门禁：校验生成的课堂笔记是否符合模板规范。

用法:
    python validate_note.py <笔记.md> [--meta <metadata.json>] [--txt-dir <转写稿目录>]

版本分派（v1 / v2 规则已移除，只支持 v4 与 v3）:
    传 --template v4，或笔记没有 frontmatter → 走 v4 规则集（语雀直导版式，见 check_note_v4）；
    frontmatter 写 template_version: v3 → 走 v3 规则集（见 check_note_v3）；
    frontmatter 写其他版本（v1 / v2 / 缺失）→ 直接报错 VERSION，提示已不再支持。
    报告的 errors/warnings 均为 {"check","message","line"} 对象数组，
    并带 template_version / note_type 字段。

    v4（2026-10-21，语雀直导版式）：无 frontmatter / 无一级标题、+ 列表、答案与
    时间索引不折叠，内容规则与 v3 相同，检查项 V4-* 见 check_note_v4。
    v3（2026-10-04，复习资料定位）：正文全面禁止时间戳，时刻只允许出现在文末
    「## 视频时间索引」的 NAV 折叠区（深链由 scripts/note_nav.py 填充），检查项
    V3-* 见 check_note_v3。

输出: stdout 打印 JSON（ok / errors / warnings / checks 各检查项明细）。
全过 exit 0；有 error exit 1。
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

IMG_MD_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
IMG_BARE_RE = re.compile(r"(?<!\()\bimages/[\w./-]+\.(?:png|jpe?g|gif)\b")
NUM_ITEM_RE = re.compile(r"^\s*\d{1,2}\s*[.、．)）]\s*\S")
FENCE_RE = re.compile(r"^\s*```")
H2_RE = re.compile(r"^##\s+(.+?)\s*$")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
# ---- 检查14（B2 新增）：锚定行（标注行）识别正则常量 ----
# 识别 ★ 符号行与三词文字标签行（**加粗** / 全角括号（X））；v3 / v4 正文已无
# 这类标注行，check_image_manifest 的时间项会自动跳过，仅保留 keyword 交叉校验。
ANCHOR_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s+)?"
    r"(?:★{1,3}"
    r"|\*\*(?:核心必考|重点掌握|了解即可)\*\*"
    r"|[（(](?:核心必考|重点掌握|了解即可)[）)])"
)
TS_LINE_RE = re.compile(r"\[(\d{1,3}):(\d{2})\]")
CHECK14_ANCHOR_GAP = 5   # 锚定行与图片引用的最大行距
CHECK14_TOLERANCE = 60   # 截图时间与锚定行秒数的允许偏差（秒）


def split_sections(text: str) -> list[tuple[str | None, list[str]]]:
    """按 ## 二级标题切分，返回 [(标题, 正文行)]；首个 ## 之前的内容标题为 None。"""
    sections: list[tuple[str | None, list[str]]] = []
    title: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        m = H2_RE.match(line)
        if m:
            sections.append((title, body))
            title, body = m.group(1).strip(), []
        else:
            body.append(line)
    sections.append((title, body))
    return sections


def substantive_lines(lines: list[str]) -> list[str]:
    """非空且非纯分隔线的行。"""
    return [l for l in lines if l.strip() and l.strip() != "---"]


# ==================== 检查14（B2 新增，自包含可搬运函数） ====================

def check_image_manifest(note_path: Path, manifest_path: Path) -> dict:
    """manifest 图注 keyword + 时间对齐交叉校验（检查14，warning 级）。

    自包含：只依赖本文件的 IMG_MD_RE / FENCE_RE / ANCHOR_LINE_RE / TS_LINE_RE
    等常量，可独立搬运或单测。规则：
      - keyword 项：笔记 alt 以 manifest `keyword` 开头，或 `keyword` ∈ alt；
      - 时间项：|img.t − 锚定行的 mm:ss| ≤ 60。锚定行 = 图片引用上方最近的
        标注行（ANCHOR_LINE_RE 命中，间距 ≤ CHECK14_ANCHOR_GAP 行、代码块外）；
        上方 5 行内无标注行、或该标注行无 [mm:ss]、或 manifest 无时间 t 时，
        跳过该图的时间项；
    - 无 manifest（或解析失败）时整体跳过，不产生 mismatch。
    返回结构化结果：
      {"checked": 命中 manifest 的图片数,
       "time_skipped": 跳过时间项的图片数,
       "mismatched": [{"file": 文件名, "reason": 原因}],
       "note": 跳过原因或 None}
    """
    result = {"checked": 0, "time_skipped": 0, "mismatched": [], "note": None}
    if not manifest_path.exists():
        result["note"] = f"无 manifest（{manifest_path.name}），跳过检查14"
        return result
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result["note"] = "manifest 解析失败，跳过检查14"
        return result
    frames = manifest.get("frames", []) if isinstance(manifest, dict) else manifest
    frame_by_file = {str(f.get("file")).replace("\\", "/").rsplit("/", 1)[-1]: f
                     for f in frames if isinstance(f, dict) and f.get("file")}

    text = note_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # 每行结束时的 fence 内外状态（fence 标记行本身与其后各行都按新状态算）
    fence_state: list[bool] = []
    st = False
    for line in lines:
        if FENCE_RE.match(line):
            st = not st
        fence_state.append(st)

    # 收集代码块外的图片引用：[(行号, alt, 文件名)]
    images: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        if fence_state[i]:
            continue
        for m in IMG_MD_RE.finditer(line):
            p, alt = m.group(2), m.group(1)
            if re.match(r"^https?://", p) or not p.replace("\\", "/").startswith("images/"):
                continue
            images.append((i, alt.strip(),
                           p.replace("\\", "/").rsplit("/", 1)[-1]))

    def anchor_seconds_above(idx: int) -> int | None:
        """图片上方最近的标注行的秒数；5 行内无标注行、或该行无 [mm:ss] → None。"""
        for j in range(idx - 1, max(-1, idx - 1 - CHECK14_ANCHOR_GAP), -1):
            if fence_state[j]:
                continue
            if ANCHOR_LINE_RE.match(lines[j]):
                m = TS_LINE_RE.search(lines[j])
                return (int(m.group(1)) * 60 + int(m.group(2))) if m else None
        return None

    for idx, alt, fname in images:
        frame = frame_by_file.get(fname)
        if not frame:
            continue  # manifest 未收录的图不判定
        result["checked"] += 1
        kw = (frame.get("keyword") or "").strip()
        if kw and not (alt.startswith(kw) or kw in alt):
            result["mismatched"].append({
                "file": fname,
                "reason": f"alt「{alt}」未以 keyword「{kw}」开头且不含该关键词"})
        a_sec = anchor_seconds_above(idx)
        if a_sec is None:
            result["time_skipped"] += 1
            continue
        t = frame.get("t")
        if isinstance(t, (int, float)):
            if abs(float(t) - a_sec) > CHECK14_TOLERANCE:
                result["mismatched"].append({
                    "file": fname,
                    "reason": f"截图时间 t={round(float(t), 1)}s 与锚定行 "
                              f"{a_sec // 60:02d}:{a_sec % 60:02d} 偏差 "
                              f"{round(abs(float(t) - a_sec))}s（> {CHECK14_TOLERANCE}）"})
    return result


# ==================== 公共解析常量与工具（v3 / v4 共用） ====================
# 说明：V2_ 前缀为历史命名（最早随 v2 门禁引入），v1/v2 规则已移除，
# 这些常量与下方工具函数现由 v3 / v4 门禁共用。

V2_ANCHOR_RE = re.compile(r"\[(\d{1,3}):(\d{2})\]")
V2_CARD_FIELD_RE = re.compile(r"^\s*[-*]\s*(定义|出现|依赖|易混)\s*[：:]\s*(.*)$")
V2_APPEAR_ITEM_RE = re.compile(r"(\d{1,2})\s*(?:[（(]([^）)]*)[）)])?")
V2_CORRESPOND_RE = re.compile(r"[（(]\s*对应\s*[：:]\s*([^）)]+?)\s*[）)]")
V2_DETAILS_OPEN_RE = re.compile(r"<details\b", re.IGNORECASE)
V2_DETAILS_CLOSE_RE = re.compile(r"</details>", re.IGNORECASE)
V2_ANSWER_RE = re.compile(r"^\s*\*\*答案：?\*\*\s*(.*)$")
V2_YESNO_RE = re.compile(r"^(?:是否|是不是|对吗)|吗\s*[？?]\s*$")
V2_NAV_BEGIN_RE = re.compile(r"<!--\s*NAV:BEGIN")
V2_NAV_END_RE = re.compile(r"<!--\s*NAV:END")
V2_H2_NUM_RE = re.compile(r"^##\s+(\d{1,2})\s*[.、．)）]")
V2_MERMAID_FIRST_RE = re.compile(r"^(?:mindmap|graph|flowchart)\b")
V2_FENCE_LANG_RE = re.compile(r"^\s*```(\S*)\s*$")
# 检查12 的代码语言白名单（可扩展）；mermaid/text/console/output/diff 等不在列
# → 非代码 fence 不检查来源（坑②），未闭合 fence 仍报错
V2_CODE_LANGS = frozenset({
    "java", "python", "py", "js", "javascript", "ts", "typescript",
    "c", "cpp", "xml", "sql", "properties", "yaml", "yml", "json",
    "bash", "shell", "sh", "html", "css"})
FM_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$")


def parse_frontmatter(text: str) -> dict:
    """解析 --- 包围的 frontmatter 为 dict（仅支持 key: value 标量，去引号）。"""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    fm: dict = {}
    for line in m.group(1).splitlines():
        km = FM_KEY_RE.match(line.strip())
        if km:
            fm[km.group(1)] = km.group(2).strip().strip('"').strip("'")
    return fm


def compute_fence_state(lines: list[str]) -> list[bool]:
    """每行结束时的 fence 内外状态（fence 标记行本身与其后各行按新状态算）。"""
    state: list[bool] = []
    st = False
    for line in lines:
        if FENCE_RE.match(line):
            st = not st
        state.append(st)
    return state


def find_h2_line(lines: list[str], keyword: str) -> int | None:
    """第一个标题含 keyword 的 ## 行号（0-based）；无则 None。"""
    for i, line in enumerate(lines):
        m = H2_RE.match(line)
        if m and keyword in m.group(1):
            return i
    return None


def next_h2_after(lines: list[str], start: int) -> int:
    """start 行（含）之后第一个 ## 行号；无则 len(lines)。"""
    for j in range(start, len(lines)):
        if H2_RE.match(lines[j]):
            return j
    return len(lines)


def parse_quiz_questions(lines: list[str], start: int, end: int) -> list[dict]:
    """解析 [start,end) 内的自测题：题干/（对应：X）/<details>/答案/答案行数。"""
    questions: list[dict] = []
    q: dict | None = None
    in_details = False
    for i in range(start, end):
        line = lines[i]
        if NUM_ITEM_RE.match(line):
            q = {"no": len(questions) + 1, "line": i + 1,
                 "stem": re.sub(r"^\s*\d{1,2}\s*[.、．)）]\s*", "", line).strip(),
                 "has_details": False, "has_answer": False, "extra_lines": 0,
                 "answer_anchor": False, "correspond": None, "answer_raw": []}
            cm = V2_CORRESPOND_RE.search(line)
            if cm:
                q["correspond"] = cm.group(1).strip()
            questions.append(q)
            in_details = False
            continue
        if q is None:
            continue
        if V2_DETAILS_OPEN_RE.search(line):
            q["has_details"] = True
            in_details = True
            continue
        if in_details:
            if V2_ANSWER_RE.match(line):
                q["has_answer"] = True
                q["answer_anchor"] = bool(V2_ANCHOR_RE.search(line))
                q["answer_raw"].append(line)   # v3：答案行全文（全角时刻复查用）
            elif V2_DETAILS_CLOSE_RE.search(line):
                in_details = False
            elif q["has_answer"] and line.strip():
                q["extra_lines"] += 1
                q["answer_raw"].append(line)
                if V2_ANCHOR_RE.search(line):
                    q["answer_anchor"] = True
    return questions


def validate_mermaid_blocks(lines: list[str], start: int,
                            end: int) -> tuple[int, list[str]]:
    """mermaid 校验：fence 语言 / 首行 mindmap|graph|flowchart / mindmap 缩进。
    返回 (mermaid 块数, 问题列表)。"""
    problems: list[str] = []
    blocks = 0
    i = start
    while i < end:
        m = V2_FENCE_LANG_RE.match(lines[i])
        if not m:
            i += 1
            continue
        lang = m.group(1).lower()
        j = i + 1
        while j < end and not FENCE_RE.match(lines[j]):
            j += 1
        if lang == "mermaid":
            blocks += 1
            content = lines[i + 1:j]
            first = ""
            for l in content:
                if l.strip():
                    first = l.strip()
                    break
            if not V2_MERMAID_FIRST_RE.match(first):
                problems.append(f"首个非空行必须以 mindmap/graph/flowchart 开头，"
                                f"实际为「{first[:30]}」")
            elif first.startswith("mindmap"):
                seen_first = False
                body_indents: list[int] = []
                for l in content:
                    if not l.strip():
                        continue
                    if not seen_first:
                        seen_first = True
                        continue
                    body_indents.append(len(l) - len(l.lstrip(" ")))
                if not body_indents or max(body_indents) < 2:
                    problems.append("mindmap 至少需要 2 级缩进（root 之下还要有子节点）")
        i = j + 1
    return blocks, problems


# ==================== v3 门禁（模板 v3：复习资料定位，正文全面禁时刻） ====================
# v3 定位（2026-10-04）：笔记是"看完课替代手写笔记"的复习资料——
#   * 正文（目录/标题/要点/代码注释/概念卡/答案）全面禁止时间戳（V3-E1，代码块
#     内同样扫描）；时刻只允许出现在文末「## 视频时间索引」的 NAV 折叠区（一级
#     章节粒度条目，深链由 scripts/note_nav.py 机械填充）；
#   * 废除 核心必考/重点掌握/了解即可 三词标签体系（V3-E4 检测残留），随之不再
#     执行 v2 的标签白名单/每讲必含核心必考/覆盖率/占比≤1/3 与行内锚点白名单
#     （±5s 白名单只用于 NAV 条目时刻）。
# 分派：check_note_dispatch 按 frontmatter template_version 路由（v3 → 本节）；
# 报告 errors/warnings 为 {"check","message","line"} 对象数组。
#
# 检查项一览（id → 语义 → 级别）:
#   V3-E1  正文时刻禁令（frontmatter/NAV 区豁免，代码块内同样扫描）          [error]
#   V3-E2  时间索引存在性（lecture 必备：章节+配对 NAV 标记+≥1 条目）；
#          条目时刻须在转写稿白名单内 [error]；条目标题与正文一级 ## 小节名
#          不一致（双方去行首序号前缀后比较）[warning]；缺 --txt-dir 跳过
#          白名单校验 [warning]
#   V3-E3  深链格式 + t=分P内相对秒 [error]；B站 lecture 条目缺链接、缺
#          --txt-dir 无法校验 t [warning]
#   V3-E4  三词标签残留（行首前缀 **X**/（X）变体、概念卡标题标签后缀）      [error]
#   V3-E5  自测题：数量 lecture 4~7 / short 2~3、答案行全角时刻、缺 details、
#          缺「**答案：**」、（对应：X）未知（lecture）                     [error]
#          答案超一行、yes/no 题干                                         [warning]
#   V3-E6  概念卡：定义/出现缺失或为空、出现引用的小节序号不存在、出现含
#          时刻（v3 收紧）                                                 [error]
#          单节概念、依赖/易混引用不存在的概念名                           [warning]
#   V3-E7  未闭合 fence；语言白名单代码块的来源注释含时刻（v3 来源可选，
#          缺失不再 warning；mermaid/text/console/output/diff 不查来源）   [error]
#   保留检查（与 v2 同语义，复用现有实现）：V3-FM frontmatter 完整性（v3 增加
#   bvid/duration，抖音按 video_id）、V3-NT note_type 取值、V3-SEC 每小节 ≥3 行
#   实质内容、V3-IDEA 正文小节以 **核心思想：** 开头、V3-KM 知识地图 mermaid
#   （lecture；short 可省）、V3-IMG 引用图片存在 [error]；V3-MANIFEST manifest
#   图注交叉校验（贴近度放宽：标注行已废除，时间项自动跳过）、V3-RATIO 字数比
#   [warning]。
#   V3-KM-LABEL mermaid 节点标签卫生（全笔记 mermaid 块，warning）：节点标签
#   折算宽度 >12 字（全角 1/半角 0.5）或含半角 ( ) [ ] { } " 字符；全角（）合法。

V3_INDEX_HEADING = "视频时间索引"
V3_LOCATE_TOLERANCE = 5   # 条目时刻归属分P的 ±5s 容差（与 note_nav.py 同口径）
# V3-E1：时刻模式——[mm:ss] / [h:mm:ss]（分钟 1~3 位）与全/半角括号形式
V3_TS_ANY_RE = re.compile(
    r"\[\d{1,3}:\d{2}(?::\d{2})?\]"
    r"|[（(]\d{1,3}:\d{2}(?::\d{2})?[）)]")
# V3-E5：答案行专用复查（全角括号形式，防 E1 漏网）
V3_TS_FULLWIDTH_RE = re.compile(r"（\d{1,3}:\d{2}(?::\d{2})?）")
# V3-E2：时间索引条目「- [mm:ss] <标题>」；脚本填链后为「- [mm:ss](URL) <标题>」
V3_NAV_ENTRY_RE = re.compile(
    r"^\s*[-*]\s+\[(\d{1,3}):(\d{2})\](?:\(([^)\s]*)\))?\s+(\S.*?)\s*$")
V3_TAG_WORDS = r"(?:核心必考|重点掌握|了解即可)"
# V3-E4：行首标签前缀（**加粗** 与 全/半角括号 两种变体；列表符号后的空格可省，
# 覆盖「-**核心必考**…」「-（重点掌握）…」等残留写法）
V3_TAG_RESIDUE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*" + V3_TAG_WORDS + r"\*\*|[（(]" + V3_TAG_WORDS + r"[）)])")
# V3-E4：概念卡标题的标签后缀「### 概念名（核心必考）」
V3_CARD_TAGGED_TITLE_RE = re.compile(
    r"^###\s+.+?[（(]" + V3_TAG_WORDS + r"[）)]\s*$")
# V3-E6：v3 概念卡标题为纯概念名（不带标签后缀）
V3_CARD_TITLE_RE = re.compile(r"^###\s+(\S.*?)\s*$")
# 标题归一化：去行首序号前缀（「1. 」「2、」等），用于条目标题与 ## 小节名比较
V3_LEADING_NUM_RE = re.compile(r"^\s*\d{1,2}\s*[.、．)）]\s*")
# V3-SEC / V3-IDEA 跳过的结构性章节（子串匹配）
V3_SEC_SKIP = ("目录", "参考时间戳", "视频时间索引")
V3_IDEA_SKIP = V3_SEC_SKIP + ("知识地图", "概念卡片", "自测题")
V3_QUIZ_RANGE = {"lecture": (4, 7), "short": (2, 3)}
# V3-E3：B站深链（frontmatter 缺 bvid 时退化用宽松格式兜底）
V3_BILI_LINK_RE = re.compile(r"^https://www\.bilibili\.com/video/\S+\?p=\d+&t=\d+$")
# V3-E3：YouTube 深链（单分P，t 带 s 后缀；frontmatter 缺 video_id 时用宽松格式兜底）
V3_YT_LINK_RE = re.compile(r"^https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}&t=\d+s$")
V3_TXT_PAGE_RE = re.compile(r"^(\d{1,3})_")   # 转写稿文件名页码前缀 <页码>_*.txt
# V3-KM-LABEL：节点标签折算宽度上限——全角字符记 1、半角记 0.5
# （如 ApplicationContext 折算 9 字），避免纯英文技术名按原始字符数误报超长
V3_LABEL_MAX_WIDTH = 12
# V3-KM-LABEL：标签内非法半角字符（mermaid 形状定界符/引号；全角（）合法）
V3_LABEL_BAD_RE = re.compile(r'[()\[\]{}"]')
# V3-KM-LABEL：mindmap 节点行的形状壳（整行被包裹时剥壳取内层文本）
V3_MM_SHAPE_RES = (
    re.compile(r"^(?:\w+\s*)?\(\((.+)\)\)$"),   # 圆形 root((text)) / ((text))
    re.compile(r"^\{\{(.+)\}\}$"),              # 六边形 {{text}}
    re.compile(r"^\((.+)\)$"),                  # 圆角 (text)
    re.compile(r"^\[(.+)\]$"),                  # 方形 [text]
)
# V3-KM-LABEL：graph/flowchart 节点标签 [text]（([text]) 的内层同样命中）
V3_GF_LABEL_RE = re.compile(r"\[([^\[\]]+)\]")


def v3_exempt_lines(text: str, lines: list[str]) -> set[int]:
    """V3-E1/E4 的豁免行号（0-based）：frontmatter 区 + NAV:BEGIN…NAV:END 区。

    frontmatter 里有 duration: "mm:ss"、NAV 区里是时间索引条目——两处时刻均合法；
    NAV 区内容由 note_nav.py 机械生成，也不做标签残留扫描。
    """
    exempt: set[int] = set()
    m = FRONTMATTER_RE.match(text)
    if m:
        n = m.group(0).count("\n")
        exempt.update(range(n if m.group(0).endswith("\n") else n + 1))
    begins = [i for i, l in enumerate(lines) if V2_NAV_BEGIN_RE.search(l)]
    ends = [i for i, l in enumerate(lines) if V2_NAV_END_RE.search(l)]
    if begins and ends and begins[0] <= ends[0]:
        exempt.update(range(begins[0], ends[0] + 1))
    return exempt


def v3_norm_title(title: str) -> str:
    """条目/小节标题归一化：去掉行首序号前缀（如「1. 」「2、」）与首尾空白。"""
    return V3_LEADING_NUM_RE.sub("", title).strip()


def v3_page_index(txt_dir: Path) -> dict[int, list[int]]:
    """{分P页码: [该分P内各时间行的总秒]}，来自 <页码>_<分P名>.txt。

    与 scripts/note_nav.py 的 build_page_index 同口径：每个分P的转写稿时间
    从 0 起（bili_transcribe 契约）。
    """
    idx: dict[int, list[int]] = {}
    for f in sorted(Path(txt_dir).glob("*.txt")):
        pm = V3_TXT_PAGE_RE.match(f.name)
        if not pm:
            continue
        times: list[int] = []
        for ln in f.read_text(encoding="utf-8").splitlines():
            tm = re.match(r"\[(\d{1,3}):(\d{2})\]", ln)
            if tm:
                times.append(int(tm.group(1)) * 60 + int(tm.group(2)))
        idx[int(pm.group(1))] = times
    return idx


def v3_locate_page(page_index: dict[int, list[int]], sec: int,
                   only_page: int | None = None):
    """±5s 把条目时刻归属到分P：按页码升序取首个命中（note_nav.locate_page 同口径）。

    返回 (页码, 命中时间行在该分P内的相对秒)；找不到返回 None。转写稿各分P时间
    从 0 起，命中行秒值即分P内相对秒（等价于 时刻总秒 − 该分P起始累计秒，
    每分P起始累计秒为 0），深链 t 必须等于该值。
    only_page：单分P笔记（每选集一份）场景把定位限制在该分P内，与 note_nav
    的 --page 同口径；否则早分P的同秒时间行会抢先命中导致 t/p 校验错位。
    """
    pages = sorted(page_index)
    if only_page is not None:
        pages = [p for p in pages if p == only_page]
    for page in pages:
        for t in page_index[page]:
            if abs(t - sec) <= V3_LOCATE_TOLERANCE:
                return page, t
    return None


def parse_concept_cards_v3(lines: list[str], start: int, end: int,
                           fence_state: list[bool]) -> list[dict]:
    """解析 [start,end) 内的 v3 概念卡：### 概念名（无标签后缀）+ 定义/出现/依赖/易混。"""
    cards: list[dict] = []
    cur: dict | None = None
    for i in range(start, end):
        if fence_state[i]:
            continue
        line = lines[i]
        tm = V3_CARD_TITLE_RE.match(line)
        if tm:
            cur = {"name": tm.group(1).strip(), "line": i + 1, "fields": {}}
            cards.append(cur)
            continue
        fmm = V2_CARD_FIELD_RE.match(line)
        if fmm and cur is not None and fmm.group(1) not in cur["fields"]:
            cur["fields"][fmm.group(1)] = (fmm.group(2).strip(), i + 1)
    return cards


def v3_label_width(label: str) -> float:
    """标签折算宽度：全角（east_asian_width F/W）记 1 字、半角记 0.5 字。

    「ApplicationContext」折算 9 字——纯英文技术名不因原始字符数超限而误报。
    """
    return sum(1.0 if unicodedata.east_asian_width(ch) in ("F", "W") else 0.5
               for ch in label)


def v3_mermaid_label_problems(lines: list[str]) -> list[tuple[int, str, str]]:
    """V3-KM-LABEL：全笔记 mermaid 块的节点标签卫生扫描。

    块定位与 validate_mermaid_blocks 同口径（V2_FENCE_LANG_RE / FENCE_RE 走
    fence，首行命中 V2_MERMAID_FIRST_RE 才处理）。标签提取（实现从简，识别
    不了的行跳过）：
      - mindmap：缩进层级文本行，整行被形状壳（root((..))/((..))/(..)/[..]/
        {{..}}）包裹时剥壳取内层，否则整行即标签；
      - graph/flowchart：行内全部 [..]（含 ([..]) 的内层文本）。
    判定：标签折算宽度 > V3_LABEL_MAX_WIDTH，或含半角 ( ) [ ] { } " 字符
    （全角（）合法）→ 问题。返回 [(行号, 标签, 问题描述)]。
    """
    problems: list[tuple[int, str, str]] = []

    def check(label: str, line_no: int) -> None:
        if V3_LABEL_BAD_RE.search(label):
            problems.append((line_no, label,
                             '含半角 ( ) [ ] { } " 字符，请改全角（）'))
        elif v3_label_width(label) > V3_LABEL_MAX_WIDTH:
            problems.append((line_no, label,
                             f"折算 {v3_label_width(label):g} 字"
                             f"（上限 {V3_LABEL_MAX_WIDTH}），请缩短标签"))

    i = 0
    while i < len(lines):
        m = V2_FENCE_LANG_RE.match(lines[i])
        if not m:
            i += 1
            continue
        lang = m.group(1).lower()
        j = i + 1
        while j < len(lines) and not FENCE_RE.match(lines[j]):
            j += 1
        if lang == "mermaid":
            first = next((l.strip() for l in lines[i + 1:j] if l.strip()), "")
            if V2_MERMAID_FIRST_RE.match(first):
                if first.startswith("mindmap"):
                    for k in range(i + 1, j):
                        stripped = lines[k].strip()
                        if not stripped or stripped.startswith(("%%", "::")):
                            continue  # 注释 / icon 指令行跳过
                        label = stripped
                        for shape in V3_MM_SHAPE_RES:
                            sm = shape.match(stripped)
                            if sm:
                                label = sm.group(1).strip()
                                break
                        check(label, k + 1)
                else:  # graph / flowchart：取行内全部 [..] 标签
                    for k in range(i + 1, j):
                        stripped = lines[k].strip()
                        if not stripped or stripped.startswith("%%"):
                            continue
                        for gm in V3_GF_LABEL_RE.finditer(stripped):
                            check(gm.group(1).strip(), k + 1)
        i = j + 1
    return problems


def check_note_v3(text: str, note_path: Path, meta_path: Path | None,
                  txt_dir: Path | None, only_page: int | None = None) -> dict:
    """v3 门禁：正文全面禁时刻（V3-E1），时间戳只进文末时间索引 NAV 区（E2/E3）。

    新规则 V3-E1~E7 + 保留检查（与 v2 同语义：frontmatter 完整性/小节实质内容/
    核心思想开头/mermaid/图片与 manifest 交叉校验/字数比）；废除 v2 的三词标签
    体系与行内锚点白名单。errors/warnings 为 {"check","message","line"} 对象数组。
    """
    errors: list[dict] = []
    warnings: list[dict] = []
    checks: dict = {}

    def err(cid, message, line=None):
        errors.append({"check": str(cid), "message": message, "line": line})

    def warn(cid, message, line=None):
        warnings.append({"check": str(cid), "message": message, "line": line})

    lines = text.splitlines()
    fence_state = compute_fence_state(lines)
    fm = parse_frontmatter(text)
    note_type = (fm.get("note_type") or "").strip().lower()
    platform = (fm.get("platform") or "").strip().lower()

    # ---- V3-FM：frontmatter 完整性（v3 增加 duration；B站要 bvid，抖音按 video_id） ----
    required = ["template_version", "note_type", "platform", "source",
                "created", "duration"]
    required.append("video_id" if platform in ("douyin", "youtube", "xiaohongshu") else "bvid")  # 抖音 / YouTube / 小红书用 video_id
    missing_fields = [f for f in required if not fm.get(f)]
    for f in missing_fields:
        err("V3-FM", f"frontmatter 缺少 {f} 字段")
    checks["frontmatter_fields"] = {"passed": not missing_fields,
                                    "missing": missing_fields}

    # ---- V3-NT：note_type 取值 ----
    if note_type and note_type not in ("lecture", "short"):
        err("V3-NT", f"note_type「{note_type}」非法，只能是 lecture / short")
    checks["note_type_valid"] = {"passed": note_type in ("lecture", "short"),
                                 "note_type": note_type or None}

    sections = split_sections(text)

    # ---- V3-SEC：每个 ## 小节（除 目录/参考时间戳/视频时间索引）≥3 行实质内容 ----
    sec_detail = {}
    for title, body in sections:
        if title is None or any(k in title for k in V3_SEC_SKIP):
            continue
        n = len(substantive_lines(body))
        sec_detail[title] = n
        if n < 3:
            err("V3-SEC", f"小节「{title}」实质内容不足（{n} 行非空文本，需至少 3 行）")
    checks["section_content"] = {"passed": not any(e["check"] == "V3-SEC" for e in errors),
                                 "lines_per_section": sec_detail}

    # ---- V3-IDEA：正文小节以 **核心思想：** 开头（知识地图/概念卡/自测题豁免） ----
    idea_missing: list[str] = []
    for title, body in sections:
        if title is None or any(k in title for k in V3_IDEA_SKIP):
            continue
        subs = substantive_lines(body)
        if not subs or not subs[0].startswith("**核心思想：**"):
            idea_missing.append(title)
            err("V3-IDEA", f"小节「{title}」未以「**核心思想：** <一句话>」开头")
    checks["core_idea"] = {"passed": not idea_missing, "missing": idea_missing}

    # ---- V3-E1：正文时刻禁令（frontmatter/NAV 区豁免；代码块内同样扫描） ----
    exempt = v3_exempt_lines(text, lines)
    e1_hits: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if i in exempt:
            continue
        for tm in V3_TS_ANY_RE.finditer(line):
            e1_hits.append((i + 1, tm.group(0)))
            err("V3-E1", f"正文出现时刻「{tm.group(0)}」（第{i + 1}行）：v3 正文全面禁止"
                         "时间戳，时刻只允许出现在文末「## 视频时间索引」的 NAV 折叠区",
                i + 1)
    checks["body_timestamp_ban"] = {"passed": not e1_hits, "hits": e1_hits}

    # ---- V3-E4：三词标签残留（v3 已废除标签体系；跳过代码块与 NAV/frontmatter 区） ----
    e4_lines: list[int] = []
    for i, line in enumerate(lines):
        if i in exempt or fence_state[i]:
            continue
        if V3_TAG_RESIDUE_RE.match(line):
            e4_lines.append(i + 1)
            err("V3-E4", f"第{i + 1}行残留三词标签前缀：{line.strip()[:40]}"
                         "（v3 已废除 核心必考/重点掌握/了解即可 标签体系）", i + 1)
        elif V3_CARD_TAGGED_TITLE_RE.match(line):
            e4_lines.append(i + 1)
            err("V3-E4", f"概念卡标题残留标签后缀（第{i + 1}行）：{line.strip()[:40]}"
                         "（v3 概念卡标题为「### 概念名」，不带标签）", i + 1)
    checks["tag_residue"] = {"passed": not e4_lines, "lines": e4_lines}

    # ---- V3-E6：概念卡片（沿用 v2 字段语义；v3 收紧：出现含时刻 → error） ----
    cards: list[dict] = []
    card_names: set[str] = set()
    card_head = find_h2_line(lines, "概念卡片")
    if card_head is not None:
        card_end = next_h2_after(lines, card_head + 1)
        cards = parse_concept_cards_v3(lines, card_head + 1, card_end, fence_state)
        card_names = {c["name"] for c in cards}
    body_sec_nums: set[int] = set()
    for line in lines:
        hm = V2_H2_NUM_RE.match(line)
        if hm:
            body_sec_nums.add(int(hm.group(1)))
    for c in cards:
        defs = c["fields"].get("定义")
        appear = c["fields"].get("出现")
        if not defs or not defs[0]:
            err("V3-E6", f"概念卡「{c['name']}」缺少非空「定义」字段", c["line"])
        if not appear or not appear[0]:
            err("V3-E6", f"概念卡「{c['name']}」缺少非空「出现」字段", c["line"])
        else:
            value, vline = appear
            tm_ts = V3_TS_ANY_RE.search(value)
            if tm_ts:
                err("V3-E6", f"概念卡「{c['name']}」的「出现」含时刻「{tm_ts.group(0)}」："
                             "v3 概念卡禁止时间戳，只写小节序号（如「出现：1、2」）", vline)
            items = V2_APPEAR_ITEM_RE.findall(value)
            if not items:
                err("V3-E6", f"概念卡「{c['name']}」的「出现」格式非法"
                             "（应为「小节序号、小节序号」，v3 不带时刻）", vline)
            else:
                secs_seen: list[int] = []
                for sec, _ts in items:
                    sec_i = int(sec)
                    secs_seen.append(sec_i)
                    if sec_i not in body_sec_nums:
                        err("V3-E6", f"概念卡「{c['name']}」的「出现」引用小节 {sec}，"
                                     "但笔记中不存在该小节序号", vline)
                if len(set(secs_seen)) < 2:
                    warn("V3-E6", f"概念卡「{c['name']}」的「出现」仅 "
                                  f"{len(set(secs_seen))} 个小节：单节概念不必单独成卡，"
                                  "考虑并入小节", vline)
        dep = c["fields"].get("依赖")
        if dep and dep[0]:
            for name in re.split(r"[、,，;；]", dep[0]):
                name = name.strip()
                if name and name not in card_names:
                    warn("V3-E6", f"概念卡「{c['name']}」的「依赖」引用「{name}」"
                                  "不在概念卡片集合内", dep[1])
        conf = c["fields"].get("易混")
        if conf and conf[0]:
            conf_name = re.split(r"——|--", conf[0])[0].strip()
            if conf_name and conf_name not in card_names:
                warn("V3-E6", f"概念卡「{c['name']}」的「易混」引用「{conf_name}」"
                              "不在概念卡片集合内", conf[1])
    checks["concept_cards"] = {"passed": not any(e["check"] == "V3-E6" for e in errors),
                               "cards": len(cards)}

    # ---- V3-E5：自测题（数量 4~7/2~3；沿用 v2 格式语义；答案行全角时刻复查） ----
    quiz_head = find_h2_line(lines, "自测题")
    questions: list[dict] = []
    if quiz_head is None:
        err("V3-E5", "缺少「自测题」章节")
        checks["quiz_count"] = {"passed": False, "count": 0, "found": False}
        checks["quiz_format"] = {"passed": False, "found": False}
    else:
        quiz_end = next_h2_after(lines, quiz_head + 1)
        questions = parse_quiz_questions(lines, quiz_head + 1, quiz_end)
        n_q = len(questions)
        want = V3_QUIZ_RANGE.get(note_type, (4, 7))
        if not want[0] <= n_q <= want[1]:
            err("V3-E5", f"自测题数量为 {n_q}，要求 {want[0]}~{want[1]} 道"
                         f"（note_type={note_type or '缺失'}）")
        checks["quiz_count"] = {"passed": want[0] <= n_q <= want[1],
                                "count": n_q, "found": True}
        fmt_ok = True
        for q in questions:
            if not q["has_details"]:
                fmt_ok = False
                err("V3-E5", f"自测题 {q['no']} 缺少紧随其后的 <details> 折叠块", q["line"])
            elif not q["has_answer"]:
                fmt_ok = False
                err("V3-E5", f"自测题 {q['no']} 的 <details> 内缺少「**答案：**」开头的行",
                    q["line"])
            else:
                if q["extra_lines"] > 0:
                    warn("V3-E5", f"自测题 {q['no']} 的答案正文超过一行"
                                  f"（答案后另有 {q['extra_lines']} 行非空文本）："
                                  "答案不应变成第二篇笔记", q["line"])
                # 答案行全角括号时刻复查（E1 兜底，防漏网）
                for ln in q["answer_raw"]:
                    fm_ts = V3_TS_FULLWIDTH_RE.search(ln)
                    if fm_ts:
                        fmt_ok = False
                        err("V3-E5", f"自测题 {q['no']} 的答案行含全角时刻"
                                     f"「{fm_ts.group(0)}」：v3 答案禁止时间戳", q["line"])
                        break
            if V2_YESNO_RE.search(q["stem"]):
                warn("V3-E5", f"自测题 {q['no']} 疑似 yes/no 题干「{q['stem'][:30]}」",
                     q["line"])
        # （对应：X）：lecture 必填且 X 须在概念卡集合内；short 可选（不检查）
        if note_type == "lecture":
            for q in questions:
                if q["correspond"] is None:
                    fmt_ok = False
                    err("V3-E5", f"自测题 {q['no']} 缺少「（对应：概念名）」标记", q["line"])
                elif q["correspond"] not in card_names:
                    fmt_ok = False
                    err("V3-E5", f"自测题 {q['no']} 的「（对应：{q['correspond']}）」"
                                 "不存在于概念卡片集合，疑为笔误", q["line"])
        checks["quiz_format"] = {"passed": fmt_ok, "count": n_q, "found": True}

    # ---- 转写稿时间行池（V3-E2 条目白名单 + V3-RATIO 共用，±5s 按总秒数） ----
    txt_files: list[Path] = sorted(Path(txt_dir).glob("*.txt")) if txt_dir else []
    legal_sec: set[int] = set()
    for f in txt_files:
        for ln in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\[(\d{1,3}):(\d{2})\]", ln)
            if m:
                legal_sec.add(int(m.group(1)) * 60 + int(m.group(2)))

    # ---- V3-E2：视频时间索引（lecture 必备；short 可省、有则同规则校验） ----
    nav_head = find_h2_line(lines, V3_INDEX_HEADING)
    begins = [i for i, l in enumerate(lines) if V2_NAV_BEGIN_RE.search(l)]
    ends = [i for i, l in enumerate(lines) if V2_NAV_END_RE.search(l)]
    paired = len(begins) == 1 and len(ends) == 1 and begins[0] < ends[0]
    entries: list[dict] = []
    if nav_head is not None:
        if not paired:
            err("V3-E2", "缺少配对的 NAV:BEGIN/NAV:END 生成区标记"
                         "（应由 scripts/note_nav.py 生成）")
        elif not (begins[0] <= nav_head <= ends[0]):
            err("V3-E2", "「## 视频时间索引」未位于 NAV:BEGIN 与 NAV:END 标记之间")
        idx_end = next_h2_after(lines, nav_head + 1)
        for i in range(nav_head + 1, idx_end):
            em = V3_NAV_ENTRY_RE.match(lines[i])
            if em:
                mm_, ss_ = int(em.group(1)), int(em.group(2))
                entries.append({"line": i + 1, "sec": mm_ * 60 + ss_,
                                "ts": f"{mm_:02d}:{ss_:02d}",
                                "url": (em.group(3) or "").strip() or None,
                                "title": em.group(4).strip()})
        if not entries:
            err("V3-E2", "「## 视频时间索引」没有条目（至少 1 条一级章节粒度条目，"
                         "格式「- [mm:ss] <标题>」）")
    elif note_type == "lecture":
        err("V3-E2", "缺少「## 视频时间索引」章节（lecture 必须有，且位于 NAV 折叠区）")
    checks["time_index"] = {"found": nav_head is not None, "nav_paired": paired,
                            "entries": len(entries)}

    # 条目时刻白名单（±5s 总秒数、可跨分钟；与 v2 检查6 同机制）
    off_whitelist: list[str] = []
    if not entries:
        checks["entry_whitelist"] = {"passed": None, "note": "无条目，跳过"}
    elif txt_dir is None:
        warn("V3-E2", "未提供 --txt-dir，无法校验时间索引条目时刻白名单，跳过该子项"
                      "（不静默通过：补齐参数后重跑）")
        checks["entry_whitelist"] = {"passed": None, "note": "缺 --txt-dir，跳过"}
    elif not legal_sec:
        warn("V3-E2", f"转写稿目录 {txt_dir} 下没有可解析的 [mm:ss] 时间行，"
                      "跳过条目白名单校验（不静默通过）")
        checks["entry_whitelist"] = {"passed": None, "note": "txt 目录无时间行"}
    else:
        for e in entries:
            if not any(abs(e["sec"] - ls) <= 5 for ls in legal_sec):
                off_whitelist.append(e["ts"])
                err("V3-E2", f"时间索引条目 [{e['ts']}]（第{e['line']}行）不在转写稿"
                             "时间戳白名单内（±5s 容差，按总秒数比较、可跨分钟）",
                    e["line"])
        checks["entry_whitelist"] = {"passed": not off_whitelist,
                                     "legal_seconds": len(legal_sec),
                                     "off_whitelist": off_whitelist}

    # 条目标题与正文一级 ## 小节名一致性（双方去行首序号前缀后须相等）[warning]
    body_titles = {v3_norm_title(t) for t, _ in sections
                   if t and not any(k in t for k in V3_SEC_SKIP)}
    title_warns = 0
    for e in entries:
        if v3_norm_title(e["title"]) not in body_titles:
            title_warns += 1
            warn("V3-E2", f"时间索引条目标题「{e['title']}」（第{e['line']}行）与正文"
                          "一级 ## 小节名不一致（比较方式：双方去掉行首序号前缀后须相等）",
                e["line"])
    checks["entry_titles"] = {"passed": title_warns == 0, "warnings": title_warns}

    # ---- V3-E3：深链校验（格式 + t=分P内相对秒；B站 lecture 缺链 → warning） ----
    link_errors = 0
    missing_link_warns = 0
    page_index: dict[int, list[int]] = {}
    if txt_dir is not None and entries:
        page_index = v3_page_index(txt_dir)
    fm_bvid = (fm.get("bvid") or "").strip()
    fm_vid = (fm.get("video_id") or "").strip()  # YouTube 深链校验用的视频 ID
    linked = [e for e in entries if e["url"]]
    for e in linked:
        if platform == "youtube":  # YouTube：watch?v=<video_id>&t=<秒>s
            yt_re = (r"https://www\.youtube\.com/watch\?v=" + re.escape(fm_vid)  # 有 video_id 时严格匹配
                     + r"&t=\d+s") if fm_vid else V3_YT_LINK_RE.pattern.strip("^$")  # 否则宽松兜底
            e["fmt_ok"] = re.fullmatch(yt_re, e["url"]) is not None  # 格式判定
            if not e["fmt_ok"]:  # 格式非法
                link_errors += 1  # 计入错误
                err("V3-E3", f"时间索引条目深链格式非法（第{e['line']}行）：{e['url'][:60]}"
                             "，必须为 https://www.youtube.com/watch?v=<video_id>&t=<非负整数>s"
                             "（由 scripts/note_nav.py 生成，勿手写）", e["line"])  # 报错
            continue  # YouTube 不走 B站格式校验
        if fm_bvid:
            fmt_ok_link = re.fullmatch(
                r"https://www\.bilibili\.com/video/" + re.escape(fm_bvid)
                + r"\?p=\d+&t=\d+", e["url"]) is not None
        else:
            fmt_ok_link = V3_BILI_LINK_RE.fullmatch(e["url"]) is not None
        e["fmt_ok"] = fmt_ok_link
        if not fmt_ok_link:
            link_errors += 1
            err("V3-E3", f"时间索引条目深链格式非法（第{e['line']}行）：{e['url'][:60]}"
                         "，必须为 https://www.bilibili.com/video/<bvid>?p=N&t=<非负整数>"
                         "（由 scripts/note_nav.py 生成，勿手写）", e["line"])
    if linked and not page_index:
        warn("V3-E3", "未提供 --txt-dir（或转写稿不可解析），无法校验深链 t 是否等于"
                      "分P内相对秒（不静默通过：补齐参数后重跑）")
    else:
        for e in linked:
            if not e["fmt_ok"]:
                continue
            loc = v3_locate_page(page_index, e["sec"], only_page)
            t_val = int(re.search(r"&t=(\d+)s?$", e["url"]).group(1))  # 兼容 YouTube 的 s 后缀
            if loc is not None and t_val != loc[1]:
                link_errors += 1
                err("V3-E3", f"深链 t={t_val} 与条目 [{e['ts']}] 归属分P（p={loc[0]}）"
                             f"后的分P内相对秒 {loc[1]} 不一致（第{e['line']}行）："
                             "请运行 scripts/note_nav.py 重新生成", e["line"])
    for e in entries:
        if (not e["url"] and note_type == "lecture" and platform in ("bilibili", "youtube")):  # 有深链语义的平台
            missing_link_warns += 1
            warn("V3-E3", f"{'YouTube' if platform == 'youtube' else 'B站'} lecture 时间索引条目 [{e['ts']}] 缺少深链"
                          f"（第{e['line']}行）：请运行 python scripts/note_nav.py "
                          "<笔记.md> --meta <metadata.json> --txt-dir <转写稿目录> 自动填充",
                e["line"])
    checks["deep_links"] = {"passed": link_errors == 0, "entries": len(entries),
                            "missing_link_warns": missing_link_warns,
                            "pages": sorted(page_index)}

    # ---- V3-E7：代码块（白名单语言来源注释含时刻 → error；来源缺失不再 warning） ----
    open_lang: str | None = None
    open_idx = -1
    unclosed = False
    src_ts: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        m = V2_FENCE_LANG_RE.match(lines[i])
        if m:
            if open_lang is None:
                open_lang, open_idx = m.group(1).lower(), i
            else:
                if open_lang in V2_CODE_LANGS:
                    for j in list(range(open_idx + 1, i)) + list(range(i + 1, i + 3)):
                        if "来源" in lines[j] and V3_TS_ANY_RE.search(lines[j]):
                            src_ts.append((j + 1, lines[j].strip()[:40]))
                open_lang = None
        i += 1
    if open_lang is not None:
        unclosed = True
    for j, snippet in src_ts:
        err("V3-E7", f"来源标注含时刻（第{j}行）：{snippet}"
                     "（v3 代码块来源可选，写仓库路径即可，不再要求时刻）", j)
    if unclosed:
        err("V3-E7", "存在未闭合的代码块（``` fence）")
    checks["code_blocks"] = {"passed": not src_ts and not unclosed,
                             "source_with_timestamp": [j for j, _ in src_ts],
                             "unclosed_fence": unclosed,
                             "note": "v3 来源标注可选（缺失不再 warning），含时刻才报错"}

    # ---- V3-KM：lecture 必须有知识地图 + 合规 mermaid（short 可省） ----
    if note_type == "lecture":
        km_head = find_h2_line(lines, "知识地图")
        if km_head is None:
            err("V3-KM", "缺少「本讲知识地图」章节（lecture 必须有 mermaid 知识地图）")
            checks["knowledge_map"] = {"passed": False, "found": False}
        else:
            km_end = next_h2_after(lines, km_head + 1)
            blocks, problems = validate_mermaid_blocks(lines, km_head + 1, km_end)
            if blocks == 0:
                err("V3-KM", "「本讲知识地图」章节内没有 mermaid 代码块")
            for p in problems:
                err("V3-KM", f"知识地图 mermaid 块校验失败：{p}")
            checks["knowledge_map"] = {"passed": blocks > 0 and not problems,
                                       "mermaid_blocks": blocks,
                                       "problems": problems}
    else:
        checks["knowledge_map"] = {"passed": None,
                                   "note": "short 型知识地图可省（不检查）"}

    # ---- V3-KM-LABEL：mermaid 节点标签卫生（全笔记 mermaid 块）[warning] ----
    label_problems = v3_mermaid_label_problems(lines)
    for ln, label, reason in label_problems:
        warn("V3-KM-LABEL", f"mermaid 节点标签「{label[:30]}」{reason}"
                            f"（第{ln}行）", ln)
    checks["mermaid_labels"] = {"passed": not label_problems,
                                "lines": [ln for ln, _, _ in label_problems]}

    # ---- V3-IMG：引用图片真实存在 ----
    referenced: set[str] = set()
    for m in IMG_MD_RE.finditer(text):
        p = m.group(2)
        if not re.match(r"^https?://", p):
            referenced.add(p)
    for m in IMG_BARE_RE.finditer(text):
        referenced.add(m.group(0))
    missing_imgs: list[str] = []
    for p in sorted(referenced):
        target = Path(p)
        if not target.is_absolute():
            target = note_path.parent / p
        if not target.exists():
            missing_imgs.append(p)
    for p in missing_imgs:
        err("V3-IMG", f"引用的图片不存在：{p}")
    checks["images"] = {"passed": not missing_imgs, "checked": len(referenced),
                        "missing": missing_imgs}

    # ---- V3-MANIFEST：manifest 图注交叉校验（复用 check_image_manifest） ----
    # v3 放宽：标注行已废除，不再要求图片贴近标注行（时间项因无锚定行自动跳过）；
    # keyword 交叉校验保留。
    manifest_path = note_path.parent / "images" / "manifest.json"
    im14 = check_image_manifest(note_path, manifest_path)
    for item in im14["mismatched"]:
        warn("V3-MANIFEST", f"图注交叉校验：{item['file']} {item['reason']}")
    checks["image_manifest_kw_time"] = {
        "passed": not im14["mismatched"], "checked": im14["checked"],
        "mismatched": [m["file"] for m in im14["mismatched"]],
        "time_skipped": im14["time_skipped"], "note": im14["note"]}

    # ---- --meta 读取（仅记录元信息，不影响检查） ----
    meta_info: dict = {"loaded": False, "note": "未提供 --meta"}
    if meta_path is not None:
        try:
            meta_raw = json.loads(Path(meta_path).read_text(encoding="utf-8"))
            meta_info = {"loaded": True,
                         "platform": meta_raw.get("platform")
                         if isinstance(meta_raw, dict) else None,
                         "duration": meta_raw.get("duration")
                         if isinstance(meta_raw, dict) else None}
        except (OSError, ValueError):
            meta_info = {"loaded": False,
                         "note": f"--meta {meta_path} 读取/解析失败（仅记录，不影响检查）"}
    checks["meta"] = meta_info

    # ---- V3-RATIO：笔记字数/转写稿字数 ∈ [0.03, 1.2] [warning，沿用 v2 检查15] ----
    if txt_dir is None:
        warn("V3-RATIO", "未提供 --txt-dir，跳过字数比对")
        checks["length_ratio"] = {"passed": None, "note": "缺 --txt-dir，跳过"}
    else:
        note_chars = len(re.sub(r"\s+", "", text))
        txt_chars = sum(len(re.sub(r"\s+", "", f.read_text(encoding="utf-8")))
                        for f in txt_files)
        if txt_chars > 0:
            ratio = note_chars / txt_chars
            if not 0.03 <= ratio <= 1.2:
                warn("V3-RATIO", f"笔记字数/转写稿字数比值为 {ratio:.4f}"
                                 f"（超出 0.03~1.2）："
                                 f"{'笔记过简' if ratio < 0.03 else '笔记可疑地长'}，"
                                 f"note_chars={note_chars}, transcript_chars={txt_chars}")
            checks["length_ratio"] = {"passed": 0.03 <= ratio <= 1.2,
                                      "ratio": round(ratio, 4),
                                      "note_chars": note_chars,
                                      "transcript_chars": txt_chars}
        else:
            warn("V3-RATIO", f"转写稿目录 {txt_dir} 下没有 .txt 文件，跳过字数比对")
            checks["length_ratio"] = {"passed": None, "note": "txt 目录为空"}

    return {"ok": not errors, "template_version": "v3",
            "note_type": note_type or None,
            "errors": errors, "warnings": warnings, "checks": checks}


# ==================== v4 门禁（模板 v4：语雀直导版式） ====================
# v4 定位（2026-10-21）：内容规则与 v3 相同（正文全面禁时刻、废除三词标签），
# 版式改为可直接导入语雀的纯 Markdown——
#   * 无 frontmatter、无一级标题（文件名 <序号>-<主题>.md 即标题），首行即
#     「> 核心主线：」引语块；元信息改由 --template v4 / --note-type / --meta 提供；
#   * 列表用 `+`；`## 小节` 下一行紧接 **核心思想：**；
#   * 概念卡「出现」写作「第 N 节「小节名」」；自测答案不折叠（禁 <details>）；
#   * 视频时间索引为文末最后一节，不折叠、无 NAV 标记，条目「+ [mm:ss](URL) 标题」。
# 分派：check_note_dispatch 收到 template="v4" 时路由到本节（不读 frontmatter）。
# 报告与 v3 同构：errors/warnings 为 {"check","message","line"} 对象数组。
#
# 检查项一览（id → 语义 → 级别）:
#   V4-FM    出现 frontmatter                                              [error]
#   V4-HEAD  出现一级标题 / 首行不是「> 核心主线：」/ 引语块缺「课程来源：」   [error]
#   V4-NAME  文件名不符合「<序号>-<主题>.md」                               [warning]
#   V4-NT    note_type 非 lecture/short                                    [error]
#   V4-LIST  正文使用 - / * 列表（应为 +）                                 [warning]
#   V4-SEC   小节实质内容 < 3 行                                           [error]
#   V4-IDEA  小节未以 **核心思想：** 开头 [error]；标题与核心思想之间有空行 [warning]
#   V4-E1    正文时刻禁令（仅「视频时间索引」节豁免，代码块内同样扫描）      [error]
#   V4-E2    时间索引：lecture 必备、须为最后一节、禁 <details>/NAV 标记、
#            ≥1 条目、条目时刻在转写稿白名单 [error]；条目标题不一致 [warning]
#   V4-E3    深链格式 + t=分P内相对秒 [error]；有深链平台的 lecture 缺链 [warning]
#   V4-E4    三词标签残留                                                  [error]
#   V4-E5    自测题：数量、缺答案、出现 <details>、答案含时刻、（对应：X）未知 [error]
#            答案超一行、yes/no 题干                                       [warning]
#   V4-E6    概念卡：定义/出现缺失、出现格式非法（须「第 N 节「名」」）、
#            小节序号不存在、出现含时刻 [error]；小节名不一致、单节概念、
#            依赖/易混引用未知概念 [warning]
#   V4-E7    未闭合 fence；代码块来源注释含时刻                             [error]
#   V4-KM    lecture 缺知识地图或 mermaid 不合规                            [error]
#   V4-KM-LABEL / V4-IMG / V4-MANIFEST / V4-RATIO 与 v3 同语义

V4_INDEX_HEADING = "视频时间索引"  # 时间索引小节标题关键词
# V4-E2：时间索引条目「+ [mm:ss] 标题」，脚本填链后为「+ [mm:ss](URL) 标题」（兼容 - / *）
V4_NAV_ENTRY_RE = re.compile(
    r"^\s*[-*+]\s+\[(\d{1,3}):(\d{2})\](?:\(([^)\s]*)\))?\s+(\S.*?)\s*$")  # 组：分/秒/URL/标题
# V4-E6：概念卡字段行，列表符号兼容 + / - / *
V4_CARD_FIELD_RE = re.compile(r"^\s*[-*+]\s*(定义|出现|依赖|易混)\s*[：:]\s*(.*)$")  # 组：字段名/值
# V4-E6：「出现」单项「第 N 节「小节名」」
V4_APPEAR_ITEM_RE = re.compile(r"第\s*(\d{1,2})\s*节\s*「([^」]*)」")  # 组：节号/小节名
# V4-E4：行首三词标签前缀（列表符号兼容 +）
V4_TAG_RESIDUE_RE = re.compile(
    r"^\s*(?:[-*+]\s*)?(?:\*\*" + V3_TAG_WORDS + r"\*\*|[（(]" + V3_TAG_WORDS + r"[）)])")  # **X** 或（X）
# V4-LIST：用 - / * 开头的列表行（`---` 分隔线与 **加粗** 不命中，因为要求符号后紧跟空白）
V4_DASH_LIST_RE = re.compile(r"^\s*[-*]\s+\S")  # - 要点 / * 要点
V4_H1_RE = re.compile(r"^#\s+\S")  # 一级标题行
V4_MAIN_QUOTE_RE = re.compile(r"^>\s*核心主线[：:]")  # 首行引语「> 核心主线：」
V4_FILENAME_RE = re.compile(r"^\d+(?:\.\d+)*-\S.*\.md$")  # <序号>-<主题>.md，序号可带小数点
V4_DETAILS_RE = re.compile(r"<details\b", re.IGNORECASE)  # 折叠块开标签


def parse_concept_cards_v4(lines: list[str], start: int, end: int,
                           fence_state: list[bool]) -> list[dict]:
    """解析 [start,end) 内的 v4 概念卡：### 概念名 + 「+ 定义/出现/依赖/易混」字段。"""
    cards: list[dict] = []  # 解析结果
    cur: dict | None = None  # 当前正在填充的卡片
    for i in range(start, end):  # 逐行扫描概念卡片节
        if fence_state[i]:  # 代码块内不解析
            continue  # 跳过
        tm = V3_CARD_TITLE_RE.match(lines[i])  # 卡片标题 ### 概念名
        if tm:  # 命中新卡片
            cur = {"name": tm.group(1).strip(), "line": i + 1, "fields": {}}  # 新建卡片
            cards.append(cur)  # 收集
            continue  # 标题行处理完毕
        fmm = V4_CARD_FIELD_RE.match(lines[i])  # 字段行
        if fmm and cur is not None and fmm.group(1) not in cur["fields"]:  # 同名字段只取第一次
            cur["fields"][fmm.group(1)] = (fmm.group(2).strip(), i + 1)  # 记录（值, 行号）
    return cards  # 返回全部卡片


def parse_quiz_questions_v4(lines: list[str], start: int, end: int) -> list[dict]:
    """解析 [start,end) 内的 v4 自测题：题干 / （对应：X）/ 不折叠的「**答案：**」行。"""
    questions: list[dict] = []  # 解析结果
    q: dict | None = None  # 当前题目
    for i in range(start, end):  # 逐行扫描自测题节
        line = lines[i]  # 当前行
        if NUM_ITEM_RE.match(line):  # 编号行 = 新题干
            q = {"no": len(questions) + 1, "line": i + 1,  # 题号与行号
                 "stem": re.sub(r"^\s*\d{1,2}\s*[.、．)）]\s*", "", line).strip(),  # 去编号的题干
                 "has_details": False, "has_answer": False,  # 折叠/答案标志
                 "extra_lines": 0, "correspond": None, "answer_raw": []}  # 多余行数/对应/答案原文
            cm = V2_CORRESPOND_RE.search(line)  # （对应：X）
            if cm:  # 有对应标记
                q["correspond"] = cm.group(1).strip()  # 记录概念名
            questions.append(q)  # 收集
            continue  # 题干行处理完毕
        if q is None:  # 第一题之前的内容
            continue  # 忽略
        if V4_DETAILS_RE.search(line):  # v4 禁止折叠
            q["has_details"] = True  # 标记，交给调用方报错
        if V2_ANSWER_RE.match(line) and not q["has_answer"]:  # 首个「**答案：**」行
            q["has_answer"] = True  # 标记有答案
            q["answer_raw"].append(line)  # 记录答案原文
        elif q["has_answer"] and line.strip() and line.strip() != "---":  # 答案之后的其它非空行
            if not re.match(r"^\s*</?(?:details|summary)\b", line, re.IGNORECASE):  # 折叠标签不计行数
                q["extra_lines"] += 1  # 计入答案超长
                q["answer_raw"].append(line)  # 一并做时刻扫描
    return questions  # 返回全部题目


def check_note_v4(text: str, note_path: Path, meta_path: Path | None,
                  txt_dir: Path | None, only_page: int | None = None,
                  note_type: str | None = None) -> dict:
    """v4 门禁：语雀直导版式（无 frontmatter / + 列表 / 答案与索引不折叠），内容规则同 v3。

    元信息来源：note_type 取参数 > metadata.json 的 suggested_note_type > lecture；
    platform / 视频 ID 取 metadata.json（缺 --meta 时深链只做宽松格式校验）。
    """
    errors: list[dict] = []  # error 对象数组
    warnings: list[dict] = []  # warning 对象数组
    checks: dict = {}  # 各检查明细

    def err(cid, message, line=None):  # 追加一条 error
        errors.append({"check": str(cid), "message": message, "line": line})  # 对象结构同 v3

    def warn(cid, message, line=None):  # 追加一条 warning
        warnings.append({"check": str(cid), "message": message, "line": line})  # 对象结构同 v3

    lines = text.splitlines()  # 全文按行
    fence_state = compute_fence_state(lines)  # 每行是否在代码块内

    # ---- 元信息：读取 --meta（platform / 视频 ID / 建议笔记类型） ----
    meta_raw: dict = {}  # metadata.json 内容
    meta_info: dict = {"loaded": False, "note": "未提供 --meta"}  # 报告用摘要
    if meta_path is not None:  # 传了 --meta
        try:  # 读取并解析
            loaded = json.loads(Path(meta_path).read_text(encoding="utf-8"))  # 解析 JSON
            meta_raw = loaded if isinstance(loaded, dict) else {}  # 只接受对象
            meta_info = {"loaded": True, "platform": meta_raw.get("platform"),  # 记录平台
                         "duration": meta_raw.get("duration")}  # 记录时长
        except (OSError, ValueError):  # 读取或解析失败
            meta_info = {"loaded": False,  # 标记未加载
                         "note": f"--meta {meta_path} 读取/解析失败（深链只做宽松校验）"}  # 原因
    checks["meta"] = meta_info  # 写入报告
    platform = (meta_raw.get("platform") or "").strip().lower()  # 平台（可能为空）
    vid = (meta_raw.get("bvid") or meta_raw.get("video_id") or "").strip()  # 视频 ID（可能为空）
    nt = (note_type or meta_raw.get("suggested_note_type") or "lecture").strip().lower()  # 笔记类型

    # ---- V4-NT：note_type 取值 ----
    if nt not in ("lecture", "short"):  # 非法取值
        err("V4-NT", f"note_type「{nt}」非法，只能是 lecture / short")  # 报错
    checks["note_type_valid"] = {"passed": nt in ("lecture", "short"), "note_type": nt}  # 明细

    # ---- V4-FM：禁止 frontmatter ----
    has_fm = FRONTMATTER_RE.match(text) is not None  # 是否以 --- 元数据块开头
    if has_fm:  # 存在 frontmatter
        err("V4-FM", "v4 笔记不写 frontmatter（语雀会把它当正文显示），"
                     "元信息改由 --template v4 / --note-type / --meta 提供", 1)  # 报错
    checks["no_frontmatter"] = {"passed": not has_fm}  # 明细

    # ---- V4-HEAD：无一级标题；首行为「> 核心主线：」；引语块含「课程来源：」 ----
    head_problems: list[str] = []  # 本项问题
    for i, line in enumerate(lines):  # 扫描一级标题
        if not fence_state[i] and V4_H1_RE.match(line):  # 代码块外的 # 标题
            head_problems.append("h1")  # 记录
            err("V4-HEAD", f"第{i + 1}行出现一级标题：v4 不写 # 标题（文件名即标题）", i + 1)  # 报错
    first_idx = next((i for i, l in enumerate(lines) if l.strip()), None)  # 首个非空行
    if first_idx is None or not V4_MAIN_QUOTE_RE.match(lines[first_idx]):  # 首行不是核心主线引语
        if not has_fm:  # 有 frontmatter 时已报 V4-FM，避免重复噪声
            head_problems.append("first_line")  # 记录
            err("V4-HEAD", "笔记第一行必须是「> 核心主线：…」引语块", (first_idx or 0) + 1)  # 报错
    else:  # 首行正确，再查课程来源
        block_end = next((j for j in range(first_idx, len(lines)) if not lines[j].strip()),
                         len(lines))  # 引语段落到第一个空行为止（兼容语雀导出的无 > 续行）
        if not any("课程来源" in lines[j] for j in range(first_idx, block_end)):  # 段内无课程来源
            head_problems.append("source")  # 记录
            err("V4-HEAD", "开头引语块缺少「课程来源：…」行", first_idx + 1)  # 报错
    checks["head_block"] = {"passed": not head_problems, "problems": head_problems}  # 明细

    # ---- V4-NAME：文件名 <序号>-<主题>.md [warning] ----
    name_ok = V4_FILENAME_RE.match(note_path.name) is not None  # 文件名是否合规
    if not name_ok:  # 不合规
        warn("V4-NAME", f"文件名「{note_path.name}」不符合「<序号>-<主题>.md」"
                        "（如 2.13-业务流程建模.md），语雀以文件名为文档标题")  # 提示
    checks["file_name"] = {"passed": name_ok, "name": note_path.name}  # 明细

    # ---- 时间索引节范围（E1/E4/LIST 的豁免区） ----
    nav_head = find_h2_line(lines, V4_INDEX_HEADING)  # 「## 视频时间索引」行号
    nav_end = next_h2_after(lines, nav_head + 1) if nav_head is not None else None  # 节结束行
    exempt: set[int] = set(range(nav_head, nav_end)) if nav_head is not None else set()  # 豁免行

    # ---- V4-LIST：正文列表应为 + [warning，聚合为一条] ----
    dash_lines = [i + 1 for i, l in enumerate(lines)  # 收集 - / * 列表行号
                  if not fence_state[i] and V4_DASH_LIST_RE.match(l)]  # 代码块外
    if dash_lines:  # 存在 - / * 列表
        warn("V4-LIST", f"{len(dash_lines)} 行使用 - / * 列表（首处第{dash_lines[0]}行），"
                        "v4 统一用 + 列表", dash_lines[0])  # 提示
    checks["list_marker"] = {"passed": not dash_lines, "lines": dash_lines[:20]}  # 明细（截断）

    sections = split_sections(text)  # 按 ## 切分

    # ---- V4-SEC：每个 ## 小节（除 目录/视频时间索引）≥3 行实质内容 ----
    sec_detail = {}  # 每节行数
    for title, body in sections:  # 遍历小节
        if title is None or any(k in title for k in V3_SEC_SKIP):  # 跳过前言/结构性小节
            continue  # 不检查
        n = len(substantive_lines(body))  # 实质内容行数
        sec_detail[title] = n  # 记录
        if n < 3:  # 不足 3 行
            err("V4-SEC", f"小节「{title}」实质内容不足（{n} 行非空文本，需至少 3 行）")  # 报错
    checks["section_content"] = {"passed": not any(e["check"] == "V4-SEC" for e in errors),
                                 "lines_per_section": sec_detail}  # 明细

    # ---- V4-IDEA：正文小节以 **核心思想：** 开头，且紧贴标题（不空行） ----
    idea_missing: list[str] = []  # 缺核心思想的小节
    for i, line in enumerate(lines):  # 按行找小节标题（需要行号）
        hm = H2_RE.match(line)  # ## 标题
        if not hm or fence_state[i] or any(k in hm.group(1) for k in V3_IDEA_SKIP):  # 非正文小节
            continue  # 跳过
        end = next_h2_after(lines, i + 1)  # 本节结束
        subs = [j for j in range(i + 1, end)  # 本节实质内容行号
                if lines[j].strip() and lines[j].strip() != "---"]  # 去空行与分隔线
        if not subs or not lines[subs[0]].startswith("**核心思想：**"):  # 首行不是核心思想
            idea_missing.append(hm.group(1))  # 记录
            err("V4-IDEA", f"小节「{hm.group(1)}」未以「**核心思想：** <一句话>」开头", i + 1)  # 报错
        elif subs[0] != i + 1:  # 核心思想与标题之间有空行
            warn("V4-IDEA", f"小节「{hm.group(1)}」标题与「**核心思想：**」之间有空行"
                            "（v4 版式：核心思想紧贴标题下一行）", i + 1)  # 提示
    checks["core_idea"] = {"passed": not idea_missing, "missing": idea_missing}  # 明细

    # ---- V4-E1：正文时刻禁令（仅时间索引节豁免；代码块内同样扫描） ----
    e1_hits: list[tuple[int, str]] = []  # 命中列表
    for i, line in enumerate(lines):  # 逐行扫描
        if i in exempt:  # 时间索引节
            continue  # 豁免
        for tm in V3_TS_ANY_RE.finditer(line):  # 所有时刻变体
            e1_hits.append((i + 1, tm.group(0)))  # 记录
            err("V4-E1", f"正文出现时刻「{tm.group(0)}」（第{i + 1}行）：正文全面禁止时间戳，"
                         "时刻只允许出现在文末「## 视频时间索引」", i + 1)  # 报错
    checks["body_timestamp_ban"] = {"passed": not e1_hits, "hits": e1_hits}  # 明细

    # ---- V4-E4：三词标签残留（跳过代码块与时间索引节） ----
    e4_lines: list[int] = []  # 命中行号
    for i, line in enumerate(lines):  # 逐行扫描
        if i in exempt or fence_state[i]:  # 豁免区与代码块
            continue  # 跳过
        if V4_TAG_RESIDUE_RE.match(line) or V3_CARD_TAGGED_TITLE_RE.match(line):  # 前缀或卡片标题后缀
            e4_lines.append(i + 1)  # 记录
            err("V4-E4", f"第{i + 1}行残留三词标签：{line.strip()[:40]}"
                         "（已废除 核心必考/重点掌握/了解即可 标签体系）", i + 1)  # 报错
    checks["tag_residue"] = {"passed": not e4_lines, "lines": e4_lines}  # 明细

    # ---- V4-E6：概念卡片（出现 = 第 N 节「小节名」） ----
    body_secs: dict[int, str] = {}  # 正文 {小节序号: 去序号标题}
    for line in lines:  # 收集 ## N. 小节
        hm = V2_H2_NUM_RE.match(line)  # ## N. 开头
        if hm:  # 命中
            body_secs[int(hm.group(1))] = v3_norm_title(H2_RE.match(line).group(1))  # 记录标题
    cards: list[dict] = []  # 概念卡
    card_names: set[str] = set()  # 概念名集合
    card_head = find_h2_line(lines, "概念卡片")  # 概念卡片节
    if card_head is not None:  # 存在该节
        card_end = next_h2_after(lines, card_head + 1)  # 节结束
        cards = parse_concept_cards_v4(lines, card_head + 1, card_end, fence_state)  # 解析
        card_names = {c["name"] for c in cards}  # 名称集合
    for c in cards:  # 逐卡检查
        defs = c["fields"].get("定义")  # 定义字段
        appear = c["fields"].get("出现")  # 出现字段
        if not defs or not defs[0]:  # 定义缺失/为空
            err("V4-E6", f"概念卡「{c['name']}」缺少非空「定义」字段", c["line"])  # 报错
        if not appear or not appear[0]:  # 出现缺失/为空
            err("V4-E6", f"概念卡「{c['name']}」缺少非空「出现」字段", c["line"])  # 报错
        else:  # 出现字段存在
            value, vline = appear  # 值与行号
            tm_ts = V3_TS_ANY_RE.search(value)  # 时刻扫描
            if tm_ts:  # 含时刻
                err("V4-E6", f"概念卡「{c['name']}」的「出现」含时刻「{tm_ts.group(0)}」，"
                             "只写「第 N 节「小节名」」", vline)  # 报错
            items = V4_APPEAR_ITEM_RE.findall(value)  # 解析各项
            if not items:  # 格式不符
                err("V4-E6", f"概念卡「{c['name']}」的「出现」格式非法：应为"
                             "「第 N 节「小节名」、第 M 节「小节名」」", vline)  # 报错
            else:  # 逐项校验
                secs_seen: set[int] = set()  # 已出现的节号
                for sec, sec_title in items:  # 每一项
                    sec_i = int(sec)  # 节号
                    secs_seen.add(sec_i)  # 记录
                    if sec_i not in body_secs:  # 节号不存在
                        err("V4-E6", f"概念卡「{c['name']}」的「出现」引用第 {sec} 节，"
                                     "但笔记中不存在该小节序号", vline)  # 报错
                    elif v3_norm_title(sec_title) != body_secs[sec_i]:  # 小节名不一致
                        warn("V4-E6", f"概念卡「{c['name']}」的「出现」第 {sec} 节名"
                                      f"「{sec_title}」与正文小节「{body_secs[sec_i]}」不一致",
                             vline)  # 提示
                if len(secs_seen) < 2:  # 单节概念
                    warn("V4-E6", f"概念卡「{c['name']}」的「出现」仅 {len(secs_seen)} 个小节："
                                  "单节概念不必单独成卡，考虑并入小节", vline)  # 提示
        dep = c["fields"].get("依赖")  # 依赖字段
        if dep and dep[0]:  # 非空
            for name in re.split(r"[、,，;；]", dep[0]):  # 逐个概念名
                if name.strip() and name.strip() not in card_names:  # 不在集合内
                    warn("V4-E6", f"概念卡「{c['name']}」的「依赖」引用「{name.strip()}」"
                                  "不在概念卡片集合内", dep[1])  # 提示
        conf = c["fields"].get("易混")  # 易混字段
        if conf and conf[0]:  # 非空
            conf_name = re.split(r"——|--", conf[0])[0].strip()  # 破折号前的概念名
            if conf_name and conf_name not in card_names:  # 不在集合内
                warn("V4-E6", f"概念卡「{c['name']}」的「易混」引用「{conf_name}」"
                              "不在概念卡片集合内", conf[1])  # 提示
    checks["concept_cards"] = {"passed": not any(e["check"] == "V4-E6" for e in errors),
                               "cards": len(cards)}  # 明细

    # ---- V4-E5：自测题（不折叠；数量 4~7 / 2~3） ----
    quiz_head = find_h2_line(lines, "自测题")  # 自测题节
    if quiz_head is None:  # 缺失
        err("V4-E5", "缺少「自测题」章节")  # 报错
        checks["quiz_count"] = {"passed": False, "count": 0, "found": False}  # 明细
        checks["quiz_format"] = {"passed": False, "found": False}  # 明细
    else:  # 存在
        quiz_end = next_h2_after(lines, quiz_head + 1)  # 节结束
        questions = parse_quiz_questions_v4(lines, quiz_head + 1, quiz_end)  # 解析题目
        n_q = len(questions)  # 题数
        want = V3_QUIZ_RANGE.get(nt, (4, 7))  # 数量范围
        if not want[0] <= n_q <= want[1]:  # 越界
            err("V4-E5", f"自测题数量为 {n_q}，要求 {want[0]}~{want[1]} 道（note_type={nt}）")  # 报错
        checks["quiz_count"] = {"passed": want[0] <= n_q <= want[1], "count": n_q,
                                "found": True}  # 明细
        fmt_ok = True  # 格式是否全部合规
        for q in questions:  # 逐题检查
            if q["has_details"]:  # 使用了折叠
                fmt_ok = False  # 不合规
                err("V4-E5", f"自测题 {q['no']} 使用了 <details> 折叠：v4 答案不折叠"
                             "（语雀导入不支持），直接写「**答案：**」行", q["line"])  # 报错
            if not q["has_answer"]:  # 缺答案
                fmt_ok = False  # 不合规
                err("V4-E5", f"自测题 {q['no']} 缺少「**答案：**」开头的答案行", q["line"])  # 报错
            elif q["extra_lines"] > 0:  # 答案超一行
                warn("V4-E5", f"自测题 {q['no']} 的答案正文超过一行（答案后另有 "
                              f"{q['extra_lines']} 行非空文本）", q["line"])  # 提示
            for ln in q["answer_raw"]:  # 答案时刻扫描
                ts = V3_TS_ANY_RE.search(ln)  # 任意时刻变体
                if ts:  # 含时刻
                    fmt_ok = False  # 不合规
                    err("V4-E5", f"自测题 {q['no']} 的答案含时刻「{ts.group(0)}」", q["line"])  # 报错
                    break  # 每题报一次
            if V2_YESNO_RE.search(q["stem"]):  # yes/no 题干
                warn("V4-E5", f"自测题 {q['no']} 疑似 yes/no 题干「{q['stem'][:30]}」",
                     q["line"])  # 提示
            if nt == "lecture":  # lecture 必须有对应
                if q["correspond"] is None:  # 缺对应
                    fmt_ok = False  # 不合规
                    err("V4-E5", f"自测题 {q['no']} 缺少「（对应：概念名）」标记", q["line"])  # 报错
                elif q["correspond"] not in card_names:  # 对应概念不存在
                    fmt_ok = False  # 不合规
                    err("V4-E5", f"自测题 {q['no']} 的「（对应：{q['correspond']}）」"
                                 "不存在于概念卡片集合，疑为笔误", q["line"])  # 报错
        checks["quiz_format"] = {"passed": fmt_ok, "count": n_q, "found": True}  # 明细

    # ---- 转写稿时间行池（E2 白名单 + RATIO 共用） ----
    txt_files: list[Path] = sorted(Path(txt_dir).glob("*.txt")) if txt_dir else []  # 转写稿
    legal_sec: set[int] = set()  # 合法总秒集合
    for f in txt_files:  # 逐文件
        for ln in f.read_text(encoding="utf-8").splitlines():  # 逐行
            m = re.match(r"\[(\d{1,3}):(\d{2})\]", ln)  # 行首时间
            if m:  # 命中
                legal_sec.add(int(m.group(1)) * 60 + int(m.group(2)))  # 记录总秒

    # ---- V4-E2：视频时间索引（lecture 必备；最后一节；不折叠、无 NAV 标记） ----
    entries: list[dict] = []  # 条目
    nav_markers = [i + 1 for i, l in enumerate(lines)  # NAV 标记行号
                   if V2_NAV_BEGIN_RE.search(l) or V2_NAV_END_RE.search(l)]  # BEGIN/END
    for ln in nav_markers:  # 每个标记
        err("V4-E2", f"第{ln}行出现 NAV 标记：v4 时间索引不写 NAV:BEGIN/NAV:END", ln)  # 报错
    if nav_head is not None:  # 存在时间索引节
        if nav_end != len(lines):  # 后面还有 ## 小节
            err("V4-E2", "「## 视频时间索引」必须是全文最后一节", nav_head + 1)  # 报错
        for i in range(nav_head + 1, nav_end):  # 扫描节内
            if V4_DETAILS_RE.search(lines[i]):  # 折叠标签
                err("V4-E2", f"时间索引第{i + 1}行使用了 <details> 折叠：v4 时间索引不折叠", i + 1)  # 报错
            em = V4_NAV_ENTRY_RE.match(lines[i])  # 条目
            if em:  # 命中
                mm_, ss_ = int(em.group(1)), int(em.group(2))  # 分、秒
                entries.append({"line": i + 1, "sec": mm_ * 60 + ss_,  # 行号与总秒
                                "ts": f"{mm_:02d}:{ss_:02d}",  # 规范化时刻
                                "url": (em.group(3) or "").strip() or None,  # 深链
                                "title": em.group(4).strip()})  # 标题
        if not entries:  # 无条目
            err("V4-E2", "「## 视频时间索引」没有条目（格式「+ [mm:ss] <标题>」）", nav_head + 1)  # 报错
    elif nt == "lecture":  # lecture 缺索引
        err("V4-E2", "缺少「## 视频时间索引」章节（lecture 必须有，位于文末最后一节）")  # 报错
    checks["time_index"] = {"found": nav_head is not None, "nav_markers": nav_markers,
                            "entries": len(entries)}  # 明细

    off_whitelist: list[str] = []  # 白名单外条目
    if not entries:  # 无条目
        checks["entry_whitelist"] = {"passed": None, "note": "无条目，跳过"}  # 明细
    elif txt_dir is None:  # 缺 --txt-dir
        warn("V4-E2", "未提供 --txt-dir，无法校验时间索引条目时刻白名单（补齐参数后重跑）")  # 提示
        checks["entry_whitelist"] = {"passed": None, "note": "缺 --txt-dir，跳过"}  # 明细
    elif not legal_sec:  # 转写稿无时间行
        warn("V4-E2", f"转写稿目录 {txt_dir} 下没有可解析的 [mm:ss] 时间行，跳过条目白名单校验")  # 提示
        checks["entry_whitelist"] = {"passed": None, "note": "txt 目录无时间行"}  # 明细
    else:  # 正常校验
        for e in entries:  # 逐条
            if not any(abs(e["sec"] - ls) <= 5 for ls in legal_sec):  # ±5s 内无合法时间
                off_whitelist.append(e["ts"])  # 记录
                err("V4-E2", f"时间索引条目 [{e['ts']}]（第{e['line']}行）不在转写稿时间戳"
                             "白名单内（±5s 容差）", e["line"])  # 报错
        checks["entry_whitelist"] = {"passed": not off_whitelist,
                                     "legal_seconds": len(legal_sec),
                                     "off_whitelist": off_whitelist}  # 明细

    body_titles = {v3_norm_title(t) for t, _ in sections  # 正文小节名集合
                   if t and not any(k in t for k in V3_IDEA_SKIP)}  # 去结构性小节
    title_warns = 0  # 不一致计数
    for e in entries:  # 逐条
        if v3_norm_title(e["title"]) not in body_titles:  # 标题不一致
            title_warns += 1  # 计数
            warn("V4-E2", f"时间索引条目标题「{e['title']}」（第{e['line']}行）与正文"
                          "一级 ## 小节名不一致（去行首序号后比较）", e["line"])  # 提示
    checks["entry_titles"] = {"passed": title_warns == 0, "warnings": title_warns}  # 明细

    # ---- V4-E3：深链校验（格式 + t=分P内相对秒） ----
    link_errors = 0  # 深链错误数
    missing_link_warns = 0  # 缺链提示数
    page_index = v3_page_index(txt_dir) if (txt_dir is not None and entries) else {}  # 分P时间索引
    for e in [x for x in entries if x["url"]]:  # 已带链接的条目
        is_yt = platform == "youtube" or (not platform and "youtube.com" in e["url"])  # 是否 YouTube 链接
        if is_yt:  # YouTube：watch?v=<id>&t=<秒>s
            pat = (r"https://www\.youtube\.com/watch\?v=" + re.escape(vid) + r"&t=\d+s"
                   if vid else V3_YT_LINK_RE.pattern.strip("^$"))  # 有 ID 严格，否则宽松
            hint = "https://www.youtube.com/watch?v=<video_id>&t=<非负整数>s"  # 报错提示
        else:  # B站：?p=N&t=S
            pat = (r"https://www\.bilibili\.com/video/" + re.escape(vid) + r"\?p=\d+&t=\d+"
                   if vid else V3_BILI_LINK_RE.pattern.strip("^$"))  # 有 ID 严格，否则宽松
            hint = "https://www.bilibili.com/video/<bvid>?p=N&t=<非负整数>"  # 报错提示
        e["fmt_ok"] = re.fullmatch(pat, e["url"]) is not None  # 格式判定
        if not e["fmt_ok"]:  # 格式非法
            link_errors += 1  # 计数
            err("V4-E3", f"时间索引条目深链格式非法（第{e['line']}行）：{e['url'][:60]}，必须为 "
                         f"{hint}（由 scripts/note_nav.py 生成，勿手写）", e["line"])  # 报错
            continue  # 格式错就不再校验 t
        if not page_index:  # 无法定位分P
            continue  # 后面统一提示
        loc = v3_locate_page(page_index, e["sec"], only_page)  # 归属分P
        t_val = int(re.search(r"&t=(\d+)s?$", e["url"]).group(1))  # 链接里的 t
        if loc is not None and t_val != loc[1]:  # t 与分P内相对秒不一致
            link_errors += 1  # 计数
            err("V4-E3", f"深链 t={t_val} 与条目 [{e['ts']}] 归属分P（p={loc[0]}）的分P内相对秒"
                         f" {loc[1]} 不一致（第{e['line']}行）：请运行 scripts/note_nav.py 重新生成",
                e["line"])  # 报错
    if any(x["url"] for x in entries) and not page_index:  # 有链接但无法校验 t
        warn("V4-E3", "未提供 --txt-dir（或转写稿不可解析），无法校验深链 t（补齐参数后重跑）")  # 提示
    for e in entries:  # 缺链检查
        if not e["url"] and nt == "lecture" and platform in ("bilibili", "youtube"):  # 有深链语义的平台
            missing_link_warns += 1  # 计数
            warn("V4-E3", f"lecture 时间索引条目 [{e['ts']}] 缺少深链（第{e['line']}行）：请运行 "
                          "python scripts/note_nav.py <笔记.md> --template v4 --meta <metadata.json>"
                          " --txt-dir <转写稿目录> 自动填充", e["line"])  # 提示
    checks["deep_links"] = {"passed": link_errors == 0, "entries": len(entries),
                            "missing_link_warns": missing_link_warns,
                            "pages": sorted(page_index)}  # 明细

    # ---- V4-E7：代码块（未闭合 fence；来源注释含时刻） ----
    open_lang: str | None = None  # 当前打开的 fence 语言
    open_idx = -1  # 打开位置
    src_ts: list[tuple[int, str]] = []  # 来源含时刻的行
    for i, line in enumerate(lines):  # 逐行
        m = V2_FENCE_LANG_RE.match(line)  # fence 行
        if not m:  # 非 fence
            continue  # 跳过
        if open_lang is None:  # 开 fence
            open_lang, open_idx = m.group(1).lower(), i  # 记录语言与位置
            continue  # 等待闭合
        if open_lang in V2_CODE_LANGS:  # 真实代码块才查来源
            for j in list(range(open_idx + 1, i)) + list(range(i + 1, min(i + 3, len(lines)))):  # 块内 + 块后 2 行
                if "来源" in lines[j] and V3_TS_ANY_RE.search(lines[j]):  # 来源含时刻
                    src_ts.append((j + 1, lines[j].strip()[:40]))  # 记录
        open_lang = None  # 闭合
    for j, snippet in src_ts:  # 报告来源含时刻
        err("V4-E7", f"来源标注含时刻（第{j}行）：{snippet}（来源可选，写仓库路径即可）", j)  # 报错
    if open_lang is not None:  # 仍未闭合
        err("V4-E7", "存在未闭合的代码块（``` fence）")  # 报错
    checks["code_blocks"] = {"passed": not src_ts and open_lang is None,
                             "source_with_timestamp": [j for j, _ in src_ts],
                             "unclosed_fence": open_lang is not None}  # 明细

    # ---- V4-KM：lecture 必须有知识地图 + 合规 mermaid ----
    if nt == "lecture":  # 仅 lecture 强制
        km_head = find_h2_line(lines, "知识地图")  # 知识地图节
        if km_head is None:  # 缺失
            err("V4-KM", "缺少「本讲知识地图」章节（lecture 必须有 mermaid 知识地图）")  # 报错
            checks["knowledge_map"] = {"passed": False, "found": False}  # 明细
        else:  # 存在
            blocks, problems = validate_mermaid_blocks(
                lines, km_head + 1, next_h2_after(lines, km_head + 1))  # 校验 mermaid 块
            if blocks == 0:  # 无 mermaid 块
                err("V4-KM", "「本讲知识地图」章节内没有 ```mermaid 代码块（推荐 graph TD）")  # 报错
            for p in problems:  # 块内问题
                err("V4-KM", f"知识地图 mermaid 块校验失败：{p}")  # 报错
            checks["knowledge_map"] = {"passed": blocks > 0 and not problems,
                                       "mermaid_blocks": blocks, "problems": problems}  # 明细
    else:  # short 可省
        checks["knowledge_map"] = {"passed": None, "note": "short 型知识地图可省（不检查）"}  # 明细

    # ---- V4-KM-LABEL：mermaid 节点标签卫生 [warning] ----
    label_problems = v3_mermaid_label_problems(lines)  # 复用 v3 扫描
    for ln, label, reason in label_problems:  # 逐个问题
        warn("V4-KM-LABEL", f"mermaid 节点标签「{label[:30]}」{reason}（第{ln}行）", ln)  # 提示
    checks["mermaid_labels"] = {"passed": not label_problems,
                                "lines": [ln for ln, _, _ in label_problems]}  # 明细

    # ---- V4-IMG：引用的本地图片真实存在 ----
    referenced: set[str] = set()  # 本地图片路径
    for m in IMG_MD_RE.finditer(text):  # ![说明](路径)
        if not re.match(r"^https?://", m.group(2)):  # 跳过网络图片（如语雀图床）
            referenced.add(m.group(2))  # 记录
    for m in IMG_BARE_RE.finditer(text):  # 裸 images/*.png
        referenced.add(m.group(0))  # 记录
    missing_imgs = [p for p in sorted(referenced)  # 不存在的图片
                    if not (Path(p) if Path(p).is_absolute() else note_path.parent / p).exists()]  # 相对笔记目录解析
    for p in missing_imgs:  # 逐个报告
        err("V4-IMG", f"引用的图片不存在：{p}")  # 报错
    checks["images"] = {"passed": not missing_imgs, "checked": len(referenced),
                        "missing": missing_imgs}  # 明细

    # ---- V4-MANIFEST：manifest 图注关键词交叉校验 [warning] ----
    im = check_image_manifest(note_path, note_path.parent / "images" / "manifest.json")  # 复用
    for item in im["mismatched"]:  # 不匹配项
        warn("V4-MANIFEST", f"图注交叉校验：{item['file']} {item['reason']}")  # 提示
    checks["image_manifest_kw_time"] = {"passed": not im["mismatched"], "checked": im["checked"],
                                        "mismatched": [m["file"] for m in im["mismatched"]],
                                        "note": im["note"]}  # 明细

    # ---- V4-RATIO：笔记字数/转写稿字数 ∈ [0.03, 1.2] [warning] ----
    if txt_dir is None:  # 缺 --txt-dir
        warn("V4-RATIO", "未提供 --txt-dir，跳过字数比对")  # 提示
        checks["length_ratio"] = {"passed": None, "note": "缺 --txt-dir，跳过"}  # 明细
    else:  # 计算比值
        note_chars = len(re.sub(r"\s+", "", text))  # 笔记字数
        txt_chars = sum(len(re.sub(r"\s+", "", f.read_text(encoding="utf-8")))
                        for f in txt_files)  # 转写稿字数
        if txt_chars > 0:  # 有转写稿
            ratio = note_chars / txt_chars  # 比值
            if not 0.03 <= ratio <= 1.2:  # 越界
                warn("V4-RATIO", f"笔记字数/转写稿字数比值为 {ratio:.4f}（超出 0.03~1.2）："
                                 f"{'笔记过简' if ratio < 0.03 else '笔记可疑地长'}")  # 提示
            checks["length_ratio"] = {"passed": 0.03 <= ratio <= 1.2, "ratio": round(ratio, 4),
                                      "note_chars": note_chars, "transcript_chars": txt_chars}  # 明细
        else:  # 无转写稿
            warn("V4-RATIO", f"转写稿目录 {txt_dir} 下没有 .txt 文件，跳过字数比对")  # 提示
            checks["length_ratio"] = {"passed": None, "note": "txt 目录为空"}  # 明细

    return {"ok": not errors, "template_version": "v4", "note_type": nt,
            "errors": errors, "warnings": warnings, "checks": checks}  # 报告


def check_note_dispatch(text: str, note_path: Path, meta_path: Path | None = None,
                        txt_dir: Path | None = None,
                        only_page: int | None = None,
                        template: str | None = None,
                        note_type: str | None = None) -> dict:
    """版本分派入口：只支持 v4 与 v3（v1 / v2 规则已移除）。

    template="v4" 或笔记没有 frontmatter → check_note_v4（note_type 仅 v4 使用）；
    frontmatter template_version=v3 → check_note_v3；其余版本 → VERSION 报错。
    main 与测试共用本函数，保证"测试跑的就是 CLI 跑的"。
    """
    fm = parse_frontmatter(text)  # 解析 frontmatter（v4 笔记为空）
    if (template or "").strip().lower() == "v4" or not FRONTMATTER_RE.match(text):  # 显式 v4 或无 frontmatter
        return check_note_v4(text, note_path, meta_path, txt_dir, only_page,
                             note_type=note_type)  # 交给 v4 规则集
    version = (fm.get("template_version") or "").strip().lower()  # frontmatter 声明的版本
    if version == "v3":  # v3 老笔记
        return check_note_v3(text, note_path, meta_path, txt_dir, only_page)  # 交给 v3 规则集
    return {"ok": False, "template_version": version or None,  # 不支持的版本
            "note_type": fm.get("note_type") or None,  # 透传笔记类型
            "errors": [{"check": "VERSION",  # 版本错误
                        "message": f"不支持的模板版本「{version or '缺失'}」：v1 / v2 规则已移除，"
                                   "请按 v4 模板重新生成笔记（v3 笔记需在 frontmatter 写 "
                                   "template_version: v3）",
                        "line": 1}],  # 指向文件开头
            "warnings": [], "checks": {}}  # 无其他检查


def _reconfigure_stdout() -> None:
    """stdout 兜底为 UTF-8：Agent 管道调用时编码常是 gbk/cp936，
    emoji 会让 print 直接 UnicodeEncodeError（第三轮复检 M3）。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def print_json_report(report: dict, **kw) -> None:
    """stdout 机器可读 JSON 报告。ensure_ascii=True：即便兜底失效、
    遇到 GBK 管道也不会崩——JSON 转义无损，下游 json.loads 照样还原 emoji。"""
    print(json.dumps(report, ensure_ascii=True, **kw))


def main():
    _reconfigure_stdout()
    ap = argparse.ArgumentParser(
        description="课堂笔记质量门禁（无 frontmatter 或 --template v4 走 v4；"
                    "frontmatter 写 v3 走 v3）")
    ap.add_argument("note", help="笔记 .md 路径")
    ap.add_argument("--meta", help="metadata.json 路径（与 --txt-dir 联用做字数比对）")
    ap.add_argument("--txt-dir", help="转写稿 .txt 所在目录"
                                      "（时间索引白名单、深链 t 与字数比校验需要）")
    ap.add_argument("--page", type=int, default=None,
                    help="单分P笔记场景：限定时间索引定位到该分P页码"
                         "（每选集一份笔记时必填，与 note_nav --page 同口径）")
    ap.add_argument("--template", choices=["v4"], default=None,
                    help="模板版本：v4（语雀直导版式，无 frontmatter）；"
                         "省略时无 frontmatter 也按 v4 处理")  # 显式指定 v4
    ap.add_argument("--note-type", choices=["lecture", "short"], default=None,
                    help="仅 v4：笔记类型；省略取 metadata.json 的 suggested_note_type，"
                         "再缺省为 lecture")  # v4 的 note_type 来源
    args = ap.parse_args()

    note_path = Path(args.note)
    if not note_path.exists():
        print_json_report({"ok": False, "template_version": None, "note_type": None,
                           "errors": [{"check": "FILE", "message": f"笔记文件不存在: {note_path}",
                                       "line": None}],  # 与其他报告同为对象数组
                           "warnings": [], "checks": {}})
        sys.exit(1)
    text = note_path.read_text(encoding="utf-8")
    meta_path = Path(args.meta) if args.meta else None
    txt_dir = Path(args.txt_dir) if args.txt_dir else None

    # 版本分派：无 frontmatter / --template v4 → v4；frontmatter v3 → v3；其余报 VERSION。
    report = check_note_dispatch(text, note_path, meta_path, txt_dir,
                                 only_page=args.page, template=args.template,
                                 note_type=args.note_type)  # v4 参数透传
    print_json_report(report, indent=2)
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
