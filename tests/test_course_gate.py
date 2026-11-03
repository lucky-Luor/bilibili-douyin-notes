import hashlib  # 检查真实UTF8字节摘要
import json  # 观察清单落盘协议
import pytest  # 参数化与隔离边界
import course_plan as cp  # 真实课程业务入口
import validate_note  # 观察进程内门禁参数
from test_course_execution import course, offline, cache_metadata, read_course, write_course, step_of, make_note  # 复用离线课程及文件夹具
from test_validate_note_v4 import GOOD  # 复用已有真实合法v4笔记
from test_validate_obsidian import OBS  # 复用真实合法黑曜石元信息及维基目录


def valid_note(root):  # 创建真实门禁可通过的首集笔记
    metadata, meta, text = cache_metadata(root)  # 保证课程目标页和元数据一致
    text.write_text("[00:00] 开场\n[05:05] 颜色\n" + "正文" * 100, encoding="utf-8")  # 时间索引来源为本集正式文本
    note = root / "001-业务流程.md"  # 合法编号文件名
    content = GOOD.replace("?p=13&", "?p=1&")  # 保留真实样例的时间归属只调整目标页
    note.write_text(content, encoding="utf-8")  # 写真实中文笔记
    return note, metadata  # 返回门禁输入


def test_mark_requires_real_gate_without_changing_course(course):  # 不合格现有文件不能冒充完成
    root = course()  # 合法课程和已确认范围
    note, _ = valid_note(root)  # 先准备完整输入
    note.write_text("错误内容", encoding="utf-8")  # 单独破坏笔记
    before = (root / "course.json").read_bytes()  # 拒绝时文件不可改变
    with pytest.raises(SystemExit) as exc:  # 门禁失败使用统一提示出口
        cp.mark(root, 1, "done", note=str(note))  # 真实完成回报
    assert "门禁" in str(exc.value)  # 原因明确指出门禁失败
    assert (root / "course.json").read_bytes() == before  # 不写入任何完成字段


def test_real_v4_gate_is_saved_and_unchanged_note_skips(course, monkeypatch):  # 至少一个完整真实合法v4集成
    root = course(first=1)  # 单集范围便于检查finished
    note, _ = valid_note(root)  # 不使用门禁成功mock
    cp.mark(root, 1, "done", note=str(note))  # 真实门禁通过才保存
    gate = read_course(root)["items"][0]["gate"]  # 观察公开缓存字段
    assert gate["errors"] == 0 and gate["sha256"] == hashlib.sha256(note.read_bytes()).hexdigest()  # 精确记录UTF8原字节摘要
    assert gate["template"] == "v4" and gate["checked_at"] and "first_errors" not in gate  # 持久化只保留门禁摘要
    def blocked(*args, **kwargs):  # 缓存未变不能重复运行门禁
        raise AssertionError("相同摘要和模板应跳过")  # 防止无效重复校验
    monkeypatch.setattr(validate_note, "check_note_dispatch", blocked)  # 只在真实成功后检查缓存命中
    result = cp.course_next(root)  # next也必须调用重检
    assert result["status"] == "finished" and result["reopened"] == []  # 每种next回执带恢复列表


@pytest.mark.parametrize("missing", ["note", "metadata"])  # 输入缺失直接返回单条门禁错误
def test_run_gate_missing_inputs(course, missing):  # 缺文件不可被校验器宽松处理为成功
    root = course()  # 完整课程
    note, metadata = valid_note(root)  # 独立删除一项
    current = read_course(root)  # 准备业务接口输入
    current["items"][0]["note"] = note.relative_to(root).as_posix()  # 当前页正式路径
    (note if missing == "note" else metadata).unlink()  # 注入缺失
    gate = cp.run_gate(root, current, current["items"][0])  # 调用进程内门禁
    assert gate["errors"] == 1 and len(gate["first_errors"]) == 1  # 返回可恢复错误而非异常或假成功


def test_run_gate_dispatch_arguments_and_error_limit(course, monkeypatch):  # 参数严格限定当前视频和当前页
    root = course()  # 首页编号和元数据稳定
    note, metadata = valid_note(root)  # 准备有效文件
    current = read_course(root)  # 读取课程模板和目标页
    current["template"] = "v4-obsidian"  # 验证模板来自课程的新合法枚举
    item = current["items"][0]  # 仅校验此项
    item["note"] = note.relative_to(root).as_posix()  # 设置正式笔记路径
    calls = []  # 保存真实分派参数
    def dispatch(*args, **kwargs):  # 专门验证边界协议并返回多条错误
        calls.append((args, kwargs))  # 记录调用而不全局屏蔽集成
        return {"errors": [{"check": "TEST", "message": str(i)} for i in range(5)], "warnings": [{"message": "警告"}]}  # 错误只提取前三条
    monkeypatch.setattr(validate_note, "check_note_dispatch", dispatch)  # 此用例仅检查适配层
    gate = cp.run_gate(root, current, item)  # 执行适配
    args, kwargs = calls[0]  # 必须进程内调用唯一入口
    assert args == (note.read_bytes().decode("utf-8"), note)  # 文本和笔记路径直接传入
    assert kwargs == dict(meta_path=metadata, txt_dir=metadata.parent, only_page=1, template="v4-obsidian", note_type=None)  # 新变体模板明确传入且其它页不能串入校验
    assert gate["errors"] == 5 and gate["warnings"] == 1 and gate["first_errors"] == ["TEST: 0", "TEST: 1", "TEST: 2"]  # 保留计数并限制首错数量


@pytest.mark.parametrize("change", ["missing", "invalid", "valid", "template", "template_valid", "legacy"])  # 覆盖模板切换的真实失败与合法元信息路径
def test_recheck_reopens_or_refreshes_once(course, monkeypatch, change):  # 仅范围内done参与恢复
    root = course(first=1)  # 范围外done不可被处理
    note, _ = valid_note(root)  # 首次真实通过
    cp.mark(root, 1, "done", note=str(note))  # 保存摘要
    current = read_course(root)  # 注入旧版或模板变化
    old_note = current["items"][0]["note"]  # 恢复时必须保持原输出位置
    current["items"][1].update(status="done", note="不存在.md")  # 范围外不能自动重开
    if change == "legacy":  # I3完成项没有门禁缓存
        current["items"][0].pop("gate")  # 兼容旧清单
    elif change in ("template", "template_valid"):  # 内容不变时切换模板必须重开且转换后可以真实通过
        current["template"] = "v4-obsidian"  # 新模板要求元信息及维基目录
        if change == "template_valid":  # 同时准备已经转换的合法笔记
            note.write_text(OBS.replace("?p=13", "?p=1").replace("page: 13", "page: 1"), encoding="utf-8")  # 保留完整元信息仅调整本集页码
    elif change == "missing":  # 文件不见了
        note.unlink()  # 必须恢复原计划路径
    else:  # 改动内容触发重新门禁
        note.write_text("坏笔记" if change == "invalid" else note.read_text(encoding="utf-8") + "\n", encoding="utf-8")  # 区分有效与无效内容变化
    saves = []  # 多项更新最多保存一次
    original = cp.save_course  # 真实保存仍执行
    def save(*args):  # 观察保存边界
        saves.append(args)  # 只记调用次数
        return original(*args)  # 保留磁盘行为
    monkeypatch.setattr(cp, "save_course", save)  # 不屏蔽校验器
    reopened = cp.recheck(root, current)  # 对同一对象做一次恢复
    assert len(saves) == 1 and current["items"][1]["status"] == "done"  # 更新只保存一次且不处理范围外
    item = read_course(root)["items"][0]  # 查看持久结果
    if change in ("missing", "invalid", "template"):  # 文件缺失内容损坏及未转换模板都需要恢复
        assert reopened == [1] and item["status"] == "pending" and item["note"] is None  # 正式完成状态撤销
        assert item["planned_note"] == old_note  # 原笔记路径稳定恢复
        assert item["reason"] == "笔记文件不见了" if change == "missing" else item["reason"].startswith("笔记门禁未通过：")  # 精确原因按方案区分
        assert item["next_step"] == ("重新制作这一集" if change == "missing" else "按门禁报告修改这篇笔记")  # 恢复动作可直接使用
    else:  # 真实校验通过仅刷新缓存
        assert reopened == [] and item["status"] == "done" and item["gate"]["template"] == current.get("template", "v4")  # 通过项不重复制作


@pytest.mark.parametrize("converted", [False, True])  # 同时验证未转换笔记重开与已转换笔记保持完成

def test_start_template_switch_next_rechecks_real_note(course, converted):  # 从用户入口贯通模板切换与下一集复查
    root = course(first=1)  # 选定首集以便准确观察结束或重开
    note, _ = valid_note(root)  # 保持最新主分支真实合法四版夹具
    cp.mark(root, 1, "done", note=str(note))  # 真实门禁建立旧模板缓存
    old_gate = read_course(root)["items"][0]["gate"]  # 保存旧缓存供切换后对比
    result = cp.start(root, first=1, template="v4-obsidian")  # 用户显式切换课程模板
    switched = read_course(root)["items"][0]  # start只提示而不提前复查
    assert result["warnings"] and switched["status"] == "done" and switched["gate"] == old_gate  # 已完成缓存由下一次next处理
    if converted:  # 模拟用户已经转换为新模板
        note.write_text(OBS.replace("?p=13", "?p=1").replace("page: 13", "page: 1"), encoding="utf-8")  # 新合法元信息维基目录与深链保持完整
    result = cp.course_next(root)  # 真实门禁不得被模拟成功
    current = read_course(root)["items"][0]  # 读取持久化复查结果
    if converted:  # 合规新模板完成项只刷新缓存
        assert result["status"] == "finished" and result["reopened"] == [] and current["status"] == "done"  # 不重复交出已转换笔记
        assert current["gate"]["template"] == "v4-obsidian" and current["gate"]["errors"] == 0  # 新模板缓存可以合法保存及读取
        assert current["gate"]["sha256"] == hashlib.sha256(note.read_bytes()).hexdigest()  # 刷新缓存仍使用真实字节摘要
    else:  # 未转换笔记必须重新交给制作步骤
        step = step_of(result, "note")  # 观察合并后完整交接协议
        assert result["reopened"] == [1] and current["status"] == "running" and current["note"] is None  # next先撤销完成再开始修改
        assert step["existing_note"] and step["note_path"] == str(note) and "V4O-FM" in step["reason"]  # 稳定原路径并提供真实失败原因
        assert step["template"] == "v4-obsidian"  # 新制作步骤知道目标模板
        for argv in (step["nav_argv"], step["validate_argv"]):  # 两个后处理脚本使用同一新模板
            assert argv[argv.index("--template") + 1] == "v4-obsidian"  # 避免交接回退旧模板


def test_next_reuses_invalid_note_with_reason(course):  # 不合格旧笔记交回修改而非重命名
    root = course(first=1)  # 单集范围
    note, _ = valid_note(root)  # 首先真实完成
    cp.mark(root, 1, "done", note=str(note))  # 保存完成缓存
    note.write_text("已损坏", encoding="utf-8")  # 修改已完成文件
    result = cp.course_next(root)  # 自动重新放回本集
    step = step_of(result, "note")  # 只检查交接字段
    assert result["reopened"] == [1] and step["existing_note"] is True  # 告知已有笔记应修改
    assert step["note_path"] == str(note) and step["reason"].startswith("笔记门禁未通过：")  # 原路径与原因直接交出


def test_verify_reports_original_done_count_and_writes_back(course, capsys):  # verify与next共用重检
    root = course()  # 四集范围
    note, _ = valid_note(root)  # 一项正常完成
    cp.mark(root, 1, "done", note=str(note))  # 真实门禁成功
    current = read_course(root)  # 模拟旧版第二项文件丢失
    current["items"][1].update(status="done", note="missing.md")  # 第二个done需要恢复
    write_course(root, current)  # 写入旧清单状态
    result = cp.main(["verify", str(root)])  # CLI公开新命令
    assert result["checked"] == 2 and result["passed"] == 1 and result["reopened"] == [2]  # checked按重检前完成数量
    assert read_course(root)["items"][1]["status"] == "pending"  # verify必须持久恢复


def test_null_gate_remains_compatible(course):  # 显式空缓存等同旧清单缺省字段
    root = course()  # 通过原规划流程创建兼容清单
    current = read_course(root)  # 只增加可选空值
    current["items"][0]["gate"] = None  # 正式方案允许显式null
    write_course(root, current)  # 保存真实JSON空值
    assert cp.load_course(root)["items"][0]["gate"] is None  # 加载不应拒绝合法空缓存


@pytest.mark.parametrize("gate", [[], {}, {"errors": -1}, {"errors": 0, "warnings": 0, "sha256": "x", "template": "v4", "checked_at": "now"}])  # 可选字段存在时应有完整有效结构
def test_invalid_gate_rejected_but_absent_is_compatible(course, gate):  # 防止损坏缓存让断点跳过
    root = course()  # 无gate旧版清单已可读取
    current = read_course(root)  # 仅注入门禁字段
    current["items"][0]["gate"] = gate  # 不合法结构
    write_course(root, current)  # 模拟持久清单损坏
    with pytest.raises(cp.HintError):  # 必须可读失败而非静默信任
        cp.load_course(root)  # 验证入口可选字段校验
