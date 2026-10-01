#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes 笔记质量门禁：校验生成的课堂笔记是否符合模板规范。

用法:
    python validate_note.py <笔记.md> [--meta <metadata.json>] [--txt-dir <转写稿目录>]

检查项（errors 必须为 0 才通过；warnings 只提示）:
    1. 每个二级小节（##）都有实质内容：除"目录/参考时间戳"外，正文至少 3 行非空文本  [error]
    2. 存在"知识地图"章节且非空                                                    [error]
    3. 存在"自测题"章节，且编号题目数量在 5~9                                       [error]
    4. 所有含 ★ 的行：★ 数量在 1~3，且星号后有要点句                                 [error]
    5. 所有代码块（``` fence）结束其后 2 行内出现"来源"标注（子项目名或 [mm:ss]）      [error]
    6. 笔记里引用的 images/*.png（相对笔记路径解析）全部真实存在                      [error]
    7. 笔记中 [mm:ss] 证据锚点总数 >= 3                                             [warning]
    8. 传 --meta --txt-dir 时：笔记字数/转写稿总字数 比值在 0.03~1.2                 [warning]

输出: stdout 打印 JSON（ok / errors / warnings / checks 各检查项明细）。
全过 exit 0；有 error exit 1。
"""
import argparse
import json
import re
import sys
from pathlib import Path

ANCHOR_RE = re.compile(r"\[\d{1,2}:\d{2}\]")
IMG_MD_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
IMG_BARE_RE = re.compile(r"(?<!\()\bimages/[\w./-]+\.(?:png|jpe?g|gif)\b")
NUM_ITEM_RE = re.compile(r"^\s*\d{1,2}\s*[.、．)）]\s*\S")
FENCE_RE = re.compile(r"^\s*```")
H2_RE = re.compile(r"^##\s+(.+?)\s*$")
SKIP_SECTION_KEYWORDS = ("目录", "参考时间戳")


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

    # ---- 检查4 + 5：★ 行规范 / 代码块来源标注（需跟踪 fence 状态） ----
    lines = text.splitlines()
    star_lines = 0
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
            rest = re.sub(r"^[\s\->*]*", "", line)
            rest = rest.replace("★", "", cnt).strip()
            if cnt > 3:
                errors.append(f"★ 数量为 {cnt}（第{i + 1}行），只能在 1~3：{line.strip()[:50]}")
            elif cnt == 0:
                pass  # 不会发生（含★才进来）
            if not rest:
                errors.append(f"★ 行缺少要点句（第{i + 1}行）：{line.strip()[:50]}")
        i += 1
    if fence_open:
        errors.append("存在未闭合的代码块（``` fence）")
    checks["stars"] = {"passed": not any("★" in e for e in errors), "star_lines": star_lines}

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
        p = m.group(1)
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

    # ---- 检查7：证据锚点总数 >= 3（warning） ----
    anchors = len(ANCHOR_RE.findall(text))
    if anchors < 3:
        warnings.append(f"[mm:ss] 证据锚点仅 {anchors} 个，建议 >= 3（时间戳须来自转写稿真实时间行）")
    checks["anchors"] = {"passed": anchors >= 3, "count": anchors}

    # ---- 检查8：笔记字数 / 转写稿总字数 比值 0.03~1.2（warning） ----
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
