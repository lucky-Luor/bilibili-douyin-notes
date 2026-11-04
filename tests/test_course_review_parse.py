import hashlib  # 独立核对实际文件摘要
import pytest  # 参数化读取和格式边界
import course_review as cr  # 课程笔记解析入口
from course_review_fixtures import card, note_text, write_note  # 复用离线最小笔记构造器
from test_validate_obsidian import OBS  # 覆盖现有门禁通过的真实黑曜石笔记
from test_validate_note_v4 import GOOD  # 复用带节间分隔线的真实四版合法讲义


def parse(tmp_path, text=None):  # 写临时文件后调用公开接口
    item = write_note(tmp_path, text=text)  # 兼容中文空格文件名
    return cr.parse_note(tmp_path / item["note"])  # 不通过内部函数绕开读文件行为


def test_sections_and_card_fields(tmp_path):  # 小节和所有概念字段完整解析
    result = parse(tmp_path, note_text([card(depends="需求、供给", confusable="需求量——数量不同。")]))  # 构造多个依赖和易混说明
    assert result["sections"] == {1: "选择", 2: "权衡"} and result["errors"] == []  # 编号直接作为键
    assert result["cards"][0] == {"name": "机会成本", "line": 13, "definition": "放弃的最佳选择。", "appear": [(2, "权衡"), (1, "选择")], "depends": ["需求", "供给"], "confusable": ["需求量——数量不同。"], "original": "Opportunity Cost"}  # 原文与出现顺序不改写


def test_quiz_stem_correspond_and_answer(tmp_path):  # 去掉对应标注而保留题干答案
    question = parse(tmp_path)["quiz"][0]  # 使用真实模板题目格式
    assert question["no"] == 1 and question["stem"] == "如何衡量代价？"  # 不把对应字段拼进题干
    assert question["correspond"] == "机会成本" and question["answer"] == "观察放弃的最佳选择。"  # 去掉答案前缀


def test_multiline_answer_is_preserved(tmp_path):  # 已有答案照搬且不折叠
    result = parse(tmp_path, note_text().replace("观察放弃的最佳选择。", "观察放弃的最佳选择。\n第二行补充。"))  # 仅扩展原始答案
    assert result["quiz"][0]["answer"] == "观察放弃的最佳选择。\n第二行补充。"  # 多行内容保持


@pytest.mark.parametrize("answer", [  # 各类答案正文都必须原样保留
    "第一段。\n\n第二段。\n\n\n第三段。",  # 保留段落之间的全部空行
    "第一段。\n\n```markdown\n1. 围栏内假题？（对应：假概念）\n\n**答案：** 围栏内假答案。\n## 围栏内假章节\n```\n\n代码之后的正文。",  # 代码内伪结构不划分真实题目
    "第一段。  \n\n    缩进内容。\n\n---\n\n末段。",  # 保留行尾空格缩进和正文分隔线
])  # 每种原文都配后续正常题验证边界

def test_answer_preserves_raw_content_and_next_question(tmp_path, answer):  # 回归多段代码正文与下一题独立提取
    text = note_text().replace("观察放弃的最佳选择。", answer + "\n\n2. 后续正常题？（对应：机会成本）\n\n**答案：** 后续正常答案。\n\n第二段正常答案。\n\n")  # 下一题后的边界空行应被剔除
    questions = parse(tmp_path, text)["quiz"]  # 通过公开解析入口读取原始文件
    assert len(questions) == 2 and [question["no"] for question in questions] == [1, 2]  # 围栏假题不会新增题目
    assert questions[0]["answer"] == answer  # 内容空行和围栏代码完整照搬
    assert questions[1]["stem"] == "后续正常题？" and questions[1]["answer"] == "后续正常答案。\n\n第二段正常答案。"  # 下一题边界不串入前题或时间索引


def test_answer_marker_on_empty_line_keeps_internal_layout(tmp_path):  # 答案标记后换行时仅去除边界空白
    text = note_text().replace("**答案：** 观察放弃的最佳选择。", "**答案：**\n\n    首段缩进。\n\n第二段。\n\n")  # 前后空白行与正文缩进分开处理
    assert parse(tmp_path, text)["quiz"][0]["answer"] == "    首段缩进。\n\n第二段。"  # 不用字符串整体去空白破坏正文缩进


@pytest.mark.parametrize("text", [OBS, GOOD])  # 直接使用现有真实四版与黑曜石合法讲义

def test_real_note_last_answer_excludes_section_separator(tmp_path, text):  # 从真实文件解析最后一题不得吞入下一节分隔线
    result = parse(tmp_path, text)  # 真实夹具写入临时笔记后经公开入口解析
    assert result["quiz"][-1]["answer"] == "活动图本身不定义 action 含义，语义由使用者赋予。"  # 原模板的节间横线不属于答案


@pytest.mark.parametrize("answer,at_eof", [  # 覆盖正文横线与节末横线的严格区分
    ("首段。\n\n---\n\n末段。", False),  # 答案内部横线保留
    ("首段。\n\n```text\n---\n```", False),  # 围栏中的横线与围栏本身保留
    ("首段。\n\n---", True),  # 文件结尾没有下一节标题时不删除横线
])  # 每个边界独立回归

def test_section_separator_removal_keeps_answer_content(tmp_path, answer, at_eof):  # 只允许剔除最后一题紧邻下一节的围栏外横线
    text = note_text().replace("观察放弃的最佳选择。", answer)  # 保留现有最小笔记结构
    text = text.split("## 视频时间索引")[0] if at_eof else text.replace("## 视频时间索引", "---\n\n## 视频时间索引")  # 仅非文件结尾情形添加模板节分隔线
    assert parse(tmp_path, text)["quiz"][-1]["answer"] == answer  # 正文内容不随节分隔线删除而丢失


def test_source_question_numbers_and_concept_references(tmp_path):  # 原始非连续题号必须贯穿概念题目引用
    from course_review_fixtures import course  # 仅本用例需要最小课程构造
    text = note_text().replace("1. 如何衡量代价？", "8. 如何衡量代价？").replace("观察放弃的最佳选择。", "观察放弃的最佳选择。\n\n12. 后续题？（对应：机会成本）\n\n**答案：** 后续答案。")  # 源题号不从一开始也不连续
    item = write_note(tmp_path, text=text)  # 保存真实来源字节
    parsed = cr.parse_note(tmp_path / item["note"])  # 独立核对解析输出
    assert [question["no"] for question in parsed["quiz"]] == [8, 12]  # 不使用门禁解析器的内部枚举编号
    model = cr.build(tmp_path, course(item))  # 验证现有构建接口沿用来源编号
    assert model["concepts"][0]["questions"] == [(1, 8), (1, 12)] and [question["no"] for question in model["questions"]] == [8, 12]  # 概念引用与扁平题库一致


def test_nav_uses_explicit_section_number(tmp_path):  # 索引列表顺序不能冒充节号
    result = parse(tmp_path, note_text().replace("1. 选择\n+ [03:12]", "9. 选择\n+ [03:12]"))  # 第一条显式编号改成九
    assert result["nav"][9] == ("00:00", "https://www.bilibili.com/video/BVtest?p=1&t=0")  # 深链原样保存
    assert result["nav"][2][0] == "03:12" and 1 not in result["nav"]  # 从条目标题而非位置取节号


def test_nav_without_link(tmp_path):  # 无深链也要保留可显示时间
    result = parse(tmp_path, note_text().replace("[03:12](https://www.bilibili.com/video/BVtest?p=1&t=192)", "[03:12]"))  # 正式无链格式
    assert result["nav"][2] == ("03:12", None)  # 让检查和渲染决定缺链行为


def test_code_fences_exclude_fake_headings_cards_and_quiz(tmp_path):  # 围栏内所有伪结构都不可解析
    fake = "\n".join(["```markdown", "## 概念卡片", card("伪卡片"), "## 自测题", "1. 假问题？（对应：伪卡片）", "**答案：** 假答案。", "## 8. 假小节", "```"])  # 示例二级标题也不能影响边界
    result = parse(tmp_path, fake + "\n" + note_text().replace("### 机会成本", fake + "\n### 机会成本"))  # 同时覆盖章节前与卡片节内示例
    assert [entry["name"] for entry in result["cards"]] == ["机会成本"]  # 不产生伪卡或截断真实卡片
    assert len(result["quiz"]) == 1 and 8 not in result["sections"] and not result["errors"]  # 题目小节也被过滤


def test_code_fence_inside_quiz_is_ignored(tmp_path):  # 题目解析器本身不识别围栏需由适配层过滤
    text = note_text().replace("1. 如何衡量", "```text\n1. 假题（对应：伪）\n**答案：** 假。\n```\n\n1. 如何衡量")  # 真题前放伪题
    assert len(parse(tmp_path, text)["quiz"]) == 1  # 只留下真实题目


def test_obsidian_frontmatter_skipped_and_lines_preserved(tmp_path):  # 真实合法变体兼容且坐标不平移
    result = parse(tmp_path, OBS)  # 使用既有单集门禁的完整讲义
    assert not result["errors"] and result["cards"] and result["quiz"] and result["nav"]  # 四类正文数据均存在
    expected_line = OBS.splitlines().index("### 活动图") + 1  # 从原始文本独立核对卡片坐标
    assert result["cards"][0]["line"] == expected_line  # 元信息必须替换空行而不是删除


def test_frontmatter_fake_content_does_not_become_cards(tmp_path):  # 元信息中的说明不进入正文结构
    prefix = "\n".join(["---", "template_version: v4-obsidian", "note_type: lecture", 'title: "## 概念卡片"', "---", ""])  # 所有元信息都在边界内
    result = parse(tmp_path, prefix + note_text())  # 解析后仍只含一个真实概念
    assert len(result["cards"]) == 1 and result["cards"][0]["line"] == 18  # 行号含五行元信息


def test_missing_concept_section_reports_error(tmp_path):  # 讲义缺概念卡章节必须可诊断
    result = parse(tmp_path, "## 1. 正文\n\n正文。\n")  # 仍返回其它可用结构
    assert result["sections"] == {1: "正文"} and any("概念卡片" in error for error in result["errors"])  # 不抛异常


def test_short_note_does_not_require_cards(tmp_path):  # 短视频模板不新增概念卡硬要求
    result = parse(tmp_path, "---\nnote_type: short\n---\n## 1. 正文\n")  # 已有变体声明笔记类型
    assert not result["errors"] and result["note_type"] == "short"  # 尊重lecture限定


@pytest.mark.parametrize("kind", ["missing", "invalid_utf8", "directory"])  # 三种读取失败都返回错误

def test_read_failures_are_collected(tmp_path, kind):  # 不把系统异常抛给调用方
    path = tmp_path / "笔记.md"  # 隔离所有破坏性操作
    if kind == "invalid_utf8":  # 模拟加密软件或坏编码
        path.write_bytes(b"\xff\xfe\xfa")  # 无法按UTF8解码
    elif kind == "directory":  # 同名目录不当作文件
        path.mkdir()  # 创建目录而不是笔记
    result = cr.parse_note(path)  # 缺失分支不创建路径
    assert result["errors"] and "原因：" in result["errors"][0] and not result["cards"]  # 使用Hints的原因影响下一步


def test_actual_sha256_is_from_bytes(tmp_path):  # 摘要不依赖换行或解码规范化
    item = write_note(tmp_path)  # 写中文笔记
    path = tmp_path / item["note"]  # 原始文件路径
    assert cr.parse_note(path)["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()  # 独立字节校验


def test_no_valid_appearances_is_empty(tmp_path):  # 无法解析的出现交给模型生成占位
    result = parse(tmp_path, note_text([card(appear="没有合法节号")]))  # 不在解析层丢弃整张卡
    assert result["cards"][0]["appear"] == []  # 保留概念供后续CR-JUMP
