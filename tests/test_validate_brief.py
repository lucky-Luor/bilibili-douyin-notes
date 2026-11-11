# -*- coding: utf-8 -*-
"""brief（轻量笔记）门禁与深链的离线测试。

覆盖：合规 brief 零 error / 零 warning；V4B-POINTS / V4B-EMPTY / V4B-NUM / V4B-CARDS / V4B-QUIZ；
brief 跳过 V4-SEC / V4-IDEA / V4-SUM / V4-KM、概念卡「出现」；照常跑 V4-E1 / V4-E5 / V4-QA /
V4-ANALOGY / V4-YUQUE / V4-FM；类型来源优先级；v4-obsidian 的 note_type: brief；CLI；note_nav。
"""
import json  # 写 metadata.json / 解析摘要
import subprocess  # 跑 CLI
import sys  # 取当前解释器
from pathlib import Path  # 路径处理

import note_nav  # 被测模块（conftest 已把 scripts/ 加入 sys.path）
import validate_note  # 被测模块

ROOT = Path(__file__).resolve().parents[1]  # 仓库根目录
NAME = "01-两分钟学会番茄钟.md"  # 合规文件名（<序号>-<主题>.md）

# 合规 brief 笔记：虚构的 2 分钟抖音短视频，不引用真实作者
GOOD = """> 核心主线：番茄钟把注意力切成 25 分钟一段，用固定的休息换来持续的专注。
> 课程来源：抖音《两分钟学会番茄钟》（作者：示例作者，约 2 分钟）

## 要点
+ 一个番茄钟由 25 分钟专注和 5 分钟休息组成
+ 专注期间只做一件事，被打断的念头先记下来稍后处理
+ 每完成 4 个番茄钟，安排一次 15 到 30 分钟的长休息
+ 番茄钟的价值在于让开始变容易，而不是逼自己坚持更久

## 细节
| 阶段 | 时长 | 做什么 |
| --- | --- | --- |
| 专注 | 25 分钟 | 只做一件事 |
| 短休息 | 5 分钟 | 离开屏幕活动一下 |

## 概念卡片
### 番茄钟
+ 定义：25 分钟专注加 5 分钟休息组成的一个时间单元

## 自测题（复习用）
1. 番茄钟为什么能帮助拖延的人开始工作？

**答案：** 因为它把任务缩成只要坚持 25 分钟的一小段。开始的门槛变低了，人就更愿意动手；而固定的休息又让人知道很快能停下来，不会一想到长时间工作就抗拒。

---

## 视频时间索引
+ [00:05] 要点
+ [01:10] 细节
"""

META = {"platform": "douyin", "video_id": "7300000000000000000",  # 抖音元数据（无深链语义）
        "suggested_note_type": "brief"}  # 抓取脚本的建议类型


def make_env(tmp_path, text=GOOD, meta=None):
    """写出笔记、metadata.json 与转写稿目录，返回 (笔记路径, meta 路径, txt 目录)。"""
    note = tmp_path / NAME  # 笔记路径
    note.write_text(text, encoding="utf-8")  # 写笔记
    meta_path = tmp_path / "meta.json"  # metadata 路径
    meta_path.write_text(json.dumps(meta or META), encoding="utf-8")  # 写元数据
    txt = tmp_path / "txt"  # 转写稿目录
    txt.mkdir(exist_ok=True)  # 创建
    (txt / "01_P1.txt").write_text("[00:05] 番茄钟是什么\n[01:10] 怎么安排休息\n" + "转写正文" * 150,
                                   encoding="utf-8")  # 单P转写稿（含两条时间行）
    return note, meta_path, txt  # 返回三个路径


def run(tmp_path, text=GOOD, note_type=None, meta=None, template="v4"):
    """走分派入口跑门禁，返回报告。"""
    note, meta_path, txt = make_env(tmp_path, text, meta)  # 准备文件
    return validate_note.check_note_dispatch(text, note, meta_path, txt, only_page=1,
                                             template=template, note_type=note_type)  # 分派到 v4


def errs(report, cid):
    """取某检查编号的 error 列表。"""
    return [e for e in report["errors"] if e["check"] == cid]  # 过滤


def warns(report, cid):
    """取某检查编号的 warning 列表。"""
    return [w for w in report["warnings"] if w["check"] == cid]  # 过滤


def points(n, text="这是一条足够长、能独立复述的要点内容"):
    """生成 n 条要点组成的「## 要点」小节正文。"""
    return "\n".join(f"+ 第{i + 1}条：{text}" for i in range(n))  # + 列表


def swap_points(body):
    """把 GOOD 的要点节正文换成 body。"""
    head, rest = GOOD.split("## 要点\n", 1)  # 要点节之前
    _, tail = rest.split("\n\n## 细节", 1)  # 要点节之后
    return head + "## 要点\n" + body + "\n\n## 细节" + tail  # 拼回


# ---------------- 合规样例与类型来源 ----------------

def test_good_brief_has_no_errors_or_warnings(tmp_path):
    r = run(tmp_path)  # metadata 建议 brief
    assert r["ok"] and r["note_type"] == "brief", r["errors"]  # 通过且类型为 brief
    assert r["warnings"] == [], r["warnings"]  # 零 warning
    assert r["checks"]["brief_points"] == {"passed": True, "found": True, "count": 4}  # 要点明细


def test_flag_overrides_metadata(tmp_path):
    r = run(tmp_path, note_type="brief", meta=dict(META, suggested_note_type="lecture"))  # 参数优先
    assert r["ok"] and r["note_type"] == "brief"  # 参数生效


def test_same_note_as_lecture_fails(tmp_path):
    r = run(tmp_path, note_type="lecture")  # 同一篇按 lecture 检查
    assert not r["ok"]  # lecture 规则不放过轻量结构
    assert errs(r, "V4-E6") and errs(r, "V4-E5")  # 概念卡缺「出现」、自测题数量不足


def test_unknown_type_still_rejected(tmp_path):
    r = run(tmp_path, meta=dict(META, suggested_note_type="tiny"))  # 非法类型
    assert errs(r, "V4-NT")  # 仍报 V4-NT


# ---------------- V4B-POINTS ----------------

def test_missing_points_section(tmp_path):
    text = GOOD.replace("## 要点\n", "## 主要内容\n")  # 标题不是「要点」
    r = run(tmp_path, text.replace("+ [00:05] 要点", "+ [00:05] 主要内容"))  # 索引标题同步
    assert errs(r, "V4B-POINTS") and r["checks"]["brief_points"]["found"] is False  # 缺要点节


def test_points_title_must_be_exact(tmp_path):
    text = GOOD.replace("## 要点\n", "## 要点回顾\n")  # 只是包含「要点」
    r = run(tmp_path, text.replace("+ [00:05] 要点", "+ [00:05] 要点回顾"))  # 索引标题同步
    assert errs(r, "V4B-POINTS")  # 精确匹配才算


def test_too_few_points(tmp_path):
    r = run(tmp_path, swap_points(points(2)))  # 2 条
    assert any("要求 3~7 条" in e["message"] for e in errs(r, "V4B-POINTS"))  # 条数越界


def test_too_many_points(tmp_path):
    r = run(tmp_path, swap_points(points(8)))  # 8 条
    assert any("有 8 条" in e["message"] for e in errs(r, "V4B-POINTS"))  # 条数越界


def test_boundary_point_counts_pass(tmp_path):
    for n in (3, 7):  # 上下限都合法
        r = run(tmp_path, swap_points(points(n)))  # 生成 n 条
        assert not errs(r, "V4B-POINTS"), (n, r["errors"])  # 不报错


def test_short_point(tmp_path):
    body = points(3) + "\n+ 太短了"  # 第 4 条只有 3 个字
    r = run(tmp_path, swap_points(body))  # 跑门禁
    bad = errs(r, "V4B-POINTS")  # 要点错误
    assert len(bad) == 1 and "只有 3 字" in bad[0]["message"] and bad[0]["line"] is not None  # 定位到该行


def test_points_count_ignores_code_block(tmp_path):
    body = points(3) + "\n```plain\n+ 代码块里的加号不算要点条目\n```"  # 代码块内的 + 行
    r = run(tmp_path, swap_points(body))  # 跑门禁
    assert not errs(r, "V4B-POINTS") and r["checks"]["brief_points"]["count"] == 3  # 只数代码块外


# ---------------- V4B-EMPTY / V4B-NUM / 跳过的长课检查 ----------------

def test_empty_optional_section(tmp_path):
    text = GOOD.replace("## 概念卡片\n### 番茄钟\n+ 定义：25 分钟专注加 5 分钟休息组成的一个时间单元\n",
                        "## 概念卡片\n### 番茄钟\n")  # 只剩卡片标题，没有内容
    r = run(tmp_path, text)  # 跑门禁
    assert any("概念卡片" in e["message"] for e in errs(r, "V4B-EMPTY"))  # 空小节报错


def test_empty_details_section(tmp_path):
    head, tail = GOOD.split("## 细节\n", 1)  # 细节节前后
    text = head + "## 细节\n\n---\n\n## 概念卡片" + tail.split("## 概念卡片", 1)[1]  # 细节节只剩分隔线
    r = run(tmp_path, text.replace("+ [01:10] 细节\n", ""))  # 索引里去掉细节条目
    assert any("细节" in e["message"] for e in errs(r, "V4B-EMPTY"))  # 分隔线不算内容


def test_numbered_section_warns_and_skips_sec(tmp_path):
    text = GOOD.replace("## 细节\n", "## 1. 细节\n")  # 编号小节
    r = run(tmp_path, text.replace("+ [01:10] 细节", "+ [01:10] 1. 细节"))  # 索引标题同步
    assert warns(r, "V4B-NUM") and not errs(r, "V4-SEC")  # 只提示改用 short


def test_long_lecture_checks_skipped(tmp_path):
    r = run(tmp_path)  # 合规 brief
    for cid in ("V4-SEC", "V4-IDEA", "V4-SUM", "V4-KM", "V4-KM-REL"):  # 长课结构检查
        assert not errs(r, cid) and not warns(r, cid), cid  # 都不出现
    assert r["checks"]["summary"]["checked"] is False  # 小结明细标明未检查


# ---------------- 概念卡与自测题 ----------------

def test_card_without_appearance_is_fine_but_definition_required(tmp_path):
    text = GOOD.replace("+ 定义：25 分钟专注加 5 分钟休息组成的一个时间单元\n",
                        "+ 依赖：\n")  # 去掉定义、保留卡片有内容
    r = run(tmp_path, text)  # 跑门禁
    assert any("定义" in e["message"] for e in errs(r, "V4-E6"))  # 定义仍必填
    assert not any("出现" in e["message"] for e in errs(r, "V4-E6"))  # 出现不要求


def test_too_many_cards_warns(tmp_path):
    cards = "".join(f"### 概念{i}\n+ 定义：第 {i} 个概念的定义\n\n" for i in range(4))  # 4 张卡
    text = GOOD.replace("### 番茄钟\n+ 定义：25 分钟专注加 5 分钟休息组成的一个时间单元\n", cards)  # 替换卡片节
    r = run(tmp_path, text)  # 跑门禁
    assert warns(r, "V4B-CARDS") and r["ok"]  # 只提示


def test_quiz_section_optional(tmp_path):
    head, tail = GOOD.split("## 自测题（复习用）\n", 1)  # 自测题节前后
    text = head + "---\n\n## 视频时间索引" + tail.split("## 视频时间索引", 1)[1]  # 整节删掉
    r = run(tmp_path, text.replace("---\n\n---\n\n", "---\n\n"))  # 去掉重复分隔线
    assert r["ok"] and r["checks"]["quiz_count"]["found"] is False, r["errors"]  # 可省


QUIZ = """{n}. 第 {n} 道题问的是什么？

**答案：** 这是第 {n} 道题的答案，先给结论，再解释原因，最后举一个和视频内容相关的例子来说明它。
"""  # 合规题目模板（答案超过 40 字）


def test_too_many_quiz_warns_not_errors(tmp_path):
    head, tail = GOOD.split("## 自测题（复习用）\n", 1)  # 自测题节前后
    qs = "\n".join(QUIZ.format(n=i) for i in (1, 2, 3))  # 3 道题
    text = head + "## 自测题（复习用）\n" + qs + "\n---\n\n## 视频时间索引" + tail.split("## 视频时间索引", 1)[1]  # 替换题目
    r = run(tmp_path, text)  # 跑门禁
    assert warns(r, "V4B-QUIZ") and not errs(r, "V4-E5"), r["errors"]  # 超量只提示


def test_quiz_missing_answer_is_error(tmp_path):
    text = GOOD.replace("**答案：** 因为", "因为")  # 去掉答案标记
    r = run(tmp_path, text)  # 跑门禁
    assert any("缺少「**答案：**」" in e["message"] for e in errs(r, "V4-E5"))  # 仍报错


def test_quiz_short_answer_warns(tmp_path):
    start = GOOD.index("**答案：** ")  # 答案开始
    end = GOOD.index("\n", start)  # 答案行结束
    text = GOOD[:start] + "**答案：** 门槛低。" + GOOD[end:]  # 换成过短答案
    r = run(tmp_path, text)  # 跑门禁
    assert warns(r, "V4-QA")  # 照常提示


def test_quiz_does_not_require_correspond(tmp_path):
    r = run(tmp_path)  # 合规样例的题目没有（对应：X）
    assert not errs(r, "V4-E5")  # brief 不要求对应


# ---------------- 照常运行的通用检查 ----------------

def test_body_timestamp_is_error(tmp_path):
    text = GOOD.replace("+ 一个番茄钟由", "+ 视频 [00:30] 处说，一个番茄钟由")  # 正文写时刻
    r = run(tmp_path, text)  # 跑门禁
    assert errs(r, "V4-E1")  # 正文禁时刻


def test_frontmatter_is_error_in_plain_v4(tmp_path):
    text = "---\nnote_type: brief\n---\n" + GOOD  # v4 不许 frontmatter
    r = run(tmp_path, text)  # 跑门禁
    assert errs(r, "V4-FM")  # 报错


def test_yuque_and_analogy_still_checked(tmp_path):
    extra = "\n```text\n纯文本\n```\n\n**打个比方** 番茄钟像跑步的间歇训练。\n"  # 两种写法问题
    text = GOOD.replace("| 短休息 | 5 分钟 | 离开屏幕活动一下 |\n", "| 短休息 | 5 分钟 | 离开屏幕活动一下 |\n" + extra)  # 放进细节节
    r = run(tmp_path, text)  # 跑门禁
    assert warns(r, "V4-YUQUE") and warns(r, "V4-ANALOGY")  # 两项照常提示


def test_index_title_mismatch_warns(tmp_path):
    r = run(tmp_path, GOOD.replace("+ [01:10] 细节", "+ [01:10] 休息安排"))  # 索引标题不对应小节
    assert warns(r, "V4-E2")  # 照常提示


# ---------------- v4-obsidian 与 CLI ----------------

OBS_FM = "\n".join([  # brief 的 Obsidian 元信息
    "---",  # 起始分隔线
    "template_version: v4-obsidian",  # 模板声明
    'title: "两分钟学会番茄钟"',  # 标题
    "note_type: brief",  # 轻量笔记
    "platform: douyin",  # 来源平台
    'source: "https://www.douyin.com/video/7300000000000000000"',  # 来源链接
    'duration: "02:00"',  # 时长
    "created: 2026-11-11",  # 生成日期
    "tags:",  # 块列表
    "  - 效率",  # 标签
    "---",  # 结束分隔线
]) + "\n"  # 正文紧接引语


def test_obsidian_brief_passes(tmp_path):
    r = run(tmp_path, OBS_FM + GOOD, template="v4-obsidian", meta=dict(META, suggested_note_type="short"))  # 元信息优先
    assert r["ok"] and r["note_type"] == "brief" and r["template_version"] == "v4-obsidian", r["errors"]  # 通过
    assert any("冲突" in w["message"] for w in warns(r, "V4O-FM"))  # 与 metadata 冲突时提示


def test_obsidian_brief_with_toc_still_checked(tmp_path):
    text = GOOD.replace("## 要点\n", "## 目录\n+ [要点](#要点)\n\n## 要点\n", 1)  # 写了传统锚点目录
    r = run(tmp_path, OBS_FM + text, template="v4-obsidian")  # 跑 Obsidian 门禁
    assert errs(r, "V4O-TOC")  # 写了目录就要求维基链接


def test_obsidian_rejects_unknown_type(tmp_path):
    r = run(tmp_path, OBS_FM.replace("note_type: brief", "note_type: tiny") + GOOD, template="v4-obsidian")  # 非法类型
    assert any("brief" in e["message"] for e in errs(r, "V4O-FM"))  # 提示合法取值含 brief


def test_cli_accepts_brief(tmp_path):
    note, meta_path, txt = make_env(tmp_path, meta=dict(META, suggested_note_type="lecture"))  # metadata 建议 lecture
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_note.py"), str(note),  # 调用 CLI
                           "--meta", str(meta_path), "--txt-dir", str(txt), "--template", "v4",  # 元数据与转写稿
                           "--note-type", "brief"],  # 显式 brief
                          capture_output=True, text=True, encoding="utf-8")  # 捕获输出
    assert proc.returncode == 0, proc.stdout + proc.stderr  # 通过
    assert json.loads(proc.stdout)["note_type"] == "brief"  # 报告类型（stdout 是一份 JSON）


# ---------------- note_nav ----------------

BILI_META = {"platform": "bilibili", "bvid": "BV1xx411c7mD", "suggested_note_type": "brief"}  # B站手动选 brief 的场景


def test_nav_fills_brief_index(tmp_path, capsys):
    note, meta_path, txt = make_env(tmp_path, meta=BILI_META)  # B站 brief
    assert note_nav.main([str(note), "--template", "v4", "--meta", str(meta_path), "--txt-dir", str(txt)]) == 0  # 正常退出
    text = note.read_text(encoding="utf-8")  # 填链后
    assert "+ [00:05](https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=5) 要点" in text  # 已填深链
    assert json.loads(capsys.readouterr().out.strip())["written"] == 2  # 两条都填


def test_nav_brief_without_index_skips(tmp_path, capsys):
    text = GOOD.split("---\n\n## 视频时间索引")[0]  # 去掉时间索引
    note, meta_path, txt = make_env(tmp_path, text, meta=BILI_META)  # B站 brief
    assert note_nav.main([str(note), "--template", "v4", "--meta", str(meta_path), "--txt-dir", str(txt),
                          "--note-type", "brief"]) == 0  # 参数接受 brief 且正常退出
    assert note.read_text(encoding="utf-8") == text  # 文件不变
    out = capsys.readouterr()  # 输出
    assert json.loads(out.out.strip())["status"] == "skipped" and "warning" in out.err  # 跳过并提示
