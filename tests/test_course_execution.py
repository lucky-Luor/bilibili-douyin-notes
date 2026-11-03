import copy  # 隔离离线响应与课程进度对象
import json  # 读写课程清单及命令行回执
import socket  # 阻止测试意外建立网络连接
import sys  # 支持从临时目录及项目测试目录加载脚本
import urllib.parse  # 校验离线接口请求中的视频编号
import urllib.request  # 阻止绕过接口桩的直接请求
from datetime import datetime, timedelta, timezone  # 固定课程执行时间以精确校验耗时
from pathlib import Path  # 处理含中文和空格的跨平台路径
from types import SimpleNamespace  # 汇总离线调用记录
import pytest  # 提供隔离夹具和参数化测试


_CANDIDATES = [Path(__file__).resolve().parents[1], Path.cwd(), *Path.cwd().parents]  # 兼容复制到项目测试目录及从项目根目录直接运行临时文件
_REPO = next(path for path in _CANDIDATES if (path / "scripts" / "course_plan.py").is_file())  # 从真实脚本定位项目而非依赖临时目录层级
sys.path.insert(0, str(_REPO / "scripts"))  # 与项目现有共享测试配置保持一致
import course_plan as cp  # 通过公开业务函数及真实命令行入口验证执行协议
import doctor  # 隔离共享配置读取器


FIXTURE = Path(cp.__file__).resolve().parents[1] / "tests" / "fixtures" / "bili_view_pages.json"  # 始终复用仓库现有四集响应
BASE_TIME = datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)  # 使用带时区的固定起点避免依赖真实时钟


def read_course(root):  # 从磁盘观察公开持久化协议
    return json.loads((Path(root) / "course.json").read_text(encoding="utf-8"))  # 不依赖被测模块的加载辅助实现


def write_course(root, course):  # 注入测试所需的历史执行状态
    (Path(root) / "course.json").write_text(json.dumps(course, ensure_ascii=False, indent=2), encoding="utf-8")  # 保留完整中文字段


def item_at(root, index=1):  # 读取指定全局序号的持久化快照
    return read_course(root)["items"][index - 1]  # 固定响应具有从一开始的连续序号


def step_of(result, kind):  # 按业务阶段查找结构化步骤
    assert isinstance(result, dict) and result["status"] == "next"  # 每次交接必须返回明确的结构化状态
    matches = [step for step in result["steps"] if step["kind"] == kind]  # 避免通过列表位置掩盖阶段重复或缺失
    assert len(matches) == 1  # 每集每种阶段恰好交出一次
    return matches[0]  # 返回完整步骤以供业务断言


def make_note(root, relative="notes/中文 空格 笔记.md"):  # 创建可供成功回报使用的真实笔记
    path = Path(root) / relative  # 笔记始终位于课程根目录下
    path.parent.mkdir(parents=True, exist_ok=True)  # 允许使用多级中文目录
    from test_validate_note_v4 import GOOD  # 复用真实合规内容而不屏蔽门禁
    content = GOOD.split("4. 为什么 action")[0]  # 短笔记保留前三道自测并省略可选时间索引
    path.write_text(content, encoding="utf-8")  # 写入真实可通过v4短笔记
    metadata, meta, _ = cache_metadata(root)  # 成功回报必须拥有对应元数据
    meta["suggested_note_type"] = "short"  # 通过已有元数据协议选择短笔记规则
    for page in meta["pages"]:  # 进度标题允许换行但Windows临时文件名不能含控制字符
        page["part"] = page["part"].replace("\n", " ").replace("\r", " ")  # 保留清单原始多行标题只规范测试字幕文件名
    metadata.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")  # 各页共用同一真实视频元数据
    for page in meta["pages"]:  # 多次完成回报可以使用同一无索引短笔记
        text = metadata.parent / f'{page["page"]:02d}_{cp.bili_fetch.safe_name(page["part"])}.txt'  # 每页独立正式转写稿
        text.write_text("[00:00] 合法课堂转写内容" * 20, encoding="utf-8")  # 门禁输入全部来自临时测试目录
    return path  # 同时支持绝对路径与相对路径回报


def cache_metadata(root, index=1, detail="none"):  # 模拟抓取器已经落盘的完整元数据
    course = read_course(root)  # 从当前课程身份构造一致缓存
    item = course["items"][index - 1]  # 选中需要构造缓存的分集
    folder = Path(root) / "videos" / item["bvid"]  # 同一视频的分集共用抓取目录
    folder.mkdir(parents=True, exist_ok=True)  # 不执行真正的抓取脚本
    pages = [dict(page=entry["page"], cid=entry["cid"], part=entry["title"], duration=entry["duration"], subtitle_detail=detail) for entry in course["items"] if entry["bvid"] == item["bvid"]]  # 保留抓取产物所需的真实分集字段
    meta = dict(bvid=item["bvid"], title=course["title"], owner=course["owner"], pages=pages, suggested_note_type="lecture")  # 构造符合现有抓取器格式的缓存
    path = folder / "metadata.json"  # 固定元数据文件位置
    path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")  # 测试使用真实文件系统检查缓存复用
    page = next(entry for entry in pages if entry["page"] == item["page"])  # 查找当前分集的正式文本名
    text = folder / f'{page["page"]:02d}_{cp.bili_fetch.safe_name(page["part"])}.txt'  # 与现有字幕及转写脚本保持一致
    return path, meta, text  # 返回独立文件位置以便构造损坏及边界场景


def invoke(capsys, *argv):  # 校验真实命令行入口的返回值和输出协议
    result = cp.main([str(arg) for arg in argv])  # 不用子进程避免引入环境和网络差异
    captured = capsys.readouterr()  # 分别检查机器摘要与用户进度
    lines = captured.out.strip().splitlines()  # 标准输出必须为单行摘要
    assert len(lines) == 1 and json.loads(lines[0]) == result  # 回执字典与唯一标准输出严格一致
    return result, captured.err  # 将人类进度单独返回给调用者


@pytest.fixture(autouse=True)  # 每个测试都封锁网络与真实登录状态

def offline(monkeypatch, tmp_path):  # 安装最小且可观察的外部服务桩
    monkeypatch.setattr(doctor, "CONFIG", tmp_path / "isolated-settings.json")  # 禁止范围确认读取本机真实配置
    response = json.loads(FIXTURE.read_text(encoding="utf-8"))  # 复用已有页面固定响应
    state = SimpleNamespace(response=response, api_calls=[], probes=[], cookie_calls=0)  # 每个用例拥有独立调用记录
    def blocked(*args, **kwargs):  # 任一漏网请求直接终止测试
        raise AssertionError("课程执行测试禁止真实网络请求")  # 不把网络隔离失败伪装成业务失败
    def api(url, cookie="", **kwargs):  # 仅允许规划时请求当前视频详情
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)  # 校验实际使用的接口参数
        assert query.get("bvid") == [state.response["data"]["bvid"]]  # 错误视频来源不能被通用成功桩掩盖
        state.api_calls.append((url, cookie))  # 记录后续执行是否意外重新抓取
        return copy.deepcopy(state.response)  # 调用方不得污染原始响应
    def sessdata():  # 禁止读取本机真实凭据
        state.cookie_calls += 1  # 允许检查执行阶段不读取登录配置
        return "offline-session"  # 使用无实际权限的固定测试字符串
    def subtitles(bvid, cid, cookie, *args, diagnostics=None, **kwargs):  # 保持现有字幕探测函数的二元返回协议
        state.probes.append((bvid, cid, cookie))  # 保留探测顺序以识别意外调用
        if diagnostics is not None:  # 支持规划器可选的请求诊断字段
            diagnostics.update(attempted=1, succeeded=1, failed=0)  # 成功确认无字幕与请求失败明确区分
        return [], False  # 所有执行缓存测试均由本地文件决定是否跳过
    monkeypatch.setattr(socket, "create_connection", blocked)  # 阻止高层套接字联网
    monkeypatch.setattr(socket.socket, "connect", blocked)  # 同时阻止低层套接字绕过高层封锁
    monkeypatch.setattr(urllib.request, "urlopen", blocked)  # 阻止标准库直接请求字幕正文
    monkeypatch.setattr(cp.bili_fetch, "api", api)  # 规划详情请求全部来自固定响应
    monkeypatch.setattr(cp.bili_fetch, "load_sessdata", sessdata)  # 不读取实际配置中的登录态
    monkeypatch.setattr(cp.bili_fetch, "fetch_subtitles", subtitles)  # 阻止所有真实字幕探测
    return state  # 允许测试检查规划后的调用增量


@pytest.fixture  # 为所有执行测试提供统一的真实规划入口

def course(tmp_path, offline):  # 以闭包支持未确认首批及指定范围三种场景
    def create(confirmed=True, **selection):  # 每个测试默认处理四集完整课程
        root = tmp_path / "中文 课程 空格"  # 所有测试自然覆盖中文和空格目录
        cp.plan(offline.response["data"]["bvid"], root, no_probe=True)  # 使用现有规划函数而非手造不完整清单
        if confirmed:  # 只有未确认测试明确跳过范围选择
            cp.start(root, **(selection or {"all_": True}))  # 使用真实范围校验与状态标记
        return root  # 公开函数均接收实际课程根目录
    return create  # 后续场景只补充自己需要的执行状态


def test_next_requires_explicit_start(course):  # 未确认范围不得自动开始执行
    root = course(confirmed=False)  # 只建立计划而不调用范围确认
    before = (root / "course.json").read_bytes()  # 保存拒绝前的完整状态
    with pytest.raises((cp.HintError, SystemExit)) as exc:  # 统一错误类型必须可由调用方捕获
        cp.course_next(root)  # 未授权选择范围时请求下一集
    assert "start" in str(exc.value)  # 提示必须给出可以执行的恢复入口
    assert (root / "course.json").read_bytes() == before  # 拒绝时不把首集改为运行中


def test_repeated_next_resumes_running_without_resetting_start(course, monkeypatch):  # 重复交接应保持同一运行任务和计时起点
    root = course()  # 默认完整范围
    monkeypatch.setattr(cp, "now", lambda: BASE_TIME.isoformat())  # 固定首次交接时刻
    first = cp.course_next(root)  # 首集由待处理变为运行中
    monkeypatch.setattr(cp, "now", lambda: (BASE_TIME + timedelta(minutes=8)).isoformat())  # 后续交接发生在更晚时刻
    second = cp.course_next(root)  # 断点恢复应继续原来一集
    assert first["index"] == second["index"] == 1  # 不能跳过尚未回报结果的运行任务
    assert second["item"]["status"] == "running"  # 回执快照同步持久化状态
    assert first["item"]["started_at"] == second["item"]["started_at"] == BASE_TIME.isoformat()  # 重复调用不能重置耗时起点
    assert item_at(root)["started_at"] == BASE_TIME.isoformat()  # 文件中的开始时间也保持不变


def test_next_orders_pending_and_skips_completed(course):  # 完成集不会再次交出且未完成集按全局顺序推进
    root = course()  # 建立四集课程
    note = make_note(root)  # 已有真实笔记可直接回报完成
    seeded = cp.mark(root, 1, "done", note=str(note))  # 模拟恢复时首集已完成
    assert seeded["status"] == "done" and seeded["done"] == 1  # 成功回执区分完成数与当前状态
    for index in (2, 3, 4):  # 后续待处理顺序不能乱序
        result = cp.course_next(root)  # 每次只交出一集
        assert result["index"] == index and result["done"] == index - 1  # 计数与已回报状态一致
        assert [step["kind"] for step in result["steps"]] == ["fetch", "transcribe", "note"]  # 交接顺序固定为抓取转写及笔记
        marked = cp.mark(root, index, "done", note=str(note))  # 回报后才允许推进
        assert marked["done"] == index  # 完成数逐集累加
    finished = cp.course_next(root)  # 完成回执新增索引与缓存信息
    assert {key: finished[key] for key in ("status", "done", "failed", "failed_items", "reopened")} == {"status": "finished", "done": 4, "failed": 0, "failed_items": [], "reopened": []}  # 保留原有全部完成及门禁恢复断言


def test_failed_item_records_default_pending_rerun_and_continues(course):  # 单集失败不会阻断后续课程
    root = course()  # 选择四集完整范围
    cp.course_next(root)  # 先交出首集记录开始时间
    result = cp.mark(root, 1, "failed", reason="模拟音频下载失败")  # 仅提供必须的原因使用默认恢复建议
    failed = item_at(root)  # 检查可用于后续状态报告的持久化字段
    assert result["status"] == "failed" and result["failed"] == 1 and result["done"] == 0  # 失败和成功计数独立
    assert failed["reason"] == "模拟音频下载失败" and failed["next_step"]  # 失败原因和下一步不得丢失
    assert all(token in failed["rerun"] for token in ("course_plan.py", '"mark"', '"1"', '"pending"'))  # 默认补跑只将失败集恢复待办
    assert root.resolve().as_posix() in failed["rerun"].replace("\\", "/")  # 补跑命令不依赖原工作目录
    assert cp.course_next(root)["index"] == 2  # 已失败条目不会被自动无限重试


def test_mark_pending_clears_all_execution_fields_and_requeues(course):  # 手动重跑清理旧成功失败及计时数据
    root = course()  # 建立可读写课程
    before = read_course(root)  # 保存稳定身份字段供清理后对照
    before["items"][0].update(status="failed", note="notes/旧笔记.md", started_at=BASE_TIME.isoformat(), finished_at=(BASE_TIME + timedelta(seconds=99)).isoformat(), seconds=99, reason="旧失败", next_step="旧建议", rerun="旧命令")  # 构造完整的历史结果
    write_course(root, before)  # 注入历史状态
    result = cp.mark(root, 1, "pending")  # 请求重新处理同一集
    item = item_at(root)  # 检查所有执行字段而非只检查状态
    assert result["status"] == item["status"] == "pending" and result["failed"] == 0  # 重置后不再计作失败
    assert all(item[field] is None for field in ("note", "started_at", "finished_at", "seconds", "reason", "next_step", "rerun"))  # 清空完整的执行结果集合
    assert all(item[field] == before["items"][0][field] for field in ("index", "bvid", "page", "cid", "title", "duration"))  # 身份和课程元数据不得改变
    assert cp.course_next(root)["index"] == 1  # 被放回待办的首集应再次交出


def test_mark_done_rejects_nonexistent_note_without_writing(course):  # 不存在的笔记不能使课程误报成功
    root = course()  # 初始化完整课程
    cp.course_next(root)  # 首集处于运行中
    before = (root / "course.json").read_bytes()  # 保存回报前状态
    with pytest.raises((cp.HintError, SystemExit)) as exc:  # 错误必须沿用公共提示类型
        cp.mark(root, 1, "done", note="notes/不存在.md")  # 指向尚未生成的笔记
    assert "笔记" in str(exc.value) and "不存在" in str(exc.value)  # 失败原因应可诊断
    assert (root / "course.json").read_bytes() == before  # 校验失败不修改当前进度


def test_mark_done_resolves_relative_note_against_root(course, tmp_path, monkeypatch):  # 相对笔记路径必须以课程目录为基准
    root = course()  # 课程所在目录与当前目录明确分离
    note = make_note(root, "notes/自定义 中文 主题.md")  # 创建带空格的真实笔记
    cp.mark(root, 1, "failed", reason="旧失败记录")  # 成功回报应同时清除历史失败提示
    monkeypatch.chdir(tmp_path)  # 防止碰巧从课程目录运行掩盖路径问题
    result = cp.mark(root, 1, "done", note="notes/自定义 中文 主题.md")  # 传入课程内相对位置
    item = item_at(root)  # 检查存储采用便于跨平台引用的格式
    assert result["status"] == "done" and item["note"] == "notes/自定义 中文 主题.md"  # 不能存成当前目录或机器绝对路径
    assert all(item[field] is None for field in ("reason", "next_step", "rerun"))  # 完成状态不残留失败操作建议
    assert Path(step_of(cp.course_next(root), "note")["note_path"]).parent == note.parent  # 后续课程沿用课程笔记目录


@pytest.mark.parametrize("selection,expected", [({"first": 2}, [1, 2]), ({"range_": "2-3"}, [2, 3])])  # 首批与任意区间分别校验范围统计

def test_scope_counts_and_completion_exclude_outside_items(course, selection, expected):  # 当前范围外历史结果不污染执行统计
    root = course(**selection)  # 使用真实范围选择入口
    course_data = read_course(root)  # 范围外注入历史成功及失败
    outside = [item for item in course_data["items"] if item["index"] not in expected]  # 按明确范围识别未选条目
    outside[0].update(status="done", note="notes/历史.md", seconds=999)  # 历史成功不参与本次均值
    outside[1].update(status="failed", reason="范围外失败")  # 历史失败不参与本次失败汇总
    write_course(root, course_data)  # 保存保留终态的合法历史清单
    note = make_note(root)  # 给范围内的真实完成回报提供文件
    for position, index in enumerate(expected):  # 只应遍历用户选定分集
        result = cp.course_next(root)  # 索引保持全局编号
        assert result["index"] == index and result["total_in_scope"] == 2  # 范围总数不能误用整课集数
        assert result["done"] == position and result["failed"] == 0  # 只计入范围内已回报结果
        marked = cp.mark(root, index, "done", note=str(note))  # 完成一集推进局部进度
        assert marked["progress"].startswith(f"[{position + 1}/2]")  # 进度序号按范围内位置显示
    finished = cp.course_next(root)  # 新回执包含绝对索引路径等附加字段
    assert {key: finished[key] for key in ("status", "done", "failed", "failed_items", "reopened")} == {"status": "finished", "done": 2, "failed": 0, "failed_items": [], "reopened": []}  # 范围外结果不改变本轮结束及恢复摘要
    assert cp.status(root)["counts"]["total"] == 2  # 只读状态采用相同范围口径


def test_existing_valid_metadata_skips_fetch_without_network(course, offline):  # 已有同源元数据复用抓取产物
    root = course()  # 规划阶段允许一次离线详情请求
    metadata, _, _ = cache_metadata(root)  # 只创建本地合法元数据
    calls = len(offline.api_calls), len(offline.probes), offline.cookie_calls  # 保存执行前外部服务调用数
    result = cp.course_next(root)  # 请求编排步骤而不真正抓取
    fetch = step_of(result, "fetch")  # 缓存命中应明确说明可跳过
    assert fetch["skip"] is True and "metadata.json" in fetch["why"]  # 跳过原因必须与实际缓存相符
    assert step_of(result, "note")["metadata"] == str(metadata.resolve())  # 后续笔记指向同一份元数据
    assert (len(offline.api_calls), len(offline.probes), offline.cookie_calls) == calls  # 编排阶段不访问接口也不读取登录态


@pytest.mark.parametrize("wrong_field", ["bvid", "page", "cid"])  # 三种身份错配都不能静默复用

def test_metadata_identity_mismatch_has_recovery_hint(course, wrong_field):  # 错配缓存必须明确提示重新抓取或跳过失败集
    root = course()  # 选择合法课程范围
    path, meta, _ = cache_metadata(root)  # 先生成完整一致元数据
    if wrong_field == "bvid":  # 视频归属错误独立于分集身份
        meta["bvid"] = "BV1wrongCache"  # 使用不同视频编号模拟陈旧缓存
    else:  # 只改目标页码或目标内容编号
        meta["pages"][0][wrong_field] += 100  # 保持其余字段合法以隔离错误原因
    path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")  # 模拟抓取缓存被错误替换
    with pytest.raises((cp.HintError, SystemExit)) as exc:  # 不得冒泡原始字典或格式异常
        cp.course_next(root)  # 缓存校验发生在交出步骤之前
    message = str(exc.value)  # 公共异常必须包含完整恢复提示
    assert "metadata.json" in message and "failed" in message and "抓取" in message  # 告诉执行者缓存位置和继续后续集的方式
    assert all(label in message for label in ("原因：", "影响：", "下一步："))  # 错误沿用项目三段式提示
    assert item_at(root)["status"] != "done"  # 错配不能产生误完成状态


@pytest.mark.parametrize("detail,present", [("cc", True), ("cc", False), ("ai", True), ("ai", False)])  # 人工字幕与自动字幕均要求正式文本落盘

def test_cc_ai_skip_requires_formal_text(course, detail, present):  # 字幕标签及存疑副本不能代替真实输入文本
    root = course()  # 建立完整四集课程
    _, _, text = cache_metadata(root, detail=detail)  # 元数据明确声明可信字幕类型
    suspect = text.with_name(text.stem + cp.bili_fetch.AI_MISMATCH_SUFFIX + ".txt")  # 模拟仅存疑副本存在的情况
    suspect.write_text("存疑字幕内容" * 100, encoding="utf-8")  # 副本体积足够大也不能作为正式字幕
    if present:  # 可信字幕即使很短也可以直接使用
        text.write_text("短字幕", encoding="utf-8")  # 刻意小于旧转写稿兼容阈值
    result = cp.course_next(root)  # 以实际产物而非探测提示决定是否转写
    assert step_of(result, "transcribe")["skip"] is present  # 缺正式文本时仍需处理


@pytest.mark.parametrize("size,marker,expected", [(99, False, False), (100, False, True), (1, True, True), (None, True, False)])  # 覆盖旧文本阈值完成标记与孤立标记四种边界

def test_transcript_completion_uses_real_text_and_txt_done(course, size, marker, expected):  # 复用现有转写完成判据并防护孤立标记
    root = course()  # 使用无可信字幕提示的课程
    _, _, text = cache_metadata(root, detail="none")  # 本场景只能依据转写产物完成状态
    if size is not None:  # 空缺文本与小文本必须区分
        text.write_bytes(b"x" * size)  # 按字节精确验证一百字节的旧文本阈值
    if marker:  # 模拟转写脚本已经生成完成标记
        text.with_name(text.name + ".done").write_text("{}", encoding="utf-8")  # 不依赖标记内容只校验其存在性
    result = cp.course_next(root)  # 下一集只判断缓存不会运行模型
    assert step_of(result, "transcribe")["skip"] is expected  # 孤立完成标记不得跳过缺失的正式文本


def test_unfetched_item_never_skips_from_subtitle_hint(course):  # 规划探测有字幕不能代表真正抓取过文本
    root = course()  # 目前课程没有任何视频产物
    course_data = read_course(root)  # 修改探测提示以覆盖保守编排行为
    course_data["items"][0]["subtitle_hint"] = "has"  # 提示存在字幕但本地元数据尚缺
    write_course(root, course_data)  # 保存合法的探测结果
    result = cp.course_next(root)  # 首次交接仍需抓取与检查实际文本
    assert step_of(result, "fetch")["skip"] is False  # 首次抓取不能被跳过
    assert step_of(result, "transcribe")["skip"] is False  # 未抓取前不能先保证转写可以跳过
    assert not Path(step_of(result, "note")["metadata"]).exists()  # 编排函数只交出步骤不自行执行抓取


def test_argv_preserves_absolute_chinese_space_paths_and_target_page(course, tmp_path, monkeypatch):  # 参数数组可安全处理空格且所有执行路径独立于当前目录
    root = course(range_="2-2")  # 非首集用于检查明确页码传递
    monkeypatch.chdir(tmp_path)  # 从课程目录外调用相对根目录
    result = cp.course_next(root.relative_to(tmp_path))  # 业务函数必须自行规范绝对路径
    fetch, transcribe, note = [step_of(result, kind) for kind in ("fetch", "transcribe", "note")]  # 三种阶段分别校验传递边界
    folder = root.resolve() / "videos" / result["item"]["bvid"]  # 课程视频共享目录的绝对位置
    metadata = folder / "metadata.json"  # 单视频抓取产物的标准位置
    assert fetch["argv"][2:] == [result["item"]["bvid"], str(folder)]  # 视频目录必须保持为单个参数
    assert transcribe["argv"][2:] == [str(metadata), "--page", "2"]  # 转写只能处理当前页而非全部视频
    for argv, script, command in ((fetch["argv"], "bili_fetch.py", fetch["cmd"]), (transcribe["argv"], "bili_transcribe.py", transcribe["cmd"]), (note["nav_argv"], "note_nav.py", note["nav_cmd"]), (note["validate_argv"], "validate_note.py", note["validate_cmd"])):  # 展示命令与可直接执行数组保持一致
        assert all(isinstance(arg, str) for arg in argv) and argv[0] == sys.executable  # 使用当前解释器且参数全部可直接执行
        assert Path(argv[1]).is_absolute() and Path(argv[1]).name == script  # 脚本定位不能依赖工作目录
        assert all('"' + arg.replace("\\", "/") + '"' in command for arg in argv if " " in arg)  # 每个含空格的路径参数都必须整体引用
    for argv in (note["nav_argv"], note["validate_argv"]):  # 导航与门禁必须使用相同元数据及转写输入
        assert argv[2:] == [note["note_path"], "--meta", str(metadata), "--txt-dir", str(folder), "--page", "2", "--template", "v4"]  # 保证页码和模板版本明确传递
    assert Path(note["note_path"]).is_absolute() and note["txt_dir"] == str(folder)  # 笔记阶段的独立字段与命令参数一致


@pytest.mark.parametrize("title,filename", [("2.13 业务流程建模", "2.13-业务流程建模.md"), ("认识 产品经理", "001-认识 产品经理.md")])  # 章节编号与普通主题使用两种确定命名

def test_note_path_supports_both_numbering_styles(course, title, filename):  # 笔记名称符合模板并保留有意义中文主题
    root = course()  # 固定首集全局序号为一
    course_data = read_course(root)  # 修改标题以覆盖命名分支
    course_data["items"][0]["title"] = title  # 不修改分集稳定身份
    write_course(root, course_data)  # 使用持久化清单作为唯一标题来源
    note = step_of(cp.course_next(root), "note")  # 读取公开回执中的目标路径
    assert Path(note["note_path"]) == root.resolve() / "notes" / filename  # 精确约束两种命名格式
    assert Path(note["note_path"]).parent.is_dir()  # 执行者收到步骤时笔记目录已准备好


def test_duplicate_lecture_names_are_disambiguated_by_global_index(course):  # 相同章节号及主题不能让后续集覆盖已有笔记
    root = course()  # 创建至少两集用于冲突模拟
    course_data = read_course(root)  # 将前两集设置为相同讲义标题
    for item in course_data["items"][:2]:  # 合集中同名讲义应稳定消歧
        item["title"] = "2.13 业务流程建模"  # 章节编号规则会生成相同候选名称
    write_course(root, course_data)  # 保存冲突标题以验证真实路径生成
    first = Path(step_of(cp.course_next(root), "note")["note_path"])  # 第一集应带全局消歧前缀
    assert first.name == "001-2.13-业务流程建模.md"  # 前缀与全局序号一致
    make_note(root, first.relative_to(root).as_posix())  # 创建真实笔记后才允许完成
    cp.mark(root, 1, "done", note=str(first))  # 推进到同名的第二集
    second = Path(step_of(cp.course_next(root), "note")["note_path"])  # 第二集不能复用第一集的输出文件
    assert second.name == "002-2.13-业务流程建模.md" and first != second  # 全局索引保证稳定且不会覆盖


def test_running_note_path_stays_stable_after_partial_file_written(course):  # 写笔记中断后仍交出原路径
    root = course()  # 初始化课程执行
    first = Path(step_of(cp.course_next(root), "note")["note_path"])  # 第一次分配输出位置
    first.write_text("尚未写完的内容", encoding="utf-8")  # 模拟写到一半中断
    second = Path(step_of(cp.course_next(root), "note")["note_path"])  # 同一运行中任务重新交接
    assert second == first  # 防覆盖逻辑不得把本集自己的草稿当成他人文件


def test_note_candidate_does_not_overwrite_saved_path_after_reindex(course):  # 模拟模式切换后旧完成集序号改变
    root = course()  # 初始当前视频课程
    data = read_course(root)  # 旧集已完成但现在被重新编号为第二集
    data["items"][0]["title"] = "入门"  # 新集候选路径会使用原序号
    data["items"][1].update(title="入门", status="done", note="notes/001-入门.md")  # 已完成集仍保留原路径
    saved = make_note(root, "notes/001-入门.md")  # 模拟真实已完成文件
    before = saved.read_bytes()  # 保存内容用于验证未被覆盖
    write_course(root, data)  # 当前索引候选名不同但已存路径冲突
    candidate = Path(step_of(cp.course_next(root), "note")["note_path"])  # 请求新集的笔记路径
    assert candidate != saved and saved.read_bytes() == before  # 新集不能写入另一集的已完成产物


def test_orphan_marker_allows_real_transcribe_entry_to_recover(course, monkeypatch, capsys):  # 验证交出命令能恢复而非仅检查skip字段
    import bili_transcribe as bt  # 不加载实际模型的入口模块
    root = course()  # 初始化真实临时课程
    metadata, _, text = cache_metadata(root)  # 写入匹配的缓存身份
    marker = text.with_name(text.name + ".done")  # 旧完成标记仍在但正文已经缺失
    marker.write_text("{}", encoding="utf-8")  # 模拟孤立完成标记
    result = cp.course_next(root)  # 编排只判断孤立标记不修改证据
    assert marker.read_text(encoding="utf-8") == "{}"  # 交出步骤时保留原标记不改名
    assert step_of(result, "transcribe")["skip"] is False  # 本集需要真实转写
    monkeypatch.setattr(bt, "load_sessdata", lambda: "")  # 不读真实配置
    monkeypatch.setattr(bt, "_cfg", lambda key, default=None: default)  # 使用默认模型与设备
    monkeypatch.setattr("apply_glossary.top_terms", lambda **kwargs: [])  # 不读取用户术语文件
    monkeypatch.setattr(bt, "download_audio", lambda bvid, cid, path, cookie: path.write_bytes(b"audio"))  # 音频下载离线替换
    monkeypatch.setattr(bt, "transcribe", lambda *args, **kwargs: "[00:01] 恢复的正式转写稿")  # 不调用模型或网络
    monkeypatch.setattr(sys, "argv", ["bili_transcribe.py", str(metadata), "--page", "1"])  # 使用交出步骤相同参数
    bt.main()  # 运行真实转写入口及完成判据
    assert text.is_file() and "恢复" in text.read_text(encoding="utf-8")  # 不得被孤立标记误判为nothing_to_do
    assert marker.is_file() and not list(marker.parent.glob(marker.name + ".orphan-*"))  # 成功覆盖正式标记且不生成改名备份


def test_cli_next_and_mark_emit_json_and_single_stderr_progress(course, capsys):  # 标准输出仅机器摘要且用户进度保持单行
    root = course()  # 先完成不会打印进度的规划和确认
    data = read_course(root)  # 在外部标题注入换行测试显示清洗
    data["items"][0]["title"] = "课程标题\n第二行\r结束"  # 外部内容不应破坏进度行格式
    write_course(root, data)  # 模拟接口中的多行标题
    result, err = invoke(capsys, "next", root)  # 下一集入口必须分发到公开执行函数
    assert result["index"] == 1 and err == ""  # 交接不输出伪完成进度
    failed, err = invoke(capsys, "mark", root, 1, "failed", "--reason", "第一行原因\n第二行原因", "--next-step", "检查缓存\r再重跑")  # 失败字段也可能来自多行异常
    assert failed["status"] == "failed" and len(err.splitlines()) == 1  # 用户可见进度严格占一行
    assert err.rstrip("\r\n") == failed["progress"] and "\n" not in failed["progress"] and "\r" not in failed["progress"]  # 字典中的文案与错误流完全一致
    assert failed["progress"].startswith("[1/4]") and "继续" in failed["progress"]  # 显示本轮位置并说明会继续后续集
    pending, err = invoke(capsys, "mark", root, 1, "pending")  # 命令行支持把失败集放回待处理
    assert pending["status"] == "pending" and len(err.splitlines()) == 1  # 重跑也输出单行进度及独立JSON
    note = make_note(root)  # 成功命令必须指向真实笔记
    done, err = invoke(capsys, "mark", root, 1, "done", "--note", note)  # 命令行必须支持完成回报及笔记路径
    assert done["status"] == "done" and done["done"] == 1 and len(err.splitlines()) == 1  # 完成回报与失败回报使用相同流协议


def test_remaining_time_reestimates_from_successful_actual_mean(course, monkeypatch):  # 实测成功均值替代初始视频时长公式
    root = course()  # 四集均处于待处理状态
    clock = SimpleNamespace(seconds=0)  # 通过可移动时钟避免任何真实等待
    monkeypatch.setattr(cp, "now", lambda: (BASE_TIME + timedelta(seconds=clock.seconds)).isoformat())  # 为开始与结束提供一致时间源
    note = make_note(root)  # 两次完成回报可引用同一个真实测试文件
    cp.course_next(root)  # 首集从零秒开始
    clock.seconds = 40  # 首集实测耗时四十秒
    first = cp.mark(root, 1, "done", note=str(note))  # 成功样本应立即更新剩余估算
    assert item_at(root)["seconds"] == 40 and first["remaining_seconds"] == pytest.approx(120)  # 四十秒均值乘剩余三集
    cp.course_next(root)  # 第二集在四十秒开始
    clock.seconds = 100  # 第二集实测耗时六十秒
    second = cp.mark(root, 2, "done", note=str(note))  # 新均值为五十秒
    assert item_at(root, 2)["seconds"] == 60 and second["remaining_seconds"] == pytest.approx(100)  # 五十秒均值乘剩余两集
    cp.course_next(root)  # 第三集仍作为未完成项计入剩余
    clock.seconds = 160  # 失败耗时不应成为成功均值样本
    failed = cp.mark(root, 3, "failed", reason="模拟转写失败")  # 只剩第四集待处理
    assert failed["remaining_seconds"] == pytest.approx(50)  # 失败不计入待做且不污染成功均值
    assert cp.status(root)["remaining_seconds"] == pytest.approx(50)  # 只读状态与回报估算采用同一口径


def test_finished_and_status_preserve_failures_without_retry(course):  # 本轮结束后仍能完整定位所有失败集
    root = course()  # 完整四集范围
    note = make_note(root)  # 用于成功条目的真实产物
    for index in range(1, 5):  # 按执行协议逐集回报
        assert cp.course_next(root)["index"] == index  # 失败不影响后续集依序交出
        if index in (2, 4):  # 两个失败条目必须保留独立详情
            result = cp.mark(root, index, "failed", reason=f"第{index}集失败")  # 默认补跑命令包含对应全局索引
            assert result["failed"] == index // 2  # 失败数只在真实失败回报后增加
        else:  # 其余两集正常完成
            cp.mark(root, index, "done", note=str(note))  # 完成数应独立累计
    before = (root / "course.json").read_bytes()  # 结束查询不得重写状态
    finished = cp.course_next(root)  # 新结束回执额外提供索引及失败补跑文案
    assert {key: finished[key] for key in ("status", "done", "failed", "failed_items", "reopened")} == {"status": "finished", "done": 2, "failed": 2, "failed_items": [2, 4], "reopened": []}  # 无待办时仍用序号列表回报失败及恢复项
    status = cp.status(root)  # 状态命令另外提供完整失败记录
    assert status["counts"]["done"] == status["counts"]["failed"] == 2  # 状态摘要与结束回执一致
    assert [item["index"] for item in status["failed_items"]] == [2, 4]  # 失败详情保持课程顺序
    assert all(item["reason"] and item["next_step"] and "pending" in item["rerun"] for item in status["failed_items"])  # 每个失败都保留可操作的恢复信息
    assert (root / "course.json").read_bytes() == before  # 结束及状态查询保持只读


def test_mark_failed_preserves_explicit_recovery_instructions(course):  # 调用者提供恢复方法时不得被默认补跑覆盖
    root = course()  # 建立真实清单
    result = cp.mark(root, 1, "failed", reason="  字幕元数据失效  ", next_step="重新抓取这一集", rerun="python custom_retry.py --page 1")  # 显式提供原因建议与命令
    item = item_at(root)  # 检查持久化后的三段恢复数据
    assert result["status"] == "failed" and item["reason"] == "字幕元数据失效"  # 原因清理外围空白且不丢正文
    assert item["next_step"] == "重新抓取这一集" and item["rerun"] == "python custom_retry.py --page 1"  # 自定义恢复方式完整保留
    assert cp.course_next(root)["index"] == 2  # 手工标记失败同样允许继续后续集
