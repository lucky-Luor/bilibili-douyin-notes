import copy  # 验证构建不修改课程清单
import pytest  # 参数化名称和原文多数规则
import course_review as cr  # 课程合并入口
from course_review_fixtures import card, note_text, write_note, course  # 共享离线文件构造


@pytest.mark.parametrize("name", ["机会 成本", "机会　成本", "机会\t成本", "机会成本"])  # 名称全部空白都应合并

def test_cross_episode_whitespace_merge(tmp_path, name):  # 跨集同一中文概念保留第一次写法
    first = write_note(tmp_path, 1)  # 基准概念
    second = write_note(tmp_path, 2, note_text([card(name=name)]))  # 空白变体
    model = cr.build(tmp_path, course(second, first))  # 清单逆序也必须按集号处理
    assert len(model["concepts"]) == 1 and model["concepts"][0]["name"] == "机会成本"  # 首次名称稳定
    assert [entry["index"] for entry in model["notes"]] == [1, 2]  # 完成笔记排序


def test_nfkc_casefold_merge(tmp_path):  # 英文全角半角和大小写共享合并键
    first = write_note(tmp_path, 1, note_text([card(name="ＡＢＣ")], correspond="abc"))  # 首集使用全角
    second = write_note(tmp_path, 2, note_text([card(name="a b c")], correspond="AbC"))  # 次集半角空格
    model = cr.build(tmp_path, course(first, second))  # 验证概念和题目同时归一
    assert len(model["concepts"]) == 1 and model["concepts"][0]["key"] == "abc"  # NFKC与casefold顺序生效
    assert model["concepts"][0]["questions"] == [(1, 1), (2, 1)]  # 题目按同一键归属


def test_appearances_sorted_by_episode_and_section(tmp_path):  # 原卡出现顺序不应影响课程导航
    model = cr.build(tmp_path, course(write_note(tmp_path, 3), write_note(tmp_path, 1)))  # 集序和节序都反向
    entries = model["concepts"][0]["appearances"]  # 已合并概念来源
    assert [(entry["index"], entry["section"]) for entry in entries] == [(1, 1), (1, 2), (3, 1), (3, 2)]  # 两维升序
    assert entries[1]["time"] == "03:12" and entries[1]["url"].endswith("t=192") and entries[0]["note"].startswith("notes/")  # 保留渲染所需来源


def test_first_definition_and_distinct_alternatives(tmp_path):  # 定义不改写且重复其它说法不重复收录
    definitions = ["  首次定义。  ", "另一种说法。", "  另一种说法。 ", "首次定义。"]  # 首尾空白不造成差异
    items = [write_note(tmp_path, index, note_text([card(definition=value)])) for index, value in enumerate(definitions, 1)]  # 四集独立文件
    concept = cr.build(tmp_path, course(*items))["concepts"][0]  # 合并所有定义
    assert concept["definition"] == "首次定义。" and concept["other_definitions"] == [(2, "另一种说法。")]  # 同一其它说法只存首次来源


@pytest.mark.parametrize("values,expected", [  # 多数按分组统计且保留代表写法
    (["Other", "Cost", "Cost"], "Cost"),  # 同写法多数
    (["First", "Second"], "First"),  # 平局取最早分组
    (["Other", "Normal Good", "normal goods", "Normal Good"], "Normal Good"),  # 单复数合并后赢过最早组
    (["Normal Good", "normal goods"], "Normal Good"),  # 组内写法平局取最早
    (["", "", ""], ""),  # 空原文不参与统计
])  # 每组独立课程

def test_original_majority_and_tie(tmp_path, values, expected):  # 代表原文可由后续集改变
    items = [write_note(tmp_path, index, note_text([card(original=value)])) for index, value in enumerate(values, 1)]  # 每集一票
    concept = cr.build(tmp_path, course(*items))["concepts"][0]  # 同名概念自动归并
    assert concept["original"] == expected  # 检查最终代表文本


def test_dependencies_and_confusables_union(tmp_path):  # 引用列表跨集去重并保持首次顺序
    first = write_note(tmp_path, 1, note_text([card(depends="需求、供给", confusable="需求量——区别。")]))  # 首次引用
    second = write_note(tmp_path, 2, note_text([card(depends="需求、均衡、供给", confusable="需求量——区别。")]))  # 混合新旧引用
    concept = cr.build(tmp_path, course(first, second))["concepts"][0]  # 引用保留易混说明
    assert concept["depends"] == ["需求", "供给", "均衡"] and concept["confusable"] == ["需求量——区别。"]  # 相同引用不重复


def test_only_done_with_note_ignores_scope(tmp_path):  # 复习范围是全课已完成笔记
    first, second = write_note(tmp_path, 1), write_note(tmp_path, 2)  # 第二集在当前scope之外
    pending = {"index": 3, "status": "pending", "note": "不能读取.md"}  # 未完成条目不读取
    empty = {"index": 4, "status": "done", "note": ""}  # 完成但无笔记也不读取
    model = cr.build(tmp_path, course(empty, second, pending, first))  # 逆序清单与单集scope
    assert [entry["index"] for entry in model["notes"]] == [1, 2] and len(model["questions"]) == 2  # 不受scope限制


def test_question_attribution_and_unclassified(tmp_path):  # 未找到概念的题仍保留来源
    item = write_note(tmp_path, text=note_text(correspond="不存在概念"))  # 未分类题
    model = cr.build(tmp_path, course(item))  # 构建不捏造新概念
    question = model["questions"][0]  # 题目扁平接口供render使用
    assert question["concept"] is None and question["url"] is None and question["time"] is None  # 未归类不能错误借用链接
    assert question["index"] == 1 and question["title"] == item["title"] and question["note"] == item["note"]  # 全部来源字段齐全
    assert model["concepts"][0]["questions"] == []  # 概念题目引用保持真实


def test_question_can_reference_concept_from_later_episode(tmp_path):  # 先收齐概念再处理题目归属
    first = write_note(tmp_path, 1, note_text(cards=[], correspond="后续概念"))  # 本集尚无卡片
    second = write_note(tmp_path, 2, note_text([card(name="后续概念")], correspond="后续概念"))  # 后续定义概念
    model = cr.build(tmp_path, course(first, second))  # 不受处理时机影响归属
    assert model["questions"][0]["concept"] == "后续概念" and model["questions"][0]["url"] is None  # 跨集可归类但不可借用时间点


def test_question_jump_uses_same_episode_first_section(tmp_path):  # 同集首次出现按节号而非原字段顺序
    first = write_note(tmp_path, 1)  # 原出现字段先写第二节
    second = write_note(tmp_path, 2, note_text().replace("?p=1", "?p=2"))  # 第二集独立深链
    questions = cr.build(tmp_path, course(first, second))["questions"]  # 同概念两集问题
    assert [question["time"] for question in questions] == ["00:00", "00:00"]  # 均取排序后的第一节
    assert "?p=2" in questions[1]["url"]  # 后集不借首集深链


def test_missing_first_nav_does_not_use_later_appearance(tmp_path):  # 首次出现没时间时不能跳到后续出现节
    text = note_text().replace("+ [00:00](https://www.bilibili.com/video/BVtest?p=1&t=0) 1. 选择\n", "")  # 只保留第二节时间
    question = cr.build(tmp_path, course(write_note(tmp_path, text=text)))["questions"][0]  # 正确首次节仍为第一节
    assert question["time"] is None and question["url"] is None  # 按方案只给笔记


def test_no_appearance_still_keeps_source(tmp_path):  # 无合法出现的卡片不被丢弃
    item = write_note(tmp_path, text=note_text([card(appear="无")]))  # 无法解析节号
    concept = cr.build(tmp_path, course(item))["concepts"][0]  # 依旧生成占位appearance
    assert concept["first"] == (1, None) and concept["appearances"][0]["section"] is None  # 对外保留空节号
    assert concept["appearances"][0]["time"] is None and concept["appearances"][0]["url"] is None  # 不伪造时间点


def test_concepts_sort_by_first_section(tmp_path):  # 概念卡排列顺序可不同于正文首次出现
    item = write_note(tmp_path, text=note_text([card(name="后出现", appear="第 2 节「权衡」"), card(name="先出现", appear="第 1 节「选择」"), card(name="无节号", appear="无")]))  # 包含空节号排序边界
    concepts = cr.build(tmp_path, course(item))["concepts"]  # 正文顺序决定展示顺序
    assert [concept["name"] for concept in concepts] == ["先出现", "后出现", "无节号"]  # 空节号置于同集末尾


def test_build_is_read_only_and_keeps_independent_course(tmp_path):  # 模型与清单不能共享可变状态
    current = course(write_note(tmp_path))  # 旧清单无复习字段
    before = copy.deepcopy(current)  # 保存调用前状态
    files = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}  # 全部现有文件快照
    model = cr.build(tmp_path, current)  # 构建只读
    model["course"]["items"][0]["title"] = "模型内修改"  # 验证深拷贝
    assert current == before  # 调用方清单不变
    assert files == {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}  # 不生成产物


def test_digest_is_order_independent_and_sensitive_to_actual_content(tmp_path):  # 不重新mark也能改变来源摘要
    first, second = write_note(tmp_path, 1), write_note(tmp_path, 2)  # 两个完成项
    first["gate"] = {"sha256": "0" * 64}  # 故意保存过期门禁摘要
    baseline = cr.build(tmp_path, course(first, second))["source_digest"]  # 实际内容作为来源
    assert cr.build(tmp_path, course(second, first))["source_digest"] == baseline  # 输入顺序无关
    path = tmp_path / first["note"]  # 只修改现有笔记
    path.write_bytes(path.read_bytes() + b"\n")  # 不更新gate
    assert cr.build(tmp_path, course(first, second))["source_digest"] != baseline  # 感知真实文件变化


def test_empty_course_has_stable_empty_model(tmp_path):  # 入口负责无完成项提示模型保持纯函数
    model = cr.build(tmp_path, course())  # 没有任何笔记
    assert model["notes"] == model["concepts"] == model["questions"] == [] and len(model["source_digest"]) == 64  # 空输入仍有有效摘要
