import copy  # 隔离课程快照与调用方清单
import hashlib  # 按实际笔记字节计算来源摘要
import json  # 稳定序列化摘要输入
import re  # 拆分引用字段与索引节号
import unicodedata  # 统一全角半角概念名称
from collections import Counter  # 统计原文分组和写法频次
from pathlib import Path  # 处理课程相对笔记路径

import validate_note as vn  # 复用单集门禁的现有解析协议
from _hints import HintError  # 为解析失败保留原因影响和下一步


def concept_key(name):  # 提供合并与后续掌握标记共享的名称规则
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name)).casefold()  # 去除全部空白后忽略大小写


def _read_note(path):  # 同时读取文本和真实字节摘要
    try:  # 文件失败交给课程级检查统一汇总
        raw = Path(path).read_bytes()  # 不信任可能过期的门禁缓存
    except OSError as exc:  # 缺失权限和目录错误都可恢复
        return None, None, str(HintError(f"无法读取笔记：{path}", "无法汇总这篇笔记", "检查笔记路径和读取权限后重新生成课程复习文件", str(exc)))  # 使用项目提示格式
    digest = hashlib.sha256(raw).hexdigest()  # 解码失败仍保留实际内容变化
    try:  # 笔记采用严格UTF8解码
        return raw.decode("utf-8-sig"), digest, None  # 兼容字节标记但摘要仍使用完整原始字节
    except UnicodeError as exc:  # 不用替换字符掩盖加密或损坏文件
        return None, digest, str(HintError(f"笔记不是有效UTF-8：{path}", "无法汇总这篇笔记", "确认文件未被加密并恢复为UTF-8后重试", str(exc)))  # 统一可读失败


def _references(value):  # 拆分依赖字段并保留原始名称
    return [part.strip() for part in re.split(r"[、,，;；]", value) if part.strip()]  # 与单集门禁的依赖分隔符一致


def parse_note(path):  # 只读解析笔记且始终返回错误数组
    result = {"sections": {}, "cards": [], "quiz": [], "nav": {}, "errors": [], "sha256": None, "note_type": "lecture"}  # 缺失字段保持稳定接口
    text, result["sha256"], error = _read_note(path)  # 一次读取保证模型与摘要来自相同字节
    if error:  # 读取或解码失败不抛出异常
        result["errors"].append(error)  # 留待CR-PARSE报告
        return result  # 不解析不存在的文本
    lines = text.splitlines()  # 保持原文件的一基行号可追踪
    if lines and lines[0] == "---":  # 有元信息时调用现有有限语法解析器
        frontmatter, errors, end = vn.parse_frontmatter_obsidian(text)  # 不引入YAML依赖
        result["note_type"] = frontmatter.get("note_type", "lecture")  # 短笔记不强求概念卡片节
        result["errors"].extend(error["message"] for error in errors)  # 元信息错误进入统一报告
        lines[:end] = [""] * end  # 用空行替换以保留原始坐标
    state = vn.compute_fence_state(lines)  # 复用门禁围栏识别口径
    visible = ["" if state[i] or vn.FENCE_RE.match(line) else line for i, line in enumerate(lines)]  # 所有标题和题目扫描均排除围栏示例
    for line in visible:  # 收集编号正文小节
        match = vn.V4_SEC_NUM_RE.match(line)  # 复用正式小节格式
        if match:  # 只接受真实小节标题
            result["sections"][int(match[1])] = match[2]  # 用数值节号建立映射
    start = vn.find_h2_line(visible, "概念卡片")  # 从过滤后的行定位真实章节
    if start is None and result["note_type"] != "short":  # 讲义缺概念节必须阻断生成
        result["errors"].append("讲义缺少「概念卡片」章节")  # 由课程检查转成CR-PARSE
    if start is not None:  # 存在概念节时复用字段解析
        end = vn.next_h2_after(visible, start + 1)  # 示例中的二级标题不能截断真实概念节
        for card in vn.parse_concept_cards_v4(visible, start + 1, end, state):  # 保持卡片名称与行号协议
            fields = {name: value[0] for name, value in card["fields"].items()}  # 去除字段行号包装
            result["cards"].append({  # 面向合并层提供扁平字段
                "name": card["name"], "line": card["line"], "definition": fields.get("定义", ""),  # 保留来源行和首次定义
                "appear": [(int(number), title) for number, title in vn.V4_APPEAR_ITEM_RE.findall(fields.get("出现", ""))],  # 每个出现节独立记录
                "depends": _references(fields.get("依赖", "")),  # 依赖按名称列表处理
                "confusable": [fields["易混"]] if fields.get("易混") else [], "original": fields.get("原文", ""),  # 易混保留说明避免改写笔记
            })  # 完成单卡数据
    start = vn.find_h2_line(visible, "自测题")  # 查找正式题目章节
    if start is not None:  # 缺题目不是本阶段新增单集门禁规则
        end = vn.next_h2_after(visible, start + 1)  # 仅解析本节
        questions = vn.parse_quiz_questions_v4(visible, start + 1, end)  # 仅复用过滤后结构识别以排除围栏假题
        for position, question in enumerate(questions):  # 按真实题目行号划定原文边界
            boundary = questions[position + 1]["line"] - 1 if position + 1 < len(questions) else end  # 下一真实题干或本节结束均不属于当前答案
            answer_start = next((index for index in range(question["line"], boundary) if vn.V2_ANSWER_RE.match(visible[index])), None)  # 答案标记只从围栏外结构识别
            answer = lines[answer_start:boundary] if answer_start is not None else []  # 正文切原始行以保留空行围栏和代码
            if answer:  # 第一行仅去掉答案标记其余正文原样保存
                answer[0] = vn.V2_ANSWER_RE.sub(r"\1", answer[0], count=1)  # 与门禁支持的答案标记保持一致
            while answer and not answer[0].strip():  # 仅剔除开头的空白行
                answer.pop(0)  # 正文非空行的缩进和行尾空格不变
            while answer and not answer[-1].strip():  # 仅剔除结尾的空白行
                answer.pop()  # 内部段落间距和正文分隔线完整保留
            last_line = next((index for index in range(boundary - 1, answer_start - 1, -1) if lines[index].strip()), None) if answer_start is not None else None  # 使用原始坐标识别最后非空行的围栏状态
            if position == len(questions) - 1 and end < len(lines) and last_line is not None and visible[last_line].strip() == "---":  # 只去掉最后一题紧邻下一真实二级标题的围栏外节分隔线
                answer.pop()  # 答案内部或围栏内的横线均不受影响
                while answer and not answer[-1].strip():  # 移除节分隔线后再次剔除边界空白行
                    answer.pop()  # 保持实际答案正文的内部格式
            number = int(re.match(r"\s*(\d+)", lines[question["line"] - 1])[1])  # 从来源题干保留原题号而不使用解析器枚举值
            result["quiz"].append({  # 输出适合渲染和导出的题目结构
                "no": number, "line": question["line"], "stem": vn.V2_CORRESPOND_RE.sub("", question["stem"]).strip(),  # 去掉对应标注而保留来源题号和正文题干
                "correspond": question["correspond"], "answer": "\n".join(answer),  # 答案不改写不折叠
            })  # 完成单题解析
    start = vn.find_h2_line(visible, "视频时间索引")  # 时间只来自文末索引
    if start is not None:  # 没有索引留待CR-JUMP提示
        end = vn.next_h2_after(visible, start + 1)  # 不扫描其它章节
        for line in visible[start + 1:end]:  # 按正式导航条目解析
            match = vn.V4_NAV_ENTRY_RE.match(line)  # 同时支持有链和无链条目
            section = re.match(r"^(\d{1,2})\s*[.、．]", match[4]) if match else None  # 必须使用标题显式节号而非列表位置
            if section:  # 只接受能够映射回小节的条目
                result["nav"].setdefault(int(section[1]), (f"{match[1]}:{match[2]}", match[3] or None))  # 重复节号保留首次索引
    return result  # 返回部分数据和完整解析错误


def _done_items(course):  # 课程复习独立于当前分批范围
    return sorted((item for item in course.get("items", []) if item.get("status") == "done" and item.get("note")), key=lambda item: item["index"])  # 仅收已完成且有笔记的条目


def _digest(pairs):  # 使用稳定无歧义的来源序列
    payload = json.dumps(sorted(pairs), ensure_ascii=False, separators=(",", ":"))  # 排序后编码索引与实际摘要
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()  # 生成课程来源总摘要


def source_digest(root, course):  # 检查阶段重新读取实际内容以发现未mark的修改
    return _digest([(0, course.get("title", ""))] + [(item["index"], item["note"], item.get("title", ""), _read_note(Path(root) / item["note"])[1]) for item in _done_items(course)])  # 零号保存课程标题且每篇摘要覆盖相对路径条目标题和实际字节


def _original_group(value):  # 按方案兼容大小写和英文单复数
    return value.casefold().removesuffix("s")  # 只去掉一个末尾字母而不做语义推断


def _choose_original(originals):  # 取多数分组中的多数写法并稳定解决平局
    values = [entry["value"] for entry in originals]  # 输入已经按集号排序
    if not values:  # 中文课允许没有原文
        return ""  # 空字段由渲染层省略
    counts = Counter(_original_group(value) for value in values)  # 同组大小写与单复数共同计票
    group = max(counts, key=counts.get)  # 字典插入顺序保证平局取最早组
    variants = Counter(value for value in values if _original_group(value) == group)  # 组内按原样写法统计
    return max(variants, key=variants.get)  # 同次数仍取最早出现的写法


def _appearance_order(appearance):  # 缺节号统一放在同集有效小节之后
    return appearance["index"], appearance["section"] if appearance["section"] is not None else float("inf")  # 避免空值与整数比较


def build(root, course):  # 只读生成课程复习模型供后续渲染接入
    model = {"course": copy.deepcopy(course), "notes": [], "concepts": [], "questions": [], "source_digest": ""}  # 不修改调用方课程状态
    concepts = {}  # 归一名称映射到共享概念
    for item in _done_items(course):  # 全课程按集序号稳定处理
        parsed = parse_note(Path(root) / item["note"])  # 相对路径从课程根解析
        source = {"index": item["index"], "title": item.get("title", ""), "note": item["note"]}  # 渲染链接与标题共享来源
        model["notes"].append({**source, "parsed": parsed, "sha256": parsed["sha256"]})  # 记录解析错误和实际摘要
        for card in parsed["cards"]:  # 每张卡按中文名称规则合并
            key = concept_key(card["name"])  # 全角空白大小写统一
            if key not in concepts:  # 首次出现保留原始名称和定义
                concepts[key] = {"key": key, "name": card["name"], "original": "", "originals": [], "definition": card["definition"].strip(), "other_definitions": [], "appearances": [], "depends": [], "confusable": [], "questions": [], "first": None}  # 明确公开概念接口
            concept = concepts[key]  # 复用同名跨集概念
            definition = card["definition"].strip()  # 定义比较忽略首尾空白
            known_definitions = {concept["definition"], *(value for _, value in concept["other_definitions"])}  # 相同其它说法不重复
            if definition and definition not in known_definitions:  # 仅保留不同且非空的说法
                concept["other_definitions"].append((item["index"], definition))  # 标明说法来源集
            if card["original"].strip():  # 空原文不形成冲突分组
                concept["originals"].append({"index": item["index"], "value": card["original"].strip()})  # 保留全部原文来源供检查
            for field in ("depends", "confusable"):  # 合并依赖与易混并保留首次顺序
                seen = {concept_key(value) for value in concept[field]}  # 同一引用兼容名称空白和大小写
                for value in card[field]:  # 遍历当前卡片引用
                    if concept_key(value) not in seen:  # 不重复收录相同引用
                        concept[field].append(value)  # 保留第一次的文本
                        seen.add(concept_key(value))  # 同张卡重复也排除
            for section, section_name in card["appear"] or [(None, "")]:  # 无有效出现也保留可诊断的来源
                time, url = parsed["nav"].get(section, (None, None))  # 时间仅从该篇索引取
                concept["appearances"].append({**source, "section": section, "section_name": section_name, "time": time, "url": url})  # 每个出现节各生成一条
        model["questions"].extend({**source, **question} for question in parsed["quiz"])  # 先收齐全部题再跨集查归属
    for concept in concepts.values():  # 完成排序与代表原文选择
        concept["appearances"].sort(key=_appearance_order)  # 每个概念按集号节号排序
        first = concept["appearances"][0]  # 每张卡至少有一个来源
        concept["first"] = (first["index"], first["section"])  # 公共接口保留真实空节号
        concept["original"] = _choose_original(concept["originals"])  # 根据分组频次选择代表写法
    for question in model["questions"]:  # 题目可引用后续集才定义的概念
        key = concept_key(question["correspond"] or "")  # 缺对应标记也归未分类
        concept = concepts.get(key)  # 仅精确归一名称匹配
        question.update(concept=key if concept else None, time=None, url=None)  # 未归类题保留稳定来源接口
        if concept:  # 成功归属的题加入概念引用
            concept["questions"].append((question["index"], question["no"]))  # 方案约定的集号题号二元组
            first = next((entry for entry in concept["appearances"] if entry["index"] == question["index"]), None)  # 只允许同集第一次出现作为回看
            if first and first["time"]:  # 没有时间点时只输出笔记链接
                question.update(time=first["time"], url=first["url"])  # 不跨集借用深链
    model["concepts"] = sorted(concepts.values(), key=lambda concept: _appearance_order(concept["appearances"][0]))  # 概念按第一次出现排序
    model["source_digest"] = _digest([(0, course.get("title", ""))] + [(note["index"], note["note"], note["title"], note["sha256"]) for note in model["notes"]])  # 课程标题与来源路径条目标题及解析时实际字节共同决定复习摘要
    return model  # 后续渲染无需再次解析笔记


def check(model, root):  # 只读检查课程复习模型和当前来源文件
    errors, warnings = [], []  # 错误阻断生成而警告保留交付
    def report(target, code, message, index=None, concept=None):  # 所有诊断保持统一字段
        target.append({"check": code, "message": message, "index": index, "concept": concept})  # 不向磁盘写报告
    for note in model["notes"]:  # 汇总每篇已完成笔记的解析失败
        for error in note["parsed"]["errors"]:  # 一篇可以有多个可修复问题
            report(errors, "CR-PARSE", error, note["index"])  # 保留具体来源集
        if not (Path(root) / note["note"]).is_file():  # 目录或缺失路径都不是有效笔记链接
            report(errors, "CR-LINK", f"笔记链接不存在：{note['note']}", note["index"])  # 生成前阻断坏链接
    course = model["course"]  # 使用构建时课程状态快照
    review = course.get("review") or {}  # 兼容I3和I4没有复习字段的课程
    current_digest = source_digest(root, course)  # 重新读取内容发现build之后的未mark变化
    missing = [name for name in ("概念表.md", "课程自测.md") if not (Path(root) / name).is_file()]  # 两个复习文件均须存在
    if missing or review.get("source_digest") != current_digest or model["source_digest"] != current_digest:  # 旧模型也不能被错误宣称为新文件
        report(errors, "CR-STALE", "课程复习文件缺失或已过期，请重新生成" + (f"：{'、'.join(missing)}" if missing else ""))  # 合并为一条课程级错误
    keys = {concept["key"] for concept in model["concepts"]}  # 跨全课检查依赖不受单集限制
    originals = {}  # 原文映射到首次中文概念
    for concept in model["concepts"]:  # 检查每个合并后的概念
        for entry in concept["appearances"]:  # 缺时间和缺深链都降低跳转率
            if not entry["time"] or not entry["url"]:  # 时间存在但没有深链也应提示
                report(warnings, "CR-JUMP", f"概念出现缺少视频时间点或深链：第 {entry['section']} 节", entry["index"], concept["name"])  # 指明集号和概念
        if len({_original_group(entry["value"]) for entry in concept["originals"]}) > 1:  # 单复数与大小写不会触发冲突
            report(warnings, "CR-ORIG", "同一概念的原文存在不同分组：" + "、".join(dict.fromkeys(entry["value"] for entry in concept["originals"])), concept["first"][0], concept["name"])  # 保留冲突名单
        for original in dict.fromkeys(entry["value"].casefold() for entry in concept["originals"]):  # 同义候选检查全部原文而非只看代表值
            previous = originals.get(original)  # 查找此前不同中文名称
            if previous is not None and previous["key"] != concept["key"]:  # 不自动合并疑似同义概念
                report(warnings, "CR-SAME", f"与「{previous['name']}」的原文相同：{original}", concept["first"][0], concept["name"])  # 提示人工统一名称
            else:  # 首次出现的原文用于后续比较
                originals[original] = concept  # 大小写一致即可视为相同原文
        for field, label in (("depends", "依赖"), ("confusable", "易混")):  # 两种引用都需在全课存在
            for value in concept[field]:  # 易混字段保留说明但仅检查破折号前名称
                names = _references(re.split(r"——|--", value, maxsplit=1)[0]) if field == "confusable" else [value]  # 复用单集引用语义避免把说明当名称
                for name in names:  # 支持一个字段引用多个概念
                    if concept_key(name) not in keys:  # 缺失引用只警告
                        report(warnings, "CR-DEP", f"{label}引用的概念不在课程中：{name}", concept["first"][0], concept["name"])  # 带上引用主体
    for question in model["questions"]:  # 未分类题仍参与总题数
        if question["concept"] is None:  # 不存在的对应概念不创建假概念
            report(warnings, "CR-QUIZ", f"第 {question['no']} 题的对应概念不在课程中：{question['correspond'] or '未指定'}", question["index"], question["correspond"])  # 题目保留原样交付
    appearances = [entry for concept in model["concepts"] for entry in concept["appearances"]]  # 统计出现次数而不是概念数
    summary = {"concepts": len(model["concepts"]), "multi_episode": sum(len({entry["index"] for entry in concept["appearances"]}) > 1 for concept in model["concepts"]), "questions": len(model["questions"]), "jump_rate": sum(bool(entry["time"] and entry["url"]) for entry in appearances) / len(appearances) if appearances else 0.0}  # 空课程跳转率定义为零
    return {"errors": errors, "warnings": warnings, "summary": summary}  # 由CLI调用方决定退出码


import copy as _review_copy  # 独立导入备份深拷贝工具避免依赖主模块导入顺序
import difflib as _review_difflib  # 为未知概念名称提供相近名称建议
import re as _review_re  # 逐行识别概念勾选记录
from datetime import datetime as _ReviewDatetime, timezone as _ReviewTimezone  # 生成带时区的复习更新时间
from pathlib import Path as _ReviewPath  # 接受字符串和路径对象形式的课程目录
import bili_fetch as _review_bili_fetch  # 复用现有原子文本写入器
from _hints import HintError as _ReviewHintError  # 复用统一的三段式失败提示


_REVIEW_CONCEPTS_FILE = "概念表.md"  # 固定课程概念表文件名
_REVIEW_QUIZ_FILE = "课程自测.md"  # 固定课程自测文件名
_REVIEW_MARK_RE = _review_re.compile(r"^\+ \[( |x|X)\] \*\*(.+?)\*\*")  # 仅识别顶层概念名上的三种合法勾选框
_REVIEW_MARK_RECOVERY = "文件可能被加密软件处理过或格式被改坏。先备份 `概念表.md`，确认后删除它重新生成；勾选会从 `course.json` 的备份恢复。"  # 保留方案规定的勾选恢复操作


def _review_now():  # 统一生成可测试且带时区的时间戳
    return _ReviewDatetime.now(_ReviewTimezone.utc).isoformat(timespec="seconds")  # 使用秒精度的标准时间记录状态变化


def read_marks(path):  # 从用户实际编辑的概念表读取掌握状态
    path = _ReviewPath(path)  # 允许调用者传入字符串路径
    try:  # 将读文件和解码异常转换为保护性提示
        text = path.read_text(encoding="utf-8-sig")  # 兼容编辑器附加的字节顺序标记
    except FileNotFoundError:  # 缺失文件允许从清单备份恢复
        return {}  # 首次生成时没有文件中的覆盖项
    except (OSError, UnicodeError) as exc:  # 权限或编码异常不能导致勾选丢失
        raise _ReviewHintError("无法读取概念表中的勾选", "已拒绝生成，不覆盖现有复习文件", _REVIEW_MARK_RECOVERY, str(exc)) from None  # 提示先备份再恢复
    marks = {}  # 按标准化概念键保留每条用户勾选
    for line in text.splitlines():  # 逐行匹配而不识别缩进的其它复选框
        match = _REVIEW_MARK_RE.match(line)  # 限定顶层加号列表及加粗概念名称
        if match:  # 忽略定义和普通正文
            name = _review_re.sub(r"\\([\\|])", r"\1", match.group(2))  # 还原课程Markdown助手添加的反斜线和竖线转义
            marks[concept_key(name)] = match.group(1).lower() == "x"  # 大写勾选与小写勾选具有相同语义
    nonblank = [line for line in text.splitlines() if line]  # 标准零概念表只包含摘要与固定用法两行
    empty_summary = r"> 课程：.*｜概念 0 个（跨集出现 0 个）｜已掌握 0 / 0｜来自 \d+ 篇已完成的笔记｜更新于 .+"  # 严格核对零概念统计摘要的全部字段
    empty_usage = "> 用法：掌握了就把 `[ ]` 改成 `[x]`，重新生成时会保留；也可以让 Agent “考考我没掌握的概念”。"  # 只允许我们生成的标准用法行
    generated_empty = len(nonblank) == 2 and _review_re.fullmatch(empty_summary, nonblank[0]) and nonblank[1] == empty_usage  # 任意额外正文都不属于标准空表
    if text and not marks and not generated_empty:  # 非空文件完全没有合法记录时保护用户编辑内容
        raise _ReviewHintError("概念表不为空，但无法识别任何勾选行", "已拒绝生成，不覆盖现有复习文件", _REVIEW_MARK_RECOVERY)  # 格式损坏不能静默恢复备份
    return marks  # 返回标准化键到布尔状态的映射


def _review_marked(marks, key):  # 同时兼容布尔映射与清单备份结构
    value = marks.get(key, False)  # 未出现的概念默认尚未掌握
    return bool(value.get("mastered", False)) if isinstance(value, dict) else bool(value)  # 清单记录只读取掌握字段


def _review_label(concept, md_text):  # 统一概念名及可选原文的显示方式
    original = concept.get("original") or ""  # 没有原文时省略括号
    return md_text(concept["name"]) + (f"（{md_text(original)}）" if original else "")  # 所有外部单行文本使用课程统一转义


def render_concepts(model, marks, generated_at=None):  # 将合并模型渲染为可直接编辑勾选的概念表
    from course_plan import _md_text, _md_link_path  # 在实际渲染时导入以避开课程入口的循环引用
    concepts = model["concepts"]  # 保持模型已经确定的首次出现顺序
    mastered = sum(_review_marked(marks, concept["key"]) for concept in concepts)  # 只统计本次仍存在的概念
    multi = sum(len({appearance["index"] for appearance in concept["appearances"]}) > 1 for concept in concepts)  # 按出现集数计算跨集概念
    updated = generated_at or _review_now()  # 调用者可固定生成时间以获得稳定产物
    lines = [f"> 课程：{_md_text(model['course'].get('title'))}｜概念 {len(concepts)} 个（跨集出现 {multi} 个）｜已掌握 {mastered} / {len(concepts)}｜来自 {len(model['notes'])} 篇已完成的笔记｜更新于 {_md_text(updated)}", "> 用法：掌握了就把 `[ ]` 改成 `[x]`，重新生成时会保留；也可以让 Agent “考考我没掌握的概念”。", ""]  # 生成无前置元数据的课程摘要
    titles = {note["index"]: note["title"] for note in model["notes"]}  # 集标题来自模型而非当前选择范围
    previous = None  # 首次出现的集变化时插入分组标题
    for concept in concepts:  # 每个标准化概念只生成一个可勾选条目
        index = concept["first"][0]  # 分组只取概念第一次出现的集号
        if index != previous:  # 概念已按首次位置排序
            lines.extend([f"## 第 {index} 集 · {_md_text(titles.get(index, ''))}", ""])  # 课程标题中的换行和竖线按统一规则处理
            previous = index  # 记录本组集号
        box = "x" if _review_marked(marks, concept["key"]) else " "  # 生成便于编辑器点击的任务列表语法
        original = concept.get("original") or ""  # 原文存在时放在加粗概念名之后
        suffix = f"（{_md_text(original)}）" if original else ""  # 空原文完全省略括号
        lines.append(f"+ [{box}] **{_md_text(concept['name'])}**{suffix}")  # 概念名称单行是回读勾选的唯一入口
        lines.append(f"  + 定义：{_md_text(concept.get('definition'))}")  # 保留首集定义且不生成新的说法
        lines.append("  + 出现：")  # 所有出现位置归入同一概念条目
        for appearance in concept["appearances"]:  # 模型已按集号与小节号排序
            section = appearance.get("section")  # 无有效出现位置时小节号为空
            where = f"第 {appearance['index']} 集"  # 即使缺少小节仍提供笔记入口
            if section is not None:  # 仅对有效小节展示小节信息
                where += f" 第 {section} 节「{_md_text(appearance.get('section_name'))}」"  # 小节名称遵循外部文本转义规则
            moment, url = appearance.get("time"), appearance.get("url")  # 保留三种时间索引可用状态
            jump = f"[▶ {_md_text(moment)}]({url})" if moment and url else (f"[{_md_text(moment)}]" if moment else "（无时间点）")  # 无深链时不伪造可点击视频链接
            lines.append(f"    + {where}：[笔记]({_md_link_path(appearance['note'])}) · {jump}")  # 编码相对笔记路径中的空格及特殊字符
        if concept.get("other_definitions"):  # 其它集说法为空时省略整个字段
            lines.append("  + 其它集的说法：")  # 不改写来源笔记中的定义
            for source_index, definition in concept["other_definitions"]:  # 保留模型给出的不同定义顺序
                lines.append(f"    + 第 {source_index} 集：{_md_text(definition)}")  # 明确每一种说法的来源集
        refs = []  # 依赖与易混引用合并到同一显示行
        for field, label in (("depends", "依赖"), ("confusable", "易混")):  # 两类引用保持方案规定顺序
            if concept.get(field):  # 空引用不产生多余标签
                refs.append(f"{label}：{'、'.join(_md_text(value) for value in concept[field])}")  # 列表中的每个外部名称单独转义
        if refs:  # 两类均为空时省略整行
            lines.append("  + " + "｜".join(refs))  # 使用全角分隔符避免表格语法干扰
        if concept.get("questions"):  # 没有关联自测题时省略自测字段
            lines.append("  + 自测：" + "、".join(f"第 {episode} 集第 {number} 题" for episode, number in concept["questions"]))  # 保留原始题号用于回查
        lines.append("")  # 概念条目之间留空便于阅读
    return "\n".join(lines).rstrip() + "\n"  # 产物统一以单个换行结尾


def _review_question_lines(question, number, focus, md_text, md_link_path):  # 统一两个题目区域的编号与回看入口
    source = f"第 {question['index']} 集第 {question['no']} 题"  # 重新编号时仍保留原始集号与题号
    correspond = md_text(question.get("correspond"))  # 不丢失原始对应概念名称
    suffix = source if focus else f"对应：{correspond}；{source}"  # 完整题目区兼顾概念归属与来源
    lines = [f"{number}. {md_text(question['stem'])}（{suffix}）", "", f"**答案：** {question.get('answer') or ''}", ""]  # 答案照搬来源文本并保留多行内容
    links = []  # 同集首个出现位置可用时才提供回看链接
    if question.get("time") and question.get("url"):  # 模型题目的时间已经限定为同一集概念首次出现位置
        links.append(f"[▶ 回看 {md_text(question['time'])}]({question['url']})")  # 缺时间或深链时不构造视频入口
    links.append(f"[笔记]({md_link_path(question['note'])})")  # 即使无法回看仍可返回来源笔记
    lines.extend([" · ".join(links), ""])  # 两个区域都保持同样的答案及链接布局
    return lines  # 调用者决定所属概念或集分组


def render_quiz(model, marks):  # 生成未掌握概念优先且包含全课程题目的自测文件
    from course_plan import _md_text, _md_link_path  # 延迟复用课程Markdown辅助函数避免循环导入
    concepts, questions = model["concepts"], model["questions"]  # 复用已合并的概念与原始题目
    unmastered_keys = {concept["key"] for concept in concepts if not _review_marked(marks, concept["key"])}  # 未归类题目不会被冒充为某个未掌握概念
    focus_questions = [question for question in questions if question.get("concept") in unmastered_keys]  # 只统计属于当前未掌握概念的题目
    lines = [f"> 课程：{_md_text(model['course'].get('title'))}｜共 {len(questions)} 题，来自 {len(model['notes'])} 篇笔记｜没掌握的概念涉及 {len(focus_questions)} 题｜答案不折叠，先遮住答案再自测", "> 在 [概念表](概念表.md) 里勾选已掌握的概念，重新生成后这里只把没掌握的放在最前面。", ""]  # 汇总不受当前课程范围限制
    if any(_review_marked(marks, concept["key"]) for concept in concepts):  # 只有至少勾选当前一个概念才启用优先复习区
        lines.extend([f"## 先复习：还没掌握的概念（{len(focus_questions)} 题）", ""])  # 全部掌握时仍明确显示零题
        grouped = {}  # 先按概念分组避免对每个概念重复扫描所有题目
        for question in focus_questions:  # 保持同一概念内的原始课程顺序
            grouped.setdefault(question["concept"], []).append(question)  # 每道题只属于一个标准化概念
        for concept in concepts:  # 复习区概念顺序与概念表一致
            selected = grouped.get(concept["key"], [])  # 没有题目的概念不产生空标题
            if selected:  # 已掌握概念与没有题目的概念均跳过
                lines.extend([f"### {_review_label(concept, _md_text)}", ""])  # 概念名含原文时附在同一标题
                for number, question in enumerate(selected, 1):  # 每个概念下从一开始连续编号
                    lines.extend(_review_question_lines(question, number, True, _md_text, _md_link_path))  # 保留原始题目与同集回看位置
    else:  # 所有当前概念都未勾选时避免重复整份题库
        lines.extend(["还没有勾选任何概念；在概念表里勾选后，这里会把没掌握的单独列出来。", ""])  # 提供建立掌握状态的操作说明
    lines.extend(["## 全部题目（按集）", ""])  # 未归类题目仍完整保留在原始集分组
    previous, number = None, 0  # 每一集的可读题号独立重置
    for question in questions:  # 模型题目已经按照集号及原始题号排序
        if question["index"] != previous:  # 新的一集开始新的标题和编号
            lines.extend([f"### 第 {question['index']} 集 · {_md_text(question['title'])}", ""])  # 集标题使用统一文本转义
            previous, number = question["index"], 0  # 后续本集题目连续编号
        number += 1  # 不直接使用可能不连续的原始题号
        lines.extend(_review_question_lines(question, number, False, _md_text, _md_link_path))  # 完整区同样注明原始集号与题号
    return "\n".join(lines).rstrip() + "\n"  # 输出纯Markdown并统一末尾换行


def _review_model(root, course):  # 在生成及只读复习查询之前验证来源模型
    model = build(root, course)  # 复用主模块的笔记解析与概念合并逻辑
    if not model["notes"]:  # 尚无已完成笔记时不能生成空复习产物
        raise _ReviewHintError("还没有完成的笔记，先做完至少一集", "无法生成课程复习内容", "先完成至少一集笔记并标记为 done，再运行 concepts")  # 保留统一的可恢复错误接口
    checked = check(model, root)  # 允许复习文件尚未生成或摘要过期
    blocking = [error for error in checked["errors"] if error["check"] in {"CR-PARSE", "CR-LINK"}]  # 仅来源解析和笔记链接错误阻止生成
    if blocking:  # 全量预检通过前不读取或改写复习产物
        detail = "；".join(f"{error['check']}：{error['message']}" for error in blocking)  # 一次反馈所有会阻止生成的来源错误
        raise _ReviewHintError("课程笔记解析或链接检查未通过", "未生成复习文件，原有勾选保持不变", "修复报告中的笔记及链接后重新运行 concepts", detail)  # 让主入口负责输出三段式提示
    return model, checked  # 返回统一摘要供生成回执复用


def _review_merge_marks(root, course, model, master=None, unmaster=None):  # 按备份小于文件小于参数的优先级合并状态
    backup = _review_copy.deepcopy(course.get("mastery") or {})  # 不修改调用者持有的原始状态备份
    marks = {key: _review_marked(backup, key) for key in backup}  # 已经消失的概念仍参与备份保留
    marks.update(read_marks(_ReviewPath(root) / _REVIEW_CONCEPTS_FILE))  # 用户编辑的概念表覆盖清单备份
    concepts = {concept["key"]: concept for concept in model["concepts"]}  # 名称参数只匹配当前仍存在的概念
    for names, mastered in ((master, True), (unmaster, False)):  # 命令行取消标记在同名冲突时最后生效
        names = [names] if isinstance(names, str) else (names or [])  # 公共接口同时接受单个名称和多个名称
        for name in names:  # 逐个验证但暂不触碰任何文件或调用者状态
            key = concept_key(name)  # 参数采用与合并模型完全相同的标准化规则
            if key not in concepts:  # 未知名称不能静默创建备份项
                close = _review_difflib.get_close_matches(key, list(concepts), n=3, cutoff=0.0)  # 确保最多给出三个最相近的当前概念
                choices = "、".join(concepts[value]["name"] for value in close) or "当前课程没有可用概念"  # 用可读原名给出建议
                raise _ReviewHintError(f"找不到概念：{name}", "没有修改掌握状态或复习文件", f"确认概念名称后重试；最接近的名称：{choices}")  # 由主入口统一处理未知名称提示
            marks[key] = mastered  # 参数覆盖文件与备份的相同键
    for key in concepts:  # 新概念默认未掌握且必须写入备份
        marks.setdefault(key, False)  # 不覆盖文件中已经存在的勾选
    return marks, backup, concepts  # 时间戳只在实际生成成功之后写入调用者状态


def generate(root, course, master=None, unmaster=None):  # 生成两个复习文件并原地更新课程但不保存清单
    root = _ReviewPath(root)  # 保持课程相对路径以固定文件名定位
    model, checked = _review_model(root, course)  # 来源解析或链接错误时禁止任何写入
    marks, backup, concepts = _review_merge_marks(root, course, model, master, unmaster)  # 在写入前验证用户勾选和全部名称参数
    generated_at = _review_now()  # 本次两个文件和新增状态使用相同时间
    concepts_text = render_concepts(model, marks, generated_at)  # 两份内容先全部渲染以免渲染异常导致部分覆盖
    quiz_text = render_quiz(model, marks)  # 不修改任何单集笔记
    for key, mastered in marks.items():  # 当前及已经消失的概念全部保留
        previous = backup.get(key)  # 记录覆盖前的备份状态用于判断变化
        name = concepts[key]["name"] if key in concepts else ((previous or {}).get("name", key))  # 当前名称优先且保留消失概念原有名称
        changed = not isinstance(previous, dict) or previous.get("mastered") != mastered  # 只有状态发生变化或首次出现才更新变化时间
        changed_at = generated_at if changed else previous.get("changed_at", generated_at)  # 未变化时保留原来的时间戳
        backup[key] = {"name": name, "mastered": mastered, "changed_at": changed_at}  # 构造可由课程清单校验的完整备份记录
    concepts_path, quiz_path = root / _REVIEW_CONCEPTS_FILE, root / _REVIEW_QUIZ_FILE  # 输出始终位于课程根目录
    try:  # 将磁盘及权限异常转换为既有失败协议
        _review_bili_fetch.atomic_write_text(concepts_path, concepts_text)  # 复用现有原子写入而不手动覆盖
        _review_bili_fetch.atomic_write_text(quiz_path, quiz_text)  # 两份文件都写成功后才提交内存状态
    except OSError as exc:  # 不把磁盘写失败伪装为生成成功
        raise _ReviewHintError("无法写入课程复习文件", "复习文件未全部更新，课程清单尚未修改", "检查课程目录的写入权限和磁盘空间后重新运行 concepts", str(exc)) from None  # 保留原始错误便于排查
    course["mastery"] = backup  # 由调用者负责最终持久化掌握状态
    course["review"] = {"concepts_file": _REVIEW_CONCEPTS_FILE, "quiz_file": _REVIEW_QUIZ_FILE, "generated_at": generated_at, "source_digest": model["source_digest"], "concepts": len(model["concepts"]), "questions": len(model["questions"])}  # 保留兼容版本一的可选复习元数据
    summary = checked["summary"]  # 模型检查已计算统一的跨集数与可跳转率
    mastered_count = sum(marks[key] for key in concepts)  # 消失概念的掌握状态不计入当前课程统计
    return {"concepts_file": str(concepts_path.resolve()), "quiz_file": str(quiz_path.resolve()), "concepts": len(concepts), "multi_episode": summary["multi_episode"], "questions": len(model["questions"]), "mastered": mastered_count, "unmastered": len(concepts) - mastered_count, "jump_rate": summary["jump_rate"], "warnings": checked["warnings"]}  # 返回公开的生成回执且不保存course.json


def unmastered(root, course):  # 只读返回尚未掌握概念与来源题目供Agent逐题复习
    model, checked = _review_model(root, course)  # 复用来源预检而不要求复习文件已经生成
    marks, backup, concepts = _review_merge_marks(root, course, model)  # 用户文件中的勾选优先于清单备份
    pending = [concept for concept in model["concepts"] if not marks[concept["key"]]]  # 保持概念首次出现的顺序
    grouped = {}  # 将题目一次分组避免重复扫描题库
    for question in model["questions"]:  # 未归类题目不会挂在任意概念下
        grouped.setdefault(question.get("concept"), []).append(question)  # 每个概念保存其原始题目顺序
    result = {"concepts": []}  # 没有未掌握概念时返回空列表
    for concept in pending[:50]:  # 限制单次只读回执大小
        questions = [{field: question.get(field) for field in ("index", "no", "stem", "answer", "url")} for question in grouped.get(concept["key"], [])]  # 只返回方案规定的出题字段
        result["concepts"].append({"name": concept["name"], "original": concept.get("original", ""), "definition": concept.get("definition", ""), "questions": questions})  # 不在只读查询中修改时间或备份
    if len(pending) > 50:  # 恰好五十个概念不属于截断
        result["truncated"] = True  # 告知Agent本次只列出了前五十个概念
    return result  # 不写复习文件也不保存课程清单
