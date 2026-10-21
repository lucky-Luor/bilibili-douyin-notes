# -*- coding: utf-8 -*-
"""validate_note.py v4 门禁（语雀直导版式）的离线测试。

覆盖：合规样例零 error、CLI 端到端、版本分派（--template v4 / 不带参数默认 v4）、
V4-FM/HEAD/NAME/LIST/IDEA/E1~E7/KM 每条规则至少一个反例。
"""
import json  # 写 metadata.json
import subprocess  # 跑 CLI
import sys  # 取当前解释器
from pathlib import Path  # 路径处理

import validate_note  # 被测模块（conftest 已把 scripts/ 加入 sys.path）

ROOT = Path(__file__).resolve().parents[1]  # 仓库根目录

# 合规 v4 lecture 笔记：无 frontmatter / 无一级标题 / + 列表 / 答案与索引不折叠
GOOD = """> 核心主线：用例是散落的珠子、活动图是串起它们的线。
> 课程来源：面向对象分析与设计（B站 BV1zMpaeTEJa，UP主：测试UP，选集 2.13，约 26 分钟）

## 目录
+ [1. 从用例到活动图](#1-从用例到活动图)
+ [2. 活动图的颜色语义](#2-活动图的颜色语义)
+ [本讲知识地图](#本讲知识地图)
+ [概念卡片](#概念卡片)
+ [自测题（复习用）](#自测题复习用)
+ [视频时间索引](#视频时间索引)

---

## 1. 从用例到活动图
**核心思想：** 用例是散的，描述协作与先后顺序需要活动图。

+ 业务流程讲的是事情的**先后顺序**
+ EBP 切分后用例全是散的，用例之间缺一种描述方式
+ 描述主参与者协作的工具就是**活动图**

## 2. 活动图的颜色语义
**核心思想：** action 的语义由使用者定义，课程用颜色赋予含义。

+ **action** 圆角矩形是活动图最基本的符号
+ 浅蓝是用例、黄色是系统外、红色是内部实现
+ 颜色约定因人因公司而异，没有固定方式

## 本讲知识地图
```mermaid
graph TD
  R((业务流程建模))
  R --> G1[符号]
  G1 --> L2[action]
  R --> G3[颜色语义]
```

## 概念卡片
### 活动图
+ 定义：描述协作与先后顺序的 UML 图
+ 出现：第 1 节「从用例到活动图」、第 2 节「活动图的颜色语义」
+ 依赖：颜色语义
+ 易混：颜色语义——前者是图，后者是约定

### 颜色语义
+ 定义：课程为 action 约定的额外含义
+ 出现：第 2 节「活动图的颜色语义」、第 1 节「从用例到活动图」
+ 依赖：活动图
+ 易混：

## 自测题（复习用）
1. 为什么基于用例的分析需要活动图来补充？（对应：活动图）

**答案：** 用例图无法表达协作与先后，需要活动图串起用例。

2. 课程的颜色约定分别表示什么？（对应：颜色语义）

**答案：** 浅蓝是用例、黄色是系统外活动、红色是系统内部实现。

3. 活动图描述的核心关系是什么？（对应：活动图）

**答案：** 主参与者之间的协作与事情的先后顺序。

4. 为什么 action 的语义需要使用者自己定义？（对应：颜色语义）

**答案：** 活动图本身不定义 action 含义，语义由使用者赋予。

---

## 视频时间索引
+ [00:00](https://www.bilibili.com/video/BV1zMpaeTEJa?p=13&t=0) 1. 从用例到活动图
+ [05:10](https://www.bilibili.com/video/BV1zMpaeTEJa?p=13&t=305) 2. 活动图的颜色语义
"""

NAME = "2.13-业务流程建模.md"  # 合规文件名


def make_env(tmp_path, text=GOOD, name=NAME, platform="bilibili"):
    """写出笔记、metadata.json 与转写稿目录，返回 (笔记路径, meta 路径, txt 目录)。"""
    note = tmp_path / name  # 笔记路径
    note.write_text(text, encoding="utf-8")  # 写笔记
    meta = tmp_path / "meta.json"  # metadata 路径
    meta.write_text(json.dumps({"platform": platform, "bvid": "BV1zMpaeTEJa",  # 平台与视频号
                                "suggested_note_type": "lecture"}), encoding="utf-8")  # 建议类型
    txt = tmp_path / "txt"  # 转写稿目录
    txt.mkdir(exist_ok=True)  # 创建
    (txt / "13_P13.txt").write_text("[00:00] 开场\n[05:05] 颜色\n" + "转写正文" * 300,
                                    encoding="utf-8")  # 第 13 分P转写稿（含两条时间行）
    return note, meta, txt  # 返回三个路径


def run(tmp_path, text=GOOD, name=NAME, note_type=None, platform="bilibili"):
    """走分派入口跑 v4 门禁，返回报告。"""
    note, meta, txt = make_env(tmp_path, text, name, platform)  # 准备文件
    return validate_note.check_note_dispatch(text, note, meta, txt, only_page=13,
                                             template="v4", note_type=note_type)  # v4 分派


def errs(report, cid):
    """取某检查编号的 error 列表。"""
    return [e for e in report["errors"] if e["check"] == cid]  # 过滤


def warns(report, cid):
    """取某检查编号的 warning 列表。"""
    return [w for w in report["warnings"] if w["check"] == cid]  # 过滤


# ---------- 合规样例 / 分派 / CLI ----------

def test_good_note_zero_errors_zero_warnings(tmp_path):
    r = run(tmp_path)  # 跑合规笔记
    assert r["ok"], r["errors"]  # 零 error
    assert r["template_version"] == "v4" and r["note_type"] == "lecture"  # 报告字段
    assert r["warnings"] == [], r["warnings"]  # 零 warning


def test_without_template_flag_defaults_to_v4(tmp_path):
    note, meta, txt = make_env(tmp_path)  # 准备文件
    r = validate_note.check_note_dispatch(GOOD, note, meta, txt, only_page=13)  # 不传 template
    assert r["template_version"] == "v4" and r["ok"], r["errors"]  # 无 frontmatter 默认走 v4


def test_cli_end_to_end(tmp_path):
    note, meta, txt = make_env(tmp_path)  # 准备文件
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_note.py"),  # 调 CLI
                           str(note), "--template", "v4", "--meta", str(meta),
                           "--txt-dir", str(txt), "--page", "13"],
                          capture_output=True, text=True, encoding="utf-8")  # 捕获输出
    assert proc.returncode == 0, proc.stdout  # 通过即退出 0
    assert json.loads(proc.stdout)["template_version"] == "v4"  # 走了 v4


def test_note_type_short_from_flag(tmp_path):
    text = GOOD.split("## 视频时间索引")[0]  # 去掉时间索引
    text = text.replace("4. 为什么 action", "X. 为什么 action")  # 第 4 题题干失去编号
    r = run(tmp_path, text, note_type="short")  # short：索引可省
    assert not errs(r, "V4-E2")  # 不要求时间索引
    assert r["note_type"] == "short"  # 参数生效


# ---------- 版式规则 ----------

def test_frontmatter_is_error(tmp_path):
    r = run(tmp_path, "---\ntemplate_version: v3\n---\n\n" + GOOD)  # 加 frontmatter
    assert errs(r, "V4-FM")  # 报 V4-FM


def test_h1_title_is_error(tmp_path):
    r = run(tmp_path, GOOD.replace("## 目录", "# 课堂笔记 01：主题\n\n## 目录"))  # 插入一级标题
    assert errs(r, "V4-HEAD")  # 报 V4-HEAD


def test_first_line_must_be_main_quote(tmp_path):
    r = run(tmp_path, "随便一句开场\n\n" + GOOD)  # 首行不是核心主线
    assert errs(r, "V4-HEAD")  # 报 V4-HEAD


def test_missing_course_source_is_error(tmp_path):
    r = run(tmp_path, GOOD.replace("> 课程来源：", "> 来源说明："))  # 去掉课程来源
    assert errs(r, "V4-HEAD")  # 报 V4-HEAD


def test_yuque_lazy_continuation_quote_accepted(tmp_path):
    r = run(tmp_path, GOOD.replace("> 课程来源：", "课程来源："))  # 语雀导出的无 > 续行
    assert not errs(r, "V4-HEAD")  # 视为同一引语段


def test_bad_file_name_warns(tmp_path):
    r = run(tmp_path, name="课堂笔记-业务流程.md")  # 不合规文件名
    assert warns(r, "V4-NAME")  # 提示
    assert r["ok"]  # 只是 warning


def test_dash_list_warns(tmp_path):
    r = run(tmp_path, GOOD.replace("+ 业务流程讲的是", "- 业务流程讲的是"))  # 改用 - 列表
    assert warns(r, "V4-LIST")  # 提示


def test_blank_line_after_heading_warns(tmp_path):
    r = run(tmp_path, GOOD.replace("## 1. 从用例到活动图\n**核心思想",
                                   "## 1. 从用例到活动图\n\n**核心思想"))  # 标题后空行
    assert warns(r, "V4-IDEA") and not errs(r, "V4-IDEA")  # 只提示不报错


def test_missing_core_idea_is_error(tmp_path):
    r = run(tmp_path, GOOD.replace("**核心思想：** 用例是散的", "用例是散的"))  # 去掉核心思想
    assert errs(r, "V4-IDEA")  # 报错


# ---------- 内容规则 ----------

def test_body_timestamp_is_error(tmp_path):
    r = run(tmp_path, GOOD.replace("+ 业务流程讲的是", "+ [03:12] 业务流程讲的是"))  # 正文时刻
    assert errs(r, "V4-E1")  # 报错


def test_tag_residue_is_error(tmp_path):
    r = run(tmp_path, GOOD.replace("+ 业务流程讲的是", "+ **核心必考** 业务流程讲的是"))  # 标签残留
    assert errs(r, "V4-E4")  # 报错


def test_appear_v3_style_is_error(tmp_path):
    r = run(tmp_path, GOOD.replace(
        "+ 出现：第 1 节「从用例到活动图」、第 2 节「活动图的颜色语义」", "+ 出现：1、2"))  # v3 写法
    assert errs(r, "V4-E6")  # 报错


def test_appear_unknown_section_is_error(tmp_path):
    r = run(tmp_path, GOOD.replace("第 1 节「从用例到活动图」、第 2 节「活动图的颜色语义」",
                                   "第 1 节「从用例到活动图」、第 9 节「不存在」"))  # 引用不存在的节
    assert errs(r, "V4-E6")  # 报错


def test_appear_title_mismatch_warns(tmp_path):
    r = run(tmp_path, GOOD.replace("第 1 节「从用例到活动图」、第 2 节「活动图的颜色语义」",
                                   "第 1 节「用例」、第 2 节「活动图的颜色语义」"))  # 节名不一致
    assert warns(r, "V4-E6") and not errs(r, "V4-E6")  # 只提示


def test_quiz_details_is_error(tmp_path):
    r = run(tmp_path, GOOD.replace(
        "**答案：** 用例图无法表达",
        "<details><summary>答案</summary>\n\n**答案：** 用例图无法表达"))  # 使用折叠
    assert errs(r, "V4-E5")  # 报错


def test_quiz_missing_answer_is_error(tmp_path):
    r = run(tmp_path, GOOD.replace("**答案：** 用例图无法表达", "用例图无法表达"))  # 去掉答案前缀
    assert errs(r, "V4-E5")  # 报错


def test_quiz_count_out_of_range_is_error(tmp_path):
    text = GOOD.replace("4. 为什么 action", "为什么 action")  # 第 4 题失去编号，只剩 3 道
    r = run(tmp_path, text)  # lecture 要 4~7 道
    assert errs(r, "V4-E5")  # 报错


def test_missing_index_lecture_is_error(tmp_path):
    r = run(tmp_path, GOOD.split("---\n\n## 视频时间索引")[0])  # 去掉时间索引
    assert errs(r, "V4-E2")  # lecture 报错


def test_index_details_and_nav_markers_are_errors(tmp_path):
    text = GOOD.replace("## 视频时间索引\n",
                        "<!-- NAV:BEGIN x -->\n## 视频时间索引\n<details>\n")  # 加 NAV 与折叠
    r = run(tmp_path, text + "</details>\n<!-- NAV:END -->\n")  # 闭合
    msgs = " ".join(e["message"] for e in errs(r, "V4-E2"))  # 汇总 E2 报错
    assert "NAV" in msgs and "<details>" in msgs  # 两类都报


def test_index_not_last_section_is_error(tmp_path):
    r = run(tmp_path, GOOD + "\n## 附录\n多余内容\n")  # 索引后还有小节
    assert any("最后一节" in e["message"] for e in errs(r, "V4-E2"))  # 报错


def test_index_entry_off_whitelist_is_error(tmp_path):
    r = run(tmp_path, GOOD.replace("+ [05:10](https://www.bilibili.com/video/BV1zMpaeTEJa?p=13&t=305)",
                                   "+ [09:59]"))  # 转写稿中不存在的时刻
    assert errs(r, "V4-E2")  # 报错


def test_wrong_deep_link_t_is_error(tmp_path):
    r = run(tmp_path, GOOD.replace("p=13&t=305", "p=13&t=999"))  # t 错误
    assert errs(r, "V4-E3")  # 报错


def test_missing_link_bilibili_lecture_warns(tmp_path):
    r = run(tmp_path, GOOD.replace("(https://www.bilibili.com/video/BV1zMpaeTEJa?p=13&t=305)", ""))  # 去链接
    assert warns(r, "V4-E3") and r["ok"]  # 只提示


def test_douyin_plain_entries_no_link_warning(tmp_path):
    text = GOOD.replace("(https://www.bilibili.com/video/BV1zMpaeTEJa?p=13&t=0)", "")  # 去链接
    text = text.replace("(https://www.bilibili.com/video/BV1zMpaeTEJa?p=13&t=305)", "")  # 去链接
    r = run(tmp_path, text, platform="douyin")  # 抖音无深链
    assert not warns(r, "V4-E3")  # 不提示缺链


def test_knowledge_map_missing_mermaid_is_error(tmp_path):
    start = GOOD.index("```mermaid")  # mermaid 起点
    end = GOOD.index("```", start + 3) + 3  # mermaid 终点
    r = run(tmp_path, GOOD[:start] + "![](https://cdn.nlark.com/x.svg)\n+ 补一行\n+ 再补一行"
            + GOOD[end:])  # 换成图片
    assert errs(r, "V4-KM")  # 报错


def test_unclosed_fence_is_error(tmp_path):
    r = run(tmp_path, GOOD + "\n```python\nprint(1)\n")  # 未闭合代码块
    assert errs(r, "V4-E7")  # 报错
