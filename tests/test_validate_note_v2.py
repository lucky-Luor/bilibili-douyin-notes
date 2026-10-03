# -*- coding: utf-8 -*-
"""validate_note.py v2 门禁（规格 §3/§4/§8，B3 批次）的离线测试。

覆盖：版本分派、标注机制（三词标签/比例/short 豁免）、四个坑的回归、
时间戳白名单（±5s 总秒数、跨分钟、三位数分钟）、覆盖率分拆、
概念卡与自测题联动（L9/L10/L12 集合定义）、自测题格式、NAV 区块检查（L11）。
v1 冻结回归由 test_validate_note.py 原有用例（零修改）承担。
"""
import json
import subprocess
import sys
from pathlib import Path

import validate_note

ROOT = Path(__file__).resolve().parents[1]


def check_v2(tmp_path, text, txt_dir=None, name="note.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return validate_note.check_note_v2(text, p, None, txt_dir)


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


# ---------- v2 lecture 合成笔记（对齐规格 §2.1 骨架） ----------

SEC1_TAGS = """- **核心必考** 控制反转把对象的创建权交给容器 [08:45]
- **重点掌握** BeanFactory 惰性实例化，ApplicationContext 默认预实例化单例 [15:40]
- **了解即可** Lombok 的 @RequiredArgsConstructor 可替代手写构造注入"""

SEC1_TAGS_COVER_BAD = """- **核心必考** 控制反转把对象的创建权交给容器 [08:45]
- **重点掌握** BeanFactory 惰性实例化 [15:40]
- **重点掌握** 容器预实例化策略因实现而异
- **了解即可** Lombok 的 @RequiredArgsConstructor 可替代手写构造注入
- **了解即可** 配置元数据可来自多种来源"""


def lecture_note() -> str:
    return f"""---
template_version: v2
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

- [1. 第一节](#1-第一节)（08:12）
- [2. 第二节](#2-第二节)（16:02）

---

## 1. 第一节（08:12）

**核心思想：** 控制反转与依赖注入是同一件事的两种说法。

{SEC1_TAGS}

```java
// 来源：仓库 demo/OrderService.java
@Service
public class OrderService {{ }}
```

## 2. 第二节（16:02）

**核心思想：** 两者是同一继承体系的不同完成度。

- **核心必考** ApplicationContext 在启动时预实例化全部单例 [16:02]
- **重点掌握** 两者都实现 BeanFactory 接口，前者是后者的超集 [16:30]
- **重点掌握** 容器启动流程分为扫描、注册、实例化三步 [17:10]

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

### 控制反转（核心必考）

- 定义：把对象创建与依赖装配的控制权从代码转移到容器
- 出现：1（08:45）、2（16:02）
- 依赖：
- 易混：

### ApplicationContext（核心必考）

- 定义：BeanFactory 的完整实现，启动即预实例化全部单例
- 出现：1（09:30）、2（16:02）
- 依赖：BeanFactory
- 易混：BeanFactory——预实例化 vs 惰性加载

### BeanFactory（重点掌握）

- 定义：Spring 最基础的容器实现
- 出现：1（15:40）、2（16:30）
- 依赖：
- 易混：

## 自测题（复习用）

1. 在需要启动即失败的场景下，为什么选 ApplicationContext？（对应：ApplicationContext）
<details><summary>答案</summary>

**答案：** ApplicationContext 在启动时预实例化全部单例，配置错误启动即暴露。[16:02]

</details>

2. 解释控制反转与依赖注入的关系。（对应：控制反转）
<details><summary>答案</summary>

**答案：** 控制反转是设计原则，依赖注入是它的实现手段。[08:45]

</details>

3. BeanFactory 与 ApplicationContext 有何区别？（对应：BeanFactory）
<details><summary>答案</summary>

**答案：** 前者惰性实例化，后者启动即预实例化。[15:40]

</details>

4. 依赖注入有哪些常见形式？（对应：控制反转）
<details><summary>答案</summary>

**答案：** 构造器注入、字段注入、Setter 注入。[08:45]

</details>

5. 什么是预实例化？（对应：ApplicationContext）
<details><summary>答案</summary>

**答案：** 容器启动时创建全部单例。[16:02]

</details>

<!-- NAV:BEGIN 由 scripts/note_nav.py 生成，请勿手工编辑 -->
## 参考时间戳

- [08:45](https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=525) 核心必考：控制反转
- [15:40](https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=940) 重点掌握：BeanFactory
- [16:02](https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=962) 核心必考：ApplicationContext
<!-- NAV:END -->
"""


def short_note() -> str:
    return """---
template_version: v2
note_type: short
platform: douyin
video_id: 7301234567890123456
source: https://www.douyin.com/video/7301234567890123456
created: 2026-10-03
duration: "00:59"
---

# 笔记：一分钟看懂三级缓存

## 1. 要点（00:10）

**核心思想：** 三级缓存解决循环依赖。

- **核心必考** 三级缓存靠提前暴露工厂引用打破循环依赖 [00:10]
- **重点掌握** 只有单例 Bean 才走三级缓存 [00:20]

## 自测题（复习用）

1. 三级缓存如何打破循环依赖？（对应：三级缓存）
<details><summary>答案</summary>

**答案：** 提前暴露 ObjectFactory 引用。[00:10]

</details>

2. 哪些 Bean 会走三级缓存？
<details><summary>答案</summary>

**答案：** 只有单例 Bean。[00:20]

</details>
"""


# ---------- v1 基线笔记（供版本分派测试；与 test_validate_note.good_note 同构） ----------

def v1_note() -> str:
    return """---
template_version: v1
---

# 课堂笔记 1：测试主题

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


# ---------- 时间戳白名单用的最小 lecture 笔记（只关心检查6） ----------

def ts_note(anchor: str) -> str:
    return f"""---
template_version: v2
note_type: lecture
platform: bilibili
source: https://x
created: 2026-10-03
---

# 笔记

## 1. 第一节（08:12）

- **核心必考** 要点一 {anchor}
- **重点掌握** 要点二 [12:34]

## 概念卡片

### 概念A（核心必考）

- 定义：测试用定义
- 出现：1（08:12）
- 依赖：
- 易混：

## 自测题（复习用）

1. 要点一是什么？（对应：概念A）
<details><summary>答案</summary>

**答案：** 内容 [12:34]

</details>
"""


def make_txt_dir(tmp_path, stamps, name="txt"):
    """stamps: [mm:ss] 列表 → 每行一个时间戳（带足量填充，保证检查15 比值合规）。"""
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    pad = "本小节讲解Spring容器与Bean生命周期以及控制反转依赖注入预实例化机制等内容" * 8
    (d / "p01.txt").write_text(
        "\n".join(f"[{s}] {pad}" for s in stamps) + "\n", encoding="utf-8")
    return d


# ==================== 版本分派（§8） ====================

def test_rules_for_dispatch():
    assert validate_note.rules_for({}) == "v1"
    assert validate_note.rules_for({"template_version": "v1"}) == "v1"
    assert validate_note.rules_for({"template_version": " v2 "}) == "v2"
    assert validate_note.rules_for({"template_version": "v3"}) == "v2"


def test_no_frontmatter_dispatches_v1(tmp_path):
    text = v1_note().replace("---\ntemplate_version: v1\n---\n", "")
    report = check_dispatch(tmp_path, text)
    assert report["template_version"] == "v1"
    assert report["ok"] is True  # 老式笔记仍通过（v1 规则冻结）


def test_v1_flag_ignores_v2_violations(tmp_path):
    """template_version: v1 + 无概念卡片等 v2 违规 → 仍 ok: True（v1 不查概念卡）。"""
    report = check_dispatch(tmp_path, v1_note())
    assert report["template_version"] == "v1"
    assert report["ok"] is True
    assert not any(str(e["check"]).startswith("L") for e in report["errors"])


def test_v2_flag_enforces_v2_rules(tmp_path):
    """template_version: v2 + 同样的（v1 式）内容 → ok: False。"""
    text = v1_note().replace(
        "template_version: v1", "template_version: v2\nnote_type: lecture")
    report = check_dispatch(tmp_path, text)
    assert report["template_version"] == "v2"
    assert report["ok"] is False
    assert any(e["check"] == "L1" for e in report["errors"])  # 缺概念卡片章节


def test_v2_report_shape_object_arrays(tmp_path):
    text = v1_note().replace("template_version: v1", "template_version: v2")
    report = check_v2(tmp_path, text)
    assert report["errors"], "v2 违规应有 errors"
    for e in report["errors"]:
        assert set(e.keys()) == {"check", "message", "line"}
    for w in report["warnings"]:
        assert set(w.keys()) == {"check", "message", "line"}
    assert report["template_version"] == "v2"
    assert report["note_type"] is None  # 该笔记 frontmatter 无 note_type


def test_v1_report_stays_string_arrays(tmp_path):
    """v1 输出保持旧的字符串数组不变（老笔记消费者不破）。"""
    p = tmp_path / "n.md"
    p.write_text(v1_note(), encoding="utf-8")
    report = validate_note.check_note(p.read_text(encoding="utf-8"), p, None, None)
    assert all(isinstance(e, str) for e in report["errors"])
    assert all(isinstance(w, str) for w in report["warnings"])


def test_main_cli_dispatch_and_fields(tmp_path):
    """CLI 端到端：v2 笔记输出带 template_version/note_type 字段，ensure_ascii JSON。"""
    note = tmp_path / "note.md"
    note.write_text(lecture_note(), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "validate_note.py"),
         str(note)], capture_output=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["template_version"] == "v2"
    assert data["note_type"] == "lecture"
    assert data["ok"] is True


# ==================== 基线：好笔记全过 ====================

def test_good_lecture_note_passes(tmp_path):
    report = check_v2(tmp_path, lecture_note())
    assert report["errors"] == []
    assert report["ok"] is True
    assert report["note_type"] == "lecture"
    # 缺 --txt-dir：检查6/15 跳过但必须输出明确 warning（不静默通过）
    assert {w["check"] for w in report["warnings"]} == {"6", "15"}


def test_good_lecture_with_txt_dir_zero_warnings(tmp_path):
    txt = make_txt_dir(tmp_path, ["00:00", "08:12", "08:45", "09:30",
                                  "15:40", "16:02", "16:30", "17:10"])
    report = check_v2(tmp_path, lecture_note(), txt_dir=txt)
    assert report["errors"] == []
    assert report["warnings"] == []
    assert report["ok"] is True
    assert report["checks"]["timestamp_whitelist"]["passed"] is True
    assert report["checks"]["core_ratio"]["ratio"] <= 0.34


def test_good_short_note_passes(tmp_path):
    report = check_v2(tmp_path, short_note())
    assert report["errors"] == []
    assert report["ok"] is True
    assert report["note_type"] == "short"
    assert report["checks"]["core_ratio"]["exempt"] is True


# ==================== 标注机制（§8：Q2 + Q5） ====================

def test_ratio_all_core_warns_not_errors(tmp_path):
    """标注行=3、核心必考=3 → 比例 1.0 → warning（不是 error）。"""
    text = short_note().replace(
        "- **重点掌握** 只有单例 Bean 才走三级缓存 [00:20]",
        "- **核心必考** 只有单例 Bean 才走三级缓存 [00:20]")
    # short 豁免比例 → 用 lecture 笔记验证：把 lecture 全部标签改成核心必考（6/6）
    text = lecture_note()
    text = text.replace("**重点掌握**", "**核心必考**").replace("**了解即可**", "**核心必考**")
    report = check_v2(tmp_path, text)
    assert errs_of(report, "9") == []
    assert len(warns_of(report, "9")) == 1
    assert "超过上限" in warns_of(report, "9")[0]["message"]
    assert report["ok"] is True  # 比例只是 warning


def test_ratio_boundary_one_third_passes(tmp_path):
    """标注行=12、核心必考=4 → 比例 0.33 → 通过（无 warning）。"""
    text = lecture_note().replace(
        "- **重点掌握** 容器启动流程分为扫描、注册、实例化三步 [17:10]",
        "- **重点掌握** 容器启动流程分为扫描、注册、实例化三步 [17:10]\n"
        "- **核心必考** 控制反转的依赖查找过程 [18:00]\n"
        "- **核心必考** ApplicationContext 的启动刷新流程 [18:30]\n"
        "- **重点掌握** 事件发布机制 [19:00]\n"
        "- **重点掌握** 生命周期回调接口 [19:30]\n"
        "- **重点掌握** Bean 后置处理器 [20:00]\n"
        "- **重点掌握** 懒加载与预实例化的取舍 [20:30]")
    report = check_v2(tmp_path, text)
    assert report["checks"]["core_min"]["core_count"] == 4
    assert report["checks"]["annotation_scope"]["annotation_count"] == 12
    assert warns_of(report, "9") == []
    assert report["errors"] == []


def test_core_zero_is_error(tmp_path):
    text = lecture_note().replace("**核心必考**", "**重点掌握**")
    report = check_v2(tmp_path, text)
    assert len(errs_of(report, "8")) == 1
    assert "核心必考标注行为 0" in errs_of(report, "8")[0]["message"]
    assert report["ok"] is False


def test_ratio_half_warns(tmp_path):
    """核心必考=5 / 标注行=10 → 0.5 → warning。"""
    text = lecture_note().replace(
        "- **重点掌握** 容器启动流程分为扫描、注册、实例化三步 [17:10]",
        "- **重点掌握** 容器启动流程分为扫描、注册、实例化三步 [17:10]\n"
        "- **核心必考** 控制反转的依赖查找过程 [18:00]\n"
        "- **核心必考** 控制反转在容器内的落地形式 [18:30]\n"
        "- **核心必考** ApplicationContext 的启动刷新流程 [19:00]\n"
        "- **重点掌握** 事件发布机制 [19:30]")
    report = check_v2(tmp_path, text)
    assert report["checks"]["core_ratio"]["ratio"] == 0.5
    assert len(warns_of(report, "9")) == 1
    assert errs_of(report, "9") == []


def test_non_whitelist_tag_is_error(tmp_path):
    for bad in ("**必考**", "**核心考点**", "**必须掌握**"):
        text = lecture_note().replace(
            "- **核心必考** 控制反转把对象的创建权交给容器 [08:45]",
            f"- {bad} 控制反转把对象的创建权交给容器 [08:45]")
        report = check_v2(tmp_path, text)
        e4 = errs_of(report, "4")
        assert e4, f"{bad} 应触发检查4 error"
        assert "非白名单标注词" in e4[0]["message"]
        assert report["ok"] is False


def test_missing_point_sentence_is_error(tmp_path):
    text = lecture_note().replace(
        "- **核心必考** 控制反转把对象的创建权交给容器 [08:45]",
        "- **核心必考**")
    report = check_v2(tmp_path, text)
    e4 = errs_of(report, "4")
    assert any("要点句" in e["message"] for e in e4)


def test_short_ratio_exempt(tmp_path):
    """short 型标注行=2、核心必考=1（比例 0.5）→ 无比例 warning（检查9 豁免）。"""
    report = check_v2(tmp_path, short_note())
    assert report["checks"]["core_ratio"]["exempt"] is True
    assert warns_of(report, "9") == []


def test_understand_level_excluded_from_coverage(tmp_path):
    """了解即可 行无锚点 → 不影响检查7（不参与统计）。"""
    text = lecture_note().replace(
        "- **了解即可** Lombok 的 @RequiredArgsConstructor 可替代手写构造注入",
        "- **了解即可** Lombok 的 @RequiredArgsConstructor 可替代手写构造注入\n"
        "- **了解即可** 配置元数据可来自多种来源")
    report = check_v2(tmp_path, text)
    assert errs_of(report, "7") == []
    assert report["checks"]["anchor_coverage"]["total"] == 5  # 2 核心 + 3 重点
    assert report["checks"]["anchor_coverage"]["ratio"] == 1.0


# ==================== 四个坑的回归（§8 必做；坑③ 属 fetch 脚本批次） ====================

def test_pit1_card_titles_not_counted(tmp_path):
    """坑①：听讲层 5 条核心必考 + 概念卡 3 张 ###（核心必考）→ 统计仍为 5（若卡
    片标题被计入会是 8）。"""
    text = lecture_note()
    text = text.replace("**重点掌握**", "**核心必考**")          # 听讲层 5 条核心必考
    text = text.replace("### BeanFactory（重点掌握）", "### BeanFactory（核心必考）")
    report = check_v2(tmp_path, text)
    assert report["checks"]["core_min"]["core_count"] == 5
    assert report["checks"]["annotation_scope"]["annotation_count"] == 6
    # 不报"数量翻倍"类 error（若卡片标题被计入会变成 8/6 且触发各处误判）
    assert not any("核心必考标注行为" in e["message"] for e in report["errors"])
    # L10：3 张核心必考卡全部被自测题覆盖
    assert errs_of(report, "L10") == []


def test_pit2_mermaid_and_console_blocks_need_no_source(tmp_path):
    """坑②：mermaid / console 块无来源 → 不报；java 块无来源 → 报错。"""
    text = lecture_note().replace(
        "// 来源：仓库 demo/OrderService.java\n@Service", "@Service")
    report = check_v2(tmp_path, text)
    e12 = errs_of(report, "12")
    assert len(e12) == 1
    assert "java" in e12[0]["message"]

    # console 块（非代码语言白名单）无来源 → 不报
    text2 = lecture_note().replace(
        "```java\n// 来源：仓库 demo/OrderService.java\n",
        "```console\n$ mvn spring-boot:run\n```\n\n```java\n// 来源：仓库 demo/OrderService.java\n")
    report2 = check_v2(tmp_path, text2)
    assert errs_of(report2, "12") == []
    # mermaid 块（基线中本就无来源）→ 不报
    assert errs_of(check_v2(tmp_path, lecture_note()), "12") == []


def test_unclosed_fence_still_reported(tmp_path):
    text = lecture_note().replace(
        "```java\n// 来源：仓库 demo/OrderService.java\n@Service\npublic class OrderService { }\n```",
        "```java\n// 来源：仓库 demo/OrderService.java\n@Service")  # 去掉闭合 fence
    report = check_v2(tmp_path, text)
    assert any("未闭合" in e["message"] for e in errs_of(report, "12"))


def test_pit4_rule_sets_differ_by_version(tmp_path):
    """坑④：无 frontmatter / v1 / v2 三种输入，规则集合不同。"""
    base = v1_note()
    no_fm = base.replace("---\ntemplate_version: v1\n---\n", "")
    r1 = check_dispatch(tmp_path, no_fm)
    r2 = check_dispatch(tmp_path, base)
    r3 = check_dispatch(tmp_path, base.replace(
        "template_version: v1", "template_version: v2\nnote_type: lecture"))
    assert r1["template_version"] == "v1" and r1["ok"] is True
    assert r2["template_version"] == "v1" and r2["ok"] is True
    assert r3["template_version"] == "v2" and r3["ok"] is False
    v2_checks = {e["check"] for e in r3["errors"]}
    assert "L1" in v2_checks  # v2 独有的 L 系列检查
    assert all(not str(e["check"]).startswith("L") for e in r1["errors"])


# ==================== 时间戳白名单（检查6，§8） ====================

def test_ts_exact_match_passes(tmp_path):
    txt = make_txt_dir(tmp_path, ["08:12", "12:34"])
    report = check_v2(tmp_path, ts_note("[08:12]"), txt_dir=txt)
    assert warns_of(report, "6") == []


def test_ts_within_5s_tolerance_passes(tmp_path):
    """笔记 [08:12]，转写稿最近为 [08:09]（差 3s ≤ 5）→ 通过。"""
    txt = make_txt_dir(tmp_path, ["08:09", "12:34"])
    report = check_v2(tmp_path, ts_note("[08:12]"), txt_dir=txt)
    assert warns_of(report, "6") == []


def test_ts_cross_minute_passes(tmp_path):
    """跨分钟：笔记 [08:02]=482s，转写稿最近 [07:59]=479s → 总秒数差 3 → 通过。"""
    txt = make_txt_dir(tmp_path, ["07:59", "12:34"])
    report = check_v2(tmp_path, ts_note("[08:02]"), txt_dir=txt)
    assert warns_of(report, "6") == []


def test_ts_off_whitelist_warns(tmp_path):
    """笔记 [12:34]，转写稿完全无此分钟 → warning（标注行与答案各 1 处）。"""
    txt = make_txt_dir(tmp_path, ["08:12"])
    report = check_v2(tmp_path, ts_note("[08:12]"), txt_dir=txt)
    w6 = warns_of(report, "6")
    assert len(w6) == 2
    assert all("12:34" in w["message"] for w in w6)
    # 检查6 是 warning 起步：绝不产生 error（ts_note 只有 1 道题，检查10 会报
    # error，但那与本检查无关）
    assert errs_of(report, "6") == []


def test_ts_missing_txt_dir_skips_with_warning(tmp_path):
    report = check_v2(tmp_path, ts_note("[08:12]"), txt_dir=None)
    w6 = warns_of(report, "6")
    assert w6 and "--txt-dir" in w6[0]["message"] and "跳过" in w6[0]["message"]
    # 检查15 同样跳过并警告
    assert warns_of(report, "15")


def test_ts_three_digit_minutes_parse(tmp_path):
    """[123:45]（超长讲座三位数分钟）→ 正常解析、白名单命中。"""
    txt = make_txt_dir(tmp_path, ["123:45", "12:34"])
    report = check_v2(tmp_path, ts_note("[123:45]"), txt_dir=txt)
    assert warns_of(report, "6") == []


def test_ts_malformed_formats_are_errors(tmp_path):
    for bad, in (("[8:5]",), ("[1234:56]",), ("[1:02:03]",)):
        report = check_v2(tmp_path, ts_note(bad))
        e4 = errs_of(report, "4")
        assert any("格式非法" in e["message"] for e in e4), bad


# ==================== 覆盖率分拆（检查7，§8） ====================

def test_coverage_split_error_even_with_unanchored_understand(tmp_path):
    """核心必考+重点掌握 3 行中 2 行带锚（0.67）→ error；了解即可无锚拉不动总覆盖率。"""
    text = lecture_note().replace(SEC1_TAGS, SEC1_TAGS_COVER_BAD)
    # 第二节降级：核心必考行删除、重点掌握改为无锚了解即可，使统计只剩 3 条（2 带锚）
    text = text.replace(
        "- **核心必考** ApplicationContext 在启动时预实例化全部单例 [16:02]\n", "")
    text = text.replace(
        "- **重点掌握** 两者都实现 BeanFactory 接口，前者是后者的超集 [16:30]",
        "- **了解即可** 两者都实现 BeanFactory 接口，前者是后者的超集")
    text = text.replace(
        "- **重点掌握** 容器启动流程分为扫描、注册、实例化三步 [17:10]",
        "- **了解即可** 容器启动流程分为扫描、注册、实例化三步")
    report = check_v2(tmp_path, text)
    e7 = errs_of(report, "7")
    assert len(e7) == 1
    assert "2/3" in e7[0]["message"]
    assert report["ok"] is False


def test_coverage_split_pass_with_unanchored_understand(tmp_path):
    """核心必考+重点掌握 全带锚，了解即可 2 行无锚 → 通过。"""
    text = lecture_note().replace(
        "- **了解即可** Lombok 的 @RequiredArgsConstructor 可替代手写构造注入",
        "- **了解即可** Lombok 的 @RequiredArgsConstructor 可替代手写构造注入\n"
        "- **了解即可** 配置元数据可来自多种来源")
    report = check_v2(tmp_path, text)
    assert errs_of(report, "7") == []
    assert report["checks"]["anchor_coverage"]["anchored"] == 5
    assert report["checks"]["anchor_coverage"]["total"] == 5


# ==================== 概念卡与自测题联动（L9/L10/L12，§8） ====================

def test_l10_core_card_not_covered_is_error(tmp_path):
    # q1 与 q5 都引用 ApplicationContext → 全部去掉才能让该核心必考卡失覆盖
    text = lecture_note().replace("（对应：ApplicationContext）", "")
    report = check_v2(tmp_path, text)
    e10 = errs_of(report, "L10")
    assert any("ApplicationContext" in e["message"] for e in e10)
    assert report["ok"] is False


def test_l9_any_level_card_can_be_referenced(tmp_path):
    """（对应：BeanFactory）引用的是重点掌握卡 → 通过（任意级别可引用）。"""
    report = check_v2(tmp_path, lecture_note())
    assert errs_of(report, "L9") == []
    assert "BeanFactory" in report["checks"]["quiz_correspond"]["covered"]


def test_l9_unknown_reference_is_error(tmp_path):
    """（对应：ApplicationContext␣）带尾随空格但卡集合无此概念 → L9 error。"""
    text = lecture_note().replace(
        "1. 在需要启动即失败的场景下，为什么选 ApplicationContext？（对应：ApplicationContext）",
        "1. 在需要启动即失败的场景下，为什么选 ApplicationContext？（对应：Spring MVC ）")
    report = check_v2(tmp_path, text)
    e9 = errs_of(report, "L9")
    assert any("Spring MVC" in e["message"] for e in e9)
    assert e9[0]["line"] > 0


def test_l4_section_number_missing_is_error(tmp_path):
    text = lecture_note().replace(
        "- 出现：1（15:40）、2（16:30）", "- 出现：1（15:40）、3（22:10）")
    report = check_v2(tmp_path, text)
    e4 = errs_of(report, "L4")
    assert any("小节 3" in e["message"] for e in e4)


def test_l5_single_section_card_warns(tmp_path):
    text = lecture_note().replace(
        "- 出现：1（08:45）、2（16:02）", "- 出现：1（08:45）")
    report = check_v2(tmp_path, text)
    w5 = warns_of(report, "L5")
    assert any("控制反转" in w["message"] for w in w5)
    assert "并入小节" in w5[0]["message"]
    assert report["ok"] is True  # warning 不挡交付


def test_l4_timestamp_outside_section_range_warns(tmp_path):
    """出现 的时间戳落在所属小节时间区间之外 → warning。"""
    text = lecture_note().replace(
        "- 出现：1（15:40）、2（16:30）", "- 出现：1（08:20）、2（16:30）")  # 小节1 起点 08:12 后又落在别节？08:20 在区间内
    # 改用明显越界：小节1 区间 [08:12, 16:02)，放 15:00 归属小节1 合法；改放 16:30（越上界）
    text = lecture_note().replace(
        "- 出现：1（08:45）、2（16:02）", "- 出现：1（15:00）、2（16:02）")
    # 15:00 仍在小节1 区间内 → 不报；真正越界：把小节2 的项放到 08:00（低于小节2 起点 16:02）
    text = lecture_note().replace(
        "- 出现：1（09:30）、2（16:02）", "- 出现：1（09:30）、2（08:00）")
    report = check_v2(tmp_path, text)
    w4 = warns_of(report, "L4")
    assert any("08:00" in w["message"] for w in w4)


def test_l12_core_line_contains_card_name(tmp_path):
    """听讲层核心必考行含核心必考卡名 → L12 通过；不含 → warning。"""
    report = check_v2(tmp_path, lecture_note())
    assert warns_of(report, "L12") == []
    text = lecture_note().replace(
        "- **核心必考** ApplicationContext 在启动时预实例化全部单例 [16:02]",
        "- **核心必考** 启动即失败的快速反馈机制 [16:02]")
    report2 = check_v2(tmp_path, text)
    w12 = warns_of(report2, "L12")
    assert len(w12) == 1
    assert "启动即失败" in w12[0]["message"]
    assert report2["ok"] is True


# ==================== 自测题格式（§8） ====================

def test_quiz_count_4_lecture_is_error(tmp_path):
    text = lecture_note().replace(
        "5. 什么是预实例化？（对应：ApplicationContext）\n"
        "<details><summary>答案</summary>\n\n"
        "**答案：** 容器启动时创建全部单例。[16:02]\n\n</details>\n\n", "")
    report = check_v2(tmp_path, text)
    assert any("4" in e["message"] and "5~9" in e["message"]
               for e in errs_of(report, "10"))


def test_quiz_count_4_short_is_error(tmp_path):
    text = short_note() + """
3. 第三问是什么？
<details><summary>答案</summary>

**答案：** 内容。[00:10]

</details>

4. 第四问是什么？
<details><summary>答案</summary>

**答案：** 内容。[00:20]

</details>
"""
    report = check_v2(tmp_path, text)
    assert any("2~3" in e["message"] for e in errs_of(report, "10"))


def test_quiz_missing_details_is_error(tmp_path):
    text = lecture_note().replace(
        "2. 解释控制反转与依赖注入的关系。（对应：控制反转）\n"
        "<details><summary>答案</summary>\n\n"
        "**答案：** 控制反转是设计原则，依赖注入是它的实现手段。[08:45]\n\n</details>\n\n",
        "2. 解释控制反转与依赖注入的关系。（对应：控制反转）\n\n")
    report = check_v2(tmp_path, text)
    e11 = errs_of(report, "11")
    assert any("details" in e["message"] for e in e11)


def test_quiz_details_without_answer_is_error(tmp_path):
    text = lecture_note().replace(
        "**答案：** ApplicationContext 在启动时预实例化全部单例，配置错误启动即暴露。[16:02]\n",
        "")
    report = check_v2(tmp_path, text)
    e11 = errs_of(report, "11")
    assert any("答案：**" in e["message"] or "答案" in e["message"] for e in e11)


def test_quiz_answer_multiline_warns(tmp_path):
    text = lecture_note().replace(
        "**答案：** ApplicationContext 在启动时预实例化全部单例，配置错误启动即暴露。[16:02]",
        "**答案：** ApplicationContext 在启动时预实例化全部单例。[16:02]\n"
        "补充的第二行答案正文，使答案超过一行。")
    report = check_v2(tmp_path, text)
    assert any("超过一行" in w["message"] for w in warns_of(report, "11"))


def test_quiz_answer_without_anchor_warns(tmp_path):
    text = lecture_note().replace(
        "**答案：** ApplicationContext 在启动时预实例化全部单例，配置错误启动即暴露。[16:02]",
        "**答案：** ApplicationContext 在启动时预实例化全部单例，配置错误启动即暴露。")
    report = check_v2(tmp_path, text)
    assert any("缺少 [mm:ss] 证据锚点" in w["message"] for w in warns_of(report, "11"))


def test_quiz_yesno_stem_warns(tmp_path):
    text = lecture_note().replace(
        "4. 依赖注入有哪些常见形式？（对应：控制反转）",
        "4. 是否应该总是使用依赖注入？（对应：控制反转）")
    report = check_v2(tmp_path, text)
    assert any("yes/no" in w["message"] for w in warns_of(report, "11"))


def test_lecture_question_without_correspond_is_l9_error(tmp_path):
    text = lecture_note().replace(
        "3. BeanFactory 与 ApplicationContext 有何区别？（对应：BeanFactory）",
        "3. BeanFactory 与 ApplicationContext 有何区别？")
    report = check_v2(tmp_path, text)
    e9 = errs_of(report, "L9")
    assert any("缺少「（对应：概念名）」" in e["message"] for e in e9)


# ==================== 概念卡字段与标题（L1~L3, L6, L7, L8, L11） ====================

def test_l2_few_cards_warns(tmp_path):
    # 移除两张卡，只剩 1 张 → L2 warning（≥2 张）
    text = lecture_note().replace(
        "### ApplicationContext（核心必考）\n\n- 定义：BeanFactory 的完整实现，"
        "启动即预实例化全部单例\n- 出现：1（09:30）、2（16:02）\n- 依赖：BeanFactory\n"
        "- 易混：BeanFactory——预实例化 vs 惰性加载\n\n", "")
    text = text.replace(
        "### BeanFactory（重点掌握）\n\n- 定义：Spring 最基础的容器实现\n"
        "- 出现：1（15:40）、2（16:30）\n- 依赖：\n- 易混：\n\n", "")
    report = check_v2(tmp_path, text)
    w2 = warns_of(report, "L2")
    assert w2 and "仅 1 张" in w2[0]["message"]
    # 注意：删卡后自测题仍引用已删卡名，会另有 L9 error（与本测试无关，不冲突）


def test_l3_empty_definition_is_error(tmp_path):
    text = lecture_note().replace(
        "- 定义：Spring 最基础的容器实现", "- 定义：")
    report = check_v2(tmp_path, text)
    assert any("非空「定义」" in e["message"] for e in errs_of(report, "L3"))


def test_l3_missing_appearance_is_error(tmp_path):
    text = lecture_note().replace(
        "### BeanFactory（重点掌握）\n\n- 定义：Spring 最基础的容器实现\n"
        "- 出现：1（15:40）、2（16:30）\n",
        "### BeanFactory（重点掌握）\n\n- 定义：Spring 最基础的容器实现\n")
    report = check_v2(tmp_path, text)
    assert any("非空「出现」" in e["message"] for e in errs_of(report, "L3"))


def test_l3_bad_card_title_is_error(tmp_path):
    text = lecture_note().replace("### BeanFactory（重点掌握）",
                                  "### BeanFactory（必考）")
    report = check_v2(tmp_path, text)
    assert any("概念卡标题格式非法" in e["message"] for e in errs_of(report, "L3"))


def test_l6_reference_outside_card_set_warns(tmp_path):
    text = lecture_note().replace(
        "- 依赖：BeanFactory", "- 依赖：事件发布机制")
    report = check_v2(tmp_path, text)
    w6 = warns_of(report, "L6")
    assert any("事件发布机制" in w["message"] for w in w6)


def test_l7_missing_knowledge_map_is_error(tmp_path):
    report = check_v2(tmp_path, lecture_note().replace("## 本讲知识地图", "## 知识图谱X"))
    assert errs_of(report, "L7")


def test_l7_bad_mermaid_first_line_is_error(tmp_path):
    text = lecture_note().replace("mindmap\n  root((Spring 容器))",
                                  "pie\n  title 占位")
    report = check_v2(tmp_path, text)
    assert any("mindmap/graph/flowchart" in e["message"] for e in errs_of(report, "L7"))


def test_l7_mindmap_needs_two_indent_levels(tmp_path):
    text = lecture_note().replace(
        "mindmap\n  root((Spring 容器))\n    三大特性\n      控制反转\n      依赖注入\n"
        "    容器实现\n      ApplicationContext",
        "mindmap\nroot((Spring 容器))\n三大特性\n容器实现")
    report = check_v2(tmp_path, text)
    assert any("缩进" in e["message"] for e in errs_of(report, "L7"))


def test_l8_missing_nav_timestamps_warns(tmp_path):
    text = lecture_note()
    # 连 NAV 标记一起去掉（整个尾部区块）
    idx = text.index("<!-- NAV:BEGIN")
    text = text[:idx].rstrip() + "\n"
    report = check_v2(tmp_path, text)
    assert warns_of(report, "L8")


def test_l8_few_deep_links_warns(tmp_path):
    text = lecture_note().replace(
        "- [16:02](https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=962) 核心必考：ApplicationContext\n",
        "- 16:02 核心必考：ApplicationContext\n")
    report = check_v2(tmp_path, text)
    w8 = warns_of(report, "L8")
    assert any("深链" in w["message"] for w in w8)


def test_l11_nav_markers_present_passes(tmp_path):
    report = check_v2(tmp_path, lecture_note())
    assert warns_of(report, "L11") == []


def test_l11_missing_nav_markers_warns(tmp_path):
    text = lecture_note().replace(
        "<!-- NAV:BEGIN 由 scripts/note_nav.py 生成，请勿手工编辑 -->\n", ""
    ).replace("<!-- NAV:END -->\n", "")
    report = check_v2(tmp_path, text)
    w11 = warns_of(report, "L11")
    assert any("NAV:BEGIN/END" in w["message"] for w in w11)


def test_l11_unpaired_nav_markers_warns(tmp_path):
    text = lecture_note().replace("<!-- NAV:END -->\n", "")
    report = check_v2(tmp_path, text)
    assert any("不配对" in w["message"] for w in warns_of(report, "L11"))


def test_l11_timestamps_outside_markers_warns(tmp_path):
    text = lecture_note().replace(
        "<!-- NAV:BEGIN 由 scripts/note_nav.py 生成，请勿手工编辑 -->\n## 参考时间戳",
        "## 参考时间戳\n\n<!-- NAV:BEGIN 由 scripts/note_nav.py 生成，请勿手工编辑 -->")
    report = check_v2(tmp_path, text)
    assert any("未位于" in w["message"] for w in warns_of(report, "L11"))


# ==================== short 分型检查（S1~S3，§8） ====================

def test_s1_too_few_tags_is_error(tmp_path):
    text = short_note().replace(
        "- **重点掌握** 只有单例 Bean 才走三级缓存 [00:20]\n", "")
    report = check_v2(tmp_path, text)
    assert any("2~6" in e["message"] for e in errs_of(report, "S1"))


def test_s1_too_many_tags_is_error(tmp_path):
    extra = "".join(
        f"- **重点掌握** 补充要点{i} [00:20]\n" for i in range(5))
    text = short_note().replace(
        "- **重点掌握** 只有单例 Bean 才走三级缓存 [00:20]",
        "- **重点掌握** 只有单例 Bean 才走三级缓存 [00:20]\n" + extra.rstrip("\n"))
    report = check_v2(tmp_path, text)
    assert any("2~6" in e["message"] for e in errs_of(report, "S1"))


def test_s2_more_than_3_cards_warns(tmp_path):
    cards = "\n\n## 概念卡片\n\n" + "\n\n".join(
        f"### 概念{i}（重点掌握）\n\n- 定义：测试定义{i}\n- 出现：1（00:10）"
        for i in range(4))
    text = short_note().replace("## 自测题（复习用）", "") + cards + "\n\n## 自测题（复习用）"
    report = check_v2(tmp_path, text)
    w2 = warns_of(report, "S2")
    assert w2 and "≤ 3" in w2[0]["message"]


def test_s3_knowledge_map_optional_for_short(tmp_path):
    """short 无知识地图/参考时间戳 → 不报（S3 豁免），检查9 也豁免。"""
    report = check_v2(tmp_path, short_note())
    assert errs_of(report, "L7") == []
    assert warns_of(report, "L8") == []
    assert warns_of(report, "9") == []
    assert report["checks"]["short_exemptions"]["passed"] is True


# ==================== 检查2 / 检查3 / 检查13 补充 ====================

def test_frontmatter_missing_fields_are_errors(tmp_path):
    text = lecture_note().replace("platform: bilibili\n", "").replace(
        "created: 2026-10-03\n", "")
    report = check_v2(tmp_path, text)
    e1 = errs_of(report, "1")
    assert {e["message"] for e in e1} >= {
        "frontmatter 缺少 platform 字段", "frontmatter 缺少 created 字段"}


def test_invalid_note_type_is_error(tmp_path):
    text = lecture_note().replace("note_type: lecture", "note_type: tutorial")
    report = check_v2(tmp_path, text)
    assert any("tutorial" in e["message"] for e in errs_of(report, "2"))


def test_thin_section_is_error(tmp_path):
    text = lecture_note().replace(
        "**核心思想：** 控制反转与依赖注入是同一件事的两种说法。\n\n"
        f"{SEC1_TAGS}\n\n```java\n// 来源：仓库 demo/OrderService.java\n"
        "@Service\npublic class OrderService { }\n```",
        "**核心思想：** 一句话。")
    report = check_v2(tmp_path, text)
    e3 = errs_of(report, "3")
    assert any("第一节" in e["message"] for e in e3)


def test_missing_image_is_error(tmp_path):
    text = lecture_note().replace(
        "**核心思想：** 两者是同一继承体系的不同完成度。",
        "**核心思想：** 两者是同一继承体系的不同完成度。\n\n![不存在](images/ghost.png)")
    report = check_v2(tmp_path, text)
    assert any("images/ghost.png" in e["message"] for e in errs_of(report, "13"))


def test_check14_reused_in_v2(tmp_path):
    """检查14 复用 check_image_manifest：keyword 不匹配 → warning（check=14）。"""
    tmp = tmp_path
    (tmp / "images").mkdir()
    (tmp / "images" / "p01_a.png").write_bytes(b"\x89PNG fake")
    (tmp / "images" / "manifest.json").write_text(json.dumps({
        "frames": [{"file": "p01_a.png", "keyword": "循环依赖", "t": 15}]
    }, ensure_ascii=False), encoding="utf-8")
    text = lecture_note().replace(
        "**核心思想：** 两者是同一继承体系的不同完成度。",
        "**核心思想：** 两者是同一继承体系的不同完成度。\n\n- **核心必考** 三级缓存的缓存映射 [16:05]\n\n"
        "![完全无关的画面](images/p01_a.png)")
    report = check_v2(tmp_path, text)
    w14 = warns_of(report, "14")
    assert w14 and "循环依赖" in w14[0]["message"]
