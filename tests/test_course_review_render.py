import copy  # 隔离渲染用例对模型的修改
import time  # 测量四十篇课程的构建与渲染耗时
import pytest  # 提供独立夹具与参数化检查
import course_review as cr  # 验证集成后的课程复习公开接口


@pytest.fixture  # 不依赖其它测试模块的独立渲染模型
def model():  # 显式构造完整公开模型协议
    appearance = {"index": 1, "title": "第一讲", "note": "notes/中文 空格+(一).md", "section": 1, "section_name": "基础", "time": "03:12", "url": "https://example.com/watch?p=1&t=192"}  # 首个概念有同集回看入口
    first = {"key": "机会成本", "name": "机会成本", "original": "Opportunity Cost", "originals": [], "definition": "放弃的价值", "other_definitions": [(2, "另一种说法")], "appearances": [appearance, {**appearance, "index": 2, "title": "第二讲", "time": "04:00", "url": None}], "depends": ["边界"], "confusable": ["效率"], "questions": [(1, 3), (2, 8)], "first": (1, 1)}  # 两次出现覆盖跨集及无深链时间点
    second = {"key": "边界", "name": "边界", "original": "", "originals": [], "definition": "可达范围", "other_definitions": [], "appearances": [{**appearance, "index": 2, "title": "第二讲", "section": None, "section_name": "", "time": None, "url": None}], "depends": [], "confusable": [], "questions": [(2, 9)], "first": (2, None)}  # 空字段及无时间点均有覆盖
    questions = [{"index": 1, "title": "第一讲", "note": appearance["note"], "no": 3, "stem": "什么是机会成本？", "correspond": "机会成本", "answer": "第一行\n第二行 | 原样", "concept": "机会成本", "time": "03:12", "url": appearance["url"]}, {"index": 2, "title": "第二讲", "note": "notes/第二讲.md", "no": 8, "stem": "再解释机会成本？", "correspond": "机会成本", "answer": "第二集答案", "concept": "机会成本", "time": "04:00", "url": None}, {"index": 2, "title": "第二讲", "note": "notes/第二讲.md", "no": 9, "stem": "什么是边界？", "correspond": "边界", "answer": "第三题答案", "concept": "边界", "time": None, "url": None}, {"index": 2, "title": "第二讲", "note": "notes/第二讲.md", "no": 12, "stem": "未归类题？", "correspond": "不存在", "answer": "仍应保留", "concept": None, "time": None, "url": None}]  # 不连续原题号检验重新编号与未归类保留
    return {"course": {"title": "离线课程"}, "notes": [{"index": 1, "title": "第一讲", "note": appearance["note"]}, {"index": 2, "title": "第二讲", "note": "notes/第二讲.md"}], "concepts": [first, second], "questions": questions, "source_digest": "test"}  # 渲染只读模型且无需读取真实笔记


def test_concepts_groups_checkboxes_summary_and_time(model):  # 课程摘要与首次集分组均正确
    text = cr.render_concepts(model, {"机会成本": True}, "2026-11-04T08:00:00+00:00")  # 固定时间使输出可核对
    assert text.startswith("> 课程：离线课程｜概念 2 个（跨集出现 1 个）｜已掌握 1 / 2｜来自 2 篇已完成的笔记")  # 统计不能按出现次数冒充概念数
    assert text.index("## 第 1 集 · 第一讲") < text.index("+ [x] **机会成本**（Opportunity Cost）") < text.index("## 第 2 集 · 第二讲") < text.index("+ [ ] **边界**")  # 按首次出现分组并使用可回读勾选框
    assert "更新于 2026-11-04T08:00:00+00:00" in text and not text.startswith("---")  # 使用纯Markdown而不产生前置元数据


def test_concepts_three_time_variants_and_encoded_note(model):  # 时间点和深链的三种状态分别展示
    text = cr.render_concepts(model, {})  # 默认所有概念未掌握
    assert "[▶ 03:12](https://example.com/watch?p=1&t=192)" in text and " · [04:00]" in text and "（无时间点）" in text  # 不为缺少深链的时间伪造视频链接
    assert "[笔记](notes/中文%20空格%2B%28一%29.md)" in text  # 复用课程路径编码处理中文空格加号与括号
    assert "第 2 集：[笔记]" in text and "第 None 节" not in text  # 缺小节时仍保留集号笔记链接而不输出无效节号


def test_concepts_optional_fields_and_sources(model):  # 非空字段保留来源与空字段省略
    text = cr.render_concepts(model, {})  # 使用含空字段和完整字段的两个概念
    assert "其它集的说法：\n    + 第 2 集：另一种说法" in text and "依赖：边界｜易混：效率" in text  # 来源说法和关联引用均完整显示
    assert "自测：第 1 集第 3 题、第 2 集第 8 题" in text  # 自测位置使用原始题号
    tail = text.split("+ [ ] **边界**", 1)[1]  # 单独检查没有可选字段的概念
    assert all(value not in tail for value in ("（）", "其它集的说法", "依赖：", "易混："))  # 空内容不留下无效标签


def test_concepts_escapes_external_text_and_reads_own_marks(model, tmp_path):  # 转义后的特殊名称仍可恢复同一勾选键
    concept = model["concepts"][0]  # 注入包含竖线及反斜线的合法外部文本
    concept.update(name="成|本\\边界", key=cr.concept_key("成|本\\边界"), definition="定义 | 一\n定义二", original="Cost | Value\n续行")  # 每个单行字段都必须经过统一转义
    model["course"]["title"] = "标题 | 一\n标题二"  # 标题换行不应生成额外结构
    text = cr.render_concepts(model, {concept["key"]: True})  # 产生实际待回读文件内容
    assert "标题 \\| 一 标题二" in text and "定义 \\| 一 定义二" in text and "Cost \\| Value 续行" in text  # 所有外部单行文本遵循课程助手规则
    path = tmp_path / "概念表.md"  # 使用独立临时概念表
    path.write_text(text, encoding="utf-8")  # 模拟生成后用户未修改的文件
    assert cr.read_marks(path)[concept["key"]] is True  # 转义不能造成掌握状态丢失


def test_quiz_without_any_current_mark_does_not_duplicate_questions(model):  # 无勾选时只显示完整题库
    text = cr.render_quiz(model, {"已经消失": True})  # 旧概念备份不能触发当前优先复习区
    assert "还没有勾选任何概念" in text and "## 先复习" not in text  # 尚未建立掌握状态时显示操作引导
    assert text.count("什么是机会成本？") == 1 and "共 4 题" in text  # 无优先区时不重复整个题库
    assert "没掌握的概念涉及 3 题" in text  # 未归类题目不冒充未掌握概念题


def test_quiz_focus_excludes_mastered_and_unclassified(model):  # 优先区只包含未掌握概念的题
    text = cr.render_quiz(model, {"机会成本": True})  # 仅边界概念尚未掌握
    focus, complete = text.split("## 全部题目（按集）", 1)  # 分开观察两份题目区域
    assert "## 先复习：还没掌握的概念（1 题）" in focus and "### 边界" in focus  # 显示正确数量与概念标题
    assert "什么是边界？（第 2 集第 9 题）" in focus and "什么是机会成本？" not in focus and "未归类题？" not in focus  # 已掌握和未归类仍仅在完整区
    assert "未归类题？" in complete and "什么是机会成本？" in complete  # 完整区不能丢题


def test_quiz_all_mastered_has_empty_focus(model):  # 全掌握时不生成空概念标题
    text = cr.render_quiz(model, {"机会成本": True, "边界": True})  # 两个概念全部勾选
    focus = text.split("## 全部题目（按集）", 1)[0]  # 只检查优先区
    assert "还没掌握的概念（0 题）" in focus and "### " not in focus  # 零题的优先区保持清楚且没有假题目


def test_quiz_numbers_are_contiguous_and_keep_original_numbers(model):  # 重新编号后仍保留来源题号
    text = cr.render_quiz(model, {})  # 第一集一题第二集三题
    second = text.split("### 第 2 集 · 第二讲", 1)[1]  # 从第二集分组观察连续编号
    assert "1. 再解释机会成本？（对应：机会成本；第 2 集第 8 题）" in second  # 原始第八题重新编号为第一题
    assert "2. 什么是边界？" in second and "3. 未归类题？" in second and "12. 未归类题？" not in second  # 题号不因原始跳号出现间隙


def test_quiz_preserves_answer_and_never_borrows_other_episode_jump(model):  # 答案保持原文且不跨集借用时间点
    text = cr.render_quiz(model, {})  # 第二集同概念只有纯文本时间点
    assert "**答案：** 第一行\n第二行 | 原样" in text  # 多行答案和其Markdown不改写
    assert "[▶ 回看 03:12](https://example.com/watch?p=1&t=192)" in text  # 有深链的第一集可直接回看
    assert "回看" not in text.split("### 第 2 集 · 第二讲", 1)[1]  # 缺深链时仅保留本集笔记链接


def test_render_functions_do_not_mutate_model_or_marks(model):  # 纯渲染接口不改变调用者状态
    before = copy.deepcopy(model)  # 保存完整模型快照
    marks = {"机会成本": True}  # 勾选状态同样作为只读输入
    cr.render_concepts(model, marks, "固定时间")  # 生成概念表字符串
    cr.render_quiz(model, marks)  # 生成题库字符串
    assert model == before and marks == {"机会成本": True}  # 下次构建不能受本次渲染副作用影响


def test_quiz_escapes_titles_and_stems(model):  # 题干与标题不允许外部换行改变结构
    model["questions"][0].update(stem="问题 | 一\n继续", title="第一讲 | 题\n续", correspond="机会 | 成本")  # 注入需转义的各个展示字段
    text = cr.render_quiz(model, {})  # 仍走真实渲染助手
    assert "问题 \\| 一 继续" in text and "第一讲 \\| 题 续" in text and "对应：机会 \\| 成本" in text  # 单行字段均遵循课程统一规则


def test_build_to_quiz_and_unmastered_preserves_answer_blocks(tmp_path):  # 真实构建后答案空行围栏与伪题号必须完整保留
    answer = "首段答案。\n\n第二段答案。\n\n```python\n1. 围栏里的伪题不能分题\nprint('保持原文')\n```\n\n结尾答案。"  # 同时覆盖段落分隔与代码块内数字开头内容
    note = tmp_path / "答案保真.md"  # 使用独立真实笔记文件
    note.write_text("# 保真课程\n\n## 1. 基础\n\n正文。\n\n## 概念卡片\n\n### 成本\n+ 定义：代价\n+ 出现：第 1 节「基础」\n+ 依赖：\n+ 易混：\n\n## 自测题\n\n1. 什么是成本？（对应：成本）\n\n**答案：** " + answer + "\n\n## 视频时间索引\n\n+ [00:10](https://example.com/watch?p=1&t=10) 1. 基础\n", encoding="utf-8")  # 输入直接采用正式笔记语法而不替换解析函数
    course = {"title": "答案保真", "items": [{"index": 1, "title": "第一讲", "status": "done", "note": note.name}]}  # 模拟真实done课程条目
    model = cr.build(tmp_path, course)  # 验证从原始笔记到模型的完整链路
    assert len(model["questions"]) == 1 and model["questions"][0]["answer"] == answer  # 围栏伪题号不能被额外解析为第二题
    assert "**答案：** " + answer in cr.render_quiz(model, {})  # 渲染不扁平化段落或改写代码块
    pending = cr.unmastered(tmp_path, course)  # 未生成复习文件时仍可只读出题
    assert pending["concepts"][0]["questions"][0]["answer"] == answer  # Agent拿到的答案与来源笔记逐字符一致


def test_forty_notes_build_and_render_under_three_seconds(tmp_path):  # 四十篇六卡五题的离线规模回归
    items = []  # 仅在计时前生成笔记避免把磁盘夹具构造计入预算
    for index in range(1, 41):  # 明确模拟四十集课程
        lines = ["# 离线课程", "", "## 1. 基础", "", "正文。", "", "## 概念卡片", ""]  # 提供合法概念卡及时间索引所需的小节
        for card in range(6):  # 每集有六张不同概念卡
            lines.extend([f"### 概念{index}-{card}", f"+ 定义：第{index}集定义{card}", "+ 出现：第 1 节「基础」", "+ 依赖：", "+ 易混：", ""])  # 保持正式单集字段格式
        lines.extend(["## 自测题", ""])  # 自测题数量固定为每篇五道
        for number in range(1, 6):  # 使用标准对应标注关联到本集概念
            lines.extend([f"{number}. 问题{number}？（对应：概念{index}-{number - 1}）", "", f"**答案：** 答案{number}。", ""])  # 不通过模型生成或下载内容
        lines.extend(["## 视频时间索引", "", f"+ [00:10](https://example.com/watch?p={index}&t=10) 1. 基础", ""])  # 每个概念都拥有同集深链
        note = tmp_path / f"第{index}集.md"  # 每篇笔记使用独立UTF8文件
        note.write_text("\n".join(lines), encoding="utf-8")  # 计时前准备好磁盘内容
        items.append({"index": index, "title": f"第{index}讲", "status": "done", "note": note.name})  # 不受当前选集范围影响
    started = time.perf_counter()  # 只计构建与两份渲染的实际耗时
    model = cr.build(tmp_path, {"title": "四十集离线课", "items": items})  # 使用真实解析器合并全部笔记
    concepts = cr.render_concepts(model, {})  # 生成完整二百四十个概念表
    quiz = cr.render_quiz(model, {})  # 生成完整二百道题库
    elapsed = time.perf_counter() - started  # 停止计时避免断言及测试输出影响预算
    assert len(model["notes"]) == 40 and len(model["concepts"]) == 240 and len(model["questions"]) == 200  # 防止夹具被错误解析导致性能假阳性
    assert concepts.count("+ [ ] **") == 240 and "共 200 题" in quiz  # 两份文本确实完整渲染
    assert elapsed < 3, f"40篇六卡五题构建与渲染耗时{elapsed:.3f}秒，超过三秒"  # 与方案指定的性能预算保持一致
