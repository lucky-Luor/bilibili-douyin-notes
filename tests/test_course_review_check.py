import copy  # 观察检查是否修改模型
import pytest  # 参数化八类检查的边界
import course_review as cr  # 课程级只读检查入口
from course_review_fixtures import card, note_text, write_note, course, make_current  # 隔离的离线课程文件


def codes(report, level="warnings"):  # 只提取目标级别的检查编号
    return [entry["check"] for entry in report[level]]  # 便于聚焦诊断类型


def ready(tmp_path, text=None):  # 构造来源和复习产物一致的课程
    return make_current(tmp_path, cr.build(tmp_path, course(write_note(tmp_path, text=text))))  # 真实模型而非mock字典


def test_parse_error_reports_episode(tmp_path):  # CR-PARSE覆盖缺概念章节
    model = ready(tmp_path, "## 1. 正文\n\n正文。\n")  # 格式可读但缺关键章节
    errors = cr.check(model, tmp_path)["errors"]  # 汇总为课程级诊断
    assert codes({"errors": errors}, "errors") == ["CR-PARSE"] and errors[0]["index"] == 1 and errors[0]["concept"] is None  # 精确来源和字段


def test_unreadable_note_reports_parse_and_link(tmp_path):  # 缺失笔记同时损坏解析和导航
    model = cr.build(tmp_path, course({"index": 2, "title": "缺失", "status": "done", "note": "notes/不存在.md"}))  # 直接构建失败来源
    result = cr.check(model, tmp_path)  # 不抛出文件异常
    assert {"CR-PARSE", "CR-LINK", "CR-STALE"} <= set(codes(result, "errors"))  # 三种独立错误同时报告


def test_deleted_note_after_build_reports_link(tmp_path):  # CR-LINK检查实际文件而非只看解析缓存
    model = ready(tmp_path)  # 构建时仍完整
    (tmp_path / model["notes"][0]["note"]).unlink()  # 构建后删除笔记
    assert "CR-LINK" in codes(cr.check(model, tmp_path), "errors")  # 指向缺失文件的来源不可生成


@pytest.mark.parametrize("missing", ["概念表.md", "课程自测.md"])  # 两个复习文件均需存在

def test_stale_when_one_output_missing(tmp_path, missing):  # CR-STALE覆盖单一产物缺失
    model = ready(tmp_path)  # 保存匹配摘要
    (tmp_path / missing).unlink()  # 独立删除其中一个
    assert "CR-STALE" in codes(cr.check(model, tmp_path), "errors")  # 摘要匹配也不能忽略缺产物


def test_stale_on_digest_mismatch(tmp_path):  # 保存摘要与当前来源不一致
    model = ready(tmp_path)  # 先建立匹配状态
    model["course"]["review"]["source_digest"] = "0" * 64  # 过期清单摘要
    assert "CR-STALE" in codes(cr.check(model, tmp_path), "errors")  # 不依赖时间戳比较


def test_actual_edit_without_mark_invalidates_even_same_model(tmp_path):  # CR-STALE必须覆盖不更新gate的文件变化
    item = write_note(tmp_path)  # 保存真实内容
    item["gate"] = {"sha256": "a" * 64}  # 故意不可信的旧门禁缓存
    model = make_current(tmp_path, cr.build(tmp_path, course(item)))  # 初始来源一致
    assert "CR-STALE" not in codes(cr.check(model, tmp_path), "errors")  # 缓存本身不影响正确来源
    path = tmp_path / item["note"]  # 只更新笔记不更新course
    path.write_bytes(path.read_bytes() + b"\n")  # 模拟用户手工编辑
    assert "CR-STALE" in codes(cr.check(model, tmp_path), "errors")  # check重新读取实际内容
    rebuilt = cr.build(tmp_path, model["course"])  # 新构建但清单仍旧
    assert "CR-STALE" in codes(cr.check(rebuilt, tmp_path), "errors")  # build后同样不能误信gate
    make_current(tmp_path, rebuilt)  # 模拟重新生成并保存新摘要
    assert "CR-STALE" not in codes(cr.check(rebuilt, tmp_path), "errors")  # 正确刷新后错误自然消失


@pytest.mark.parametrize("text", [note_text(nav=False), note_text().replace("[03:12](https://www.bilibili.com/video/BVtest?p=1&t=192)", "[03:12]"), note_text([card(appear="无")])])  # 缺索引缺深链和无出现均检查

def test_jump_warning_for_missing_time_or_link(tmp_path, text):  # CR-JUMP三种缺失来源
    result = cr.check(ready(tmp_path, text), tmp_path)  # 文件和摘要均完整
    assert "CR-JUMP" in codes(result) and not result["errors"]  # 仅警告不阻断生成


def test_original_conflict(tmp_path):  # CR-ORIG覆盖真正不同的英文分组
    first = write_note(tmp_path, 1, note_text([card(original="Cost")]))  # 首次原文
    second = write_note(tmp_path, 2, note_text([card(original="Price")]))  # 同概念不同原文
    model = make_current(tmp_path, cr.build(tmp_path, course(first, second)))  # 合并课程
    assert codes(cr.check(model, tmp_path)) == ["CR-ORIG"]  # 只报告真实冲突


def test_original_case_and_plural_not_conflict(tmp_path):  # 大小写和单复数容错不产生噪声
    first = write_note(tmp_path, 1, note_text([card(original="Normal Good")]))  # 单数原文
    second = write_note(tmp_path, 2, note_text([card(original="normal goods")]))  # 小写复数
    model = make_current(tmp_path, cr.build(tmp_path, course(first, second)))  # 同组归一
    assert "CR-ORIG" not in codes(cr.check(model, tmp_path))  # 方案明确排除这类冲突


def test_same_original_different_chinese(tmp_path):  # CR-SAME只提示不自动合并
    model = ready(tmp_path, note_text([card(), card(name="替代成本", original="opportunity cost")]))  # 不同中文相同英文
    report = cr.check(model, tmp_path)  # 检查全部原文
    assert "CR-SAME" in codes(report) and report["summary"]["concepts"] == 2  # 概念保持两个


def test_same_uses_all_originals_not_just_selected(tmp_path):  # 少数原文也可能暴露中文异名
    first = write_note(tmp_path, 1, note_text([card(original="Cost"), card(name="另一名称", original="Value")]))  # 第二概念原文Value
    second = write_note(tmp_path, 2, note_text([card(original="Value")]))  # 第一概念少数原文Value
    third = write_note(tmp_path, 3, note_text([card(original="Cost")]))  # 第一概念代表仍为Cost
    model = make_current(tmp_path, cr.build(tmp_path, course(first, second, third)))  # 全量原文供检查
    assert "CR-SAME" in codes(cr.check(model, tmp_path))  # 不能只检查最终original字段


@pytest.mark.parametrize("field", ["depends", "confusable"])  # 两种引用都检查存在性

def test_dependency_missing_warns(tmp_path, field):  # CR-DEP指出引用主体和缺失名称
    value = "未出现概念——解释。" if field == "confusable" else "未出现概念"  # 易混保留说明
    report = cr.check(ready(tmp_path, note_text([card(**{field: value})])), tmp_path)  # 整课都没有目标概念
    assert "CR-DEP" in codes(report) and any("未出现概念" in entry["message"] and entry["concept"] == "机会成本" for entry in report["warnings"])  # 诊断有上下文


def test_dependency_can_be_defined_in_other_episode(tmp_path):  # 引用检查范围不能限于当前笔记
    first = write_note(tmp_path, 1, note_text([card(depends="需求", confusable="需求——区别说明。")]))  # 当前集引用后续概念
    second = write_note(tmp_path, 2, note_text([card(name="需求", original="Demand")], correspond="需求"))  # 后集定义需求
    model = make_current(tmp_path, cr.build(tmp_path, course(first, second)))  # 整课检查
    assert "CR-DEP" not in codes(cr.check(model, tmp_path))  # 说明文字不可当成名称导致误报


def test_unclassified_question_warns(tmp_path):  # CR-QUIZ覆盖缺失的对应概念
    report = cr.check(ready(tmp_path, note_text(correspond="未知概念")), tmp_path)  # 来源仍有效
    assert codes(report) == ["CR-QUIZ"] and report["warnings"][0]["concept"] == "未知概念"  # 未归类题可供人工修复


def test_summary_counts_appearances_and_multi_episode(tmp_path):  # 汇总按出现次数计算跳转率
    first = write_note(tmp_path, 1)  # 一个概念两次出现都能跳转
    second = write_note(tmp_path, 2, note_text([card(appear="第 1 节「选择」")], nav=False))  # 同概念第三次出现缺时间
    model = make_current(tmp_path, cr.build(tmp_path, course(first, second)))  # 两篇两题一个跨集概念
    summary = cr.check(model, tmp_path)["summary"]  # 对外JSON汇总协议
    assert summary == {"concepts": 1, "multi_episode": 1, "questions": 2, "jump_rate": 2 / 3}  # 分母不是概念数或笔记数


def test_empty_summary_has_zero_jump_rate(tmp_path):  # 无完成项不会除零
    model = make_current(tmp_path, cr.build(tmp_path, course()))  # 空模型也可检查
    assert cr.check(model, tmp_path)["summary"] == {"concepts": 0, "multi_episode": 0, "questions": 0, "jump_rate": 0.0}  # 无数据的明确结果


def test_check_does_not_write_or_mutate(tmp_path):  # PR1只读契约覆盖文件和模型
    model = ready(tmp_path)  # 全部产物存在且摘要匹配
    before = copy.deepcopy(model)  # 模型快照
    files = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}  # 内容快照
    result = cr.check(model, tmp_path)  # 仅返回诊断
    assert not result["errors"] and not result["warnings"] and model == before  # 好模型无误报无状态修改
    assert files == {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}  # 不写文件不刷新清单
