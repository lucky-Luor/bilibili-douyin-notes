import argparse  # 解析命令行参数
import copy  # 复制已有进度以保证校验失败不修改原对象
import hashlib  # 计算笔记原始UTF8字节摘要
import json  # 读写课程清单
import math  # 校验数值并计算剩余耗时
import re  # 严格解析范围参数
import sys  # 配置终端编码并输出提示
import time  # 控制字幕探测间隔
from datetime import datetime, timezone  # 生成带时区的时间戳
from pathlib import Path  # 处理课程目录
from urllib.parse import quote  # 编码可点击的相对笔记链接

import bili_fetch  # 复用抓取接口和共享转写比例
import clean_media  # 复用已有媒体候选收集规则
from doctor import load_config  # 复用既有标准库配置读取工具
from _hints import HintError, hint_exit, run_cli  # 复用三段式错误提示和入口包装

NOTE_SECONDS = 60  # I4前十五集总耗时减去ASR耗时的中位数为六十秒
FETCH_SECONDS_PER_ITEM = 5  # 每集抓取的预计时间
MAX_ITEMS = 200  # 一次选择全部时允许的最大集数
MAX_HOURS = 100  # 一次选择全部时允许的最大视频小时数
PROBE_LIMIT = 60  # 默认最多探测的集数
PROBE_INTERVAL = 0.3  # 相邻字幕探测之间的间隔秒数
COURSE_FILE = "course.json"  # 课程清单的固定文件名
ITEM_METADATA = {"index", "bvid", "page", "cid", "title", "section", "video_title", "duration"}  # 重建清单时必须更新的元数据
ITEM_STATES = {"pending", "running", "done", "failed", "out_of_scope"}  # 支持的执行状态


def now():  # 返回可供其它阶段复用的当前时间
    return datetime.now(timezone.utc).isoformat(timespec="seconds")  # 使用带时区的标准时间格式


def _error(reason, next_step="检查参数或课程清单后重试"):  # 构造统一可读错误
    return HintError(str(reason), "课程计划未更新", next_step)  # 保持原因影响下一步三段信息


def _number(value, label="时长"):  # 将接口数值转换为有限非负数
    if isinstance(value, bool):  # 布尔值不是合法数值
        raise _error(f"{label}必须是非负数")  # 避免静默接受错误元数据
    try:  # 兼容接口返回的数字字符串
        result = float(value or 0)  # 缺失时按零处理
    except (TypeError, ValueError, OverflowError):  # 非数值给出可读提示
        raise _error(f"{label}必须是非负数") from None  # 隐藏底层转换异常
    if not math.isfinite(result) or result < 0:  # 拒绝无穷数和负数
        raise _error(f"{label}必须是有限非负数")  # 防止非法估算进入文件
    return result  # 返回有效数值


def _positive(value, label):  # 校验严格的正整数
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):  # 不接受小数负数和布尔值
        raise _error(f"{label}必须是正整数")  # 统一参数错误说明
    result = int(value)  # 转换通过格式校验的整数
    if result < 1:  # 零不属于可选索引
        raise _error(f"{label}必须大于零")  # 明确索引从一开始
    return result  # 返回正整数


def _validate_course(course):  # 校验读取到的清单结构
    if not isinstance(course, dict) or course.get("version") != 1:  # 当前阶段仅支持第一版清单
        raise _error("课程清单格式或版本无效")  # 避免错误覆盖不兼容文件
    if course.get("kind") not in ("pages", "season") or not isinstance(course.get("source_url"), str):  # 校验课程来源和类别
        raise _error("课程清单缺少有效来源或类别")  # 返回结构错误提示
    if "probe" in course and course["probe"] not in ("default", "all", "none"):  # 兼容缺字段旧清单并校验新增探测枚举
        raise _error("课程字幕探测模式必须是 default、all 或 none")  # 拒绝未知模式避免误导报告
    if "template" in course and course["template"] not in ("v4", "v4-obsidian"):  # 可选字段缺失保持旧课程兼容
        raise _error("课程模板必须是 v4 或 v4-obsidian")  # 坏模板禁止进入编排步骤
    if course.get("asr_lang") not in (None, "zh", "en", "auto"):  # 缺字段旧清单等同未设置语言
        raise _error("课程 asr_lang 必须是 zh、en、auto 或 null")  # 阻止非法语言进入后续命令
    items = course.get("items")  # 获取全部课程条目
    if not isinstance(items, list) or not items:  # 已保存课程必须至少有一集
        raise _error("课程清单没有有效条目")  # 拒绝损坏的空清单
    for index, item in enumerate(items, 1):  # 验证全局连续索引
        if not isinstance(item, dict) or item.get("index") != index:  # 防止重复或跳号索引
            raise _error("课程条目索引必须从一连续递增")  # 避免范围选择错误
        if not item.get("bvid") or not item.get("cid") or item.get("status") not in ITEM_STATES:  # 校验必需执行字段
            raise _error(f"第 {index} 集缺少有效标识或状态")  # 明确损坏条目位置
        _positive(item.get("page"), "分P序号")  # 确保分P序号有效
        _number(item.get("duration"), "视频时长")  # 确保耗时计算不会接受非法值
        if item.get("gate") is not None:  # 旧清单允许缺少或空门禁摘要但非空字段必须完整
            gate = item["gate"]  # 读取当前条目可选缓存
            if not isinstance(gate, dict) or any(type(gate.get(key)) is not int or gate[key] < 0 for key in ("errors", "warnings")):  # 拒绝缺失负数和布尔计数
                raise _error(f"第 {index} 集门禁计数无效")  # 损坏缓存不得直接跳过校验
            if not isinstance(gate.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", gate["sha256"]) or gate.get("template") not in ("v3", "v4", "v4-obsidian") or not isinstance(gate.get("checked_at"), str) or not gate["checked_at"]:  # 保留旧模板摘要并允许黑曜石变体的真实门禁缓存
                raise _error(f"第 {index} 集门禁摘要无效")  # 给出可读的清单修复提示
    if not isinstance(course.get("scope"), dict):  # 范围对象必须独立存在
        raise _error("课程清单缺少有效范围")  # 提示清单不完整
    selected_items(course)  # 验证已保存的范围配置
    return course  # 供读取和保存共同使用


def load_course(root):  # 读取并校验课程清单
    path = Path(root) / COURSE_FILE  # 固定从根目录加载清单
    try:  # 将文件和编码问题转换为可读错误
        course = json.loads(path.read_text(encoding="utf-8-sig"))  # 兼容带字节标记的既有文件
    except (OSError, ValueError, UnicodeError) as exc:  # 捕获文件缺失和格式错误
        raise _error(f"无法读取课程清单：{exc}", "确认目录中的 course.json 存在且是有效 JSON；首次使用请先运行 plan") from None  # 不输出堆栈
    return _validate_course(course)  # 返回经过结构校验的对象


def save_course(root, course):  # 原子保存课程清单
    _validate_course(course)  # 写入前完整校验清单
    path = Path(root) / COURSE_FILE  # 构造目标路径
    try:  # 统一目录和写文件错误
        text = json.dumps(course, ensure_ascii=False, indent=2, allow_nan=False) + "\n"  # 保留中文并拒绝非有限数
        path.parent.mkdir(parents=True, exist_ok=True)  # 校验完成后才创建根目录
        bili_fetch.atomic_write_text(path, text)  # 复用已有原子写入实现
    except (OSError, TypeError, ValueError, UnicodeError) as exc:  # 转换文件权限和序列化错误
        raise _error(f"无法保存课程清单：{exc}", "检查输出目录权限和课程数据后重试") from None  # 保留三段式提示
    return path  # 返回已保存文件的位置


def _bounds(scope, count):  # 将独立范围对象转换为包含首尾的索引区间
    mode = scope.get("mode")  # 获取范围选择方式
    if mode in (None, "all"):  # 尚未确认时展示全部计划条目
        return 1, count  # 全部范围不依赖条目状态
    if mode == "first":  # 选择前若干集
        return 1, min(_positive(scope.get("first"), "首批集数"), count)  # 正数超出总数时截断
    if mode == "range":  # 选择严格的连续区间
        value = scope.get("range")  # 支持清单保存的两端列表
        if isinstance(value, str):  # 兼容命令行风格的历史值
            match = re.fullmatch(r"([0-9]+)-([0-9]+)", value)  # 不接受隐式空格和省略边界
            value = list(match.groups()) if match else None  # 将有效文本转成两个边界
        if not isinstance(value, (list, tuple)) or len(value) != 2:  # 区间必须恰有两个边界
            raise _error("范围必须使用起止序号，例如 2-5")  # 给出可直接采用的示例
        first, last = _positive(value[0], "范围起点"), _positive(value[1], "范围终点")  # 严格校验两个整数
        if first > last or last > count:  # 范围禁止反向和越界
            raise _error(f"范围必须满足 1 ≤ 起点 ≤ 终点 ≤ {count}")  # 明确有效区间
        return first, last  # 返回包含端点的区间
    raise _error("课程范围模式无效")  # 拒绝未知模式


def selected_items(course):  # 依据独立范围选择条目而不依赖执行状态
    items = course.get("items", [])  # 获取全部条目
    first, last = _bounds(course.get("scope") or {}, len(items))  # 计算合法范围边界
    return [item for item in items if first <= item["index"] <= last]  # 返回原条目引用供后续阶段更新


def estimate(items):  # 估算给定条目的规模和处理耗时
    items = list(items)  # 同时支持列表和迭代器
    duration = sum(_number(item.get("duration")) for item in items)  # 累加视频秒数
    count = len(items)  # 每个分P独立计作一项
    asr = sum(_number(item.get("duration")) for item in items if item.get("subtitle_hint", "unknown") != "has") * bili_fetch.ASR_RATIO  # 仅需转写的条目采用共享比例
    seconds = asr + count * (NOTE_SECONDS + FETCH_SECONDS_PER_ITEM)  # 按完整规定公式估算
    hours = duration / 3600  # 阈值以视频小时数计算
    return {"videos": len({item["bvid"] for item in items}), "items": count, "asr_items": sum(item.get("subtitle_hint", "unknown") != "has" for item in items), "hours": hours, "duration": duration, "asr_seconds": asr, "note_seconds": count * NOTE_SECONDS, "fetch_seconds": count * FETCH_SECONDS_PER_ITEM, "total_seconds": seconds, "seconds": seconds, "estimated_seconds": seconds, "oversize": count > MAX_ITEMS or hours > MAX_HOURS}  # 返回规模和可机器读取的估算


def remaining_seconds(course):  # 按当前选择范围估算剩余耗时
    items = selected_items(course)  # 仅统计已选择的条目
    done = [item for item in items if item.get("status") == "done" and item.get("seconds") not in (None, "") and _number(item["seconds"], "实际耗时") > 0]  # 仅正数实测耗时参与均值
    pending = [item for item in items if item.get("status") in ("pending", "running")]  # 失败条目不自动算作剩余任务
    if done:  # 已有完成样本时采用实际耗时
        return sum(_number(item.get("seconds"), "实际耗时") for item in done) / len(done) * len(pending)  # 平均耗时乘以剩余条目数
    return estimate(pending)["seconds"]  # 无样本时退回剩余条目的完整公式


def _source_bvid(source):  # 将视频链接规范化为来源标识
    if not isinstance(source, str) or not source.strip():  # 空来源必须立即拒绝
        raise _error("视频来源不能为空")  # 避免无意义的接口请求
    return bili_fetch.extract_bvid(source)  # 复用已有短链接和标准链接解析


def _view(bvid, cookie):  # 获取视频接口的完整数据对象
    data = bili_fetch.api(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", cookie).get("data")  # 公共接口返回完整响应
    if not isinstance(data, dict) or not data:  # 缺失数据不能生成清单
        raise _error("视频接口缺少有效 data")  # 将格式异常转换为可读错误
    return data  # 保留 pages 和合集等所有字段


def _pages(info):  # 提取视频分P并保留单P兼容路径
    pages = info.get("pages")  # 优先使用完整分P列表
    if isinstance(pages, list) and pages:  # 存在分P列表时直接采用
        return pages  # 后续逐项校验必要字段
    page = info.get("page")  # 合集条目通常仅提供一个 page 对象
    if isinstance(page, dict) and page:  # 缺少 pages 时使用 episode.page
        return [page]  # 不额外请求每个合集视频
    if info.get("cid"):  # 普通单P元数据缺列表时保留有限兼容
        return [{"page": 1, "cid": info["cid"], "part": info.get("title", ""), "duration": info.get("duration", 0)}]  # 合成单P元数据
    raise _error("视频条目缺少分P信息")  # 不生成不可执行条目


def _item(index, bvid, page, video_title, section=""):  # 构造统一的课程执行条目
    cid = _positive(page.get("cid"), "视频 CID")  # 每项必须具有真实内容标识
    title = str(page.get("part") or page.get("title") or video_title)  # 优先采用分P名称
    return {"index": index, "bvid": bvid, "page": _positive(page.get("page", 1), "分P序号"), "cid": cid, "title": title, "section": section, "video_title": video_title, "duration": _number(page.get("duration", 0)), "subtitle_hint": "unknown", "status": "pending", "note": "", "started_at": None, "finished_at": None, "seconds": 0, "reason": "", "next_step": "", "rerun": ""}  # 初始化所有必需元数据和进度字段


def _build(info, bvid, kind):  # 按分P或合集模式展开全局连续清单
    if kind == "pages":  # 视频分P按原顺序展开
        return [_item(index, bvid, page, str(info.get("title") or bvid)) for index, page in enumerate(_pages(info), 1)]  # 从一连续编号
    season = info.get("ugc_season")  # B站合集数据由视频详情接口提供
    if not isinstance(season, dict) or not season:  # 显式合集模式必须确实存在合集
        raise _error("该视频没有可用合集", "改用 --scope pages 或换一个有合集的视频")  # 拒绝非法切换
    items = []  # 保存各章节顺序展开的结果
    for section in season.get("sections") or []:  # 按接口章节顺序处理
        for episode in section.get("episodes") or []:  # 按章节内部的视频顺序处理
            arc = episode.get("arc") or {}  # 视频标题和时长可能位于 arc 内
            episode_bvid = episode.get("bvid") or arc.get("bvid")  # 兼容两种合集视频标识位置
            if not episode_bvid:  # 不允许将缺失来源的视频默认为当前视频
                raise _error("合集条目缺少 BV 号")  # 避免抓错内容
            video_title = str(episode.get("title") or arc.get("title") or episode_bvid)  # 取得合集中的视频名称
            for page in _pages(episode):  # 缺少 episode.pages 时自动使用 episode.page
                item = _item(len(items) + 1, episode_bvid, page, video_title, str(section.get("title") or ""))  # 跨章节全局连续编号
                if not item["duration"]:  # 单P合集常把时长只放在 arc 内
                    item["duration"] = _number(episode.get("duration") or arc.get("duration")) if len(_pages(episode)) == 1 else 0  # 多P不重复使用视频总时长
                items.append(item)  # 保留合集顺序和全部条目
    if not items:  # 空合集不能作为有效课程
        raise _error("合集没有可用视频条目")  # 明确拒绝保存空计划
    return items  # 返回展开后的完整清单


def _key(item):  # 返回稳定的进度匹配键
    return item.get("bvid"), item.get("cid"), item.get("page")  # 相同分P在不同范围间共享结果


def _merge(items, previous):  # 重建元数据并保留共同条目的结果和历史字段
    old = {_key(item): item for item in (previous or {}).get("history", []) + (previous or {}).get("items", [])}  # 在模式切换往返后仍保留已执行结果
    for item in items:  # 遍历新计划中的所有条目
        history = old.get(_key(item), {})  # 查找共同条目
        item.update({key: value for key, value in history.items() if key not in ITEM_METADATA})  # 保留状态字幕提示和可选扩展历史
    return items  # 返回就地合并后的新条目


def _apply_scope(course):  # 将范围标记与结果状态协调更新
    selected = {item["index"] for item in selected_items(course)}  # 独立范围是唯一筛选依据
    for item in course["items"]:  # 范围外仍保留历史完成和失败结果
        if item["index"] not in selected and item["status"] in ("pending", "running"):  # 范围外未完成执行条目
            item["status"] = "out_of_scope"  # 明确标记为本轮不处理
        elif item["index"] in selected and item["status"] == "out_of_scope":  # 新范围重新包含此前排除条目
            item["status"] = "pending"  # 恢复为待处理状态


def _probe(items, cookie, probe_all=False):  # 以单轮字幕接口探测课程提示
    candidates = items if probe_all else items[:PROBE_LIMIT]  # 默认限制探测规模
    for index, item in enumerate(candidates):  # 顺序探测避免请求并发
        if index:  # 首次请求不等待
            time.sleep(PROBE_INTERVAL)  # 每次探测之间至少间隔零点三秒
        diagnostics = {}  # 接收公共抓取函数的请求成功和失败计数
        try:  # 单集异常不阻断整个计划
            usable, saw_track = bili_fetch.fetch_subtitles(item["bvid"], item["cid"], cookie, rounds=1, diagnostics=diagnostics)  # 只读轨道信息不下载字幕正文
            if diagnostics.get("failed", 0) and not diagnostics.get("succeeded", 0):  # 明确全失败时才不能作字幕判断
                item["subtitle_hint"] = "unknown"  # 不把请求失败误判为无字幕
            else:  # 至少一个接口请求成功才允许作出判断
                item["subtitle_hint"] = "has" if usable else "track_only" if saw_track else "none"  # 区分可用字幕轨道和真实无字幕
        except Exception:  # 包括网络异常和接口异常
            item["subtitle_hint"] = "unknown"  # 保守保留未知状态供后续阶段处理


def _season_summary(info, items, kind):  # 仅持久化合集的精简摘要
    if kind != "season":  # 分P模式不持久化完整合集响应
        return None  # 用空值明确当前不是合集计划
    season = info.get("ugc_season") or {}  # 获取内嵌合集元数据
    videos = len({item["bvid"] for item in items})  # 根据实际展开结果统计视频数
    return {"id": season.get("id"), "title": season.get("title", ""), "ep_count": season.get("ep_count", videos), "videos": videos}  # 保留必要的身份和规模信息


def _duration_text(seconds):  # 将预计耗时转为适合用户阅读的文案
    minutes = math.ceil(seconds / 60)  # 向上取整避免显示零分钟
    return f"约 {minutes // 60} 小时 {minutes % 60} 分钟" if minutes > 90 else f"约 {minutes} 分钟"  # 超过九十分钟改用小时分钟


def _report(course, root):  # 构造机器回执和可直接转述的确认文案
    full = estimate(course["items"])  # 计划展示完整课程规模
    current = estimate(selected_items(course))  # 确认回执展示当前范围估算
    alternative = course.get("season_alt")  # 仅多P有合集时存在替代规模
    options = "要怎么做？\n1. 全部做\n2. 先做前 3 集看效果\n3. 指定范围（例如 2-5）"  # 非超限课程提供三种选择
    if full["oversize"]:  # 超限确认不提供全部做选项
        options = "超过单次处理上限，要怎么做？\n1. 先做前 3 集看效果\n2. 指定范围（例如 2-5）"  # 仅提供小范围选项
    text = f"这是一门{'分P' if course['kind'] == 'pages' else '合集'}课程《{course.get('title', '')}》：共 {full['items']} 集、约 {full['hours']:.1f} 小时，{full['asr_items']} 集需要语音识别，预计{_duration_text(current['total_seconds'])}（按 6 核笔记本 CPU 估算，先做第 1 集后按实际用时修正）。\n{options}"  # 规模和时间依据完整说明
    if course.get("probe", "default") == "none":  # 完全跳过探测时明确解释保守估算
        text += "\n本次未探测字幕，全部按需要语音识别预估"  # 未探测不等于确认没有字幕
    elif course.get("probe", "default") == "default" and full["items"] > PROBE_LIMIT:  # 仅默认模式且超限才提示前六十集
        text += f"\n默认最多探测前 {PROBE_LIMIT} 集；其余集数按需要语音识别预估，可用 --probe-all 全部探测。"  # 探测失败或跳过均不会承诺有字幕
    if course["kind"] == "season":  # 合集单P入口应提供另一种范围
        text += "\n也可以只做这一个视频（--scope pages）。"  # 不强制展开合集
    if alternative is not None and course["kind"] == "pages":  # 多P同时有合集时给出替代规模
        suffix = "——超出单次处理上限，只能指定范围" if alternative["oversize"] else ""  # 防止诱导处理超大合集
        text += f"\n也可按合集展开（{alternative['videos']} 个视频、共 {alternative['items']} 集、约 {alternative['hours']:.1f} 小时）{suffix}。"  # 明确另一种组织方式的规模
    result = {"course": True, "root": str(Path(root)), "course_json": str(Path(root) / COURSE_FILE), "course_file": str(Path(root) / COURSE_FILE), "source_url": course["source_url"], "kind": course["kind"], "title": course.get("title", ""), "owner": course.get("owner", ""), "items": full["items"], "videos": full["videos"], "hours": full["hours"], "asr_items": full["asr_items"], "oversize": full["oversize"], "estimate_minutes": current["total_seconds"] / 60, "estimate": copy.deepcopy(current), "scope": copy.deepcopy(course["scope"]), "season_alt": copy.deepcopy(alternative), "confirm_text": text, "probe_limit": PROBE_LIMIT}  # 回执的集数字段始终为数值
    if course.get("hint_optional"):  # 保留可选登录建议
        result["hint_optional"] = course["hint_optional"]  # 业务函数返回提示但不主动打印
    return result  # 由主入口统一输出 JSON


def plan(source_url, root, scope=None, probe_all=False, no_probe=False):  # 建立或刷新课程计划
    if scope not in (None, "pages", "season") or probe_all and no_probe:  # 离线直接调用也要校验互斥参数
        raise _error("课程模式或字幕探测参数无效")  # 在任何写入前拒绝参数
    previous = load_course(root) if (Path(root) / COURSE_FILE).exists() else None  # 既有清单损坏时禁止覆盖
    bvid = _source_bvid(source_url)  # 将当前来源规范为视频标识
    if previous and _source_bvid(previous["source_url"]) != bvid:  # 同一目录禁止混用不同视频来源
        raise _error("输出目录已属于另一个视频来源", "使用原视频链接或换一个空目录")  # 异源拒绝并且不写文件
    cookie = bili_fetch.load_sessdata()  # 只在实际计划命令中读取共享登录状态
    info = _view(bvid, cookie)  # 一次获取视频详情和内嵌合集
    pages, season = _pages(info), info.get("ugc_season")  # 获取默认模式的判断依据
    if scope is None and len(pages) == 1 and not season:  # 单P且没有合集不视为课程
        return {"course": False, "source_url": source_url, "bvid": bvid, "pages": len(pages), "title": str(info.get("title") or bvid), "root": str(Path(root))}  # 不创建目录或清单
    kind = scope or ("pages" if len(pages) > 1 else "season")  # 多P默认分P单P有合集默认合集
    if previous and previous["kind"] != kind:  # plan 不静默切换已有课程种类
        raise _error("已有课程模式不同", f"使用 start {root} --scope {kind} 并显式选择处理范围")  # 交给已授权的显式切换路径
    items = _merge(_build(info, bvid, kind), previous)  # 展开课程并按稳定标识复用结果
    course = copy.deepcopy(previous) if previous else {}  # 保留可选的顶层历史字段
    course.setdefault("asr_lang", None)  # 新清单和旧清单刷新统一显式存储未指定语言
    course.update({"version": 1, "source_url": previous["source_url"] if previous else source_url, "kind": kind, "title": str(((season or {}).get("title") or info.get("title") or bvid) if kind == "season" else info.get("title") or bvid), "owner": (info.get("owner") or {}).get("name", ""), "season": _season_summary(info, items, kind), "created_at": previous.get("created_at") if previous else now(), "scope": copy.deepcopy(previous["scope"]) if previous else {"mode": None, "first": None, "range": None, "confirmed_at": None}, "estimate": estimate(items), "items": items})  # 写入完整课程模式和执行元数据
    if len(pages) > 1 and season:  # 仅多P同时带合集时提供替代规模提示
        course["season_alt"] = estimate(_build(info, bvid, "season"))  # 计算合集视频数条目数小时数和超限标记
    else:  # 避免保留已失效的替代提示
        course.pop("season_alt", None)  # 单P合集不提供该字段
    for item in items:  # 新一轮探测不继承旧字幕判断
        item["subtitle_hint"] = "unknown"  # 未探测及关闭探测时全部保持未知
    course["probe"] = "none" if no_probe else "all" if probe_all else "default"  # 持久化本轮实际使用的探测模式
    if not no_probe:  # 显式关闭时不调用字幕接口
        _probe(items, cookie, probe_all)  # 默认仅前六十集其余保留未知
    course["estimate"] = estimate(selected_items(course))  # 探测后按当前范围重新估算转写开销
    known = [item for item in items if item["subtitle_hint"] != "unknown"]  # 提示比例跳过未知条目
    course.pop("hint_optional", None)  # 刷新旧的可选登录提示
    if not cookie and known and sum(item["subtitle_hint"] == "none" for item in known) > len(known) / 2:  # 未登录且已知条目无字幕超过一半
        course["hint_optional"] = "本次未登录且多数已探测条目没有字幕；可运行 python scripts/bili_fetch.py --login 后重新 plan。"  # 登录仅为可选提示
    _apply_scope(course)  # 同范围重跑时保持范围外终态结果
    save_course(root, course)  # 所有元数据和选择校验通过后才落盘
    result = _report(course, root)  # 返回计划结果供入口和离线测试使用
    result["reused"] = previous is not None  # 明确本次是否复用了历史清单
    return result  # 复用标记只属于命令回执


def start(root, first=None, range_=None, all_=False, scope=None, template=None, asr_lang=None):  # 保留模板位置参数并在尾部追加语言
    if asr_lang not in (None, "zh", "en", "auto"):  # 直接调用与命令行采用相同枚举
        raise _error("ASR 语言必须是 zh、en 或 auto")  # 参数无效时不读写清单
    if scope not in (None, "pages", "season"):  # 直接调用也必须拒绝未知类别
        raise _error("课程模式必须是 pages 或 season")  # 无效参数不得修改磁盘
    if sum((first is not None, range_ is not None, bool(all_))) != 1:  # 三种范围必须恰选一种
        raise _error("请且仅请选择 --first、--range 或 --all", "例如 start ROOT --first 5")  # 明确必需的确认动作
    first = _positive(first, "首批集数") if first is not None else None  # 尽早拒绝非正整数
    course = copy.deepcopy(load_course(root))  # 保证校验过程不会修改已加载进度
    if template is None:  # 仅缺省参数才访问可选配置
        config = load_config()  # 测试替换共享工具路径，禁止读取真实配置
        template = config.get("note_template") or "v4" if isinstance(config, dict) else "v4"  # 无配置仍采用原四版
    if template not in ("v4", "v4-obsidian"):  # 直接业务调用与命令行一致校验
        raise _error("笔记模板必须是 v4 或 v4-obsidian")  # 不写入未知模板
    previous_template = course.get("template", "v4")  # 旧课程缺字段按原四版处理
    template_warnings = []  # 仅在实际切换并有完成笔记时提示
    done_count = sum(item["status"] == "done" for item in course["items"])  # 统计已有完成篇数
    if template != previous_template and done_count:  # 已完成结果留给下一轮门禁复查
        template_warnings.append(f"课程模板已改为 {template}，已有 {done_count} 篇完成笔记；下次 next 将复查。")  # 明确篇数与恢复入口
    course["template"] = template  # 顶层持久化新模板而不清空既有结果
    course["asr_lang"] = asr_lang if asr_lang is not None else course.get("asr_lang")  # 重选范围未传语言时保留既有选择
    if scope is not None and scope != course["kind"]:  # 仅显式 scope 支持切换类别
        bvid = _source_bvid(course["source_url"])  # 切换始终复用原来源
        info = _view(bvid, bili_fetch.load_sessdata())  # 获取同源的另一类条目
        items = _merge(_build(info, bvid, scope), course)  # 共有分P保留全部结果和扩展字段
        history = {_key(item): copy.deepcopy(item) for item in course.get("history", []) + course["items"]}  # 保存暂时移出模式的集结果
        course["history"] = list(history.values())  # 切换回原模式时按稳定身份恢复
        course.update({"kind": scope, "items": items, "title": str((info.get("ugc_season") or {}).get("title") or info.get("title") or bvid) if scope == "season" else str(info.get("title") or bvid), "season": _season_summary(info, items, scope), "estimate": estimate(items)})  # 更新类别相关元数据
        if len(_pages(info)) > 1 and info.get("ugc_season"):  # 替代规模只适用于多P合集
            course["season_alt"] = estimate(_build(info, bvid, "season"))  # 切换后同步替代规模
        else:  # 当前来源不再需要替代提示
            course.pop("season_alt", None)  # 避免返回过期字段
    selection = {"mode": "first" if first is not None else "range" if range_ is not None else "all", "first": min(first, len(course["items"])) if first is not None else None, "range": range_, "confirmed_at": now()}  # 创建独立确认对象
    bounds = _bounds(selection, len(course["items"]))  # 校验严格范围后再变更状态
    if selection["mode"] == "range":  # 在清单中统一保存两个整数边界
        selection["range"] = list(bounds)  # 避免后续阶段重复解析文本
    if all_ and estimate(course["items"])["oversize"]:  # 过大课程禁止一次确认全部
        raise _error(f"课程超过 {MAX_ITEMS} 集或 {MAX_HOURS} 小时，不能选择 --all", "使用 --first N 或 --range A-B 分批确认")  # 保证拒绝时不写文件
    course["scope"] = selection  # 完整校验后才应用选择范围
    _apply_scope(course)  # 更新范围外待执行状态并保留完成失败结果
    course["estimate"] = estimate(selected_items(course))  # 持久估算始终按已确认范围重算
    save_course(root, course)  # 只保存合法确认结果
    result = _report(course, root)  # 构造确认回执
    result["items_in_scope"] = len(selected_items(course))  # 返回当前范围集数
    result["pending"] = sum(item["status"] == "pending" for item in selected_items(course))  # 返回当前范围待执行集数
    result["remaining_seconds"] = remaining_seconds(course)  # 附加当前范围的剩余耗时
    result["template"], result["warnings"] = template, template_warnings  # 回执包含实际模板及切换提示
    return result  # 不在业务函数中打印输出


INDEX_FILE = "课程索引.md"  # 根目录索引采用固定的纯Markdown文件名
MEDIA_SUFFIXES = {".m4s", ".m4a", ".mp4", ".mp3", ".wav", ".flac", ".aac", ".ogg", ".opus", ".webm", ".mkv", ".mov", ".avi", ".ts", ".flv"}  # 仅认可实际音视频缓存类型


def _md_link_path(path):  # 按路径保留中文并编码RFC3986非保留字符
    return "".join(char if "\u4e00" <= char <= "\u9fff" else quote(char, safe="/-._~") for char in str(path).replace("\\", "/"))  # 避免空格加号括号和百分号破坏Markdown链接


def _md_text(value):  # 清理外部文本以保持Markdown表格与列表结构
    return str(value or "").replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")  # 保留标题内容并转义表格分隔符


def _clock_text(seconds):  # 索引视频时长采用分秒或时分秒格式
    total = int(_number(seconds))  # 时长统一为非负整秒
    return f"{total // 3600}:{total // 60 % 60:02d}:{total % 60:02d}" if total >= 3600 else f"{total // 60:02d}:{total % 60:02d}"  # 小时出现时分秒补齐两位


def write_index(root, course) -> Path:  # 从当前清单生成纯Markdown课程索引
    root = Path(root)  # 接受字符串及路径对象
    chosen = selected_items(course)  # 目录和执行进度仅采用所选范围
    counts = {state: sum(item["status"] == state for item in chosen) for state in ("done", "failed", "pending", "running")}  # 分别统计各执行状态
    kind = "B站" if course["kind"] == "pages" else "合集"  # 区分分P课程和合集课程
    lines = [f"> 课程：{_md_text(course.get('title'))}（{kind}，UP主：{_md_text(course.get('owner'))}）", f"> 来源：{course['source_url']}", f"> 进度：完成 {counts['done']}/{len(chosen)}，失败 {counts['failed']}，待做 {counts['pending']}，进行中 {counts['running']}；总集 {len(course['items'])}，约 {estimate(course['items'])['hours']:.1f} 小时；更新时间：{now()}", "", "## 目录", ""]  # 头部只用引用块而不写前置元数据
    section = None  # 合集每次章节变化时重新开表
    header = ["| 序号 | 标题 | 时长 | 状态 | 笔记 | 视频 |", "| --- | --- | --- | --- | --- | --- |"]  # 表头顺序固定便于阅读与解析
    if course["kind"] == "pages":  # 分P目录不展示章节小标题
        lines.extend(header)  # 单表覆盖所选分P
    for item in chosen:  # 保持清单的全局课程序号
        if course["kind"] == "season" and item.get("section", "") != section:  # 合集按原始章节连续分组
            section = item.get("section", "")  # 空章节也有可读的默认标题
            lines.extend([f"### {_md_text(section) or '未分组'}", "", *header])  # 每个章节独立表头
        label = {"done": "✅ 完成", "failed": "❌ 失败", "running": "🔄 进行中", "pending": "⏳ 待做", "out_of_scope": "⏳ 待做"}[item["status"]]  # 不按结果状态反推选择范围
        if item["status"] == "pending" and str(item.get("reason") or "").startswith("笔记门禁未通过"):  # 仅待办门禁恢复项显示需要修改
            label = "⏳ 待修改"  # 不覆盖失败或运行中的实际状态
        note = f"[笔记]({_md_link_path(item['note'])})" if item.get("note") else "—"  # 仅引用已记录的正式相对笔记路径
        video = f"https://www.bilibili.com/video/{item['bvid']}?p={item['page']}"  # 每行都定位到真实分P
        lines.append(f"| {item['index']} | {_md_text(item['title'])} | {_clock_text(item['duration'])} | {label} | {note} | [视频]({video}) |")  # 所有动态单元格保持单行
    lines.extend(["", "## 失败与补跑", ""])  # 恢复信息与目录分开便于执行
    failed = [item for item in chosen if item["status"] == "failed"]  # 不展示范围外历史失败
    for item in failed:  # 每个失败完整保留三个恢复字段
        lines.append(f"+ 第 {item['index']} 集 {_md_text(item['title'])}；原因：{_md_text(item.get('reason'))}；下一步：{_md_text(item.get('next_step'))}")  # 失败原因与恢复步骤采用中文标签
        lines.append(f"  补跑：`{str(item.get('rerun') or '').replace(chr(13), ' ').replace(chr(10), ' ')}`")  # 下一行保留可直接复制的反引号补跑命令
    if not failed:  # 无失败时明确当前结果
        lines.append("无失败集。")  # 不虚构补跑命令
    lines.extend(["", "## 未选择的集", "", f"共 {len(course['items']) - len(chosen)} 集；可运行 start {command_text([str(root.resolve())])} --range A-B 选择范围。", ""])  # 未选章节仅提供数量及范围命令
    path = root / INDEX_FILE  # 固定根目录索引位置
    try:  # 将写入失败转换为统一提示
        bili_fetch.atomic_write_text(path, "\n".join(lines))  # 复用已有临时文件及原子替换实现
    except OSError as exc:  # 文件权限和磁盘异常不能伪装成成功
        raise _error(f"无法写入课程索引：{exc}", "检查课程目录权限后运行 index ROOT 重建索引") from None  # 课程清单仍可用于重建
    return path  # 按接口约定返回Path对象


def index(root):  # 命令行主动重建当前课程索引
    root = Path(root).resolve()  # 命令回执始终采用绝对路径
    return {"index_file": str(write_index(root, load_course(root)))}  # 读取真实清单并返回索引位置


def _cache_candidates(root):  # 为干跑和实际删除共同收集并预检媒体候选
    root = Path(root).resolve()  # 课程根目录是唯一安全边界
    videos = root / "videos"  # 只遍历课程视频缓存目录
    def within(path, boundary):  # 跟随符号链接后检查候选仍属于给定目录
        resolved = path.resolve()  # 绝对路径点点和符号链接使用同一套判断
        if not resolved.is_relative_to(boundary):  # 越界必须明确拒绝而非静默忽略
            raise HintError(f"拒绝清理 videos 目录外路径：{path}", "本次未删除任何缓存", "修正视频目录链接或 metadata.media_path 后重试 clean")  # 所有预检结束前不删除
        return resolved  # 返回解析后的身份用于去重和产物保护
    boundary = within(videos, root)  # 即使videos本身指向课程外也必须拒绝
    if not videos.is_dir():  # 尚未抓取任何视频属于正常空缓存
        return []  # 不创建缓存目录
    folders = sorted(path for path in videos.iterdir() if path.is_dir())  # 仅遍历videos的直接视频子目录
    for folder in folders:  # 收集前检查每个目录防止进入外部缓存
        within(folder, boundary)  # 子目录链接不能逃逸实际videos边界
    protected = {(folder / "metadata.json").resolve() for folder in folders}  # 元数据即使链接到媒体扩展名也必须保护
    if (root / COURSE_FILE).is_file():  # 清理也可用于尚未建立清单的空课程目录
        course = load_course(root)  # 读取已记录的笔记位置防止扩展名异常的产物被删除
        protected.update((root / item[key]).resolve() for item in course.get("history", []) + course["items"] for key in ("note", "planned_note") if item.get(key))  # 正式笔记和中断草稿同样受保护
    candidates = {}  # 同一候选路径仅计算一次
    for folder in folders:  # 对每个视频目录复用原有收集器
        for path in clean_media.collect(folder):  # 不改动clean_media的单视频行为
            path = Path(path)  # 兼容收集器返回的路径对象
            resolved = within(path, boundary)  # 所有候选先做逃逸校验再判断媒体类型
            suffix = path.suffix.lower()  # 大小写扩展名采用相同判据
            protected_suffixes = {".txt", ".json", ".done", ".md", ".markdown", ".srt", ".vtt", ".ass", ".lrc"}  # 正式文本笔记元数据及完成标记始终保护
            parts = path.name.lower().split(".")[1:]  # 临时产物链中任一明确文本类型都不清理
            artifact = any("." + part in protected_suffixes for part in parts)  # 同时保护txt.part及txt.done.part等产物
            temporary = suffix == ".part" and not artifact  # 保持原收集器任意分片临时名称规则
            if resolved not in protected and not artifact and (suffix in MEDIA_SUFFIXES or temporary):  # 元数据不能将明确产物伪装成缓存候选
                candidates.setdefault(resolved, (path, path.stat().st_size))  # 同一实际路径的点点或链接别名只统计并删除一次
    return sorted(candidates.values())  # 完整候选及大小全部确定后才允许删除


def clean(root, apply=False):  # 默认只统计缓存显式apply才删除
    try:  # 文件系统错误统一转换为可读恢复提示
        candidates = _cache_candidates(root)  # 删除前完成整个批次的路径及产物保护预检
        total = sum(size for path, size in candidates)  # 与完成回执共享同一份字节统计
        deleted, freed = 0, 0  # 干跑始终保持零删除
        if apply:  # 只有调用方显式选择才执行逐项删除
            for path, size in candidates:  # 预检已覆盖所有候选避免越界引发部分删除
                path.unlink()  # 删除候选文件而不递归删除目录
                deleted += 1  # 仅成功删除后计数
                freed += size  # 返回实际成功释放的媒体字节数
    except OSError as exc:  # 直接业务调用也不泄漏原始文件异常
        raise HintError(f"无法清理课程缓存：{exc}", "缓存清理未完成", "检查 videos 目录权限后重新运行 clean") from None  # 保持项目三段式失败提示
    return {"mode": "apply" if apply else "dry-run", "files": len(candidates), "paths": [str(path) for path, size in candidates], "bytes": total, "deleted": deleted, "freed_bytes": freed}  # 干跑保留数量并返回完整候选清单


def _finished_receipt(root, course):  # 无待处理条目时写索引并汇总完成情况
    chosen = selected_items(course)  # 完成回执不包含范围外历史结果
    done = sum(item["status"] == "done" for item in chosen)  # 精确统计本轮成功
    failed = [item for item in chosen if item["status"] == "failed"]  # 保留完整恢复字段构造文案
    index_file = write_index(root, course).resolve()  # 发出回执前保证索引已落盘
    notes_dir = (root / "notes").resolve()  # 笔记目录即使尚不存在也使用绝对路径
    dry_run = clean(root)  # 完成回执直接复用clean的干跑逻辑
    cache = {key: dry_run[key] for key in ("files", "bytes")}  # 机器字段只包含数量及字节
    lines = [f"课程《{course.get('title', '')}》本次范围 {len(chosen)} 集，完成 {done} 篇笔记，失败 {len(failed)} 集。", f"课程索引：{index_file}", f"笔记目录：{notes_dir}"]  # 首句明确课程所选规模及完成失败数量
    if failed:  # 没有失败则不生成补跑段落
        lines.append("失败集补跑：")  # 用户可以逐集恢复而不重复已完成任务
        for item in failed:  # 每集都列出原因建议及实际补跑命令
            lines.append(f"+ 第 {item['index']} 集 {item['title']}：{item.get('reason') or ''}；下一步：{item.get('next_step') or ''}；补跑：{item.get('rerun') or ''}")  # 保留调用方提供的恢复步骤
    lines.append(f"缓存的视频和音频共 {cache['bytes'] / 1048576:.1f} MB，要清理吗？（清理不影响笔记和转写稿）" if cache["bytes"] else "没有需要清理的缓存。")  # 明确只询问媒体缓存且不影响正式产物
    return {"status": "finished", "done": done, "failed": len(failed), "failed_items": [item["index"] for item in failed], "index_file": str(index_file), "notes_dir": str(notes_dir), "cache": cache, "receipt_text": "\n".join(lines)}  # 扩展原结束回执并保持已有结果字段


def status(root):  # 只读查询当前范围的进度
    course = load_course(root)  # 不创建目录或重写清单
    items = selected_items(course)  # 状态统计只包含独立选择范围
    counts = {state: sum(item["status"] == state for item in items) for state in sorted(ITEM_STATES)}  # 所有状态始终提供明确计数
    counts["total"] = len(items)  # 提供所选总集数
    return {"course": True, "root": str(Path(root)), "index_file": str((Path(root) / INDEX_FILE).resolve()), "source_url": course["source_url"], "kind": course["kind"], "title": course.get("title", ""), "scope": copy.deepcopy(course["scope"]), "counts": counts, "failed_items": [copy.deepcopy(item) for item in items if item["status"] == "failed"], "items": copy.deepcopy(items), "remaining_seconds": remaining_seconds(course)}  # 返回完整的只读进度回执及绝对索引位置


def script_argv(name, *args):  # 为 Agent 提供不经 shell 解析的参数数组
    return [sys.executable, str(Path(__file__).resolve().parent / name), *(str(arg) for arg in args)]  # 使用当前解释器和绝对脚本路径


def command_text(argv):  # 展示命令始终给参数加引号
    return " ".join('"' + value.replace("\\", "/").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"' for value in argv)  # 实际执行优先使用参数数组


def _note_path(root, course, item):  # 默认按讲义编号命名并对重复标题消歧
    if item.get("note") or item.get("planned_note"):  # 补跑和中断恢复均保留已分配位置
        return root / (item.get("note") or item["planned_note"])  # 正式路径与草稿路径都相对课程目录
    def filename(entry):  # 统一生成候选文件名
        title = bili_fetch.safe_name(entry["title"])  # 先清洗视频标题中的非法字符
        match = re.match(r"^(\d+\.\d+)(?:[\s.、_\-]+|$)(.*)", title)  # 识别章节式编号
        prefix, topic = (match.group(1), match.group(2).strip()) if match else (f'{entry["index"]:03d}', title)  # 其它标题使用全局序号
        return f'{prefix}-{topic or "未命名"}.md'  # 保证主题非空
    name = filename(item)  # 先采用方案规定的文件名
    collisions = [entry for entry in course["items"] if filename(entry).casefold() == name.casefold()]  # Windows 同名比较不区分大小写
    if len(collisions) > 1:  # 合集中不同视频可能拥有相同讲义编号
        name = f'{item["index"]:03d}-{name}'  # 用全局序号避免相互覆盖
    reserved = {(root / path).resolve().as_posix().casefold() for entry in course.get("history", []) + course["items"] if _key(entry) != _key(item) for path in (entry.get("note"), entry.get("planned_note")) if path}  # 历史完成路径和已分配草稿都不能被其它集占用
    candidate = root / "notes" / name  # 先尝试标准候选名称
    serial = 0  # 文件已存在或被历史记录占用时稳定增加后缀
    while candidate.resolve().as_posix().casefold() in reserved or candidate.exists():  # 不覆盖未知文件或其它集的已完成笔记
        serial += 1  # 同时处理多次清洗或重新编号造成的重复
        candidate = root / "notes" / f'{Path(name).stem}-{item["index"]:03d}-{serial}.md'  # 保留方案要求的前导编号与主题
    return candidate  # 只有确认没有占用的路径才交给 Agent


def run_gate(root, course, item):  # 进程内执行本集笔记门禁并返回摘要
    import validate_note  # 惰性加载现有门禁而不创建子进程
    root = Path(root)  # 统一业务路径类型
    note = root / (item.get("note") or item.get("planned_note") or "")  # 优先检查正式笔记并支持已计划路径
    folder = root / "videos" / item["bvid"]  # 只使用本视频的抓取目录
    metadata = folder / "metadata.json"  # 门禁元数据位置固定
    result = {"errors": 0, "warnings": 0, "sha256": "", "template": course.get("template", "v4"), "checked_at": now(), "first_errors": []}  # 返回统一门禁摘要结构
    try:  # 路径读取和编码异常统一转为门禁错误
        note.resolve().relative_to(root.resolve())  # 课程外笔记不能冒充课程产物
        if not note.is_file() or not metadata.is_file():  # 缺笔记或元数据必须失败而不能宽松通过
            result.update(errors=1, first_errors=["GATE: 笔记文件不存在" if not note.is_file() else "GATE: 元数据文件不存在"])  # 提供可直接转述的首错
            return result  # 不调用缺少输入的校验器
        raw = note.read_bytes()  # 摘要严格基于文件原始UTF8字节
        language_options = {"source_lang": course["asr_lang"]} if course.get("asr_lang") else {}  # 非空课程语言显式转发且旧调用不增加参数
        report = validate_note.check_note_dispatch(raw.decode("utf-8"), note, meta_path=metadata, txt_dir=folder, only_page=item["page"], template=result["template"], note_type=None, **language_options)  # 无显式语言时由目标分集元数据回退
        result.update(errors=len(report["errors"]), warnings=len(report["warnings"]), sha256=hashlib.sha256(raw).hexdigest(), first_errors=[f"{error['check']}: {error['message']}" for error in report["errors"][:3]])  # 保存计数和最多三条首错
    except (OSError, ValueError, TypeError, KeyError) as exc:  # 读取或路径问题也必须阻止完成而非泄漏内部异常
        result.update(errors=1, first_errors=[f"GATE: 无法检查笔记：{exc}"])  # 明确门禁失败原因供恢复使用
    return result  # 调用者决定拒绝完成或恢复待办


def recheck(root, course):  # 重新核验选定范围中的完成笔记并一次保存变更
    root = Path(root)  # 文件路径以课程目录为基准
    reopened, changed = [], False  # 恢复列表与持久化变更分开记录
    for item in selected_items(course):  # 范围外历史完成项不受影响
        if item["status"] != "done":  # 只复核原已完成项
            continue  # 待办运行中和失败项均保持原样
        note = root / (item.get("note") or "")  # 不把草稿误作正式完成文件
        reason, next_step = "", ""  # 仅需要重开时填写恢复原因
        if not note.is_file():  # 正式文件缺失直接恢复制作
            reason, next_step = "笔记文件不见了", "重新制作这一集"  # 使用方案规定文案
        else:  # 文件仍在时先检查原字节摘要和模板
            gate = item.get("gate") or {}  # 没有缓存的旧版完成项需要真实门禁
            try:  # 读取失败仍需交给统一门禁回报
                digest = hashlib.sha256(note.read_bytes()).hexdigest()  # 内容未变可免去重复校验
            except OSError:  # 不可读文件不能命中摘要缓存
                digest = None  # 随后的门禁生成可恢复错误
            if gate.get("sha256") == digest and gate.get("template") == course.get("template", "v4"):  # 仅摘要和模板均一致时跳过
                continue  # 不刷新时间也不重写清单
            checked = run_gate(root, course, item)  # 旧版或已变化项重新执行真实门禁
            if not checked["errors"]:  # 有效改动保持完成并刷新缓存
                item["gate"] = {key: value for key, value in checked.items() if key != "first_errors"}  # 持久化摘要不带错误正文
                changed = True  # 缓存更新也是需要保存的变化
                continue  # 成功项不进入恢复列表
            reason, next_step = "笔记门禁未通过：" + checked["first_errors"][0], "按门禁报告修改这篇笔记"  # 首错直接供恢复交接
        item.update(status="pending", planned_note=item.get("note"), note=None, reason=reason, next_step=next_step)  # 保持原输出路径并撤销完成状态
        reopened.append(item["index"])  # 返回全局课程序号
        changed = True  # 多集变化统一延后保存
    if changed:  # 无变化保持只读且不刷新清单时间
        save_course(root, course)  # 全部重检结束只保存一次
    return reopened  # next和verify共享恢复结果


def verify(root):  # 显式重检选定范围的已完成笔记
    course = load_course(root)  # 读取最新清单并验证可选门禁字段
    checked = sum(item["status"] == "done" for item in selected_items(course))  # 统计重检前原完成数量
    reopened = recheck(root, course)  # 与next使用同一恢复和持久化逻辑
    write_index(root, course)  # 重检恢复或缓存更新后始终刷新课程索引
    return {"checked": checked, "passed": checked - len(reopened), "reopened": reopened}  # 重开项从通过数量中扣除


def course_next(root):  # 交出一集的执行步骤而不执行抓取或模型
    root = Path(root).resolve()  # 将所有输出路径变成绝对路径
    course = load_course(root)  # 读取唯一状态来源
    if not course["scope"].get("mode"):  # 未得到用户选择不得开始
        hint_exit("尚未确认课程范围", "本次不会开始制作笔记", "先向用户展示确认文案，再运行 start 选择范围")  # 给出恢复步骤
    reopened = recheck(root, course)  # 开始交接前恢复缺失或不合格的完成笔记
    chosen = selected_items(course)  # 历史完成状态与当前范围分开计算
    item = next((entry for entry in chosen if entry["status"] == "running"), None)  # 中断后优先继续原来的一集
    if item is None:  # 没有未完成的运行中任务
        item = next((entry for entry in chosen if entry["status"] == "pending"), None)  # 按课程序号取下一集
    counts = {state: sum(entry["status"] == state for entry in chosen) for state in ("done", "failed")}  # 仅统计范围内结果
    if item is None:  # 失败集不会自动重试造成无限循环
        return {**_finished_receipt(root, course), "reopened": reopened}  # 写索引和回执并保留门禁恢复列表
    if item["status"] != "running":  # 首次交出时才设置开始时间
        item.update(status="running", started_at=now())  # 重复 next 不覆盖计时起点
        save_course(root, course)  # 原子保存后再交出命令
    folder = root / "videos" / item["bvid"]  # 同一 BV 共享一次抓取结果
    metadata = folder / "metadata.json"  # 与现有单视频流程目录一致
    fetched, page, completed = metadata.is_file(), None, False  # 首轮尚未抓取时必须输出转写步骤
    if fetched:  # 已有元数据必须校验归属与目标分P
        try:  # 文件内容出错不能伪装成已完成
            meta = json.loads(metadata.read_text(encoding="utf-8"))  # 元数据按 UTF-8 读取
            page = next((entry for entry in meta["pages"] if entry["page"] == item["page"] and entry["cid"] == item["cid"]), None)  # 校验页码和音频身份
            if meta.get("bvid") != item["bvid"] or page is None:  # 阻止过期或错配元数据
                raise ValueError("BV号或目标分P与课程清单不一致")  # 由三段提示报告原因
            text = folder / f'{page["page"]:02d}_{bili_fetch.safe_name(page["part"])}.txt'  # 只使用目标正式字幕文件
            if text.is_file():  # 孤立完成标记不能让文本缺失的集被跳过
                from bili_transcribe import txt_done  # 不加载 faster-whisper 等重型依赖
                completed = page.get("subtitle_detail") in ("cc", "ai") or txt_done(folder, page)  # 兼容旧转写稿并复用原判据
        except (OSError, ValueError, KeyError, TypeError) as exc:  # 所有可预期文件错误都给恢复提示
            raise HintError("已有视频元数据不可用", "这一集暂时无法生成正确的执行步骤", f"核对并重新抓取 {metadata}，或将本集 mark 为 failed 后继续", str(exc)) from None  # 保留 running 以便定位和恢复
    note = _note_path(root, course, item)  # 生成确定且不冲突的输出路径
    try:  # 笔记目录由编排器预先建立
        note.parent.mkdir(parents=True, exist_ok=True)  # 支持新课程及多级目录
    except OSError as exc:  # 写权限失败使用统一提示
        raise HintError("无法创建笔记目录", "这一集尚未开始写笔记", "检查目录权限后重试 next", str(exc)) from None  # 不产生误完成状态
    if not item.get("planned_note"):  # 一旦分配便持久保留，写到一半也不会漂移
        item["planned_note"] = note.relative_to(root).as_posix()  # 独立于正式完成结果，pending可重用自己的草稿
        save_course(root, course)  # 恢复时不把自己的文件当成陌生文件
    fetch = script_argv("bili_fetch.py", item["bvid"], folder)  # 抓取命令只针对当前 BV
    transcribe = script_argv("bili_transcribe.py", metadata, "--page", item["page"])  # ASR 一次只处理这一集
    if course.get("asr_lang") is not None:  # 不覆盖转写器原有缺省行为
        transcribe.extend(["--lang", course["asr_lang"]])  # 将明确选择原样传给识别入口
    template = course.get("template", "v4")  # 缺字段旧课程继续采用四版
    common = [note, "--meta", metadata, "--txt-dir", folder, "--page", item["page"], "--template", template]  # 导航与门禁共用课程模板及页码
    nav, validate = script_argv("note_nav.py", *common), script_argv("validate_note.py", *common)  # 按先导航后最终门禁顺序执行
    if course.get("asr_lang") not in (None, "zh"):  # 外语与自动模式显式交给术语门禁
        validate.extend(["--source-lang", course["asr_lang"]])  # 导航器不接收语言参数
    steps = [  # 脚本不执行这些命令，交给 Agent 串行处理
        {"kind": "fetch", "skip": fetched, "cmd": command_text(fetch), "argv": fetch, "why": "这个视频已抓取过（metadata.json 已存在）" if fetched else "首次处理这个视频，先抓取元数据和字幕"},  # 同 BV 不重复抓取
        {"kind": "transcribe", "skip": completed, "cmd": command_text(transcribe), "argv": transcribe, "why": "这一集已有可信字幕或完成的转写稿" if completed else "抓取后按 subtitle_detail 决定：cc / ai 且正式文本存在则跳过，否则仅转写这一集"},  # 探测提示不是实际字幕产出的保证
        {"kind": "note", "template": template, "note_path": str(note), "txt_dir": str(folder), "metadata": str(metadata), "nav_cmd": command_text(nav), "nav_argv": nav, "validate_cmd": command_text(validate), "validate_argv": validate, "existing_note": note.is_file(), "reason": item.get("reason")},  # 模板及已有笔记恢复原因共同交给制作步骤
    ]  # 每集固定三个阶段
    return {"status": "next", "index": item["index"], "total_in_scope": len(chosen), **counts, "item": dict(item), "steps": steps, "reopened": reopened}  # 正常交接回执包含恢复序号


def mark(root, index, state, note=None, reason=None, next_step=None, rerun=None):  # 回报单集结果并输出人类可读进度
    from _hints import failure_record  # 与其它脚本共享单集失败记录结构
    from datetime import datetime  # 课程时间使用已有秒级时间格式
    root = Path(root).resolve()  # 补跑命令与笔记定位不依赖工作目录
    course = load_course(root)  # 每次重新读取最新清单
    item = next((entry for entry in course["items"] if entry["index"] == index), None)  # 全局序号不随选择范围变化
    if item is None or state not in ("done", "failed", "pending"):  # API 调用与 CLI 一致校验
        hint_exit("集序号或结果状态无效", "本次没有修改课程清单", "使用 status 查看序号，并选择 done、failed 或 pending")  # 明确支持的状态
    if state == "done":  # 存在笔记才允许回报成功
        path = Path(note) if note else Path("")  # 缺省值留给统一校验
        if note and not path.is_absolute():  # 支持相对课程目录路径
            path = root / path  # 相对路径有明确基准
        try:  # 约束课程笔记路径便于 I4 相对引用
            relative = path.resolve().relative_to(root)  # 拒绝把课程外文件伪装为课程产物
        except ValueError:  # 相对引用不可成立
            hint_exit("笔记路径不在课程目录内", "这一集尚未标记完成", "把笔记放入课程 notes 目录后重新 mark")  # 不移动或覆盖外部文件
        if not note or not path.is_file():  # 目录不能冒充笔记文件
            hint_exit("笔记文件不存在", "这一集尚未标记完成", "先完成笔记与门禁，再通过 --note 指定真实文件")  # 门禁是否通过由 Agent 负责
        item["note"] = relative.as_posix()  # 存储跨平台相对路径
        gate = run_gate(root, course, item)  # 原路径校验通过后执行真实笔记门禁
        if gate["errors"]:  # 有任何错误都不能提交完成状态
            hint_exit("笔记门禁未通过：" + gate["first_errors"][0], "这一集尚未标记完成", "按门禁报告修改这篇笔记后重新 mark")  # 失败退出且不写清单
        item["gate"] = {key: value for key, value in gate.items() if key != "first_errors"}  # 只持久化通过后的门禁摘要
    if state == "failed" and not (reason or "").strip():  # 失败必须附有可操作原因
        hint_exit("标记失败时缺少原因", "本次没有修改课程清单", "通过 --reason 填写失败原因，可附 --next-step")  # 禁止空白原因
    if state == "pending":  # 手工补跑清空结果而不改集身份
        for key in ("note", "started_at", "finished_at", "seconds", "reason", "next_step", "rerun"):  # 只重置执行相关字段
            item[key] = None  # 下一次 next 会重新计时
    else:  # 成功和失败都记录结束时刻及可用耗时
        finished = now()  # 一次读取保证字段一致
        item["finished_at"] = finished  # 记录这一轮结束时间
        _number(item.get("seconds"), "实际耗时")  # 损坏的历史耗时不能参与计算
        try:  # 统一转换旧清单中损坏的日期
            elapsed = (datetime.fromisoformat(finished) - datetime.fromisoformat(item["started_at"])).total_seconds() if item.get("started_at") else None  # 手工标记不凭空制造耗时
        except (TypeError, ValueError):  # 清单日期格式不合法时给可读错误
            raise HintError("课程开始时间无效", "本次未修改课程清单", "核对 started_at 或将本集标记 pending 后重试") from None  # 不输出内部日期堆栈
        item["seconds"] = max(0, elapsed) if elapsed is not None else None  # 系统时钟回拨时不保存负数
        if state == "failed":  # 三段字段由统一工具生成
            rerun = rerun or command_text(script_argv("course_plan.py", "mark", root, index, "pending"))  # 默认只将这一集放回待办
            rec = failure_record(HintError(reason.strip(), "这一集没有完成，后续集将继续处理", next_step or "按补跑命令重试这一集"), "这一集没有完成")  # 使用统一原因和恢复文案
            rec["rerun"] = rerun  # 补跑命令独立保存，避免重复混入下一步字段
            item.update({key: rec[key] for key in ("reason", "next_step", "rerun")})  # 使用 I4 预留字段
        else:  # 成功后清除过去失败记录
            item.update(reason=None, next_step=None, rerun=None)  # 不保留过期补跑提示
    item["status"] = state  # 校验结束后才改变状态
    save_course(root, course)  # 只用原子写更新清单
    write_index(root, course)  # 成功失败及放回待办每次都刷新课程索引
    chosen = selected_items(course)  # 进度独立计算当前选定范围
    done = sum(entry["status"] == "done" for entry in chosen)  # 范围外历史结果不计入本次进度
    failed = sum(entry["status"] == "failed" for entry in chosen)  # 只统计本轮失败
    position = next((i for i, entry in enumerate(chosen, 1) if entry["index"] == index), index)  # 指定范围从第一集开始显示进度
    remaining = remaining_seconds(course)  # 完成集平均耗时或初始公式
    if state == "failed":  # 失败明确说明会继续
        progress = f'[{position}/{len(chosen)}] ❌ {item["title"]}：{item["reason"]}｜下一步：{item["next_step"]}；已记录，继续下一集'  # 可原样转述给用户
    elif state == "done":  # 成功显示视频时长与实际耗时
        seconds = int(item["seconds"] or 0)  # 手工标记时显示零秒而不参与平均耗时
        duration = int(item["duration"])  # 展示整秒时长
        progress = f'[{position}/{len(chosen)}] ✅ {item["title"]}（{duration // 60} 分 {duration % 60:02d} 秒的视频，用时 {seconds // 60} 分 {seconds % 60:02d} 秒）｜已完成 {done}，失败 {failed}，剩余约 {math.ceil(remaining / 60)} 分钟'  # 进度行保持单行
    else:  # 补跑也向用户报告状态
        progress = f'[{position}/{len(chosen)}] ⏳ {item["title"]}：已放回待处理｜已完成 {done}，失败 {failed}，剩余约 {math.ceil(remaining / 60)} 分钟'  # 不误报制作成功
    progress = progress.replace("\n", " ").replace("\r", " ")  # 外部标题与原因不能破坏单行进度
    print(progress, file=sys.stderr)  # stdout 保持机器摘要
    return {"index": index, "status": state, "done": done, "failed": failed, "remaining_seconds": remaining, "progress": progress}  # main 最后统一打印 JSON


class _Parser(argparse.ArgumentParser):  # 让参数错误沿用项目三段式提示
    def error(self, message):  # 替换默认的英文用法错误出口
        raise _error(f"命令行参数错误：{message}", "运行 course_plan.py --help 查看 plan/start/status 用法")  # 由统一入口处理而不打印堆栈


def main(argv=None):  # 支持显式参数列表以便离线调用
    for stream in (sys.stdout, sys.stderr):  # 同时设置标准输出和错误输出编码
        try:  # 捕获测试流或宿主不支持重新配置的情况
            stream.reconfigure(encoding="utf-8", errors="replace")  # 保证中文 JSON 和提示可打印
        except (AttributeError, OSError, ValueError):  # 内存流无需重新配置
            pass  # 保持原有可写流
    parser = _Parser(description="B站课程：规划、执行、索引和媒体缓存清理")  # 完整列出当前开放的课程操作
    commands = parser.add_subparsers(dest="command", required=True)  # 必须选择一个命令
    plan_parser = commands.add_parser("plan", help="建立或刷新课程计划")  # 定义计划子命令
    plan_parser.add_argument("source_url", metavar="URL")  # 接受视频链接或 BV 号
    plan_parser.add_argument("root", metavar="ROOT")  # 接受输出课程根目录
    plan_parser.add_argument("--scope", choices=("pages", "season"))  # 允许显式指定课程类别
    probes = plan_parser.add_mutually_exclusive_group()  # 两种探测开关不能同时使用
    probes.add_argument("--probe-all", action="store_true")  # 允许显式扩大字幕探测范围
    probes.add_argument("--no-probe", action="store_true")  # 允许完全跳过字幕探测
    start_parser = commands.add_parser("start", help="确认本次处理范围")  # 定义范围确认子命令
    start_parser.add_argument("root", metavar="ROOT")  # 从指定根目录读取既有清单
    start_parser.add_argument("--scope", choices=("pages", "season"))  # 显式切换同源课程类别
    start_parser.add_argument("--template", choices=("v4", "v4-obsidian"))  # 可选覆盖配置中的默认笔记模板
    start_parser.add_argument("--asr-lang", choices=("zh", "en", "auto"))  # 明确课程识别语言并保留不传时的原行为
    choices = start_parser.add_mutually_exclusive_group(required=True)  # 必须恰好确认一种范围
    choices.add_argument("--first")  # 首批集数在业务函数中严格校验
    choices.add_argument("--range", dest="range_")  # 接收包含首尾的区间文本
    choices.add_argument("--all", dest="all_", action="store_true")  # 选择全部时执行规模门禁
    status_parser = commands.add_parser("status", help="只读查询当前范围进度")  # 定义只读查询子命令
    status_parser.add_argument("root", metavar="ROOT")  # 接受清单根目录
    verify_parser = commands.add_parser("verify", help="重检已完成笔记并恢复待办")  # 显式提供与next共享的复核入口
    verify_parser.add_argument("root", metavar="ROOT")  # 从指定课程目录加载清单
    next_parser = commands.add_parser("next", help="交出下一集执行步骤")  # 串行编排只产生命令
    next_parser.add_argument("root", metavar="ROOT")  # 从清单恢复执行状态
    mark_parser = commands.add_parser("mark", help="记录单集结果或补跑")  # 所有结果写入唯一清单
    mark_parser.add_argument("root", metavar="ROOT")  # 课程根目录
    mark_parser.add_argument("index", type=int)  # 全局集序号
    mark_parser.add_argument("state", choices=("done", "failed", "pending"))  # 只开放三种回报状态
    mark_parser.add_argument("--note")  # 成功笔记路径
    mark_parser.add_argument("--reason")  # 单集失败原因
    mark_parser.add_argument("--next-step")  # 面向用户的恢复步骤
    mark_parser.add_argument("--rerun")  # 可选显式补跑命令
    index_parser = commands.add_parser("index", help="重建课程Markdown索引")  # 主动刷新当前所选条目索引
    index_parser.add_argument("root", metavar="ROOT")  # 索引来自根目录课程清单
    clean_parser = commands.add_parser("clean", help="查看或清理课程媒体缓存")  # 默认仅统计不删除
    clean_parser.add_argument("root", metavar="ROOT")  # 仅处理课程videos下的候选缓存
    clean_parser.add_argument("--apply", action="store_true")  # 显式开关才实际删除媒体
    try:  # 参数和文件错误统一生成可读提示
        args = parser.parse_args(argv)  # 支持调用者提供参数列表
        options = vars(args).copy()  # 生成业务函数关键字参数
        command = options.pop("command")  # 子命令名称不传入业务函数
        result = {"plan": plan, "start": start, "status": status, "next": course_next, "mark": mark, "verify": verify, "index": index, "clean": clean}[command](**options)  # 保留复核并分发索引及清理入口
    except HintError as exc:  # 已经具备三段式信息的错误
        print(json.dumps({"status": "error", **exc.to_dict()}, ensure_ascii=False))  # 即使失败也保留末行机器可读 JSON
        raise  # 交给公共入口包装输出人类提示
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:  # 将文件参数和接口结构异常转换为可读错误
        error = _error(f"无法完成课程操作：{exc}")  # 隐藏底层错误堆栈
        print(json.dumps({"status": "error", **error.to_dict()}, ensure_ascii=False))  # 输出统一错误摘要
        raise error from None  # 交给公共入口包装处理
    for warning in result.get("warnings", []):  # 模板切换提示也供人类直接阅读
        print("warning: " + warning, file=sys.stderr)  # 标准输出保持唯一机器摘要
    if result.get("hint_optional"):  # 可选登录建议同时出现在结构化回执中
        print("提示：" + result["hint_optional"], file=sys.stderr)  # 人类提示只输出到错误流
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))  # 标准输出最后一行为中文无转义 JSON
    return result  # 便于离线测试和后续阶段直接调用


if __name__ == "__main__":  # 只在直接执行脚本时运行命令行
    run_cli(main)  # 复用项目入口确保错误只有三段提示没有堆栈
