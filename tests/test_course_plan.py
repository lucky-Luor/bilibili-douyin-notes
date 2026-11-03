import copy  # 深拷贝响应以隔离用例状态
import json  # 解析命令行输出和课程文件
import socket  # 禁止测试意外发起网络连接
import urllib.parse  # 从模拟接口地址提取视频编号
import urllib.request  # 禁止测试绕过接口桩访问网络
from pathlib import Path  # 管理临时课程目录与固定响应
from types import SimpleNamespace  # 保存离线桩调用记录
import pytest  # 提供参数化测试与隔离夹具
import course_plan as cp  # 只通过公开命令行入口检验课程规划


FIXTURES = Path(__file__).parent / "fixtures"  # 固定响应全部来自仓库测试目录


def read_fixture(name):  # 每次读取独立对象避免跨用例污染
    return json.loads((FIXTURES / f"bili_view_{name}.json").read_text(encoding="utf-8"))  # 明确使用中文编码


def read_course(root):  # 通过磁盘产物检查持久化行为
    return json.loads((root / "course.json").read_text(encoding="utf-8"))  # 不依赖实现内部加载函数


def write_course(root, course):  # 模拟下游处理器回写进度
    (root / "course.json").write_text(json.dumps(course, ensure_ascii=False, indent=2), encoding="utf-8")  # 保留中文课程名


@pytest.fixture(autouse=True)  # 每个用例都执行强制网络隔离
def offline(monkeypatch):  # 默认桩覆盖网络与真实登录配置
    state = SimpleNamespace(views={}, api_calls=[], probes=[], sleeps=[], cookie="", modes={})  # 集中记录外部交互
    def blocked(*args, **kwargs):  # 任何漏网请求立即失败
        raise AssertionError("离线测试禁止真实网络请求")  # 失败应指出隔离问题
    def api(url, cookie="", **kwargs):  # 模拟视频详情接口
        state.api_calls.append((url, cookie))  # 记录请求和登录态
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)  # 从真实调用地址读取参数
        bvid = query.get("bvid", [None])[0]  # 接口按照视频编号路由
        assert bvid in state.views, f"未模拟的接口请求：{url}"  # 防止错误请求被通用成功桩掩盖
        return copy.deepcopy(state.views[bvid])  # 实现不能修改夹具原始数据
    def subtitles(bvid, cid, cookie, *args, diagnostics=None, **kwargs):  # 模拟四类字幕探测结果
        state.probes.append((bvid, cid, cookie))  # 保留探测顺序和调用次数
        mode = state.modes.get(cid, "none")  # 默认成功确认无字幕
        if diagnostics is not None:  # 兼容可选诊断参数
            diagnostics.update(attempted=2, succeeded=0 if mode == "failed" else 2, failed=2 if mode == "failed" else 0)  # 全失败不得记为无字幕
        if mode == "exception":  # 模拟探测过程本身抛出异常
            raise RuntimeError("模拟播放器超时")  # 单项失败仍应允许生成计划
        tracks = [{"lan": "zh-CN", "subtitle_url": "https://invalid.example/sub.json"}] if mode == "has" else []  # 可用字幕带真实字段
        return tracks, mode in ("has", "track_only")  # 保持原函数二元返回协议
    monkeypatch.setattr(socket, "create_connection", blocked)  # 禁止套接字建立连接
    monkeypatch.setattr(urllib.request, "urlopen", blocked)  # 禁止标准库直接联网
    monkeypatch.setattr(cp.bili_fetch, "api", api)  # 所有元数据请求均走离线桩
    monkeypatch.setattr(cp.bili_fetch, "load_sessdata", lambda: state.cookie)  # 永远不读真实配置
    monkeypatch.setattr(cp.bili_fetch, "fetch_subtitles", subtitles)  # 禁止字幕探测请求网络
    monkeypatch.setattr(cp.time, "sleep", lambda seconds: state.sleeps.append(seconds))  # 记录节流但不真实等待
    return state  # 允许用例调整响应与探测结果


def install(offline, name="pages"):  # 安装一个完整的固定课程响应
    response = read_fixture(name)  # 每个测试使用新的响应对象
    data = response["data"]  # 读取当前视频详情
    offline.views[data["bvid"]] = response  # 当前视频可以直接查询
    for section in data.get("ugc_season", {}).get("sections", []):  # 展开全部分节的附属视频
        for episode in section["episodes"]:  # 为每个合集成员提供独立详情
            bvid = episode["bvid"]  # 合集成员的稳定编号
            if bvid in offline.views:  # 当前视频已经包含完整分集
                continue  # 保留原始当前视频响应
            arc = episode["arc"]  # 成员概览携带分集数量和总时长
            count = arc.get("videos", 1)  # 未提供分集数量时按单集处理
            first = episode["page"]  # 合集只内嵌首个分集
            pages = [copy.deepcopy(first)]  # 首集使用固定响应中的真实数据
            for index in range(2, count + 1):  # 补全需要通过详情接口获取的后续分集
                pages.append(dict(cid=first["cid"] + index - 1, page=index, part=f"后续分集{index}", duration=(arc["duration"] - first["duration"]) // (count - 1)))  # 精确分配剩余时长
            member = dict(bvid=bvid, aid=episode["aid"], title=episode["title"], cid=first["cid"], duration=arc["duration"], videos=count, pages=pages, owner=data["owner"])  # 构造独立详情
            offline.views[bvid] = {"code": 0, "data": member}  # 模拟正常业务响应
    return data  # 返回可供当前测试调整的当前视频详情


def invoke(capsys, *argv):  # 所有成功路径均通过黑盒命令行入口执行
    result = cp.main([str(arg) for arg in argv])  # 使用真实参数解析和分发
    captured = capsys.readouterr()  # 登录建议可能输出至标准错误
    output = captured.out + captured.err  # 同时检验用户可见文案和末行JSON
    parsed = json.loads(captured.out.strip().splitlines()[-1])  # 标准输出末行仍必须可解析
    assert isinstance(result, dict) and result == parsed  # 返回值必须与末行JSON严格一致
    return parsed, output  # 同时支持文案和结构化结果检查


def plan(capsys, offline, root, name="pages", *options):  # 复用固定响应建立课程计划
    data = install(offline, name)  # 设置完整离线详情
    return invoke(capsys, "plan", f"https://www.bilibili.com/video/{data['bvid']}", root, *options)  # 保留真实链接解析


def assert_failure(capsys, *argv):  # 错误路径也必须由命令行入口处理
    with pytest.raises((SystemExit, cp.HintError)) as exc:  # 黑盒函数可抛出统一提示异常或进程退出
        cp.main([str(arg) for arg in argv])  # 使用与用户相同的命令参数
    captured = capsys.readouterr()  # 提示可能通过异常文本或标准错误输出
    text = f"{exc.value}\n{captured.out}\n{captured.err}"  # 收集可见的全部失败信息
    assert "原因：" in text and "影响：" in text and "下一步：" in text  # 每次错误都应具备三段提示
    assert "Traceback" not in text  # 用户可见文本不得包含内部堆栈
    return text  # 个别测试还要核对具体下一步


def test_scope_round_trip_preserves_hidden_results(tmp_path, offline, capsys):  # 模式切换往返不丢失暂时隐藏的视频结果
    plan(capsys, offline, tmp_path, "season", "--no-probe")  # 先建立当前视频分P计划
    invoke(capsys, "start", tmp_path, "--all", "--scope", "season")  # 展开六集合集
    course = read_course(tmp_path)  # 模拟合集第六集已完成
    course["items"][5].update(status="done", note="notes/006.md", seconds=50)  # 保存稳定身份关联的结果
    write_course(tmp_path, course)  # 持久化完成记录
    invoke(capsys, "start", tmp_path, "--all", "--scope", "pages")  # 切回只有当前视频的三集
    invoke(capsys, "start", tmp_path, "--all", "--scope", "season")  # 再次展开之前的合集
    restored = read_course(tmp_path)["items"][5]  # 共同身份从历史记录恢复
    assert restored["status"] == "done" and restored["note"] == "notes/006.md" and restored["seconds"] == 50  # 防止重复制作和丢失产物


@pytest.mark.parametrize("seconds,expected", [(5400, "约 90 分钟"), (5460, "约 1 小时 31 分钟")])  # 九十分钟边界使用规定文案
def test_duration_text_boundary(seconds, expected):  # 时间展示不依赖真实网络
    assert cp._duration_text(seconds) == expected  # 超过九十分钟才显示小时


@pytest.mark.parametrize("name,scope,count,has_alt", [("pages", "pages", 4, False), ("season", "pages", 3, True), ("season_single", "season", 3, False)])  # 覆盖多分集与两种合集识别
def test_plan_recognizes_course(capsys, offline, tmp_path, name, scope, count, has_alt):  # 自动识别默认规划范围
    result, output = plan(capsys, offline, tmp_path, name, "--no-probe")  # 仅检查课程结构识别
    assert result["items"] == count and bool(result["season_alt"]) is has_alt  # 合集优先级与替代选项正确
    assert read_course(tmp_path)["kind"] == scope and read_course(tmp_path)["scope"]["mode"] is None  # 模式与尚未确认的选择范围独立
    assert result["course_json"] == str(tmp_path / "course.json")  # 输出定位实际产物
    assert result["reused"] is False and result["confirm_text"]  # 新计划需要提供可确认的文案


def test_single_video_is_not_course_without_directory(capsys, offline, tmp_path):  # 单独单集不应误判成课程
    data = install(offline, "single")  # 普通单集没有课程结构
    root = tmp_path / "尚未创建"  # 使用不存在的目录验证没有写入副作用
    result, _ = invoke(capsys, "plan", data["bvid"], root, "--no-probe")  # 返回非课程结果供调用者正常降级
    assert result["course"] is False and result["bvid"] == data["bvid"] and result["pages"] == 1  # 非课程摘要包含稳定来源和分集数量
    assert not root.exists()  # 非课程识别不得创建输出目录


@pytest.mark.parametrize("name,scope,count", [("season", "season", 6), ("season", "pages", 3), ("season_single", "pages", 1)])  # 显式范围可覆盖自动识别
def test_plan_scope_override(capsys, offline, tmp_path, name, scope, count):  # 范围覆盖不能丢失成员分集
    result, _ = plan(capsys, offline, tmp_path, name, "--scope", scope, "--no-probe")  # 执行显式范围命令
    assert result["items"] == count and read_course(tmp_path)["kind"] == scope  # 输出及持久化模式一致


def test_missing_pages_uses_episode_fallback(capsys, offline, tmp_path):  # 合集成员缺完整分集列表时只保留内嵌首集
    data = install(offline, "season")  # 原始固定响应应具有三集单集两集结构
    episode = data["ugc_season"]["sections"][0]["episodes"][-1]  # 选择原本包含两个分集的视频
    episode.pop("pages", None)  # 显式移除完整列表以覆盖回退路径
    result, _ = invoke(capsys, "plan", data["bvid"], tmp_path, "--scope", "season", "--no-probe")  # 不额外请求成员详情
    assert result["items"] == 5  # 回退只展开成员首集而不虚构第二集
    assert len(offline.api_calls) == 1  # 缺少列表绝不能触发额外详情请求
    member_items = [item for item in read_course(tmp_path)["items"] if item["bvid"] == episode["bvid"]]  # 检查回退成员身份
    assert len(member_items) == 1 and member_items[0]["cid"] == episode["page"]["cid"]  # 回退严格使用内嵌首集


def test_multiple_sections_have_continuous_global_index(capsys, offline, tmp_path):  # 多分节课程应按节顺序展开
    data = install(offline, "season")  # 当前视频包含三个分集
    season = data["ugc_season"]  # 修改合集分节组织
    section = season["sections"][0]  # 原始默认分节
    tail = section["episodes"].pop()  # 把最后一个视频移动到第二节
    season["sections"].append(dict(id=20002, title="进阶章节", episodes=[tail]))  # 构造真实多分节形态
    invoke(capsys, "plan", data["bvid"], tmp_path, "--scope", "season", "--no-probe")  # 展开所有分节和分集
    course = read_course(tmp_path)  # 验证最终持久化结构
    assert [item["index"] for item in course["items"]] == list(range(1, 7))  # 全局序号不可在分节处重置
    assert [item["page"] for item in course["items"]] == [1, 2, 3, 1, 1, 2]  # 视频内页码保留原始意义
    assert [item["bvid"] for item in course["items"]][-2:] == [tail["bvid"]] * 2  # 第二节视频的两个分集顺序正确
    assert course["season"]["id"] == 2000 and course["season"]["ep_count"] == 3  # 合集身份与视频数保持稳定


@pytest.mark.parametrize("mode,hint", [("has", "has"), ("track_only", "track_only"), ("none", "none"), ("failed", "unknown"), ("exception", "unknown")])  # 四种状态以及异常路径必须区分
def test_subtitle_probe_classification(capsys, offline, tmp_path, mode, hint):  # 请求全失败不得误判没有字幕
    data = install(offline)  # 提供四个分集
    offline.modes = {page["cid"]: mode for page in data["pages"]}  # 为所有分集使用相同探测模式
    invoke(capsys, "plan", data["bvid"], tmp_path)  # 启用默认字幕探测
    assert {item["subtitle_hint"] for item in read_course(tmp_path)["items"]} == {hint}  # 分类严格匹配诊断结果
    assert len(offline.probes) == 4  # 每个分集只探测一次


def test_no_probe_never_calls_subtitle_service(capsys, offline, tmp_path):  # 显式跳过探测应完全隔离字幕接口
    result, _ = plan(capsys, offline, tmp_path, "pages", "--no-probe")  # 保留未知状态并估算语音识别
    assert offline.probes == [] and offline.sleeps == []  # 不执行请求与节流
    assert {item["subtitle_hint"] for item in read_course(tmp_path)["items"]} == {"unknown"}  # 未探测不代表无字幕
    assert result["asr_items"] == 4  # 未知字幕按需要语音识别进行保守估算


def expand_pages(data, count, seconds=60):  # 构造限额和大课程测试所需详情
    data["pages"] = [dict(cid=800000 + index, page=index, part=f"第{index}讲", duration=seconds) for index in range(1, count + 1)]  # 使用稳定且互不重复的成员身份
    data["videos"] = count  # 总分集数量与列表一致
    data["duration"] = count * seconds  # 总时长与全部分集一致


@pytest.mark.parametrize("all_probes,expected", [(False, 60), (True, 65)])  # 默认上限可通过显式开关解除
def test_probe_limit_and_interval(capsys, offline, tmp_path, all_probes, expected):  # 探测必须有上限并串行节流
    data = install(offline)  # 提供合法的视频身份
    expand_pages(data, 65)  # 课程比默认探测上限略大
    args = ["--probe-all"] if all_probes else []  # 显式全探测应覆盖全部成员
    invoke(capsys, "plan", data["bvid"], tmp_path, *args)  # 使用真实命令行探测选项
    hints = [item["subtitle_hint"] for item in read_course(tmp_path)["items"]]  # 未探测成员应保留未知
    assert len(offline.probes) == expected and hints.count("unknown") == 65 - expected  # 限额不得越界
    assert len(offline.sleeps) == expected - 1 and all(delay == 0.3 for delay in offline.sleeps)  # 连续探测严格间隔三百毫秒


@pytest.mark.parametrize("cookie,expects_login", [("", True), ("fake-session", False)])  # 未登录与已登录的无字幕提示不同
def test_none_subtitles_login_hint(capsys, offline, tmp_path, cookie, expects_login):  # 无字幕时提供准确的登录建议
    offline.cookie = cookie  # 模拟登录态且永不读取真实配置
    _, output = plan(capsys, offline, tmp_path)  # 默认模拟成功确认无字幕
    assert ("--login" in output) is expects_login  # 只向缺少登录态的用户提示登录
    assert all(call[2] == cookie for call in offline.probes)  # 探测必须使用已加载的登录态


def test_estimate_components_are_exact(capsys, offline, tmp_path):  # 用固定四集时长验证所有估算分量
    result, _ = plan(capsys, offline, tmp_path)  # 四集共一千八百秒且没有字幕
    estimate = read_course(tmp_path)["estimate"]  # 持久化完整估算便于后续状态使用
    assert estimate["items"] == 4 and estimate["hours"] == 0.5  # 数量与课程小时数精确
    assert estimate["asr_items"] == 4 and estimate["asr_seconds"] == 360  # 语音识别按视频时长两成计算
    assert estimate["note_seconds"] == 360 and estimate["fetch_seconds"] == 20  # 笔记每集九十秒及抓取每集五秒
    assert estimate["total_seconds"] == 740  # 总耗时必须由各分量相加
    assert result["estimate_minutes"] == pytest.approx(740 / 60, abs=0.1)  # 输出分钟数保持相同口径


def test_mixed_subtitles_exclude_available_asr(capsys, offline, tmp_path):  # 只有可用字幕能免除语音识别估算
    data = install(offline)  # 固定时长依次为六百五百四百三百秒
    offline.modes = dict(zip([page["cid"] for page in data["pages"]], ["has", "track_only", "none", "failed"]))  # 覆盖全部字幕状态
    invoke(capsys, "plan", data["bvid"], tmp_path)  # 执行混合字幕课程规划
    estimate = read_course(tmp_path)["estimate"]  # 查看精确耗时分量
    assert estimate["asr_items"] == 3 and estimate["asr_seconds"] == 240  # 无法用的字幕轨仍按语音识别估算
    assert estimate["total_seconds"] == 620  # 总耗时扣除首集的语音识别成本


def test_confirm_text_uses_hour_for_long_estimate(capsys, offline, tmp_path):  # 长课程确认文案不应只显示大分钟数
    data = install(offline)  # 使用四集课程结构
    expand_pages(data, 4, seconds=18000)  # 二十小时内容产生超过一小时的处理估算
    result, _ = invoke(capsys, "plan", data["bvid"], tmp_path, "--no-probe")  # 无字幕信息时保守估算
    assert result["estimate_minutes"] > 60 and "小时" in result["confirm_text"]  # 长估算以小时向用户说明


@pytest.mark.parametrize("count,seconds,oversize", [(200, 60, False), (201, 60, True), (2, 180000, False), (2, 180001, True)])  # 精确验证数量与时长阈值均采用严格大于
def test_oversize_boundaries_and_all_guard(capsys, offline, tmp_path, count, seconds, oversize):  # 大课程仍可规划但不得一键全部启动
    data = install(offline)  # 使用合法课程身份
    expand_pages(data, count, seconds)  # 构造边界附近的分集数量或总时长
    result, _ = invoke(capsys, "plan", data["bvid"], tmp_path, "--no-probe")  # 规划阶段不应直接拒绝
    assert result["oversize"] is oversize  # 严格边界必须与约定一致
    before = (tmp_path / "course.json").read_bytes()  # 拒绝启动不得改写课程
    if oversize:  # 超大课程只允许局部选择
        assert_failure(capsys, "start", tmp_path, "--all")  # 用户必须得到可操作提示
        assert (tmp_path / "course.json").read_bytes() == before  # 拒绝过程中不应改变进度
        selected, _ = invoke(capsys, "start", tmp_path, "--first", "1")  # 小批量启动仍然可用
        assert selected["items_in_scope"] == 1  # 局部选择必须生效
    else:  # 阈值以内允许完整启动
        selected, _ = invoke(capsys, "start", tmp_path, "--all")  # 正常全量选择
        assert selected["items_in_scope"] == count  # 恰好边界不能被误拒绝


@pytest.mark.parametrize("args,expected", [(["--first", "2"], [1, 2]), (["--range", "2-3"], [2, 3]), (["--all"], [1, 2, 3, 4])])  # 三种合法选择器都通过公开入口
def test_start_selects_requested_items(capsys, offline, tmp_path, args, expected):  # 选择结果和待处理数量应一致
    plan(capsys, offline, tmp_path, "pages", "--no-probe")  # 建立四集未处理课程
    result, _ = invoke(capsys, "start", tmp_path, *args)  # 执行真实选择器参数
    assert result["kind"] == "pages" and result["scope"] == read_course(tmp_path)["scope"] and result["items_in_scope"] == len(expected)  # 输出模式选择对象及数量正确
    assert result["pending"] == len(expected) and result["estimate_minutes"] > 0  # 未处理课程需要估算全部选中项
    course = read_course(tmp_path)  # 从文件读取最终选择状态
    selection = course["scope"]  # 持久化选择对象是范围的唯一事实来源
    if args[0] == "--all":  # 全量范围不需要额外索引字段
        assert selection["mode"] == "all"  # 全量模式明确记录
    elif args[0] == "--first":  # 首批范围保存正整数数量
        assert selection["mode"] == "first" and selection["first"] == len(expected)  # 首批选择与命令一致
    else:  # 连续范围保存两个包含端点的整数
        assert selection["mode"] == "range" and selection["range"] == [expected[0], expected[-1]]  # 精确保存范围边界
    assert selection["confirmed_at"]  # 范围确认需具备时间戳


@pytest.mark.parametrize("args", [["--first", "0"], ["--first", "-1"], ["--first", "1.5"], ["--range", "0-2"], ["--range", "3-2"], ["--range", "2-5"], ["--range", "abc"], ["--range", "1"], ["--range", "-1-2"], ["--first", "1", "--all"], []])  # 零负数倒序超界和互斥规则均需验证
def test_invalid_start_does_not_write(capsys, offline, tmp_path, args):  # 非法请求必须保持课程文件不变
    plan(capsys, offline, tmp_path, "pages", "--no-probe")  # 创建可比较的初始课程
    before = (tmp_path / "course.json").read_bytes()  # 保存完整原始字节
    assert_failure(capsys, "start", tmp_path, *args)  # 所有非法选择器均给出三段式提示
    assert (tmp_path / "course.json").read_bytes() == before  # 参数错误不允许留下部分写入


def mark_results(root):  # 模拟两个下游笔记处理结果
    course = read_course(root)  # 修改磁盘中的公开进度协议
    course["items"][0].update(status="done", note="笔记/第一讲.md")  # 成功结果必须持久保留
    course["items"][1].update(status="failed", note="", reason="模拟转写失败", error="模拟转写失败")  # 失败原因必须可诊断
    write_course(root, course)  # 模拟真实执行器保存结果
    return copy.deepcopy(course["items"][:2])  # 保存期望结果用于后续对照


def test_replan_keeps_identity_scope_and_results(capsys, offline, tmp_path):  # 重复规划不可清空用户已有结果
    data = install(offline, "season")  # 使用可在两个范围间切换的课程
    invoke(capsys, "plan", data["bvid"], tmp_path, "--scope", "season", "--no-probe")  # 初次选择完整合集
    invoke(capsys, "start", tmp_path, "--range", "2-4")  # 保存已确认的部分范围
    selection = copy.deepcopy(read_course(tmp_path)["scope"])  # 重规划必须完整保留选择对象
    previous = mark_results(tmp_path)  # 写入成功及失败状态
    identities = [(item["bvid"], item["cid"]) for item in read_course(tmp_path)["items"]]  # 以真实视频和分集身份比对
    result, _ = invoke(capsys, "plan", f"https://www.bilibili.com/video/{data['bvid']}?p=2", tmp_path, "--scope", "season", "--no-probe")  # 相同合集模式下链接页码变化仍属同一来源
    course = read_course(tmp_path)  # 检查复用后的课程
    assert result["reused"] is True and course["kind"] == "season" and course["scope"] == selection  # 未显式覆盖时保留模式和用户范围
    assert [(item["bvid"], item["cid"]) for item in course["items"]] == identities  # 稳定身份和顺序不变
    assert course["items"][:2] == previous  # 已有状态路径和失败信息全部保留


def test_different_source_is_rejected_without_write(capsys, offline, tmp_path):  # 不同视频不能覆盖已有课程
    plan(capsys, offline, tmp_path, "pages", "--no-probe")  # 先创建当前课程
    before = (tmp_path / "course.json").read_bytes()  # 保存原文件字节
    other = install(offline, "season")  # 新来源也是真实可规划课程
    assert_failure(capsys, "plan", other["bvid"], tmp_path, "--no-probe")  # 应指导用户选择其他目录
    assert (tmp_path / "course.json").read_bytes() == before  # 不同来源绝不能污染原课程


def test_scope_switch_preserves_completed_results(capsys, offline, tmp_path):  # 课程范围切换必须合并稳定身份结果
    plan(capsys, offline, tmp_path, "season", "--no-probe")  # 首次默认只选择当前多分集视频
    previous = mark_results(tmp_path)  # 当前视频两集已有处理结果
    result, _ = invoke(capsys, "start", tmp_path, "--scope", "season", "--all")  # 扩大到完整合集
    course = read_course(tmp_path)  # 查阅扩展后的课程
    assert result["kind"] == "season" and result["scope"] == course["scope"] and result["items_in_scope"] == 6  # 全部三个视频的六集可选择
    for old in previous:  # 以稳定身份查找已有条目
        item = next(item for item in course["items"] if (item["bvid"], item["cid"]) == (old["bvid"], old["cid"]))  # 全局序号变化不能导致丢失结果
        assert item["status"] == old["status"] and item["note"] == old["note"]  # 用户处理结果保持一致
    result, _ = invoke(capsys, "start", tmp_path, "--scope", "pages", "--all")  # 切回当前视频范围
    assert result["items_in_scope"] == 3  # 当前视频仍具有原来的三个分集
    assert read_course(tmp_path)["items"][0]["status"] == "done"  # 再次缩小范围也不能丢失结果


def test_status_is_readonly_and_reports_failures(capsys, offline, tmp_path):  # 状态命令不能修改课程或查询网络
    plan(capsys, offline, tmp_path, "pages", "--no-probe")  # 初始化四集课程
    mark_results(tmp_path)  # 一集完成一集失败其余待处理
    path = tmp_path / "course.json"  # 用字节和时间同时约束只读行为
    before = path.read_bytes(), path.stat().st_mtime_ns  # 保存文件原样状态
    calls = len(offline.api_calls), len(offline.probes)  # 状态命令应无需联网获取详情
    result, _ = invoke(capsys, "status", tmp_path)  # 调用只读入口
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before  # 不得刷新更新时间或重写文件
    assert (len(offline.api_calls), len(offline.probes)) == calls  # 不得重新请求详情或探测字幕
    assert result["counts"]["done"] == 1 and result["counts"]["failed"] == 1  # 失败与完成必须分别统计
    assert len(result["items"]) == 4 and len(result["failed_items"]) == 1  # 完整条目列表与失败详情准确
    assert "模拟转写失败" in json.dumps(result["failed_items"], ensure_ascii=False)  # 失败详情需保留可读原因
    assert result["remaining_seconds"] == 330  # 无有效耗时样本时仅对待处理两集使用完整公式


def test_start_pending_excludes_completed(capsys, offline, tmp_path):  # 已完成结果不应该再次列入待处理
    plan(capsys, offline, tmp_path, "pages", "--no-probe")  # 所有条目未知字幕时保守估算
    mark_results(tmp_path)  # 将首集标记完成第二集标记失败
    result, _ = invoke(capsys, "start", tmp_path, "--all")  # 重试剩余任务
    assert result["pending"] == 2 and result["items_in_scope"] == 4  # 已完成与失败均不属于本轮待处理
    assert result["estimate_minutes"] == pytest.approx(740 / 60, abs=0.1) and result["remaining_seconds"] == 330  # 范围整体估算与仅待做的剩余耗时独立


def test_chinese_json_is_utf8_and_write_is_atomic(capsys, offline, tmp_path, monkeypatch):  # 中文持久化与原子替换都必须得到验证
    replacements = []  # 跟踪真正的文件替换调用
    original = cp.bili_fetch.os.replace  # 保留底层操作供包装调用
    def replace(source, target, *args, **kwargs):  # 原子替换时检查临时文件完整性
        source_path, target_path = Path(source), Path(target)  # 统一路径类型
        assert source_path.parent == target_path.parent  # 临时文件需与目标处于同一文件系统
        assert source_path != target_path and source_path.exists()  # 不得直接覆写目标文件
        payload = json.loads(source_path.read_text(encoding="utf-8"))  # 替换前临时文件必须是完整有效JSON
        assert "第二章" in json.dumps(payload, ensure_ascii=False)  # 中文内容在替换前已经完整写入
        replacements.append((source_path, target_path))  # 记录可观察的原子写证据
        return original(source, target, *args, **kwargs)  # 执行真实原子替换
    monkeypatch.setattr(cp.bili_fetch.os, "replace", replace)  # 包装真实文件系统边界而不是保存函数
    plan(capsys, offline, tmp_path, "pages", "--no-probe")  # 从真实命令入口生成课程
    raw = (tmp_path / "course.json").read_bytes()  # 使用原始字节验证编码
    assert "第二章".encode("utf-8") in raw and b"\\u7b2c" not in raw  # 中文直接保存为UTF8而非转义
    assert replacements and all(not source.exists() for source, _ in replacements)  # 原子写完成后临时文件消失


@pytest.mark.parametrize("command", ["start", "status"])  # 缺少课程时两条入口都应可读失败
def test_missing_course_three_part_error(capsys, tmp_path, command):  # 用户拿到下一步而不是文件异常
    args = ["--all"] if command == "start" else []  # 启动命令提供合法选择器
    assert_failure(capsys, command, tmp_path, *args)  # 未创建课程时提示先规划


def test_api_failure_three_part_error_without_partial_file(capsys, offline, tmp_path):  # 元数据失败应统一解释并避免残留
    data = install(offline)  # 安装视频编号供解析
    offline.views[data["bvid"]] = {"code": -404, "message": "视频不存在"}  # 模拟明确业务失败
    assert_failure(capsys, "plan", data["bvid"], tmp_path, "--no-probe")  # 错误响应必须转成可操作提示
    assert not (tmp_path / "course.json").exists()  # 接口失败不得生成半成品课程


def test_first_above_count_is_clamped(capsys, offline, tmp_path):  # 首批正整数超过课程规模时按总数截断
    plan(capsys, offline, tmp_path, "pages", "--no-probe")  # 固定课程共有四集
    result, _ = invoke(capsys, "start", tmp_path, "--first", "99")  # 合法正数即使超过总数也应接受
    assert result["items_in_scope"] == 4 and read_course(tmp_path)["scope"]["first"] == 4  # 摘要和持久化均采用截断后的数量


def test_remaining_uses_valid_done_mean_and_pending_running(capsys, offline, tmp_path):  # 有实测耗时后按已完成均值估算待做数量
    plan(capsys, offline, tmp_path, "pages", "--no-probe")  # 初始化未知字幕的课程
    course = read_course(tmp_path)  # 模拟执行器产生的实时状态
    course["items"][0].update(status="done", seconds=40)  # 有效完成样本提供四十秒实际耗时
    course["items"][1].update(status="failed", seconds=999)  # 失败耗时既不作为样本也不作为待做
    course["items"][2]["status"] = "running"  # 正在执行的条目仍计入剩余工作
    write_course(tmp_path, course)  # 保存执行器状态
    result, _ = invoke(capsys, "status", tmp_path)  # 状态只读计算剩余时间
    assert result["remaining_seconds"] == 80  # 一个有效样本均值乘两项待做


def test_range_does_not_erase_outside_final_results(capsys, offline, tmp_path):  # 缩小选择范围时保留范围外终态
    plan(capsys, offline, tmp_path, "pages", "--no-probe")  # 固定四集课程
    mark_results(tmp_path)  # 第一集成功第二集失败
    invoke(capsys, "start", tmp_path, "--range", "3-3")  # 仅选择第三集进行处理
    course = read_course(tmp_path)  # 查询范围外原有结果
    assert [item["status"] for item in course["items"]] == ["done", "failed", "pending", "out_of_scope"]  # 范围外终态不改写且未处理条目正确排除
    result, _ = invoke(capsys, "status", tmp_path)  # 状态统计必须依据独立范围
    assert len(result["items"]) == 1 and result["counts"]["pending"] == 1 and result["counts"]["done"] == 0  # 不通过状态反推范围
