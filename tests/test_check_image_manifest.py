# -*- coding: utf-8 -*-
"""validate_note.check_image_manifest（检查14，B2 新增）的离线测试。

覆盖规格 §4.2 检查14：
    - alt 以 manifest keyword 开头，或 keyword ∈ alt；
    - |img.t − 锚定行的 mm:ss| ≤ 60，锚定行 = 图片上方最近标注行（间距 ≤5 行）；
    - 5 行内无标注行 / 标注行无时间戳 → 跳过该图的时间项；
    - 无 manifest → 整体跳过。
"""
import json

import validate_note


def make_note(star="- ★★★ 循环依赖的三级缓存 [00:10]",
              gap=1,
              alt="循环依赖图示",
              img="images/p01_a.png"):
    """star 行与图片之间隔 gap 个空行。"""
    return (f"# 笔记\n\n{star}\n" + "\n" * gap + f"![{alt}]({img})\n")


def make_manifest(keyword="循环依赖", t=15):
    return {"frames": [{"file": "p01_a.png", "caption": "循环依赖的图示",
                        "keyword": keyword, "t": t}]}


def setup_case(tmp_path, note_text, manifest=None):
    (tmp_path / "note.md").write_text(note_text, encoding="utf-8")
    note_path = tmp_path / "note.md"
    manifest_path = tmp_path / "images" / "manifest.json"
    if manifest is not None:
        manifest_path.parent.mkdir(exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False),
                                 encoding="utf-8")
    return note_path, manifest_path


# ---------- keyword 项 ----------

def test_keyword_prefix_match_ok(tmp_path):
    np_, mp = setup_case(tmp_path, make_note(), make_manifest())
    res = validate_note.check_image_manifest(np_, mp)
    assert res["mismatched"] == []
    assert res["checked"] == 1


def test_keyword_contained_in_alt_ok(tmp_path):
    np_, mp = setup_case(tmp_path, make_note(alt="Spring 的循环依赖讲解"),
                         make_manifest())
    res = validate_note.check_image_manifest(np_, mp)
    assert res["mismatched"] == []


def test_keyword_absent_is_mismatch(tmp_path):
    np_, mp = setup_case(tmp_path, make_note(alt="完全无关的画面"),
                         make_manifest())
    res = validate_note.check_image_manifest(np_, mp)
    assert len(res["mismatched"]) == 1
    assert res["mismatched"][0]["file"] == "p01_a.png"
    assert "循环依赖" in res["mismatched"][0]["reason"]


def test_missing_keyword_field_skips_keyword_check(tmp_path):
    """旧产物无 keyword 字段 → keyword 项不判定（兼容）。"""
    np_, mp = setup_case(tmp_path, make_note(alt="完全无关的画面"),
                         {"frames": [{"file": "p01_a.png", "t": 15}]})
    res = validate_note.check_image_manifest(np_, mp)
    assert res["mismatched"] == []


# ---------- 时间项 ----------

def test_time_within_tolerance_ok(tmp_path):
    np_, mp = setup_case(tmp_path, make_note(), make_manifest(t=70))  # |70-10|=60 ≤ 60
    res = validate_note.check_image_manifest(np_, mp)
    assert res["mismatched"] == []


def test_time_drift_beyond_60s_is_mismatch(tmp_path):
    np_, mp = setup_case(tmp_path, make_note(), make_manifest(t=200))
    res = validate_note.check_image_manifest(np_, mp)
    assert len(res["mismatched"]) == 1
    assert "200" in res["mismatched"][0]["reason"]


def test_no_anchor_line_within_5_lines_skips_time(tmp_path):
    # ★ 行与图片隔 6 行 → 超出 5 行间距，时间项跳过（即使 t 偏差大也不报）
    np_, mp = setup_case(tmp_path, make_note(gap=6), make_manifest(t=200))
    res = validate_note.check_image_manifest(np_, mp)
    assert res["mismatched"] == []
    assert res["time_skipped"] == 1


def test_anchor_line_without_timestamp_skips_time(tmp_path):
    np_, mp = setup_case(tmp_path, make_note(star="- ★★★ 无锚点的要点"),
                         make_manifest(t=200))
    res = validate_note.check_image_manifest(np_, mp)
    assert res["mismatched"] == []
    assert res["time_skipped"] == 1


def test_v2_tag_line_works_as_anchor(tmp_path):
    """预留分支：v2 三词文字标签行（加粗/全角括号）同样可作为锚定行。"""
    for star in ("- **核心必考** 控制反转 [00:10]",
                 "- （重点掌握）三级缓存 [00:10]"):
        np_, mp = setup_case(tmp_path, make_note(star=star), make_manifest(t=200))
        res = validate_note.check_image_manifest(np_, mp)
        assert len(res["mismatched"]) == 1  # 时间项生效（t=200 偏差大被报）


def test_image_inside_code_block_ignored(tmp_path):
    note = "# 笔记\n\n```\n![无关](images/p01_a.png)\n```\n"
    np_, mp = setup_case(tmp_path, note, make_manifest())
    res = validate_note.check_image_manifest(np_, mp)
    assert res["checked"] == 0
    assert res["mismatched"] == []


# ---------- 无 manifest / 注册通道 ----------

def test_no_manifest_skips(tmp_path):
    np_, mp = setup_case(tmp_path, make_note(), None)
    res = validate_note.check_image_manifest(np_, mp)
    assert res["checked"] == 0
    assert res["mismatched"] == []
    assert res["note"]


def test_registered_into_check_note_warnings(tmp_path):
    """check_note 中通过现有 warning 通道输出检查14 结果。"""
    note = ("---\ntemplate_version: v1\n---\n\n# 笔记\n\n## 第一节\n\n"
            "- ★★★ 循环依赖的三级缓存 [00:10]\n\n![无关画面](images/p01_a.png)\n\n"
            "正文一二三四五六七。\n\n## 本讲知识地图\n\n- A\n- B\n- C\n\n"
            "## 自测题（复习用）\n\n1. 一？\n2. 二？\n3. 三？\n4. 四？\n5. 五？\n\n"
            "## 参考时间戳\n\n- [00:10] 开场\n- [00:20] 中段\n- [00:30] 末尾\n")
    (tmp_path / "note.md").write_text(note, encoding="utf-8")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "manifest.json").write_text(
        json.dumps(make_manifest(t=200), ensure_ascii=False), encoding="utf-8")
    report = validate_note.check_note(note, tmp_path / "note.md", None, None)
    assert any("检查14" in w for w in report["warnings"])
