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
        for question in vn.parse_quiz_questions_v4(visible, start + 1, end):  # 直接复用题目和答案提取
            answer = list(question["answer_raw"])  # 保留多行答案且不修改解析器结果
            if answer:  # 只去掉第一行答案标记
                answer[0] = vn.V2_ANSWER_RE.sub(r"\1", answer[0], count=1)  # 与门禁支持的答案标记保持一致
            result["quiz"].append({  # 输出适合渲染和导出的题目结构
                "no": question["no"], "line": question["line"], "stem": vn.V2_CORRESPOND_RE.sub("", question["stem"]).strip(),  # 去掉对应标注而保留正文题干
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
    return _digest([(item["index"], _read_note(Path(root) / item["note"])[1]) for item in _done_items(course)])  # 即使gate存在也不能省略实际文件读取


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
    model["source_digest"] = _digest([(note["index"], note["sha256"]) for note in model["notes"]])  # 由解析时实际读取的内容生成摘要
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
