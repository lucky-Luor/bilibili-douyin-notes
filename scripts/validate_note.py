#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes 笔记质量门禁：校验生成的课堂笔记是否符合模板规范。

用法:
    python validate_note.py <笔记.md> [--meta <metadata.json>] [--txt-dir <转写稿目录>]

检查项（errors 必须为 0 才通过；warnings 只提示）:
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


def main():
    ap = argparse.ArgumentParser(description="课堂笔记质量门禁")
    ap.add_argument("note", help="笔记 .md 路径")
    ap.add_argument("--meta", help="metadata.json 路径（与 --txt-dir 联用做字数比对）")
    ap.add_argument("--txt-dir", help="转写稿 .txt 所在目录")
    args = ap.parse_args()

    note_path = Path(args.note)
    if not note_path.exists():
        print(json.dumps({"ok": False, "errors": [f"笔记文件不存在: {note_path}"],
                          "warnings": [], "checks": {}}, ensure_ascii=False))
        sys.exit(1)
    text = note_path.read_text(encoding="utf-8")
    meta_path = Path(args.meta) if args.meta else None
    txt_dir = Path(args.txt_dir) if args.txt_dir else None

    report = check_note(text, note_path, meta_path, txt_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
