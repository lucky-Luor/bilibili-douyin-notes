import copy  # 比较生成及只读查询前后的清单状态
from pathlib import Path  # 核对写入路径与公开绝对路径回执
import pytest  # 参数化保护性读取及掌握状态测试
import bili_fetch  # 观察真实原子写入器的复用
import course_review as cr  # 调用课程复习公开接口
from _hints import HintError  # 检查失败遵循项目统一提示协议


@pytest.fixture  # 每个用例独立构造真实笔记与课程数据
def course(tmp_path, monkeypatch):  # 不复用或触碰PR1测试夹具
    root = tmp_path / "中文 空格课程"  # 自动覆盖中文与空格根目录
    root.mkdir()  # 仅写入隔离临时目录
    note = root / "第一讲.md"  # 不依赖真实样例或用户笔记
    note.write_text("# 离线课\n\n## 1. 基础\n\n正文。\n\n## 概念卡片\n\n### 机会成本\n+ 定义：放弃的价值\n+ 出现：第 1 节「基础」\n+ 依赖：边界\n+ 易混：\n\n### 边界\n+ 定义：可达范围\n+ 出现：第 1 节「基础」\n+ 依赖：\n+ 易混：\n\n## 自测题\n\n1. 何为机会成本？（对应：机会成本）\n\n**答案：** 放弃的价值。\n\n2. 何为边界？（对应：边界）\n\n**答案：** 可达范围。\n\n## 视频时间索引\n\n+ [00:10](https://example.com/watch?p=1&t=10) 1. 基础\n", encoding="utf-8")  # 笔记采用真实解析器支持的标准语法
    data = {"version": 1, "title": "离线课程", "items": [{"index": 1, "title": "第一讲", "status": "done", "note": note.name}]}  # I4旧清单不需要已有复习字段
    (root / "course.json").write_text("用户持有的清单不由generate保存", encoding="utf-8")  # 验证业务函数不会擅自保存清单
    monkeypatch.setattr(cr, "_review_now", lambda: "2026-11-04T08:00:00+00:00")  # 固定新增状态及生成时间
    return root, data  # 用例可独立注入文件勾选或备份


@pytest.mark.parametrize("box,expected", [(" ", False), ("x", True), ("X", True)])  # 三种正式勾选状态均可读取
def test_read_marks_normalizes_name_and_case(tmp_path, box, expected):  # 文件和模型采用完全相同的合并键
    path = tmp_path / "概念表.md"  # 无需构建课程即可检查回读协议
    path.write_text(f"+ [{box}] **Ａ B　成本**（Cost）\n", encoding="utf-8")  # 全角半角及空白混合名称
    assert cr.read_marks(path) == {"ab成本": expected}  # 原文括号不参与概念键


def test_read_marks_missing_and_empty_files(tmp_path):  # 缺失与真正空文件均允许从备份恢复
    path = tmp_path / "概念表.md"  # 初始不存在
    assert cr.read_marks(path) == {}  # 首次生成不需要文件
    path.write_text("", encoding="utf-8")  # 用户清空文件视为没有覆盖项
    assert cr.read_marks(path) == {}  # 不对真正空文件误报保护错误


@pytest.mark.parametrize("text", ["用户自定义文本", "+ [y] **机会成本**", "- [x] **机会成本**", "  + [x] **机会成本**", "\n", "> 课程：伪空表｜概念 0 个\n", "> 课程：伪空表｜概念 0 个（跨集出现 0 个）｜已掌握 0 / 0｜来自 1 篇已完成的笔记｜更新于 时间\n错误正文\n"])  # 非标准格式不能伪装成合法空表
def test_read_marks_rejects_nonempty_unreadable_format(tmp_path, text):  # 避免把用户损坏文件覆盖掉
    path = tmp_path / "概念表.md"  # 每条数据都使用真实文件
    path.write_text(text, encoding="utf-8")  # 保留原始格式以模拟用户编辑
    with pytest.raises(HintError, match="无法识别任何勾选行") as error:  # 必须产生明确可恢复提示
        cr.read_marks(path)  # 不容许静默返回空映射
    assert "先备份" in str(error.value) and "course.json" in str(error.value) and path.read_text(encoding="utf-8") == text  # 提示给出恢复操作且文件保持原样


def test_read_marks_rejects_invalid_encoding(tmp_path):  # 被加密或非UTF8文件不能误恢复备份
    path = tmp_path / "概念表.md"  # 真实二进制内容触发解码异常
    path.write_bytes(b"\xff\xfe\x81\x80")  # 不涉及网络或外部加密软件
    with pytest.raises(HintError, match="无法读取概念表") as error:  # 不暴露底层解码堆栈
        cr.read_marks(path)  # 转换为统一三段式提示
    assert "先备份" in str(error.value) and path.read_bytes() == b"\xff\xfe\x81\x80"  # 原始字节保持不变


def test_read_marks_permission_error_is_protected(tmp_path, monkeypatch):  # 读取权限失败与损坏格式采用同一保护协议
    path = tmp_path / "概念表.md"  # 只针对目标路径模拟系统异常
    def denied(*args, **kwargs):  # 不依赖Windows权限差异
        raise PermissionError("离线模拟拒绝读取")  # 强制覆盖读取异常分支
    monkeypatch.setattr(Path, "read_text", denied)  # 在读取边界注入系统异常
    with pytest.raises(HintError, match="无法读取概念表"):  # 不允许权限失败变成空掌握状态
        cr.read_marks(path)  # 原因影响和恢复建议由业务模块生成


def test_generate_merge_priority_backup_file_then_parameters(course):  # 明确验证三层状态优先级
    root, data = course  # 两个当前概念用于分别验证覆盖和保留
    data["mastery"] = {"机会成本": {"name": "机会成本", "mastered": False, "changed_at": "旧时间"}, "边界": {"name": "边界", "mastered": True, "changed_at": "旧时间"}}  # 初始清单状态与文件不同
    (root / "概念表.md").write_text("+ [x] **机会成本**\n+ [ ] **边界**\n", encoding="utf-8")  # 文件覆盖备份
    result = cr.generate(root, data, master=["边 界"], unmaster=["机会成本"])  # 参数再次覆盖文件且名称归一化
    assert result["mastered"] == 1 and data["mastery"]["边界"]["mastered"] is True and data["mastery"]["机会成本"]["mastered"] is False  # 最终状态必须由参数决定
    assert cr.read_marks(root / "概念表.md") == {"机会成本": False, "边界": True}  # 落盘勾选与备份一致


def test_file_marks_override_backup_without_parameters(course):  # 单独证明文件比备份优先
    root, data = course  # 使用真实解析与生成流程
    data["mastery"] = {"机会成本": {"name": "机会成本", "mastered": False, "changed_at": "旧时间"}}  # 备份未掌握
    (root / "概念表.md").write_text("+ [X] **机会成本**\n", encoding="utf-8")  # 用户用大写完成勾选
    assert cr.generate(root, data)["mastered"] == 1 and data["mastery"]["机会成本"]["mastered"] is True  # 用户文件的勾选实际生效


def test_generate_keeps_marks_across_regeneration_and_recovers_deleted_file(course):  # 重建与删除后恢复均不丢勾选
    root, data = course  # 首次由名称参数建立备份
    cr.generate(root, data, master="机会成本")  # 支持单个字符串名称
    assert cr.generate(root, data)["mastered"] == 1  # 正常重建保留用户勾选
    (root / "概念表.md").unlink()  # 模拟用户确认删除损坏文件后的恢复场景
    assert cr.generate(root, data)["mastered"] == 1 and cr.read_marks(root / "概念表.md")["机会成本"]  # 缺文件时从清单备份重建


def test_changed_at_only_updates_on_state_change(course, monkeypatch):  # 文件重建不冒充新的掌握状态变化
    root, data = course  # 初次生成记录两个未掌握状态
    cr.generate(root, data)  # 固定初始时间
    old = data["mastery"]["机会成本"]["changed_at"]  # 保存首次建立状态的时间
    monkeypatch.setattr(cr, "_review_now", lambda: "2026-11-05T09:00:00+00:00")  # 模拟隔天重建
    cr.generate(root, data)  # 状态没有变化
    assert data["mastery"]["机会成本"]["changed_at"] == old  # 单纯更新时间不能覆盖状态时间
    cr.generate(root, data, master=["机会成本"])  # 当前状态从未掌握改为掌握
    assert data["mastery"]["机会成本"]["changed_at"] == "2026-11-05T09:00:00+00:00" and data["mastery"]["边界"]["changed_at"] == old  # 只有发生变化的键更新时间


def test_disappeared_concepts_remain_in_backup(course):  # 笔记临时消失后掌握记录仍可恢复
    root, data = course  # 当前模型不包含历史概念
    data["mastery"] = {"历史概念": {"name": "历史概念", "mastered": True, "changed_at": "历史时间"}}  # 预先保存已消失概念
    result = cr.generate(root, data)  # 正常生成当前两个概念
    assert data["mastery"]["历史概念"] == {"name": "历史概念", "mastered": True, "changed_at": "历史时间"} and result["mastered"] == 0  # 保留历史备份但不计入当前统计


@pytest.mark.parametrize("raw", [b"\xff\xfe", "错误格式".encode("utf-8")])  # 同时覆盖解码失败和无勾选坏格式
def test_bad_marks_refuse_generate_without_any_write(course, raw):  # 保护性预检必须发生在任何文件写入之前
    root, data = course  # 生成前保存清单快照
    concept_path, quiz_path = root / "概念表.md", root / "课程自测.md"  # 两份产物都应受保护
    concept_path.write_bytes(raw)  # 模拟现有坏勾选表
    quiz_path.write_text("原有题库", encoding="utf-8")  # 确保另一份产物也不会被覆盖
    before = copy.deepcopy(data)  # 验证失败不修改调用者清单
    with pytest.raises(HintError):  # 未经恢复不可强制生成
        cr.generate(root, data, master="机会成本")  # 参数也不能绕过保护
    assert concept_path.read_bytes() == raw and quiz_path.read_text(encoding="utf-8") == "原有题库" and data == before  # 两份磁盘与内存状态都完全保持


@pytest.mark.parametrize("failure", ["parse", "link"])  # 来源解析和链接错误都会阻止落盘
def test_source_errors_refuse_generate(course, failure):  # 不把缺失或坏笔记生成为空课程
    root, data = course  # 使用真实检查器产生阻断错误
    note = root / data["items"][0]["note"]  # 定位唯一来源笔记
    if failure == "parse":  # 讲义缺少概念卡片属于解析失败
        note.write_text("# 坏讲义\n## 1. 正文\n没有概念卡\n", encoding="utf-8")  # 不伪造check返回值
    else:  # 文件不存在属于链接错误并伴随读取错误
        note.unlink()  # 真实触发CR-LINK
    with pytest.raises(HintError, match="检查未通过"):  # 缺少复习文件的CR-STALE不是主要失败原因
        cr.generate(root, data)  # 必须在写复习文件前退出
    assert not (root / "概念表.md").exists() and "review" not in data and "mastery" not in data  # 不留下看似成功的状态


def test_unknown_name_suggests_closest_three_and_does_not_write(course):  # 错误名称不允许静默建立新掌握记录
    root, data = course  # 两个真实候选均可供建议
    with pytest.raises(HintError, match="找不到概念") as error:  # 主CLI负责把此异常转为用户可读退出
        cr.generate(root, data, master=["机会成木"])  # 制造接近真实名称的错别字
    assert "最接近" in str(error.value) and "机会成本" in str(error.value) and not (root / "概念表.md").exists() and "mastery" not in data  # 提示真实候选且无写入


def test_generate_atomic_writes_metadata_and_never_saves_course_json(course, monkeypatch):  # 公开回执与清单元数据一致
    root, data = course  # 旧清单仍可直接使用
    writes = []  # 记录原子写入调用路径
    original = bili_fetch.atomic_write_text  # 包装真实写入器而非替换写入行为
    def record(path, text):  # 核对两个文件都走统一写入边界
        writes.append(Path(path))  # 保留参数路径供后续断言
        return original(path, text)  # 继续使用真实原子替换
    monkeypatch.setattr(bili_fetch, "atomic_write_text", record)  # 模块通过共享库对象引用现有写入器
    result = cr.generate(root, data)  # 允许初次CR-STALE检查后生成
    assert writes == [root / "概念表.md", root / "课程自测.md"] and result["concepts"] == 2 and result["questions"] == 2 and result["jump_rate"] == 1.0  # 两个产物都存在且统计完整
    assert Path(result["concepts_file"]).is_absolute() and data["review"]["concepts_file"] == "概念表.md" and data["review"]["quiz_file"] == "课程自测.md"  # 公开绝对路径与清单相对路径分工明确
    assert (root / "course.json").read_text(encoding="utf-8") == "用户持有的清单不由generate保存" and not cr.check(cr.build(root, data), root)["errors"]  # generate不保存清单且生成后不再过期


def test_write_failure_does_not_commit_course_state(course, monkeypatch):  # 写入失败不能提前提交内存备份
    root, data = course  # 尚无复习状态的初始清单
    before = copy.deepcopy(data)  # 保存失败前完整清单
    def denied(*args, **kwargs):  # 在真正写入边界模拟磁盘异常
        raise OSError("磁盘空间不足")  # 与权限异常同属可恢复系统错误
    monkeypatch.setattr(bili_fetch, "atomic_write_text", denied)  # 不需要改变系统文件权限
    with pytest.raises(HintError, match="无法写入课程复习文件"):  # 写失败转换为统一提示
        cr.generate(root, data, master="机会成本")  # 不可把参数状态预先提交
    assert data == before  # 调用者可安全重试原清单


def test_unmastered_is_readonly_and_file_marks_have_priority(course):  # 出题查询不刷新任何文件与状态
    root, data = course  # 构造用户文件覆盖备份的场景
    data["mastery"] = {"机会成本": {"name": "机会成本", "mastered": False, "changed_at": "过去"}}  # 备份中仍未掌握
    path = root / "概念表.md"  # 用户已经在编辑器勾选
    path.write_text("+ [x] **机会成本**\n+ [ ] **边界**\n", encoding="utf-8")  # 文件中的状态优先
    before, raw, stamp = copy.deepcopy(data), path.read_bytes(), path.stat().st_mtime_ns  # 完整记录内存及磁盘状态
    result = cr.unmastered(root, data)  # 只读查询未掌握题目
    assert [entry["name"] for entry in result["concepts"]] == ["边界"] and result["concepts"][0]["questions"][0] == {"index": 1, "no": 2, "stem": "何为边界？", "answer": "可达范围。", "url": "https://example.com/watch?p=1&t=10"}  # 输出方案规定的最小题目字段
    assert data == before and path.read_bytes() == raw and path.stat().st_mtime_ns == stamp and not (root / "课程自测.md").exists()  # 查询不能偷偷刷新或保存


@pytest.mark.parametrize("size,truncated", [(50, False), (51, True)])  # 精确覆盖截断边界两侧
def test_unmastered_limits_to_fifty_concepts(course, monkeypatch, size, truncated):  # 一次回执最多返回五十个概念
    root, data = course  # 仅替换模型规模而复用真实查询与状态合并
    model = cr.build(root, data)  # 保持模型协议来自实际构建
    prototype = model["concepts"][0]  # 用真实概念字段生成独立同构记录
    model["concepts"] = [{**copy.deepcopy(prototype), "key": f"概念{index}", "name": f"概念{index}"} for index in range(size)]  # 各概念使用不同键避免合并
    monkeypatch.setattr(cr, "build", lambda *args: model)  # 不创建五十一篇与本边界无关的笔记
    result = cr.unmastered(root, data)  # 不生成文件的公开查询接口
    assert len(result["concepts"]) == 50 and result.get("truncated", False) is truncated  # 恰好五十个不报告截断


def test_no_completed_notes_is_readable_error(tmp_path):  # 无完成笔记不能制造虚假空复习文件
    with pytest.raises(HintError, match="还没有完成的笔记"):  # 主入口负责最终人话退出
        cr.generate(tmp_path, {"title": "空课", "items": []})  # 与合法零概念短笔记区分
    assert not (tmp_path / "概念表.md").exists()  # 不留下无来源产物


def test_zero_concept_short_can_regenerate_own_empty_table(course):  # 合法短笔记零概念表应能够重复回读与生成
    root, data = course  # 保留至少一篇done笔记满足来源约束
    note = root / data["items"][0]["note"]  # 将夹具改成合法short元信息笔记
    note.write_text("---\nnote_type: short\n---\n# 短笔记\n\n## 1. 要点\n\n简短正文。\n", encoding="utf-8")  # short允许没有概念卡片章节
    first = cr.generate(root, data)  # 首次生成非空头部但没有勾选行
    path = root / "概念表.md"  # 再次读取的是我们实际生成的空表
    assert first["concepts"] == 0 and cr.read_marks(path) == {}  # 标准空表不是坏格式用户文件
    assert cr.generate(root, data)["concepts"] == 0 and data["mastery"] == {}  # 重复生成不会拒绝自己的产物
    before = path.read_bytes()  # 标准头部后添加任意非空正文都不再是合法空表
    path.write_bytes(before + "用户错误条目\n".encode("utf-8"))  # 模拟错误格式用户修改
    with pytest.raises(HintError, match="无法识别任何勾选行"):  # 不能只凭摘要中出现零概念就放过所有格式
        cr.generate(root, data)  # 严格拒绝并保护该文件
    assert path.read_bytes() == before + "用户错误条目\n".encode("utf-8")  # 保护原则在空表边界同样成立
