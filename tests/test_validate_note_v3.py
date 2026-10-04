# -*- coding: utf-8 -*-
"""validate_note.py v3 门禁（正文全面禁时刻 / 时间索引 NAV 区 / 标签废除）的离线测试。

覆盖：V3-E1~E7 每条规则至少一正一反例、lecture/short 分型差异、B站/抖音平台
差异（抖音纯文本条目不 warning）、保留检查（frontmatter/小节内容/核心思想/
mermaid/图片/manifest/字数比）与版本分派回归。
v1/v2 冻结回归由 test_validate_note.py / test_validate_note_v2.py（零修改）承担。
"""
import json
import subprocess
import sys
from pathlib import Path

import validate_note

ROOT = Path(__file__).resolve().parents[1]


def check_v3(tmp_path, text, txt_dir=None, name="note.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return validate_note.check_note_v3(text, p, None, txt_dir)


def check_dispatch(tmp_path, text, txt_dir=None, name="note.md"):
    """走真正的版本分派入口（与 CLI main 同一条路径）。"""
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return validate_note.check_note_dispatch(text, p, None, txt_dir)


def errs_of(report, cid):
    """取某检查编号的 error 对象列表。"""
    return [e for e in report["errors"] if e["check"] == cid]


def warns_of(report, cid):
    return [w for w in report["warnings"] if w["check"] == cid]


# ---------- v3 lecture 合成笔记（正文零时刻；时间戳只在 frontmatter 与 NAV 区） ----------

def lecture_note() -> str:
    return """---
template_version: v3
note_type: lecture
platform: bilibili
bvid: BV1xx411c7mD
source: https://www.bilibili.com/video/BV1xx411c7mD
created: 2026-10-03
duration: "42:10"
---

# 课堂笔记 03：Spring 容器与 Bean 生命周期

> 课程来源：Spring 容器与 Bean 生命周期（B站 BV1xx411c7mD，UP主：测试UP）

## 目录

- [1. 第一节](#1-第一节)
- [2. 第二节](#2-第二节)

---

## 1. 第一节

**核心思想：** 控制反转与依赖注入是同一件事的两种说法。

- 控制反转把对象的创建权交给容器，依赖注入是它的实现手段
- BeanFactory 惰性实例化，ApplicationContext 默认预实例化单例
- Lombok 的 @RequiredArgsConstructor 可替代手写构造注入

```java
// 来源：仓库 demo/OrderService.java
@Service
public class OrderService { }
```

## 2. 第二节

**核心思想：** 两者是同一继承体系的不同完成度。

- ApplicationContext 在启动时预实例化全部单例
- 两者都实现 BeanFactory 接口，前者是后者的超集
- 容器启动流程分为扫描、注册、实例化三步

## 本讲知识地图

```mermaid
mindmap
  root((Spring 容器))
    三大特性
      控制反转
      依赖注入
    容器实现
      ApplicationContext
```

## 概念卡片

### 控制反转

- 定义：把对象创建与依赖装配的控制权从代码转移到容器
- 出现：1、2
- 依赖：
- 易混：

### ApplicationContext

- 定义：BeanFactory 的完整实现，启动即预实例化全部单例
- 出现：1、2
- 依赖：BeanFactory
- 易混：BeanFactory——预实例化 vs 惰性加载

### BeanFactory

- 定义：Spring 最基础的容器实现
- 出现：1、2
- 依赖：
- 易混：

## 自测题（复习用）

1. 在需要启动即失败的场景下，为什么选 ApplicationContext？（对应：ApplicationContext）
<details><summary>答案</summary>

**答案：** ApplicationContext 在启动时预实例化全部单例，配置错误启动即暴露。

</details>

2. 解释控制反转与依赖注入的关系。（对应：控制反转）
<details><summary>答案</summary>

**答案：** 控制反转是设计原则，依赖注入是它的实现手段。

</details>

3. BeanFactory 与 ApplicationContext 有何区别？（对应：BeanFactory）
<details><summary>答案</summary>

**答案：** 前者惰性实例化，后者启动即预实例化。

</details>

4. 依赖注入有哪些常见形式？（对应：控制反转）
<details><summary>答案</summary>

**答案：** 构造器注入、字段注入、Setter 注入。

</details>

<!-- NAV:BEGIN 由 scripts/note_nav.py 生成，请勿手工编辑 -->
## 视频时间索引

- [08:12](https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=492) 1. 第一节
- [16:02](https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=962) 2. 第二节
<!-- NAV:END -->
"""


def short_note() -> str:
    """v3 short（抖音）：时间索引整节省略；自测题不带（对应：…）（short 可选）。"""
    return """---
template_version: v3
note_type: short
platform: douyin
video_id: 7301234567890123456
source: https://www.douyin.com/video/7301234567890123456
created: 2026-10-03
duration: "00:59"
---

# 笔记：一分钟看懂三级缓存

## 1. 要点

**核心思想：** 三级缓存解决循环依赖。

- 三级缓存靠提前暴露工厂引用打破循环依赖
- 只有单例 Bean 才走三级缓存
- 循环依赖只发生在单例作用域的 setter/字段注入场景

## 自测题（复习用）

1. 三级缓存如何打破循环依赖？
<details><summary>答案</summary>

**答案：** 提前暴露 ObjectFactory 引用。

</details>

2. 哪些 Bean 会走三级缓存？
<details><summary>答案</summary>

**答案：** 只有单例 Bean。

</details>
"""


def make_txt_dir(tmp_path, stamps, name="txt"):
    """stamps: [mm:ss] 列表 → 单分P <页码>_<分P名>.txt（带足量填充，保证字数比合规）。"""
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    pad = "本小节讲解Spring容器与Bean生命周期以及控制反转依赖注入预实例化机制等内容" * 8
    (d / "01_第一节.txt").write_text(
        "\n".join(f"[{s}] {pad}" for s in stamps) + "\n", encoding="utf-8")
    return d


def make_pages_dir(tmp_path, name="pages"):
    """两个分P、时间各自从 0 起（bili_transcribe 契约），用于分P归属测试。"""
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    pad = "本小节讲解Spring容器与Bean生命周期以及控制反转依赖注入预实例化机制等内容" * 8
    (d / "01_上.txt").write_text(f"[00:10] {pad}\n[00:20] {pad}\n", encoding="utf-8")
    (d / "02_下.txt").write_text(f"[00:30] {pad}\n[00:40] {pad}\n", encoding="utf-8")
    return d


def make_meta(tmp_path, platform="bilibili", name="meta.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"platform": platform, "bvid": "BV1xx411c7mD",
                             "duration": "42:10"}, ensure_ascii=False),
                 encoding="utf-8")
    return p


# ==================== 版本分派 ====================

def test_dispatch_v3_routes_to_v3(tmp_path):
    report = check_dispatch(tmp_path, lecture_note())
    assert report["template_version"] == "v3"
    assert report["note_type"] == "lecture"
    assert report["ok"] is True


def test_rules_for_still_returns_v2_for_v3():
    """rules_for 保持 v2 时代语义（冻结测试钉死）；v3 判断只在 dispatch 内做。"""
    assert validate_note.rules_for({"template_version": "v3"}) == "v2"


def test_v1_v2_dispatch_unchanged(tmp_path):
    """v1/v2/缺失 仍走原路径（规则回归由 test_validate_note*.py 冻结用例承担）。"""
    v1_text = "---\ntemplate_version: v1\n---\n\n# 笔记\n\n正文"
    assert check_dispatch(tmp_path, v1_text)["template_version"] == "v1"
    v2_text = v1_text.replace("v1", "v2")
    assert check_dispatch(tmp_path, v2_text)["template_version"] == "v2"
    assert check_dispatch(tmp_path, "# 无 frontmatter")["template_version"] == "v1"


def test_v3_report_shape_object_arrays(tmp_path):
    text = lecture_note().replace(
        "- 控制反转把对象的创建权交给容器，依赖注入是它的实现手段",
        "- **核心必考** 控制反转")
    report = check_v3(tmp_path, text)
    assert report["errors"]
    for e in report["errors"]:
        assert set(e.keys()) == {"check", "message", "line"}
    for w in report["warnings"]:
        assert set(w.keys()) == {"check", "message", "line"}
    assert report["template_version"] == "v3"


# ==================== 基线：好笔记全过 ====================

def test_good_lecture_no_txt_dir_only_expected_warnings(tmp_path):
    """缺 --txt-dir：白名单/t 校验与字数比跳过，但必须输出明确 warning（不静默通过）。"""
    report = check_v3(tmp_path, lecture_note())
    assert report["errors"] == []
    assert report["ok"] is True
    assert {w["check"] for w in report["warnings"]} == {"V3-E2", "V3-E3", "V3-RATIO"}
    assert report["checks"]["time_index"]["found"] is True
    assert report["checks"]["time_index"]["entries"] == 2


def test_good_lecture_with_txt_and_meta_zero_warnings(tmp_path):
    txt = make_txt_dir(tmp_path, ["00:00", "08:12", "08:45", "09:30",
                                  "15:40", "16:02", "16:30", "17:10"])
    meta = make_meta(tmp_path)
    p = tmp_path / "note.md"
    p.write_text(lecture_note(), encoding="utf-8")
    report = validate_note.check_note_v3(
        p.read_text(encoding="utf-8"), p, meta, txt)
    assert report["errors"] == []
    assert report["warnings"] == []
    assert report["ok"] is True
    assert report["checks"]["entry_whitelist"]["passed"] is True
    assert report["checks"]["meta"]["loaded"] is True


def test_good_short_note_passes(tmp_path):
    """short：时间索引整节省略合法；自测题 2 道、（对应：…）可选；知识地图可省。"""
    report = check_v3(tmp_path, short_note())
    assert report["errors"] == []
    assert report["ok"] is True
    assert report["note_type"] == "short"
    assert report["checks"]["time_index"]["found"] is False
    assert errs_of(report, "V3-E5") == []
    assert report["checks"]["knowledge_map"]["passed"] is None


def test_main_cli_v3_end_to_end(tmp_path):
    """CLI 端到端：v3 笔记输出带 template_version/note_type 字段，exit 0。"""
    note = tmp_path / "note.md"
    note.write_text(lecture_note(), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "validate_note.py"),
         str(note)], capture_output=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["template_version"] == "v3"
    assert data["note_type"] == "lecture"
    assert data["ok"] is True


# ==================== V3-E1：正文时刻禁令 ====================

def test_e1_bracket_timestamp_in_body_is_error(tmp_path):
    text = lecture_note().replace(
        "- 控制反转把对象的创建权交给容器，依赖注入是它的实现手段",
        "- 控制反转把对象的创建权交给容器 [08:45]")
    report = check_v3(tmp_path, text)
    e1 = errs_of(report, "V3-E1")
    assert e1 and "08:45" in e1[0]["message"]
    assert e1[0]["line"] > 0
    assert report["ok"] is False


def test_e1_paren_and_hms_variants_are_errors(tmp_path):
    for snippet in ("（08:45）", "(08:45)", "[1:02:03]", "[123:45]"):
        text = lecture_note().replace(
            "**核心思想：** 控制反转与依赖注入是同一件事的两种说法。",
            f"**核心思想：** 控制反转与依赖注入是同一件事的两种说法。{snippet}")
        report = check_v3(tmp_path, text)
        e1 = errs_of(report, "V3-E1")
        assert e1, snippet
        assert snippet in e1[0]["message"], snippet


def test_e1_timestamp_in_code_block_is_error(tmp_path):
    """v3 禁代码内时刻：fence 里的注释同样扫描。"""
    text = lecture_note().replace(
        "// 来源：仓库 demo/OrderService.java",
        "// 来源：仓库 demo/OrderService.java\n// 参考 P06 [12:34]")
    report = check_v3(tmp_path, text)
    e1 = errs_of(report, "V3-E1")
    assert e1 and "[12:34]" in e1[0]["message"]


def test_e1_frontmatter_and_nav_region_exempt(tmp_path):
    """frontmatter 的 duration、NAV 区条目时刻均合法（基线零 E1）。"""
    report = check_v3(tmp_path, lecture_note())
    assert errs_of(report, "V3-E1") == []


# ==================== V3-E2：时间索引存在性 / 白名单 / 条目标题 ====================

def test_e2_missing_index_lecture_is_error(tmp_path):
    text = lecture_note()
    text = text[:text.index("<!-- NAV:BEGIN")].rstrip() + "\n"
    report = check_v3(tmp_path, text)
    e2 = errs_of(report, "V3-E2")
    assert e2 and "视频时间索引" in e2[0]["message"]
    assert report["ok"] is False


def test_e2_unpaired_nav_markers_is_error(tmp_path):
    text = lecture_note().replace("<!-- NAV:END -->\n", "")
    report = check_v3(tmp_path, text)
    assert any("NAV:BEGIN/NAV:END" in e["message"] for e in errs_of(report, "V3-E2"))


def test_e2_index_outside_nav_markers_is_error(tmp_path):
    text = lecture_note().replace(
        "<!-- NAV:BEGIN 由 scripts/note_nav.py 生成，请勿手工编辑 -->\n## 视频时间索引",
        "## 视频时间索引\n\n<!-- NAV:BEGIN 由 scripts/note_nav.py 生成，请勿手工编辑 -->")
    report = check_v3(tmp_path, text)
    assert any("未位于" in e["message"] for e in errs_of(report, "V3-E2"))


def test_e2_no_entries_is_error(tmp_path):
    text = lecture_note().replace(
        "- [08:12](https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=492) 1. 第一节\n"
        "- [16:02](https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=962) 2. 第二节\n",
        "")
    report = check_v3(tmp_path, text)
    assert any("没有条目" in e["message"] for e in errs_of(report, "V3-E2"))


def test_e2_entry_off_whitelist_is_error(tmp_path):
    txt = make_txt_dir(tmp_path, ["08:12", "16:02"])
    text = lecture_note().replace("[08:12]", "[12:34]")  # 转写稿完全无此时刻
    report = check_v3(tmp_path, text, txt_dir=txt)
    e2 = errs_of(report, "V3-E2")
    assert len(e2) == 1 and "12:34" in e2[0]["message"]
    assert "白名单" in e2[0]["message"]


def test_e2_entry_title_mismatch_warns(tmp_path):
    text = lecture_note().replace(") 1. 第一节", ") 别的章节名")
    report = check_v3(tmp_path, text)
    w = warns_of(report, "V3-E2")
    assert any("别的章节名" in x["message"] for x in w)
    assert report["ok"] is True  # warning 不挡交付


def test_e2_entry_title_matches_after_number_strip(tmp_path):
    """条目标题「第一节」与正文小节「1. 第一节」去序号前缀后相等 → 不 warning。"""
    txt = make_txt_dir(tmp_path, ["08:12", "16:02"])
    text = lecture_note().replace(") 1. 第一节", ") 第一节")
    report = check_v3(tmp_path, text, txt_dir=txt)
    assert warns_of(report, "V3-E2") == []
    assert report["ok"] is True


def test_short_with_index_same_rules_no_link_warning(tmp_path):
    """抖音 short + 时间索引：纯文本条目合法，缺链接不 warning（抖音条目无深链）。"""
    txt = make_txt_dir(tmp_path, ["00:10"])
    text = short_note() + """
<!-- NAV:BEGIN 由 scripts/note_nav.py 生成，请勿手工编辑 -->
## 视频时间索引

- [00:10] 1. 要点
<!-- NAV:END -->
"""
    report = check_v3(tmp_path, text, txt_dir=txt)
    assert errs_of(report, "V3-E2") == []
    assert warns_of(report, "V3-E3") == []
    assert report["ok"] is True


# ==================== V3-E3：深链校验 ====================

def test_e3_bad_link_format_is_error(tmp_path):
    text = lecture_note().replace(
        "https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=492",
        "https://example.com/watch?v=x&p=1&t=492")
    report = check_v3(tmp_path, text)
    e3 = errs_of(report, "V3-E3")
    assert e3 and "格式非法" in e3[0]["message"]
    assert report["ok"] is False


def test_e3_wrong_bvid_is_error(tmp_path):
    text = lecture_note().replace(
        "https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=492",
        "https://www.bilibili.com/video/BVother999?p=1&t=492")
    report = check_v3(tmp_path, text)
    assert any("格式非法" in e["message"] for e in errs_of(report, "V3-E3"))


def test_e3_wrong_t_is_error(tmp_path):
    """t 必须等于条目时刻归属分P后的分P内相对秒（此处 962）。"""
    txt = make_txt_dir(tmp_path, ["00:00", "08:12", "08:45", "09:30",
                                  "15:40", "16:02", "16:30", "17:10"])
    text = lecture_note().replace("?p=1&t=962", "?p=1&t=999")
    report = check_v3(tmp_path, text, txt_dir=txt)
    e3 = errs_of(report, "V3-E3")
    assert e3 and "999" in e3[0]["message"] and "962" in e3[0]["message"]


def test_e3_correct_t_passes_with_pages(tmp_path):
    txt = make_txt_dir(tmp_path, ["00:00", "08:12", "08:45", "09:30",
                                  "15:40", "16:02", "16:30", "17:10"])
    report = check_v3(tmp_path, lecture_note(), txt_dir=txt)
    assert errs_of(report, "V3-E3") == []
    assert report["checks"]["deep_links"]["pages"] == [1]


def test_e3_multipage_relative_t(tmp_path):
    """多分P：条目 [00:30] 归属分P2，t=30（分P内相对秒）；p 与 t 均正确才通过。"""
    pages = make_pages_dir(tmp_path)
    good = lecture_note().replace(
        "- [08:12](https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=492) 1. 第一节\n"
        "- [16:02](https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=962) 2. 第二节",
        "- [00:30](https://www.bilibili.com/video/BV1xx411c7mD?p=2&t=30) 1. 第一节")
    report = check_v3(tmp_path, good, txt_dir=pages)
    assert errs_of(report, "V3-E3") == []
    bad = good.replace("?p=2&t=30", "?p=2&t=99")
    report2 = check_v3(tmp_path, bad, txt_dir=pages)
    e3 = errs_of(report2, "V3-E3")
    assert e3 and "99" in e3[0]["message"] and "30" in e3[0]["message"]


def test_e3_bilibili_missing_link_warns(tmp_path):
    """B站 lecture 条目缺链接 → warning（提示运行 note_nav.py），非 error。"""
    text = lecture_note().replace(
        "- [08:12](https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=492) 1. 第一节",
        "- [08:12] 1. 第一节")
    report = check_v3(tmp_path, text)
    w3 = warns_of(report, "V3-E3")
    assert any("note_nav.py" in x["message"] for x in w3)
    assert report["ok"] is True


# ==================== V3-E4：三词标签残留 ====================

def test_e4_bold_tag_line_residue_is_error(tmp_path):
    text = lecture_note().replace(
        "- 控制反转把对象的创建权交给容器，依赖注入是它的实现手段",
        "- **核心必考** 控制反转把对象的创建权交给容器")
    report = check_v3(tmp_path, text)
    e4 = errs_of(report, "V3-E4")
    assert e4 and "核心必考" in e4[0]["message"]
    assert report["ok"] is False


def test_e4_paren_variant_and_card_title_residue(tmp_path):
    """行首全角括号标签 与 概念卡标题标签后缀 两种残留形式都要抓。"""
    text = lecture_note().replace(
        "- BeanFactory 惰性实例化，ApplicationContext 默认预实例化单例",
        "-（重点掌握）BeanFactory 惰性实例化")
    report = check_v3(tmp_path, text)
    assert any("重点掌握" in e["message"] for e in errs_of(report, "V3-E4"))

    text2 = lecture_note().replace("### BeanFactory", "### BeanFactory（核心必考）")
    report2 = check_v3(tmp_path, text2)
    assert any("标签后缀" in e["message"] for e in errs_of(report2, "V3-E4"))
    assert report2["ok"] is False


# ==================== V3-E5：自测题 ====================

EXTRA_QUESTIONS = """
5. 什么是预实例化？（对应：ApplicationContext）
<details><summary>答案</summary>

**答案：** 容器启动时创建全部单例。

</details>

6. 什么是惰性实例化？（对应：BeanFactory）
<details><summary>答案</summary>

**答案：** 首次获取时才创建。

</details>

7. 什么是依赖注入？（对应：控制反转）
<details><summary>答案</summary>

**答案：** 把依赖的装配交给容器。

</details>

8. 什么是单例作用域？（对应：ApplicationContext）
<details><summary>答案</summary>

**答案：** 容器中该 Bean 只有一个实例。

</details>

"""


def test_e5_quiz_over_limit_lecture_is_error(tmp_path):
    """lecture 上限 7 道：插到 NAV 区之前（自测题节到 ## 视频时间索引 为止）。"""
    text = lecture_note().replace(
        "<!-- NAV:BEGIN", EXTRA_QUESTIONS + "<!-- NAV:BEGIN")
    report = check_v3(tmp_path, text)
    e5 = errs_of(report, "V3-E5")
    assert any("8" in e["message"] and "4~7" in e["message"] for e in e5)
    assert report["ok"] is False


def test_e5_quiz_under_limit_lecture_is_error(tmp_path):
    text = lecture_note().replace(
        "4. 依赖注入有哪些常见形式？（对应：控制反转）\n"
        "<details><summary>答案</summary>\n\n"
        "**答案：** 构造器注入、字段注入、Setter 注入。\n\n</details>\n\n", "")
    report = check_v3(tmp_path, text)
    assert any("3" in e["message"] and "4~7" in e["message"]
               for e in errs_of(report, "V3-E5"))


def test_e5_quiz_count_short_range(tmp_path):
    """short 2~3 道：第 3 道合法；第 4 道越界报错。"""
    third = """
3. 什么时候会触发循环依赖？
<details><summary>答案</summary>

**答案：** 单例 Bean 的 setter/字段注入场景。

</details>
"""
    report = check_v3(tmp_path, short_note() + third)
    assert errs_of(report, "V3-E5") == []
    fourth = third.replace("3. 什么时候会触发循环依赖？", "4. 第四问是什么？")
    report2 = check_v3(tmp_path, short_note() + third + fourth)
    assert any("2~3" in e["message"] for e in errs_of(report2, "V3-E5"))


def test_e5_answer_fullwidth_timestamp_is_error(tmp_path):
    """答案行全角时刻：E1 与 V3-E5 双重兜底都要命中。"""
    text = lecture_note().replace(
        "**答案：** ApplicationContext 在启动时预实例化全部单例，配置错误启动即暴露。",
        "**答案：** ApplicationContext 在启动时预实例化全部单例，配置错误（16:02）即暴露。")
    report = check_v3(tmp_path, text)
    assert any("全角时刻" in e["message"] for e in errs_of(report, "V3-E5"))
    assert errs_of(report, "V3-E1")
    assert report["ok"] is False


def test_e5_missing_details_and_answer_are_errors(tmp_path):
    text = lecture_note().replace(
        "2. 解释控制反转与依赖注入的关系。（对应：控制反转）\n"
        "<details><summary>答案</summary>\n\n"
        "**答案：** 控制反转是设计原则，依赖注入是它的实现手段。\n\n</details>\n\n",
        "2. 解释控制反转与依赖注入的关系。（对应：控制反转）\n\n")
    report = check_v3(tmp_path, text)
    assert any("details" in e["message"] for e in errs_of(report, "V3-E5"))

    text2 = lecture_note().replace(
        "**答案：** 控制反转是设计原则，依赖注入是它的实现手段。", "")
    report2 = check_v3(tmp_path, text2)
    assert any("答案：**" in e["message"] for e in errs_of(report2, "V3-E5"))


def test_e5_answer_multiline_warns(tmp_path):
    text = lecture_note().replace(
        "**答案：** ApplicationContext 在启动时预实例化全部单例，配置错误启动即暴露。",
        "**答案：** ApplicationContext 在启动时预实例化全部单例。\n补充的第二行答案正文。")
    report = check_v3(tmp_path, text)
    assert any("超过一行" in w["message"] for w in warns_of(report, "V3-E5"))
    assert report["ok"] is True


def test_e5_yesno_stem_warns(tmp_path):
    text = lecture_note().replace(
        "4. 依赖注入有哪些常见形式？（对应：控制反转）",
        "4. 是否应该总是使用依赖注入？（对应：控制反转）")
    report = check_v3(tmp_path, text)
    assert any("yes/no" in w["message"] for w in warns_of(report, "V3-E5"))


def test_e5_correspond_unknown_lecture_is_error(tmp_path):
    text = lecture_note().replace("（对应：BeanFactory）", "（对应：不存在概念）")
    report = check_v3(tmp_path, text)
    assert any("不存在概念" in e["message"] for e in errs_of(report, "V3-E5"))
    assert report["ok"] is False


# ==================== V3-E6：概念卡片 ====================

def test_e6_missing_definition_is_error(tmp_path):
    text = lecture_note().replace("- 定义：Spring 最基础的容器实现", "- 定义：")
    report = check_v3(tmp_path, text)
    assert any("非空「定义」" in e["message"] for e in errs_of(report, "V3-E6"))


def test_e6_missing_appearance_is_error(tmp_path):
    text = lecture_note().replace(
        "### BeanFactory\n\n- 定义：Spring 最基础的容器实现\n- 出现：1、2\n",
        "### BeanFactory\n\n- 定义：Spring 最基础的容器实现\n")
    report = check_v3(tmp_path, text)
    assert any("非空「出现」" in e["message"] for e in errs_of(report, "V3-E6"))


def test_e6_appearance_section_missing_is_error(tmp_path):
    text = lecture_note().replace(
        "- 定义：把对象创建与依赖装配的控制权从代码转移到容器\n- 出现：1、2",
        "- 定义：把对象创建与依赖装配的控制权从代码转移到容器\n- 出现：1、9")
    report = check_v3(tmp_path, text)
    assert any("小节 9" in e["message"] for e in errs_of(report, "V3-E6"))


def test_e6_appearance_with_timestamp_is_error(tmp_path):
    """v3 收紧：出现 含时刻 → error（v2 里是可选合法项）。"""
    text = lecture_note().replace(
        "- 定义：把对象创建与依赖装配的控制权从代码转移到容器\n- 出现：1、2",
        "- 定义：把对象创建与依赖装配的控制权从代码转移到容器\n- 出现：1（08:45）、2")
    report = check_v3(tmp_path, text)
    e6 = errs_of(report, "V3-E6")
    assert any("（08:45）" in e["message"] and "含时刻" in e["message"] for e in e6)
    assert errs_of(report, "V3-E1")  # 正文时刻禁令同时命中
    assert report["ok"] is False


def test_e6_single_section_card_warns(tmp_path):
    text = lecture_note().replace(
        "- 定义：Spring 最基础的容器实现\n- 出现：1、2",
        "- 定义：Spring 最基础的容器实现\n- 出现：1")
    report = check_v3(tmp_path, text)
    w6 = warns_of(report, "V3-E6")
    assert any("BeanFactory" in w["message"] for w in w6)
    assert any("并入小节" in w["message"] for w in w6)
    assert report["ok"] is True


def test_e6_unknown_dependency_warns(tmp_path):
    text = lecture_note().replace("- 依赖：BeanFactory", "- 依赖：事件发布机制")
    report = check_v3(tmp_path, text)
    w6 = warns_of(report, "V3-E6")
    assert any("事件发布机制" in w["message"] for w in w6)
    assert report["ok"] is True


# ==================== V3-E7：代码块 ====================

def test_e7_unclosed_fence_is_error(tmp_path):
    text = lecture_note().replace(
        "```java\n// 来源：仓库 demo/OrderService.java\n@Service\n"
        "public class OrderService { }\n```",
        "```java\n// 来源：仓库 demo/OrderService.java\n@Service")
    report = check_v3(tmp_path, text)
    assert any("未闭合" in e["message"] for e in errs_of(report, "V3-E7"))
    assert report["ok"] is False


def test_e7_source_with_timestamp_is_error(tmp_path):
    """白名单语言代码块的来源注释含时刻 → E7 与 E1 同时命中。"""
    text = lecture_note().replace(
        "// 来源：仓库 demo/OrderService.java", "// 来源：P06 [12:34]")
    report = check_v3(tmp_path, text)
    e7 = errs_of(report, "V3-E7")
    assert e7 and "[12:34]" in e7[0]["message"]
    assert errs_of(report, "V3-E1")


def test_e7_source_missing_ok(tmp_path):
    """v3 放宽：来源标注可选，缺失不再 warning/error。"""
    text = lecture_note().replace("// 来源：仓库 demo/OrderService.java\n", "")
    report = check_v3(tmp_path, text)
    assert errs_of(report, "V3-E7") == []
    assert report["checks"]["code_blocks"]["passed"] is True


# ==================== 保留检查（V3-FM/NT/SEC/IDEA/KM/IMG/MANIFEST/RATIO） ====================

def test_fm_missing_fields_are_errors(tmp_path):
    text = lecture_note().replace("platform: bilibili\n", "").replace(
        "created: 2026-10-03\n", "").replace('duration: "42:10"\n', "")
    report = check_v3(tmp_path, text)
    got = {e["message"] for e in errs_of(report, "V3-FM")}
    assert got >= {"frontmatter 缺少 platform 字段", "frontmatter 缺少 created 字段",
                   "frontmatter 缺少 duration 字段"}


def test_fm_bvid_vs_video_id_by_platform(tmp_path):
    text = lecture_note().replace("bvid: BV1xx411c7mD\n", "")
    assert any("bvid" in e["message"] for e in errs_of(check_v3(tmp_path, text), "V3-FM"))
    text2 = short_note().replace("video_id: 7301234567890123456\n", "")
    assert any("video_id" in e["message"]
               for e in errs_of(check_v3(tmp_path, text2, name="s.md"), "V3-FM"))


def test_invalid_note_type_is_error(tmp_path):
    text = lecture_note().replace("note_type: lecture", "note_type: tutorial")
    report = check_v3(tmp_path, text)
    assert any("tutorial" in e["message"] for e in errs_of(report, "V3-NT"))


def test_thin_section_is_error(tmp_path):
    text = lecture_note().replace(
        "**核心思想：** 控制反转与依赖注入是同一件事的两种说法。\n\n"
        "- 控制反转把对象的创建权交给容器，依赖注入是它的实现手段\n"
        "- BeanFactory 惰性实例化，ApplicationContext 默认预实例化单例\n"
        "- Lombok 的 @RequiredArgsConstructor 可替代手写构造注入\n\n"
        "```java\n// 来源：仓库 demo/OrderService.java\n@Service\n"
        "public class OrderService { }\n```",
        "**核心思想：** 一句话。")
    report = check_v3(tmp_path, text)
    e = errs_of(report, "V3-SEC")
    assert any("第一节" in x["message"] for x in e)


def test_core_idea_missing_is_error(tmp_path):
    text = lecture_note().replace(
        "**核心思想：** 两者是同一继承体系的不同完成度。", "本节讲两个容器实现。")
    report = check_v3(tmp_path, text)
    e = errs_of(report, "V3-IDEA")
    assert any("第二节" in x["message"] for x in e)
    assert report["ok"] is False


def test_km_missing_lecture_is_error(tmp_path):
    text = lecture_note().replace("## 本讲知识地图", "## 知识图谱X")
    report = check_v3(tmp_path, text)
    assert any("知识地图" in e["message"] for e in errs_of(report, "V3-KM"))


def test_km_bad_mermaid_is_error(tmp_path):
    text = lecture_note().replace("mindmap\n  root((Spring 容器))",
                                  "pie\n  title 占位")
    report = check_v3(tmp_path, text)
    assert any("mindmap/graph/flowchart" in e["message"]
               for e in errs_of(report, "V3-KM"))


def test_missing_image_is_error(tmp_path):
    text = lecture_note().replace(
        "**核心思想：** 两者是同一继承体系的不同完成度。",
        "**核心思想：** 两者是同一继承体系的不同完成度。\n\n![不存在](images/ghost.png)")
    report = check_v3(tmp_path, text)
    assert any("images/ghost.png" in e["message"] for e in errs_of(report, "V3-IMG"))


def test_manifest_keyword_mismatch_warns(tmp_path):
    """manifest 交叉校验保留；标注行已废除 → 时间项自动跳过（贴近度放宽）。"""
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "p01_a.png").write_bytes(b"\x89PNG fake")
    (tmp_path / "images" / "manifest.json").write_text(json.dumps({
        "frames": [{"file": "p01_a.png", "keyword": "循环依赖", "t": 15}]
    }, ensure_ascii=False), encoding="utf-8")
    text = lecture_note().replace(
        "**核心思想：** 两者是同一继承体系的不同完成度。",
        "**核心思想：** 两者是同一继承体系的不同完成度。\n\n![完全无关的画面](images/p01_a.png)")
    report = check_v3(tmp_path, text)
    w = warns_of(report, "V3-MANIFEST")
    assert w and "循环依赖" in w[0]["message"]
    assert report["checks"]["image_manifest_kw_time"]["time_skipped"] == 1
    assert report["ok"] is True  # warning 不挡交付


def test_ratio_out_of_range_warns(tmp_path):
    """转写稿只有 2 行（字数远少于笔记）→ 比值 > 1.2 → V3-RATIO warning。"""
    txt = make_txt_dir(tmp_path, ["08:12", "16:02"])
    report = check_v3(tmp_path, lecture_note(), txt_dir=txt)
    wr = warns_of(report, "V3-RATIO")
    assert wr and "超出" in wr[0]["message"]
    assert report["checks"]["length_ratio"]["passed"] is False
    assert report["ok"] is True
