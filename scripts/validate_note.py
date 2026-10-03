#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes 笔记质量门禁：校验生成的课堂笔记是否符合模板规范。

用法:
    python validate_note.py <笔记.md> [--meta <metadata.json>] [--txt-dir <转写稿目录>]

版本分派（模板 v2 规格 §4.1）:
    按 frontmatter 的 template_version 分派规则集；无 frontmatter / v1 → 走下方
    v1 规则（完全冻结，v1 笔记零改动通过）；v2 → 走 v2 规则集（三词文字标签
    核心必考/重点掌握/了解即可，26 项检查：通用 15 项 + lecture L1~L12 +
    short S1~S3，见 check_note_v2）。输出 JSON 额外带 template_version /
    note_type 字段。
    报告格式差异（§4.5 破坏性变更，仅 v2）：v2 的 errors/warnings 是
    {"check","message","line"} 对象数组；v1 保持旧的字符串数组不变。

v1 检查项（errors 必须为 0 才通过；warnings 只提示）:
    1. 每个二级小节（##）都有实质内容：除"目录/参考时间戳"外，正文至少 3 行非空文本  [error]
    2. 存在"知识地图"章节且非空                                                    [error]
    3. 存在"自测题"章节，且编号题目数量在 5~9                                       [error]
    4. ★ 强制门禁（统计时跳过 ``` 代码块内内容）：
       4a. 每条 ★ 行：★ 数量在 1~3，且星号后有要点句                                [error]
       4b. ★ 行总数 >= 3                                                          [error]
       4c. 有 ★ 行时，★★★ 行数在 2~4（避免"全是重点=没有重点"）                      [error]
       4d. ★ 行的 [mm:ss] 锚点覆盖率 >= 0.8                                        [warning]
    5. 所有代码块（``` fence）结束其后 2 行内出现"来源"标注（子项目名或 [mm:ss]）      [error]
    6. 笔记里引用的 images/*.png（相对笔记路径解析）全部真实存在                      [error]
    7. 笔记中 [mm:ss] 证据锚点总数 >= 3                                             [warning]
    8. 传 --meta --txt-dir 时：笔记字数/转写稿总字数 比值在 0.03~1.2                 [warning]
    9. (E3) 笔记同目录存在 images/manifest.json 时：每个 ![说明](images/x.png) 的说明
       与 manifest 里该文件 caption 比较关键词交集（中文 2 字片段 + 英文 token，
       去停用词），无交集提示"图注可能错位"；无 manifest 则整项跳过                    [warning]
   10. (E12) frontmatter（--- 包围的元数据块）里有 template_version 字段             [warning]

输出: stdout 打印 JSON（ok / errors / warnings / checks 各检查项明细）。
全过 exit 0；有 error exit 1。
"""
import argparse
import json
import re
import sys
from pathlib import Path

ANCHOR_RE = re.compile(r"\[\d{1,2}:\d{2}\]")
IMG_MD_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
IMG_BARE_RE = re.compile(r"(?<!\()\bimages/[\w./-]+\.(?:png|jpe?g|gif)\b")
NUM_ITEM_RE = re.compile(r"^\s*\d{1,2}\s*[.、．)）]\s*\S")
FENCE_RE = re.compile(r"^\s*```")
H2_RE = re.compile(r"^##\s+(.+?)\s*$")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
EN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]+")
SKIP_SECTION_KEYWORDS = ("目录", "参考时间戳")
# ---- 检查14（B2 新增）：锚定行（标注行）识别正则常量 ----
# v1 为 ★ 符号行（沿用现有 ★ 行语法：[-*] ★{1,3} 要点句 [mm:ss]）；
# 同时预留 v2 三词文字标签（**加粗** 与 全角括号（X）两种形式）的匹配分支——
# v2 切换为文字标签时只需调整本常量，函数结构与签名不变。
ANCHOR_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s+)?"
    r"(?:★{1,3}"
    r"|\*\*(?:核心必考|重点掌握|了解即可)\*\*"
    r"|[（(](?:核心必考|重点掌握|了解即可)[）)])"
)
TS_LINE_RE = re.compile(r"\[(\d{1,3}):(\d{2})\]")
CHECK14_ANCHOR_GAP = 5   # 锚定行与图片引用的最大行距
CHECK14_TOLERANCE = 60   # 截图时间与锚定行秒数的允许偏差（秒）
# 中文 2 字片段里公认无区分度的通用词，不参与图注关键词交集
CJK_STOPWORDS = {"截图", "画面", "视频", "笔记", "内容", "一个", "对应", "来自", "这个"}
EN_STOPWORDS = {"and", "the", "this", "that", "for", "with", "from",
                "image", "images", "screenshot", "png", "jpg", "jpeg"}


def extract_keywords(text: str) -> set[str]:
    """提取比对用关键词：中文片段的全部 2 字滑窗 + 英文 token（小写，去停用词）。"""
    kw: set[str] = set()
    for run in CJK_RUN_RE.findall(text):
        for i in range(len(run) - 1):
            gram = run[i:i + 2]
            if gram not in CJK_STOPWORDS:
                kw.add(gram)
    for tok in EN_TOKEN_RE.findall(text):
        t = tok.lower()
        if t not in EN_STOPWORDS:
            kw.add(t)
    return kw


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
    """manifest 图注 keyword + 时间对齐交叉校验（规格 §4.2 检查14，warning 级）。

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


def check_note(text: str, note_path: Path, meta_path: Path | None,
               txt_dir: Path | None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict = {}

    sections = split_sections(text)

    # ---- 检查1：每个二级小节都有实质内容（>=3 行非空文本） ----
    detail = {}
    for title, body in sections:
        if title is None:
            continue  # 首个 ## 之前（H1/引言），不算小节
        if any(k in title for k in SKIP_SECTION_KEYWORDS):
            continue
        n = len(substantive_lines(body))
        detail[title] = n
        if n < 3:
            errors.append(f"小节「{title}」实质内容不足（{n} 行非空文本，需至少 3 行）")
    checks["section_content"] = {"passed": not any("实质内容不足" in e for e in errors),
                                 "lines_per_section": detail}

    # ---- 检查2：知识地图章节存在且非空 ----
    km = [(t, b) for t, b in sections if t and "知识地图" in t]
    if not km:
        errors.append("缺少「知识地图」章节")
        checks["knowledge_map"] = {"passed": False, "found": False}
    else:
        n = len(substantive_lines(km[0][1]))
        if n == 0:
            errors.append("「知识地图」章节为空")
        checks["knowledge_map"] = {"passed": n > 0, "found": True, "lines": n}

    # ---- 检查3：自测题章节，编号题目 5~9 ----
    quiz = [(t, b) for t, b in sections if t and "自测题" in t]
    if not quiz:
        errors.append("缺少「自测题」章节")
        checks["quiz"] = {"passed": False, "found": False}
    else:
        n = sum(1 for l in quiz[0][1] if NUM_ITEM_RE.match(l))
        if not 5 <= n <= 9:
            errors.append(f"自测题编号题目数量为 {n}，要求 5~9 道")
        checks["quiz"] = {"passed": 5 <= n <= 9, "found": True, "count": n}

    # ---- 检查4 + 5：★ 强制门禁 / 代码块来源标注（需跟踪 fence 状态） ----
    lines = text.splitlines()
    star_lines = 0
    star_three = 0
    star_anchored = 0
    star_errors: list[str] = []
    fence_open = False
    fence_close_idx: list[int] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if FENCE_RE.match(line):
            if not fence_open:
                fence_open = True
            else:
                fence_open = False
                fence_close_idx.append(i)
            i += 1
            continue
        if not fence_open and "★" in line:
            star_lines += 1
            cnt = line.count("★")
            if cnt == 3:
                star_three += 1
            if ANCHOR_RE.search(line):
                star_anchored += 1
            rest = re.sub(r"^[\s\->*]*", "", line)
            rest = rest.replace("★", "", cnt).strip()
            if cnt > 3:
                star_errors.append(f"★ 数量为 {cnt}（第{i + 1}行），只能在 1~3：{line.strip()[:50]}")
            if not rest:
                star_errors.append(f"★ 行缺少要点句（第{i + 1}行）：{line.strip()[:50]}")
        i += 1
    if fence_open:
        errors.append("存在未闭合的代码块（``` fence）")
    # ★ 强制门禁：总数 / ★★★ 区间 / 锚点覆盖率
    if star_lines < 3:
        star_errors.append(f"★ 行总数为 {star_lines}，至少 3 行（本讲主线要点必须显式分级标注）")
    if star_lines > 0 and not 2 <= star_three <= 4:
        star_errors.append(f"★★★ 行数为 {star_three}，要求 2~4 个（避免『全是重点=没有重点』）")
    if star_lines > 0:
        coverage = star_anchored / star_lines
        if coverage < 0.8:
            warnings.append(
                f"★ 行锚点覆盖率为 {star_anchored}/{star_lines}（{coverage:.0%}），低于 0.8："
                "建议每条要点句补 [mm:ss] 证据锚点（时间须来自转写稿真实时间行）")
        checks["stars"] = {"passed": not star_errors, "star_lines": star_lines,
                           "three_star_lines": star_three,
                           "anchor_coverage": round(coverage, 2)}
    else:
        checks["stars"] = {"passed": not star_errors, "star_lines": 0,
                           "three_star_lines": 0, "anchor_coverage": None}
    errors.extend(star_errors)

    src_blocks = 0
    for idx in fence_close_idx:
        src_blocks += 1
        after = lines[idx + 1: idx + 3]
        if not any("来源" in l for l in after):
            errors.append(f"代码块（结束于第{idx + 1}行）其后 2 行内缺少「来源」标注")
    checks["code_sources"] = {"passed": not any("来源」标注" in e for e in errors),
                              "code_blocks": src_blocks}

    # ---- 检查6：引用的图片必须真实存在 ----
    referenced: set[str] = set()
    for m in IMG_MD_RE.finditer(text):
        p = m.group(2)
        if not re.match(r"^https?://", p):
            referenced.add(p)
    for m in IMG_BARE_RE.finditer(text):
        referenced.add(m.group(0))
    missing = []
    for p in sorted(referenced):
        target = Path(p)
        if not target.is_absolute():
            target = note_path.parent / p
        if not target.exists():
            missing.append(p)
    if missing:
        for p in missing:
            errors.append(f"引用的图片不存在：{p}")
    checks["images"] = {"passed": not missing, "checked": len(referenced), "missing": missing}

    # ---- 检查7 (E3)：images/manifest.json 图注交叉校验（warning，无 manifest 跳过） ----
    manifest_path = note_path.parent / "images" / "manifest.json"
    manifest_checked = 0
    mismatched: list[str] = []
    manifest_loaded = False
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_loaded = True
        except (OSError, ValueError):
            manifest = None
        frames: list = []
        if isinstance(manifest, dict):
            frames = manifest.get("frames", [])
        elif isinstance(manifest, list):
            frames = manifest
        cap_by_file = {f.get("file"): (f.get("caption") or "") for f in frames
                       if isinstance(f, dict) and f.get("file")}
        for m in IMG_MD_RE.finditer(text):
            p, alt = m.group(2), m.group(1)
            if re.match(r"^https?://", p) or not p.replace("\\", "/").startswith("images/"):
                continue
            fname = p.replace("\\", "/").rsplit("/", 1)[-1]
            caption = cap_by_file.get(fname)
            if not caption:
                continue  # manifest 未收录（或无 caption）不判定
            manifest_checked += 1
            if not (extract_keywords(alt) & extract_keywords(caption)):
                mismatched.append(fname)
                warnings.append(
                    f"图注可能错位：{p}（笔记说明「{alt}」与 manifest 图注「{caption}」"
                    "无关键词交集，请核对截图是否嵌入错位置）")
    checks["image_manifest"] = {"passed": not mismatched, "checked": manifest_checked,
                                "mismatched": mismatched,
                                "note": None if manifest_loaded else
                                "无 images/manifest.json（或解析失败），跳过图注交叉校验"}

    # ---- 检查14（B2 新增）：manifest keyword + 时间对齐交叉校验（自包含函数） ----
    im14 = check_image_manifest(note_path, manifest_path)
    for item in im14["mismatched"]:
        warnings.append(f"图注交叉校验（检查14）：{item['file']} {item['reason']}")
    checks["image_manifest_kw_time"] = {
        "passed": not im14["mismatched"], "checked": im14["checked"],
        "mismatched": [m["file"] for m in im14["mismatched"]],
        "time_skipped": im14["time_skipped"], "note": im14["note"]}

    # ---- 检查8：证据锚点总数 >= 3（warning） ----
    anchors = len(ANCHOR_RE.findall(text))
    if anchors < 3:
        warnings.append(f"[mm:ss] 证据锚点仅 {anchors} 个，建议 >= 3（时间戳须来自转写稿真实时间行）")
    checks["anchors"] = {"passed": anchors >= 3, "count": anchors}

    # ---- 检查9：笔记字数 / 转写稿总字数 比值 0.03~1.2（warning） ----
    if meta_path and txt_dir:
        note_chars = len(re.sub(r"\s+", "", text))
        txt_chars = 0
        for f in sorted(Path(txt_dir).glob("*.txt")):
            txt_chars += len(re.sub(r"\s+", "", f.read_text(encoding="utf-8")))
        if txt_chars > 0:
            ratio = note_chars / txt_chars
            if not 0.03 <= ratio <= 1.2:
                warnings.append(
                    f"笔记字数/转写稿字数比值为 {ratio:.4f}（超出 0.03~1.2）："
                    f"{'笔记过简' if ratio < 0.03 else '笔记可疑地长'}，"
                    f"note_chars={note_chars}, transcript_chars={txt_chars}")
            checks["length_ratio"] = {"passed": 0.03 <= ratio <= 1.2,
                                      "ratio": round(ratio, 4),
                                      "note_chars": note_chars,
                                      "transcript_chars": txt_chars}
        else:
            warnings.append(f"转写稿目录 {txt_dir} 下没有 .txt 文件，跳过字数比对")
            checks["length_ratio"] = {"passed": None, "note": "txt 目录为空"}
    elif meta_path or txt_dir:
        warnings.append("字数比对需要同时提供 --meta 与 --txt-dir，本次跳过")
        checks["length_ratio"] = {"passed": None, "note": "参数不全"}

    # ---- 检查10 (E12)：frontmatter template_version（warning） ----
    fm = FRONTMATTER_RE.match(text)
    has_field = bool(fm and re.search(r"^template_version\s*:", fm.group(1), re.MULTILINE))
    if not has_field:
        if fm:
            warnings.append("frontmatter 缺少 template_version 字段"
                            "（模板升级后无法追溯笔记依据的版本，建议补上）")
        else:
            warnings.append("笔记缺少 frontmatter（--- 包围的元数据块），"
                            "无法读取 template_version，建议补上并标注模板版本")
    checks["template_version"] = {"passed": has_field, "frontmatter": bool(fm)}

    return {"ok": not errors, "errors": errors, "warnings": warnings, "checks": checks}


# ==================== v2 门禁（规格 §3/§4，B3 批次新增；v1 规则冻结不动） ====================
# v2 一律使用三词文字标签（核心必考/重点掌握/了解即可），不再接受 ★ 符号（§4.1）。
# v2 报告的 errors/warnings 为 {"check","message","line"} 对象数组（§4.5）。

V2_TAGS = ("核心必考", "重点掌握", "了解即可")
V2_TAG_LINE_RE = re.compile(
    r"^\s*[-*]\s+\*\*(核心必考|重点掌握|了解即可)\*\*(?:\s+(\S.*))?\s*$")
# 加粗开头的列表行（§3.1：非白名单标注词 → error，封死同义词漂移）
V2_BOLD_LIST_RE = re.compile(r"^\s*[-*]\s+\*\*([^*]+)\*\*")
V2_ANCHOR_RE = re.compile(r"\[(\d{1,3}):(\d{2})\]")
# §3.1 锚点 [mm:ss] 的非法变体（秒非两位 / 分钟超 3 位 / 时:分:秒）
V2_BAD_TS_RES = (
    (re.compile(r"\[\d{1,3}:\d(?:\D|\Z)"), "秒必须是两位数字"),
    (re.compile(r"\[\d{4,}:\d{2}\]"), "分钟最多 3 位数字"),
    (re.compile(r"\[\d{1,3}:\d{2}:\d{2}\]"), "不支持 时:分:秒 格式"),
)
V2_CARD_TITLE_RE = re.compile(
    r"^###\s+(.+?)\s*[（(](核心必考|重点掌握|了解即可)[）)]\s*$")
V2_CARD_FIELD_RE = re.compile(r"^\s*[-*]\s*(定义|出现|依赖|易混)\s*[：:]\s*(.*)$")
V2_APPEAR_ITEM_RE = re.compile(r"(\d{1,2})\s*[（(]([^）)]*)[）)]")
V2_CORRESPOND_RE = re.compile(r"[（(]\s*对应\s*[：:]\s*([^）)]+?)\s*[）)]")
V2_DETAILS_OPEN_RE = re.compile(r"<details\b", re.IGNORECASE)
V2_DETAILS_CLOSE_RE = re.compile(r"</details>", re.IGNORECASE)
V2_ANSWER_RE = re.compile(r"^\s*\*\*答案：?\*\*\s*(.*)$")
V2_YESNO_RE = re.compile(r"^(?:是否|是不是|对吗)|吗\s*[？?]\s*$")
V2_NAV_BEGIN_RE = re.compile(r"<!--\s*NAV:BEGIN")
V2_NAV_END_RE = re.compile(r"<!--\s*NAV:END")
V2_H2_NUM_RE = re.compile(r"^##\s+(\d{1,2})\s*[.、．)）]")
V2_H2_TS_RE = re.compile(r"[（(](\d{1,3}):(\d{2})[）)]")
V2_DEEP_LINK_RE = re.compile(r"\[\d{1,3}:\d{2}\]\(https?://")
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


def rules_for(frontmatter: dict) -> str:
    """§4.1 版本分派：无 frontmatter / `v1` → v1（冻结规则），其余 → v2。"""
    v = (frontmatter.get("template_version") or "").strip()
    return "v1" if v in ("", "v1") else "v2"


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


def parse_concept_cards(lines: list[str], start: int, end: int,
                        fence_state: list[bool]) -> list[dict]:
    """解析 [start,end) 内的概念卡：### 概念名（标签）+ 定义/出现/依赖/易混 字段。"""
    cards: list[dict] = []
    cur: dict | None = None
    for i in range(start, end):
        if fence_state[i]:
            continue
        line = lines[i]
        tm = V2_CARD_TITLE_RE.match(line)
        if tm:
            cur = {"name": tm.group(1).strip(), "tag": tm.group(2),
                   "line": i + 1, "fields": {}}
            cards.append(cur)
            continue
        fmm = V2_CARD_FIELD_RE.match(line)
        if fmm and cur is not None and fmm.group(1) not in cur["fields"]:
            cur["fields"][fmm.group(1)] = (fmm.group(2).strip(), i + 1)
    return cards


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
                 "answer_anchor": False, "correspond": None}
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
            elif V2_DETAILS_CLOSE_RE.search(line):
                in_details = False
            elif q["has_answer"] and line.strip():
                q["extra_lines"] += 1
                if V2_ANCHOR_RE.search(line):
                    q["answer_anchor"] = True
    return questions


def validate_mermaid_blocks(lines: list[str], start: int,
                            end: int) -> tuple[int, list[str]]:
    """§3.3 mermaid 校验：fence 语言 / 首行 mindmap|graph|flowchart / mindmap 缩进。
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


def check_note_v2(text: str, note_path: Path, meta_path: Path | None,
                  txt_dir: Path | None) -> dict:
    """v2 门禁（规格 §4.2 通用 15 项 + §4.3 分型 L1~L12 / S1~S3）。

    errors/warnings 为对象数组 {"check","message","line"}（§4.5 破坏性变更，
    仅 v2 报告使用；v1 报告保持字符串数组）。
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

    # ---- 检查1：frontmatter 必填字段 [error] ----
    missing_fields = [f for f in ("template_version", "note_type", "platform",
                                  "source", "created") if not fm.get(f)]
    for f in missing_fields:
        err("1", f"frontmatter 缺少 {f} 字段")
    checks["frontmatter_fields"] = {"passed": not missing_fields,
                                    "missing": missing_fields}

    # ---- 检查2：note_type 取值 [error] ----
    if note_type and note_type not in ("lecture", "short"):
        err("2", f"note_type「{note_type}」非法，只能是 lecture / short")
    checks["note_type_valid"] = {"passed": note_type in ("lecture", "short"),
                                 "note_type": note_type or None}

    # ---- 检查3：每个 ## 小节（除 目录/参考时间戳）≥3 行实质内容 [error] ----
    sections = split_sections(text)
    sec_detail = {}
    for title, body in sections:
        if title is None:
            continue
        if any(k in title for k in SKIP_SECTION_KEYWORDS):
            continue
        n = len(substantive_lines(body))
        sec_detail[title] = n
        if n < 3:
            err("3", f"小节「{title}」实质内容不足（{n} 行非空文本，需至少 3 行）")
    checks["section_content"] = {"passed": not any(e["check"] == "3" for e in errors),
                                 "lines_per_section": sec_detail}

    # ---- 检查5（统计分段，坑①）+ 检查4：标注行扫描（仅听讲层、跳过代码块） ----
    card_head = find_h2_line(lines, "概念卡片")
    quiz_head = find_h2_line(lines, "自测题")
    listen_end = card_head if card_head is not None else (
        quiz_head if quiz_head is not None else len(lines))
    annotations: list[dict] = []
    for i in range(listen_end):
        if fence_state[i]:
            continue
        line = lines[i]
        bm = V2_BOLD_LIST_RE.match(line)
        if bm:
            tag = bm.group(1).strip()
            if tag in V2_TAGS:
                tm = V2_TAG_LINE_RE.match(line)
                if tm and tm.group(2):
                    annotations.append({
                        "line": i + 1, "tag": tag, "text": tm.group(2).strip(),
                        "anchor": bool(V2_ANCHOR_RE.search(line))})
                else:
                    err("4", f"标注行「**{tag}**」后缺少一个空格 + 非空要点句"
                             f"（第{i + 1}行）", i + 1)
            else:
                err("4", f"非白名单标注词「**{tag}**」（第{i + 1}行）：标签必须是 "
                         f"{'/'.join(V2_TAGS)} 之一", i + 1)
        for pat, reason in V2_BAD_TS_RES:
            if pat.search(line):
                err("4", f"[mm:ss] 格式非法（{reason}，第{i + 1}行）："
                         f"{line.strip()[:50]}", i + 1)
    n_tags = len(annotations)
    n_core = sum(1 for a in annotations if a["tag"] == "核心必考")
    checks["annotation_scope"] = {
        "passed": True, "annotation_count": n_tags, "core_count": n_core,
        "listen_layer_end_line": (listen_end + 1) if listen_end < len(lines) else None,
        "note": "标注行统计只作用于听讲层（## 概念卡片 或 ## 自测题 之前）并跳过代码块（检查5/坑①）"}

    # ---- 检查7：锚点覆盖率分拆（核心必考+重点掌握 ≥0.8；了解即可不参与）[error] ----
    core_imp = [a for a in annotations if a["tag"] in ("核心必考", "重点掌握")]
    anchored = sum(1 for a in core_imp if a["anchor"])
    if core_imp:
        cov = anchored / len(core_imp)
        if cov < 0.8:
            err("7", f"核心必考+重点掌握行的锚点覆盖率为 {anchored}/{len(core_imp)}"
                     f"（{cov:.0%}），低于 0.8（了解即可行不参与统计）")
        checks["anchor_coverage"] = {"passed": cov >= 0.8, "anchored": anchored,
                                     "total": len(core_imp), "ratio": round(cov, 2)}
    else:
        checks["anchor_coverage"] = {"passed": None, "anchored": 0, "total": 0,
                                     "note": "听讲层无 核心必考/重点掌握 行"}

    # ---- 检查8：核心必考数量 ≥1 [error] ----
    if n_core < 1:
        err("8", f"核心必考标注行为 0（听讲层共 {n_tags} 行标注行），至少需要 1 条核心必考")
    checks["core_min"] = {"passed": n_core >= 1, "core_count": n_core,
                          "total": n_tags}

    # ---- 检查9：核心必考占比 ≤1/3 [warning，仅 lecture；short 豁免] ----
    if note_type == "lecture" and n_tags > 0:
        ratio = n_core / n_tags
        if ratio > 0.34:
            warn("9", f"核心必考占标注行总数的 {n_core}/{n_tags}（{ratio:.0%}），"
                      "超过上限 1/3：全篇都是重点等于没有重点")
        checks["core_ratio"] = {"passed": ratio <= 0.34, "core": n_core,
                                "total": n_tags, "ratio": round(ratio, 4),
                                "exempt": False}
    else:
        checks["core_ratio"] = {"passed": None, "core": n_core, "total": n_tags,
                                "exempt": note_type == "short",
                                "note": "short 型豁免比例上限"
                                        if note_type == "short" else "无标注行或 note_type 非法"}

    # ---- --meta 读取（检查15 不依赖 meta，仅记录元信息） ----
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

    # ---- 检查6：时间戳白名单（±5s 按总秒数，可跨分钟）[warning 起步] ----
    txt_files: list[Path] = sorted(Path(txt_dir).glob("*.txt")) if txt_dir else []
    legal_sec: set[int] = set()
    for f in txt_files:
        for ln in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\[(\d{1,3}):(\d{2})\]", ln)
            if m:
                legal_sec.add(int(m.group(1)) * 60 + int(m.group(2)))
    if txt_dir is None:
        warn("6", "未提供 --txt-dir，无法构建转写稿时间戳白名单，跳过检查 6"
                  "（不静默通过：补齐参数后重跑）")
        checks["timestamp_whitelist"] = {"passed": None, "note": "缺 --txt-dir，跳过"}
    elif not legal_sec:
        warn("6", f"转写稿目录 {txt_dir} 下没有可解析的 [mm:ss] 时间行，"
                  "跳过检查 6（不静默通过）")
        checks["timestamp_whitelist"] = {"passed": None, "note": "txt 目录无时间行"}
    else:
        off_whitelist: list[str] = []
        for i, line in enumerate(lines):
            if fence_state[i]:
                continue
            for m in V2_ANCHOR_RE.finditer(line):
                mm_, ss_ = int(m.group(1)), int(m.group(2))
                t = mm_ * 60 + ss_
                if not any(abs(t - ls) <= 5 for ls in legal_sec):
                    ts = f"{mm_:02d}:{ss_:02d}"
                    off_whitelist.append(ts)
                    warn("6", f"[{ts}]（第{i + 1}行）不在转写稿时间戳白名单内"
                              "（±5s 容差，按总秒数比较、可跨分钟）", i + 1)
        checks["timestamp_whitelist"] = {"passed": not off_whitelist,
                                         "legal_seconds": len(legal_sec),
                                         "off_whitelist": off_whitelist}

    # ---- 检查15：笔记字数/转写稿字数 ∈ [0.03, 1.2] [warning] ----
    if txt_dir is None:
        warn("15", "未提供 --txt-dir，跳过字数比对（检查 15）")
        checks["length_ratio"] = {"passed": None, "note": "缺 --txt-dir，跳过"}
    else:
        note_chars = len(re.sub(r"\s+", "", text))
        txt_chars = sum(len(re.sub(r"\s+", "", f.read_text(encoding="utf-8")))
                        for f in txt_files)
        if txt_chars > 0:
            ratio = note_chars / txt_chars
            if not 0.03 <= ratio <= 1.2:
                warn("15", f"笔记字数/转写稿字数比值为 {ratio:.4f}（超出 0.03~1.2）："
                           f"{'笔记过简' if ratio < 0.03 else '笔记可疑地长'}，"
                           f"note_chars={note_chars}, transcript_chars={txt_chars}")
            checks["length_ratio"] = {"passed": 0.03 <= ratio <= 1.2,
                                      "ratio": round(ratio, 4),
                                      "note_chars": note_chars,
                                      "transcript_chars": txt_chars}
        else:
            warn("15", f"转写稿目录 {txt_dir} 下没有 .txt 文件，跳过字数比对")
            checks["length_ratio"] = {"passed": None, "note": "txt 目录为空"}

    # ---- 检查10/11：自测题数量与格式 ----
    questions: list[dict] = []
    if quiz_head is None:
        err("10", "缺少「自测题」章节")
        checks["quiz_count"] = {"passed": False, "count": 0, "found": False}
        checks["quiz_format"] = {"passed": False, "found": False}
    else:
        quiz_end = next_h2_after(lines, quiz_head + 1)
        questions = parse_quiz_questions(lines, quiz_head + 1, quiz_end)
        n_q = len(questions)
        want = (2, 3) if note_type == "short" else (5, 9)
        if not want[0] <= n_q <= want[1]:
            err("10", f"自测题数量为 {n_q}，要求 {want[0]}~{want[1]} 道"
                      f"（note_type={note_type or '缺失'}）")
        checks["quiz_count"] = {"passed": want[0] <= n_q <= want[1],
                                "count": n_q, "found": True}
        for q in questions:
            if not q["has_details"]:
                err("11", f"自测题 {q['no']} 缺少紧随其后的 <details> 折叠块", q["line"])
            elif not q["has_answer"]:
                err("11", f"自测题 {q['no']} 的 <details> 内缺少「**答案：**」开头的行",
                    q["line"])
            else:
                if q["extra_lines"] > 0:
                    warn("11", f"自测题 {q['no']} 的答案正文超过一行"
                               f"（答案后另有 {q['extra_lines']} 行非空文本）："
                               "答案不应变成第二篇笔记", q["line"])
                if not q["answer_anchor"]:
                    warn("11", f"自测题 {q['no']} 的答案缺少 [mm:ss] 证据锚点", q["line"])
            if V2_YESNO_RE.search(q["stem"]):
                warn("11", f"自测题 {q['no']} 疑似 yes/no 题干「{q['stem'][:30]}」",
                     q["line"])
        checks["quiz_format"] = {"passed": not any(e["check"] == "11" for e in errors),
                                 "count": n_q, "found": True}

    # ---- 检查12：代码块来源标注（仅语言白名单；坑②）+ 未闭合 fence [error] ----
    src_missing: list[tuple[int, str, int]] = []
    unclosed = False
    open_lang: str | None = None
    open_idx = -1
    i = 0
    while i < len(lines):
        m = V2_FENCE_LANG_RE.match(lines[i])
        if m:
            if open_lang is None:
                open_lang, open_idx = m.group(1).lower(), i
            else:
                if open_lang in V2_CODE_LANGS:
                    seg = lines[open_idx + 1:i]
                    after = lines[i + 1:i + 3]
                    if (not any("来源" in l for l in seg)
                            and not any("来源" in l for l in after)):
                        src_missing.append((open_idx + 1, open_lang, i + 1))
                open_lang = None
        i += 1
    if open_lang is not None:
        unclosed = True
    for open_line, lang, close_line in src_missing:
        err("12", f"{lang} 代码块（第{open_line}~{close_line}行）缺少「来源」标注"
                  "（块内注释或结束后 2 行内均可）", close_line)
    if unclosed:
        err("12", "存在未闭合的代码块（``` fence）")
    checks["code_sources"] = {"passed": not src_missing and not unclosed,
                              "missing": [l for l, _, _ in src_missing],
                              "unclosed_fence": unclosed,
                              "lang_whitelist": sorted(V2_CODE_LANGS)}

    # ---- 检查13：引用图片真实存在 [error] ----
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
        err("13", f"引用的图片不存在：{p}")
    checks["images"] = {"passed": not missing_imgs, "checked": len(referenced),
                        "missing": missing_imgs}

    # ---- 检查14：manifest 图注 keyword + 时间对齐（复用 check_image_manifest）[warning] ----
    manifest_path = note_path.parent / "images" / "manifest.json"
    im14 = check_image_manifest(note_path, manifest_path)
    for item in im14["mismatched"]:
        warn("14", f"图注交叉校验：{item['file']} {item['reason']}")
    checks["image_manifest_kw_time"] = {
        "passed": not im14["mismatched"], "checked": im14["checked"],
        "mismatched": [m["file"] for m in im14["mismatched"]],
        "time_skipped": im14["time_skipped"], "note": im14["note"]}

    # ---- 概念卡片解析（L 系列与 S2 共用） ----
    cards: list[dict] = []
    card_names: set[str] = set()
    if card_head is not None:
        card_sec_end = next_h2_after(lines, card_head + 1)
        cards = parse_concept_cards(lines, card_head + 1, card_sec_end, fence_state)
        card_names = {c["name"] for c in cards}
        # 概念卡标题格式（§3.2）：### 概念名（三词白名单标签）
        for i in range(card_head + 1, card_sec_end):
            if fence_state[i]:
                continue
            stripped = lines[i].lstrip()
            if stripped.startswith("### ") and not V2_CARD_TITLE_RE.match(lines[i]):
                err("L3", f"概念卡标题格式非法（应为「### 概念名"
                          f"（核心必考|重点掌握|了解即可）」）：{stripped.strip()[:40]}",
                    i + 1)

    # ---- 小节序号 → 时间区间（L4 的区间越界 warning 用） ----
    sec_nums: list[tuple[int, int | None]] = []
    for i, line in enumerate(lines):
        if V2_H2_NUM_RE.match(line):
            tm = V2_H2_TS_RE.search(line)
            sec_nums.append((int(V2_H2_NUM_RE.match(line).group(1)),
                             int(tm.group(1)) * 60 + int(tm.group(2)) if tm else None))
    sec_range: dict[int, tuple[int | None, int | None]] = {}
    for idx, (num, ts) in enumerate(sec_nums):
        end_ts = next((t2 for _, t2 in sec_nums[idx + 1:] if t2 is not None), None)
        sec_range[num] = (ts, end_ts)

    # ---- 检查10 的 covered 集合 + L9/L10（lecture） ----
    covered: set[str] = set()
    if note_type == "lecture":
        for q in questions:
            if q["correspond"] is None:
                err("L9", f"自测题 {q['no']} 缺少「（对应：概念名）」标记", q["line"])
            else:
                covered.add(q["correspond"])
                if q["correspond"] not in card_names:
                    err("L9", f"自测题 {q['no']} 的「（对应：{q['correspond']}）」"
                              "不存在于概念卡片集合（任意级别），疑为笔误", q["line"])
        checks["quiz_correspond"] = {
            "passed": not any(e["check"] == "L9" for e in errors),
            "covered": sorted(covered)}

    # ================= lecture 分型检查（L1~L12） =================
    if note_type == "lecture":
        # L1：存在 ## 概念卡片 章节 [error]
        if card_head is None:
            err("L1", "缺少「## 概念卡片」章节（lecture 必须有概念卡片）")
            checks["cards_exist"] = {"passed": False, "found": False, "count": 0}
        else:
            checks["cards_exist"] = {"passed": True, "found": True,
                                     "count": len(cards)}

        # L2：概念卡片 ≥2 张 [warning]
        if len(cards) < 2:
            warn("L2", f"概念卡片仅 {len(cards)} 张，建议 ≥ 2 张")
        checks["card_count"] = {"passed": len(cards) >= 2, "count": len(cards)}

        core_cards = [c for c in cards if c["tag"] == "核心必考"]
        core_card_names = [c["name"] for c in core_cards]

        # L3/L4/L5/L6：卡片字段（§3.2）
        for c in cards:
            defs = c["fields"].get("定义")
            appear = c["fields"].get("出现")
            if not defs or not defs[0]:
                err("L3", f"概念卡「{c['name']}」缺少非空「定义」字段", c["line"])
            if not appear or not appear[0]:
                err("L3", f"概念卡「{c['name']}」缺少非空「出现」字段", c["line"])
            else:
                items = V2_APPEAR_ITEM_RE.findall(appear[0])
                if not items:
                    err("L3", f"概念卡「{c['name']}」的「出现」格式非法"
                              "（应为「小节序号（mm:ss）、小节序号（mm:ss）」）", appear[1])
                else:
                    secs_seen: list[int] = []
                    for sec, ts in items:
                        sec_i = int(sec)
                        secs_seen.append(sec_i)
                        if sec_i not in sec_range:
                            err("L4", f"概念卡「{c['name']}」的「出现」引用小节 {sec}，"
                                      "但笔记中不存在该小节序号", appear[1])
                            continue
                        ts_clean = ts.strip().strip("[]")
                        if not ts_clean:
                            continue
                        tmm = re.fullmatch(r"(\d{1,3}):(\d{2})", ts_clean)
                        if not tmm:
                            err("L4", f"概念卡「{c['name']}」的「出现」时间戳"
                                      f"「{ts_clean}」格式非法", appear[1])
                            continue
                        t = int(tmm.group(1)) * 60 + int(tmm.group(2))
                        start_ts, end_ts = sec_range[sec_i]
                        if (start_ts is not None
                                and (t < start_ts
                                     or (end_ts is not None and t >= end_ts))):
                            warn("L4", f"概念卡「{c['name']}」的「出现」时间戳 "
                                       f"{ts_clean} 落在小节 {sec} 的时间区间之外",
                                 appear[1])
                    if len(set(secs_seen)) < 2:
                        warn("L5", f"概念卡「{c['name']}」的「出现」仅 "
                                   f"{len(set(secs_seen))} 个小节：单节概念不必单独成卡，"
                                   "考虑并入小节", appear[1])
            dep = c["fields"].get("依赖")
            if dep and dep[0]:
                for name in re.split(r"[、,，;；]", dep[0]):
                    name = name.strip()
                    if name and name not in card_names:
                        warn("L6", f"概念卡「{c['name']}」的「依赖」引用「{name}」"
                                   "不在概念卡片集合内", dep[1])
            conf = c["fields"].get("易混")
            if conf and conf[0]:
                conf_name = re.split(r"——|--", conf[0])[0].strip()
                if conf_name and conf_name not in card_names:
                    warn("L6", f"概念卡「{c['name']}」的「易混」引用「{conf_name}」"
                               "不在概念卡片集合内", conf[1])
        checks["card_fields"] = {
            "passed": not any(e["check"] == "L3" for e in errors), "cards": len(cards)}

        # L7：本讲知识地图 + 合规 mermaid 块 [error]
        km_head = find_h2_line(lines, "知识地图")
        if km_head is None:
            err("L7", "缺少「本讲知识地图」章节（lecture 必须有 mermaid 知识地图）")
            checks["knowledge_map"] = {"passed": False, "found": False}
        else:
            km_end = next_h2_after(lines, km_head + 1)
            blocks, problems = validate_mermaid_blocks(lines, km_head + 1, km_end)
            if blocks == 0:
                err("L7", "「本讲知识地图」章节内没有 mermaid 代码块")
            for p in problems:
                err("L7", f"知识地图 mermaid 块校验失败：{p}")
            checks["knowledge_map"] = {"passed": blocks > 0 and not problems,
                                       "mermaid_blocks": blocks,
                                       "problems": problems}

        # L8：参考时间戳 ≥3 条深链 [warning]
        nav_head = find_h2_line(lines, "参考时间戳")
        if nav_head is None:
            warn("L8", "缺少「参考时间戳」章节（lecture 建议保留，深链 ≥3 条）")
            checks["nav_timestamps"] = {"passed": False, "found": False}
        else:
            nav_end = next_h2_after(lines, nav_head + 1)
            deep = sum(1 for j in range(nav_head + 1, nav_end)
                       if V2_DEEP_LINK_RE.search(lines[j]))
            if deep < 3:
                warn("L8", f"「参考时间戳」仅 {deep} 条深链，建议 ≥ 3 条")
            checks["nav_timestamps"] = {"passed": deep >= 3, "deep_links": deep}

        # L10：核心必考卡片 ⊆ 被（对应：…）覆盖的集合 [error]
        uncovered = [name for name in core_card_names if name not in covered]
        for name in uncovered:
            err("L10", f"核心必考卡片「{name}」未被任何自测题的「（对应：…）」覆盖")
        checks["core_covered"] = {"passed": not uncovered,
                                  "core_cards": core_card_names,
                                  "uncovered": uncovered}

        # L11：参考时间戳位于 NAV:BEGIN/NAV:END 之间 [warning]
        begins = [i for i, l in enumerate(lines) if V2_NAV_BEGIN_RE.search(l)]
        ends = [i for i, l in enumerate(lines) if V2_NAV_END_RE.search(l)]
        if nav_head is not None:
            if not begins and not ends:
                warn("L11", "缺少 NAV:BEGIN/END 生成区标记（应由 scripts/note_nav.py 生成）")
            elif not begins or not ends:
                warn("L11", "NAV:BEGIN/END 标记不配对")
            elif not (begins[0] < nav_head < ends[0]):
                warn("L11", "「参考时间戳」未位于 NAV:BEGIN 与 NAV:END 标记之间")
        checks["nav_block"] = {"passed": not any(e["check"] == "L11" for e in errors),
                               "begin_lines": [b + 1 for b in begins],
                               "end_lines": [b + 1 for b in ends]}

        # L12：听讲层核心必考行包含某张核心必考卡的卡名（子串）[warning]
        if core_card_names:
            for a in annotations:
                if a["tag"] == "核心必考" and not any(
                        nm in a["text"] for nm in core_card_names):
                    warn("L12", f"核心必考行「{a['text'][:30]}」未包含任何核心必考卡片名"
                                "（正文与卡片需呼应，防止绕过覆盖检查）", a["line"])
        checks["core_line_card_name"] = {
            "passed": not any(w["check"] == "L12" for w in warnings),
            "core_card_names": core_card_names}

    # ================= short 分型检查（S1~S3） =================
    if note_type == "short":
        # S1：标注行数 ∈ [2, 6] [error]
        if not 2 <= n_tags <= 6:
            err("S1", f"标注行数为 {n_tags}，short 型要求 2~6 行（下限防敷衍，上限防注水）")
        checks["tag_count"] = {"passed": 2 <= n_tags <= 6, "count": n_tags}
        # S2：概念卡片出现时 ≤3 张 [warning]
        if cards:
            if len(cards) > 3:
                warn("S2", f"概念卡片 {len(cards)} 张，short 型出现时 ≤ 3 张")
            checks["card_count_limit"] = {"passed": len(cards) <= 3,
                                          "count": len(cards)}
        else:
            checks["card_count_limit"] = {"passed": None,
                                          "note": "short 无概念卡片（可省）"}
        # S3：知识地图与参考时间戳可省（不检查）；检查9 对 short 豁免
        checks["short_exemptions"] = {
            "passed": True,
            "note": "知识地图与参考时间戳可省（不检查）；比例上限（检查9）对 short 豁免"}

    return {"ok": not errors, "template_version": "v2",
            "note_type": note_type or None,
            "errors": errors, "warnings": warnings, "checks": checks}


def check_note_dispatch(text: str, note_path: Path, meta_path: Path | None = None,
                        txt_dir: Path | None = None) -> dict:
    """§4.1 版本分派入口：按 frontmatter 的 template_version 选择 v1/v2 规则集。

    v1：check_note（冻结，报告保持字符串数组），仅追加 template_version /
    note_type 两个信息字段；v2：check_note_v2（对象数组报告）。
    main 与测试共用本函数，保证"测试跑的就是 CLI 跑的"。
    """
    fm = parse_frontmatter(text)
    if rules_for(fm) == "v1":
        report = check_note(text, note_path, meta_path, txt_dir)
        report["template_version"] = "v1"
        report["note_type"] = fm.get("note_type") or None
        return report
    return check_note_v2(text, note_path, meta_path, txt_dir)


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
        description="课堂笔记质量门禁（按 frontmatter template_version 分派 v1/v2 规则）")
    ap.add_argument("note", help="笔记 .md 路径")
    ap.add_argument("--meta", help="metadata.json 路径（与 --txt-dir 联用做字数比对）")
    ap.add_argument("--txt-dir", help="转写稿 .txt 所在目录（v2 检查6/15 需要）")
    args = ap.parse_args()

    note_path = Path(args.note)
    if not note_path.exists():
        print_json_report({"ok": False, "template_version": None, "note_type": None,
                           "errors": [f"笔记文件不存在: {note_path}"],
                           "warnings": [], "checks": {}})
        sys.exit(1)
    text = note_path.read_text(encoding="utf-8")
    meta_path = Path(args.meta) if args.meta else None
    txt_dir = Path(args.txt_dir) if args.txt_dir else None

    # §4.1 版本分派：无 frontmatter / v1 → v1 冻结规则（报告保持字符串数组，
    # 仅追加 template_version/note_type 两个信息字段）；v2 → 新规则集（对象数组）。
    report = check_note_dispatch(text, note_path, meta_path, txt_dir)
    print_json_report(report, indent=2)
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
