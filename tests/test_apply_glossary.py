# -*- coding: utf-8 -*-
"""apply_glossary.py 的离线测试：dry-run / --apply / .bak / 幂等 / 词表加载。"""
import json

import apply_glossary


GLOSSARY_JSON = {"map": {
    "全线": "权限（Reactive权限例子语境）",
    "art component": "@Component",
    "Stutter": "Starter",
}}


def _write_glossary(tmp_path):
    p = tmp_path / "glossary.json"
    p.write_text(json.dumps(GLOSSARY_JSON, ensure_ascii=False), encoding="utf-8")
    return p


def _write_doc(tmp_path, text="这一节讲 Reactive 的全线校验，用 art component 注解。\n"):
    p = tmp_path / "doc.txt"
    p.write_text(text, encoding="utf-8")
    return p


# ---------- 词表加载 ----------

def test_load_glossary_takes_main_word_before_paren(tmp_path):
    g = apply_glossary.load_glossary(_write_glossary(tmp_path))
    assert g["全线"] == "权限"  # 括号内只是语境说明


def test_load_glossary_md_arrow_format(tmp_path):
    p = tmp_path / "glossary.md"
    p.write_text("# 注释行\n错法A→正确B\n", encoding="utf-8")
    assert apply_glossary.load_glossary(p) == {"错法A": "正确B"}


def test_build_pattern_longest_key_first(tmp_path):
    g = {"购语言": "Java", "购语": "Ja"}
    pat = apply_glossary.build_pattern(g)
    m = pat.search("这里说购语言")
    assert m.group(0) == "购语言"  # 长键优先


# ---------- dry-run（默认，不改文件） ----------

def test_dry_run_reports_without_modifying(tmp_path, capsys):
    gp = _write_glossary(tmp_path)
    doc = _write_doc(tmp_path)
    original = doc.read_text(encoding="utf-8")
    g = apply_glossary.load_glossary(gp)
    pat = apply_glossary.build_pattern(g)
    summary = apply_glossary.process_file(doc, g, pat, apply=False)
    assert summary["hits"] == 2       # 全线 / art component
    assert summary["replaced"] == 0
    assert summary["backup"] is None
    assert doc.read_text(encoding="utf-8") == original  # 原文件未动
    out = capsys.readouterr().out
    assert '"全线" -> 权限' in out


# ---------- --apply（.bak 备份 + 原子替换） ----------

def test_apply_replaces_and_creates_bak(tmp_path):
    gp = _write_glossary(tmp_path)
    doc = _write_doc(tmp_path)
    original = doc.read_text(encoding="utf-8")
    g = apply_glossary.load_glossary(gp)
    pat = apply_glossary.build_pattern(g)
    summary = apply_glossary.process_file(doc, g, pat, apply=True)
    assert summary["replaced"] == 2
    bak = tmp_path / "doc.txt.bak"
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == original
    new_text = doc.read_text(encoding="utf-8")
    assert "权限" in new_text and "@Component" in new_text
    assert "全线" not in new_text and "art component" not in new_text
    assert not (tmp_path / "doc.txt.tmp").exists()  # 原子写无残留


def test_apply_is_idempotent(tmp_path):
    gp = _write_glossary(tmp_path)
    doc = _write_doc(tmp_path)
    g = apply_glossary.load_glossary(gp)
    pat = apply_glossary.build_pattern(g)
    apply_glossary.process_file(doc, g, pat, apply=True)
    summary2 = apply_glossary.process_file(doc, g, pat, apply=True)
    assert summary2["hits"] == 0  # 第二趟无命中（替换结果不再被匹配）


def test_case_insensitive_match(tmp_path):
    gp = tmp_path / "g.json"
    gp.write_text(json.dumps({"map": {"Stutter": "Starter"}}), encoding="utf-8")
    doc = tmp_path / "d.txt"
    doc.write_text("use the STUTTER dependency\n", encoding="utf-8")
    g = apply_glossary.load_glossary(gp)
    pat = apply_glossary.build_pattern(g)
    hits, new_text = apply_glossary.scan_text(doc.read_text(encoding="utf-8"), g, pat)
    assert len(hits) == 1
    assert "Starter" in new_text


def test_nearest_ts_placeholder_before_first_stamp():
    positions, stamps = apply_glossary.build_ts_index("前文 [00:10] 全线 后文")
    assert apply_glossary.nearest_ts(positions, stamps, 6) == "00:10"
    assert apply_glossary.nearest_ts(positions, stamps, 0) == "--:--"
