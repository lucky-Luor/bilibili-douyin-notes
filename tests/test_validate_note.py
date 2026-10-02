# -*- coding: utf-8 -*-
"""validate_note.py 五类报告（errors/warnings）的离线测试。"""
import json

import validate_note


# ---------- 合成笔记骨架 ----------

def good_note() -> str:
    return """---
template_version: v1
---

# 课堂笔记 1：测试主题

> 课程来源：测试视频（B站 BV1xx411c7mD，UP主：测试UP，共1个小节约1分钟）

## 目录

- 第一节

---

## 第一节（1分钟）

**核心思想：**测试用。

- ★★★ 核心要点一 [00:10]
- ★★★ 核心要点二 [00:20]
- ★★ 次要要点 [00:30]

```python
print("hello")
```

来源：P01 [00:10]

补充正文一行。

## 本讲知识地图

- 知识点A
- 知识点B
- 知识点C

## 自测题（复习用）

1. 问题一是什么？
2. 问题二是什么？
3. 问题三是什么？
4. 问题四是什么？
5. 问题五是什么？

## 参考时间戳

- [00:10] 开场
- [00:20] 重点
- [00:30] 总结
"""


def write_note(tmp_path, text, name="note.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def check(tmp_path, text):
    p = write_note(tmp_path, text)
    return validate_note.check_note(text, p, None, None)


# ---------- 基线：好笔记全过 ----------

def test_good_note_passes(tmp_path):
    report = check(tmp_path, good_note())
    assert report["errors"] == []
    assert report["warnings"] == []
    assert report["ok"] is True


# ---------- 一、★ 行总数 < 3 [error] ----------

def test_too_few_star_lines_is_error(tmp_path):
    text = good_note().replace("- ★★ 次要要点 [00:30]\n", "")
    report = check(tmp_path, text)
    assert any("★ 行总数" in e for e in report["errors"])
    assert report["ok"] is False


# ---------- 二、★★★ 行数越界 [error] ----------

def test_too_few_three_star_is_error(tmp_path):
    text = good_note().replace("- ★★★ 核心要点二 [00:20]", "- ★ 核心要点二 [00:20]")
    report = check(tmp_path, text)
    assert any("★★★ 行数" in e for e in report["errors"])


def test_too_many_three_star_is_error(tmp_path):
    text = good_note().replace(
        "- ★★ 次要要点 [00:30]",
        "- ★★★ 次要要点 [00:30]\n- ★★★ 次要要点三 [00:35]\n"
        "- ★★★ 次要要点四 [00:40]")  # ★★★ 共 5 个，超出 2~4
    report = check(tmp_path, text)
    assert any("★★★ 行数" in e for e in report["errors"])


# ---------- 三、★ 行锚点覆盖率 < 0.8 [warning] ----------

def test_low_anchor_coverage_warns(tmp_path):
    text = good_note().replace("- ★★ 次要要点 [00:30]", "- ★★ 次要要点")
    report = check(tmp_path, text)
    assert any("锚点覆盖率" in w for w in report["warnings"])
    assert report["errors"] == []  # 只是 warning，不算 error


# ---------- 四、图注错位 [warning，需 manifest] ----------

def test_caption_mismatch_warns(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "p01_a.png").write_bytes(b"\x89PNG fake")
    (tmp_path / "images" / "manifest.json").write_text(json.dumps({
        "frames": [{"file": "p01_a.png", "caption": "循环依赖的三级缓存图示"}]
    }, ensure_ascii=False), encoding="utf-8")
    text = good_note().replace(
        "补充正文一行。",
        "![BeanFactory 继承体系](images/p01_a.png)\n\n补充正文一行。")
    report = check(tmp_path, text)
    assert any("图注可能错位" in w for w in report["warnings"])


def test_caption_match_no_warning(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "p01_a.png").write_bytes(b"\x89PNG fake")
    (tmp_path / "images" / "manifest.json").write_text(json.dumps({
        "frames": [{"file": "p01_a.png", "caption": "循环依赖的三级缓存图示"}]
    }, ensure_ascii=False), encoding="utf-8")
    text = good_note().replace(
        "补充正文一行。",
        "![循环依赖图示](images/p01_a.png)\n\n补充正文一行。")
    report = check(tmp_path, text)
    assert not any("图注可能错位" in w for w in report["warnings"])


def test_missing_image_file_is_error(tmp_path):
    text = good_note().replace(
        "补充正文一行。", "![不存在](images/ghost.png)\n\n补充正文一行。")
    report = check(tmp_path, text)
    assert any("图片不存在" in e for e in report["errors"])


# ---------- 五、template_version 缺失 [warning] ----------

def test_missing_template_version_warns(tmp_path):
    text = good_note().replace("template_version: v1\n", "")
    report = check(tmp_path, text)
    assert any("template_version" in w for w in report["warnings"])


def test_no_frontmatter_warns(tmp_path):
    text = good_note().replace("---\ntemplate_version: v1\n---\n", "")
    assert "template_version" not in text.split("#")[0]
    report = check(tmp_path, text)
    assert any("template_version" in w for w in report["warnings"])


# ---------- 其他门禁（结构性 error） ----------

def test_missing_knowledge_map_is_error(tmp_path):
    report = check(tmp_path, good_note().replace("## 本讲知识地图", "## 知识图谱X"))
    assert any("知识地图" in e for e in report["errors"])


def test_quiz_count_out_of_range_is_error(tmp_path):
    text = good_note().replace("5. 问题五是什么？\n", "")  # 只剩 4 道，低于 5
    report = check(tmp_path, text)
    assert any("自测题编号题目数量" in e for e in report["errors"])


def test_code_block_without_source_is_error(tmp_path):
    text = good_note().replace("来源：P01 [00:10]\n", "")
    report = check(tmp_path, text)
    assert any("来源」标注" in e for e in report["errors"])
