import copy  # 隔离损坏清单与刷新失败的内存状态
import json  # 验证命令行输出与课程持久化协议
import socket  # 封锁测试中的意外网络请求
from pathlib import Path  # 观察含中文空格的真实课程产物
import pytest  # 提供离线夹具和参数化回归
import course_plan as cp  # 通过真实课程业务和命令行入口验收接入
import course_review as cr  # 只调用另一个模块公开的复习接口
from test_validate_note_v4 import GOOD  # 复用带概念卡自测和时间索引的合法笔记


@pytest.fixture(autouse=True)  # 每个用例都限制为离线且不读取真实登录配置

def offline(monkeypatch):  # 任何漏网的外部访问直接失败
    def blocked(*args, **kwargs):  # 不将误联网掩盖成成功结果
        raise AssertionError("课程复习测试禁止网络和真实登录配置访问")  # 提供明确失败定位
    monkeypatch.setattr(socket.socket, "connect", blocked)  # 禁止底层网络连接
    monkeypatch.setattr(cp.bili_fetch, "api", blocked)  # 禁止课程规划接口意外请求
    monkeypatch.setattr(cp.bili_fetch, "load_sessdata", blocked)  # 禁止真实登录信息读取


@pytest.fixture  # 所有集成场景使用真实文件和可加载的旧格式清单

def course(tmp_path):  # 提供两篇完成笔记及一条待办
    root = tmp_path / "中文 空格 课程"  # 自然覆盖中文和空格路径
    (root / "notes").mkdir(parents=True)  # 所有产物限制在临时目录
    items = [cp._item(index, "BV1offline", {"page": index, "cid": 100 + index, "part": f"第{index}讲", "duration": 600}, "离线课程") for index in range(1, 4)]  # 三集便于验证范围外完成笔记
    for item in items[:2]:  # 直接准备完成笔记避免依赖抓取器
        note = root / "notes" / f"{item['index']:03d}-中文 笔记.md"  # 文件路径也包含中文和空格
        note.write_text(GOOD, encoding="utf-8")  # 生成器使用真实解析和渲染而不是桩
        item.update(status="done", note=note.relative_to(root).as_posix())  # 第三集保持待办以便测试刷新入口
    data = {"version": 1, "kind": "pages", "title": "课程 | 测试", "owner": "测试老师", "source_url": "https://www.bilibili.com/video/BV1offline", "scope": {"mode": "first", "first": 1}, "items": items}  # 旧清单不含复习字段且范围只选第一集
    cp.save_course(root, data)  # 真实加载和保存共同验证向后兼容
    return root, data  # 每个用例独立修改


def snapshot(root):  # 只读入口必须保持全部文件内容和修改时间
    return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns) for path in root.rglob("*") if path.is_file()}  # 同时发现新增删除和重写行为


def invoke(capsys, *argv):  # 真实命令行解析分发并保证标准输出为单个摘要
    result = cp.main([str(arg) for arg in argv])  # 不产生子进程与额外环境依赖
    output = capsys.readouterr()  # 人类警告与机器输出分开捕获
    assert json.loads(output.out) == result and len(output.out.splitlines()) == 1  # 中文输出可直接解析并与返回结果一致
    return result, output.err  # 调用方按场景核对错误流


def gate(root, course, item):  # 自动刷新场景聚焦编排而不重复单集门禁测试
    note = Path(root) / item["note"]  # 摘要仍使用真实笔记原始字节
    return {"errors": 0, "warnings": 0, "sha256": cp.hashlib.sha256(note.read_bytes()).hexdigest(), "template": "v4", "checked_at": cp.now(), "first_errors": []}  # 使用生产协议构造通过的门禁结果


def test_concepts_generate_legacy_course_and_all_done_notes(course, capsys):  # 生成覆盖范围外完成笔记并保存新摘要
    root, before = course  # 第三集待办不得参与概念汇总
    result, _ = invoke(capsys, "concepts", root)  # 不传选项即生成两个文件
    assert result["concepts"] == 2 and result["multi_episode"] == 2 and result["questions"] == 8  # 两篇相同笔记合并概念但保留全部原题
    assert result["mastered"] == 0 and result["unmastered"] == 2 and result["jump_rate"] == 1  # 无勾选起点和完整跳转率
    for key, name in (("concepts_file", "概念表.md"), ("quiz_file", "课程自测.md")):  # 文件名称和绝对回执遵守接口契约
        assert Path(result[key]) == root / name and (root / name).is_file()  # 回执必须指向已经落盘的产物
    saved = cp.load_course(root)  # 确认编排器保存了生成器原地更新的字段
    assert saved["items"] == before["items"] and saved["scope"] == before["scope"]  # 生成复习不改变课程执行状态或用户选择
    assert saved["review"]["concepts"] == 2 and saved["review"]["questions"] == 8 and len(saved["mastery"]) == 2  # 摘要和掌握备份已经持久化


def test_concepts_generate_and_marks_refresh_index(course, capsys):  # 生成与命令行勾选后索引立即反映复习入口和掌握数
    root, _ = course  # 两篇完成笔记的课程
    result, _ = invoke(capsys, "concepts", root)  # 首次生成复习文件
    index = (root / cp.INDEX_FILE).read_text(encoding="utf-8")  # 读取同步刷新的索引
    assert Path(result["index_file"]) == root / cp.INDEX_FILE  # 回执给出索引位置
    assert "> 复习：[概念表](概念表.md)（2 个概念，已掌握 0）" in index  # 无需额外运行 index 即出现入口
    name = cr.build(root, cp.load_course(root))["concepts"][0]["name"]  # 取一个真实概念名
    invoke(capsys, "concepts", root, "--master", name)  # 命令行标记一个已掌握
    assert "已掌握 1）" in (root / cp.INDEX_FILE).read_text(encoding="utf-8")  # 索引掌握数随之更新


def test_concepts_check_success_is_readonly(course, capsys):  # 新鲜生成文件检查通过且完全不写文件
    root, _ = course  # 隔离真实课程
    cp.concepts(root)  # 准备已生成的复习产物
    before = snapshot(root)  # 保存全部文件与修改时间
    report, _ = invoke(capsys, "concepts", root, "--check")  # 检查入口走真实模型和门禁
    assert set(report) == {"errors", "warnings", "summary"} and report["errors"] == []  # 返回值严格包含三组门禁信息
    assert report["summary"]["concepts"] == 2 and report["summary"]["questions"] == 8  # 检查包含真实汇总统计
    assert snapshot(root) == before  # 检查不能重写摘要勾选索引或笔记


def test_concepts_check_error_prints_json_before_exit_one(course, capsys):  # 未生成文件作为过期错误报告而不是通用失败
    root, _ = course  # 清单与笔记存在但复习文件尚不存在
    before = snapshot(root)  # 检查失败仍不能写入
    with pytest.raises(SystemExit) as exc:  # 主入口承担检查退出码
        cp.main(["concepts", str(root), "--check"])  # 真实命令行参数分发
    report = json.loads(capsys.readouterr().out)  # 错误报告仍是唯一完整的机器摘要
    assert exc.value.code == 1 and any(item["check"] == "CR-STALE" for item in report["errors"])  # 先打印结构化报告再退出一
    assert "summary" in report and "status" not in report and snapshot(root) == before  # 不替换成普通异常摘要且保持只读


def test_concepts_check_direct_call_returns_errors_without_exit(course):  # 业务函数由调用方决定是否退出
    root, _ = course  # 没有复习文件时存在过期错误
    report = cp.concepts(root, check=True)  # 直接调用不触发进程退出
    assert report["errors"] and set(report) == {"errors", "warnings", "summary"}  # 返回完整检查报告


def test_concepts_check_dict_warnings_stderr(course, capsys):  # 结构化警告不能触发字符串拼接异常
    root, _ = course  # 修改时间索引只影响跳转警告
    note = root / "notes" / "001-中文 笔记.md"  # 只去掉一篇时间索引
    note.write_text(GOOD.split("## 视频时间索引")[0], encoding="utf-8")  # 保留概念与题目使生成仍合法
    cp.concepts(root)  # 更新摘要使检查只报告警告
    result, stderr = invoke(capsys, "concepts", root, "--check")  # 主入口打印字典警告的人类消息
    assert result["errors"] == [] and any(item["check"] == "CR-JUMP" for item in result["warnings"])  # 缺跳转属于警告而不影响退出
    assert "warning:" in stderr  # 标准错误包含可读提示


def test_concepts_master_and_unmaster_repeat_options_together(course, capsys):  # 同次正反标记允许且重复参数以列表传递
    root, _ = course  # 使用两个已知概念
    invoke(capsys, "concepts", root, "--master", "活动图", "--master", "颜色语义")  # 重复选项一次掌握多个概念
    result, _ = invoke(capsys, "concepts", root, "--master", "活动图", "--unmaster", "颜色语义")  # 同次正反标记不会被参数解析器互斥
    saved = cp.load_course(root)["mastery"]  # 观察真实生成器更新后的备份
    assert result["mastered"] == 1 and result["unmastered"] == 1  # 统计与正反选项一致
    assert saved["活动图"]["mastered"] is True and saved["颜色语义"]["mastered"] is False  # 清单备份同步保留真假状态


@pytest.mark.parametrize("options", [("--check", "--unmastered"), ("--check", "--master", "活动图"), ("--check", "--unmaster", "活动图"), ("--unmastered", "--master", "活动图"), ("--unmastered", "--unmaster", "活动图")])  # 三组互斥的五种组合全部覆盖

def test_concepts_modes_are_mutually_exclusive_without_write(course, capsys, options):  # 失败发生在任何生成或清单保存之前
    root, _ = course  # 存在有效数据避免空课程掩盖互斥错误
    before = snapshot(root)  # 只比较真实磁盘结果
    with pytest.raises(cp.HintError, match="互斥"):  # 命令行沿用三段式可恢复提示
        cp.main(["concepts", str(root), *options])  # 走真实命令行入口
    assert snapshot(root) == before and json.loads(capsys.readouterr().out)["status"] == "error"  # 参数错误仍保留机器摘要且没有写入


@pytest.mark.parametrize("options", [{}, {"check": True}, {"unmastered": True}, {"master": ["活动图"]}])  # 生成检查查询与标记采用同一空课程提示

def test_concepts_no_done_note_uses_hint(course, options):  # 状态完成但没有笔记路径也属于无完成笔记
    root, data = course  # 清空路径而保留完成状态验证筛选条件
    for item in data["items"]:  # 所有条目都不能进入复习数据源
        item["note"] = ""  # 状态本身不代替真实笔记引用
    cp.save_course(root, data)  # 注入合法旧清单状态
    before = snapshot(root)  # 空课程拒绝不得创建文件
    with pytest.raises(SystemExit, match="还没有完成的笔记，先做完至少一集") as exc:  # 不输出底层堆栈
        cp.concepts(root, **options)  # 所有模式均走相同入口
    assert all(label in str(exc.value) for label in ("原因：", "影响：", "下一步：")) and snapshot(root) == before  # 错误三段完整且零写入


@pytest.mark.parametrize("option", ["master", "unmaster"])  # 正反标记未知名称均走同一个三段式退出

def test_concepts_unknown_master_has_nearby_name_without_write(course, option):  # 错别字提示最相近概念而不覆盖文件
    root, _ = course  # 实际存在活动图概念
    cp.concepts(root)  # 已有复习文件应保留
    before = snapshot(root)  # 备份用户现有产物
    with pytest.raises(SystemExit) as exc:  # 未知名称按方案使用不可恢复的参数退出
        cp.concepts(root, **{option: ["活动囹"]})  # 仅一个字符不同用于正反标记的近似名建议
    assert "活动图" in str(exc.value) and "下一步：" in str(exc.value) and snapshot(root) == before  # 建议包含正确名称且错误请求不写入


def test_concepts_bad_checkbox_file_keeps_hint_error_and_does_not_overwrite(course, capsys):  # 显式生成不能吞掉用户勾选保护错误
    root, _ = course  # 准备真实已生成的复习文件
    cp.concepts(root, master=["活动图"])  # 原备份保留一个已掌握概念
    (root / "概念表.md").write_text("文件被编辑器改坏，已没有有效勾选行", encoding="utf-8")  # 非空无勾选文件必须拒绝覆盖
    before = snapshot(root)  # 保存损坏文件及其它文件的字节和时间
    with pytest.raises(cp.HintError) as exc:  # 不能将勾选保护变为成功统计或只给机器摘要
        cp.main(["concepts", str(root)])  # 真实入口保留生成器的可恢复异常
    assert "备份" in str(exc.value) and "删除" in str(exc.value) and "概念表.md" in str(exc.value)  # 下一步明确恢复流程
    assert json.loads(capsys.readouterr().out)["status"] == "error" and snapshot(root) == before  # 人类提示交给统一入口且原产物完全不动


def test_concepts_manual_marks_and_missing_file_restore_backup(course):  # 用户改文件优先于备份且删除文件可以恢复
    root, _ = course  # 初始化未掌握的课程
    cp.concepts(root)  # 生成两个当前概念
    path = root / "概念表.md"  # 用户唯一勾选入口
    path.write_text(path.read_text(encoding="utf-8").replace("+ [ ] **活动图**", "+ [X] **活动图**"), encoding="utf-8")  # 模拟编辑器手工勾选而不修改备份
    assert cp.concepts(root)["mastered"] == 1  # 重新生成保留文件中的勾选
    path.unlink()  # 模拟用户确认后删除坏文件进行恢复
    assert cp.concepts(root)["mastered"] == 1 and "+ [x] **活动图**" in path.read_text(encoding="utf-8")  # 备份恢复已掌握状态


def test_concepts_unmastered_is_readonly_and_contains_original_questions(course, capsys):  # 复习模式只读返回未掌握概念的题目
    root, _ = course  # 两个概念各有同课原题
    cp.concepts(root, master=["活动图"])  # 只保留颜色语义未掌握
    before = snapshot(root)  # 查询不能改变备份或概念表
    result, _ = invoke(capsys, "concepts", root, "--unmastered")  # 走真实查询分发
    assert [item["name"] for item in result["concepts"]] == ["颜色语义"]  # 已掌握概念不再返回
    concept = result["concepts"][0]  # 获取剩余概念用于出题
    assert {"name", "original", "definition", "questions"} <= concept.keys()  # 概念信息满足复习接口
    assert concept["questions"] and all({"index", "no", "stem", "answer", "url"} <= question.keys() for question in concept["questions"])  # 返回原题答案及回看地址
    assert snapshot(root) == before  # 只读查询对全部文件零写入


def test_concepts_unmastered_truncates_at_fifty(course):  # 大课程的查询上限由公开入口验收
    root, data = course  # 仅保留一篇包含五十一个概念的笔记
    data["items"][1]["status"] = "pending"  # 不混入原有两个概念
    note = root / data["items"][0]["note"]  # 概念源使用现有已完成条目
    cards = "\n".join(f"### 概念{index}\n+ 定义：定义{index}\n+ 出现：第 1 节「基础」\n+ 依赖：\n+ 易混：\n" for index in range(51))  # 构造可解析且不同名的五十一个概念
    note.write_text("## 1. 基础\n\n## 概念卡片\n" + cards + "\n## 自测题（复习用）\n\n## 视频时间索引\n+ [00:00](https://www.bilibili.com/video/BV1offline?p=1&t=0) 1. 基础\n", encoding="utf-8")  # 查询不要求单集门禁但使用真实解析格式
    cp.save_course(root, data)  # 保存修改后的完成条目
    result = cp.concepts(root, unmastered=True)  # 不预先生成也应支持只读查询
    assert len(result["concepts"]) == 50 and result["truncated"] is True  # 超限明确说明截断


@pytest.mark.parametrize("entry", ["mark", "verify", "index", "finished"])  # 四个原有索引刷新时机全部接入

def test_review_refresh_all_four_entrypoints(course, monkeypatch, entry):  # 自动生成文件并保存复习摘要
    root, data = course  # 两篇已完成笔记会参与真实生成
    monkeypatch.setattr(cp, "run_gate", gate)  # 这里只隔离与接入无关的单集门禁
    if entry == "mark":  # 将第三集标记失败也应刷新已有笔记
        result = cp.mark(root, 3, "failed", reason="离线测试失败")  # 不仅成功状态才会刷新
    elif entry == "verify":  # 真实重检使用合法门禁桩保留完成状态
        result = cp.verify(root)  # 复核后生成两个复习文件
    elif entry == "index":  # 主动索引命令同样生成复习
        result = cp.index(root)  # 真实课程加载和保存
    else:  # 第一集已经完成使当前范围直接结束
        result = cp.course_next(root)  # 触发结束回执刷新
    saved = cp.load_course(root)  # 保存责任属于课程编排层
    assert saved["review"]["concepts"] == 2 and saved["mastery"] and "review_warning" not in result  # 真实生成成功并持久化
    assert all((root / name).is_file() for name in ("概念表.md", "课程自测.md", "课程索引.md"))  # 写索引前完成全部复习产物


@pytest.mark.parametrize("error", [cp.HintError("勾选文件损坏", "复习未更新", "先备份再重建"), OSError("权限不足"), UnicodeError("编码损坏"), ValueError("数据无效")])  # 可预期刷新失败只给警告
@pytest.mark.parametrize("entry", ["mark", "verify", "index", "finished"])  # 所有入口都保留主操作和恢复警告

def test_review_refresh_failure_is_nonblocking_and_retains_backup(course, monkeypatch, capsys, error, entry):  # 错误不能撤销状态或污染掌握备份
    root, _ = course  # 先生成有效复习备份以验证失败保护
    cp.concepts(root, master=["活动图"])  # 原掌握状态必须保留
    before = copy.deepcopy(cp.load_course(root))  # 记录旧复习摘要和勾选备份
    def fail(root, course, master=None, unmaster=None):  # 模拟生成器在修改内存后失败
        course["mastery"].clear()  # 捕获失败后不能把中间状态持久化
        raise error  # 触发可恢复失败降级
    monkeypatch.setattr(cr, "generate", fail)  # 不接触其它模块文件
    monkeypatch.setattr(cp, "run_gate", gate)  # 复核只验证刷新编排错误行为
    if entry == "mark":  # 主操作保存完成后刷新失败
        result = cp.mark(root, 3, "failed", reason="离线失败")  # 失败条目应仍然持久化
    elif entry == "verify":  # 复核不会被复习文件失败阻断
        result = cp.verify(root)  # 保持已有门禁接口结果
    elif entry == "index":  # 重建索引保留可恢复警告
        result = cp.index(root)  # 仍写课程索引
    else:  # 当前范围第一集已完成直接返回交付回执
        result = cp.course_next(root)  # 旧复习文件保持可用
    output = capsys.readouterr()  # 检查人类可见警告
    saved = cp.load_course(root)  # 观察失败后真实磁盘备份
    assert result["review_warning"] and result["review_warning"] in output.err and "Traceback" not in output.err  # 机器与人类均能发现刷新问题
    assert saved["review"] == before["review"] and saved["mastery"] == before["mastery"]  # 原有掌握备份不会被部分修改污染
    assert (root / "课程索引.md").is_file()  # 主索引操作仍完成
    if entry == "mark":  # 失败降级不能回滚已提交课程状态
        assert saved["items"][2]["status"] == "failed"  # 主操作成功保留


def test_review_refresh_skips_empty_course(course, monkeypatch):  # 尚无完成笔记时不调用生成器
    root, data = course  # 清空所有已完成状态
    for item in data["items"]:  # 范围内外统一改为待办
        item["status"] = "pending"  # 现有笔记文件不能代替完成状态
    cp.save_course(root, data)  # 保存空复习数据源
    def blocked(*args, **kwargs):  # 若意外调用即暴露问题
        raise AssertionError("无完成笔记不应生成复习")  # 不模拟成功来掩盖错误
    monkeypatch.setattr(cr, "generate", blocked)  # 只观察是否调用生成器
    cp.index(root)  # 仍应生成普通课程索引
    assert "review" not in cp.load_course(root) and not (root / "概念表.md").exists()  # 保持旧清单和空复习状态


def test_index_review_links_count_only_current_marks_and_preserve_missing_concepts(course):  # 已消失概念保留备份但不得污染索引统计
    root, _ = course  # 当前真实概念有两个
    cp.concepts(root, master=["活动图"])  # 当前掌握一个概念
    data = cp.load_course(root)  # 再注入已经消失的掌握备份
    data["mastery"]["已消失概念"] = {"name": "已消失概念", "mastered": True, "changed_at": cp.now()}  # 恢复笔记时仍需要保存历史状态
    cp.save_course(root, data)  # 持久保留历史备份
    cp.index(root)  # 真正刷新后摘要不得把历史掌握数加进来
    text = (root / "课程索引.md").read_text(encoding="utf-8")  # 检查交付的索引文案
    assert "> 复习：[概念表](概念表.md)（2 个概念，已掌握 1）｜[课程自测](课程自测.md)（8 题）" in text  # 路径统计和当前勾选一致
    assert text.index("> 进度：") < text.index("> 复习：") < text.index("## 目录")  # 新入口紧随进度行
    assert cp.load_course(root)["mastery"]["已消失概念"]["mastered"] is True  # 自动刷新不得删除历史备份


def test_finished_receipt_includes_review_files_and_counts(course, monkeypatch):  # 交付回执在索引后展示两个复习文件
    root, _ = course  # 当前范围只有一集且已完成
    monkeypatch.setattr(cp, "run_gate", gate)  # 保留已完成状态使真正进入结束分支
    result = cp.course_next(root)  # 自动生成复习和索引后构造回执
    assert result["status"] == "finished" and result["done"] == 1  # 课程完成数仍只统计本轮范围
    lines = result["receipt_text"].splitlines()  # 两行复习信息要紧跟索引
    index = next(number for number, line in enumerate(lines) if line.startswith("课程索引："))  # 不依赖首句具体格式
    assert lines[index + 1] == f"概念表：{root / '概念表.md'}（2 个概念）"  # 概念统计覆盖全课完成笔记
    assert lines[index + 2] == f"课程自测：{root / '课程自测.md'}（8 题）"  # 自测统计不受当前处理范围限制
    assert Path(result["concepts_file"]) == root / "概念表.md" and Path(result["quiz_file"]) == root / "课程自测.md"  # 机器回执明确两个绝对文件位置


@pytest.mark.parametrize("field,value", [("review", None), ("review", []), ("review", {}), ("mastery", None), ("mastery", []), ("mastery", {"概念": True}), ("mastery", {"": {"name": "名称", "mastered": True, "changed_at": "时间"}})])  # 顶层新增字段和记录形状严格校验

def test_invalid_review_and_mastery_structure_rejected(course, field, value):  # 损坏新字段不能被读取后静默覆盖
    root, data = course  # 旧清单保持其它字段合法
    data[field] = value  # 仅注入当前被测错误
    (root / "course.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")  # 绕过保存以模拟磁盘损坏
    with pytest.raises(cp.HintError):  # 加载与保存共同采用项目可读异常
        cp.load_course(root)  # 坏结构必须立即拒绝


@pytest.mark.parametrize("field,value", [("concepts_file", None), ("quiz_file", 3), ("generated_at", ""), ("source_digest", "x" * 64), ("concepts", True), ("concepts", -1), ("questions", 1.0)])  # 字符串计数摘要与布尔整数边界

def test_review_fields_strict_types(course, field, value):  # 完整摘要只有被测字段损坏
    root, data = course  # 准备合法旧课程
    data["review"] = {"concepts_file": "概念表.md", "quiz_file": "课程自测.md", "generated_at": cp.now(), "source_digest": "a" * 64, "concepts": 2, "questions": 8}  # 符合方案的新摘要结构
    data["review"][field] = value  # 损坏单字段确保归因准确
    with pytest.raises(cp.HintError):  # 保存入口也不得接受无效复习摘要
        cp.save_course(root, data)  # 保持旧磁盘清单完整


@pytest.mark.parametrize("field,value", [("name", None), ("name", ""), ("mastered", 1), ("mastered", "false"), ("changed_at", None), ("changed_at", "")])  # 备份字段严格拒绝真假值混淆和空字符串

def test_mastery_fields_strict_types(course, field, value):  # 每条记录必须完整有效
    root, data = course  # 其它课程元数据保持合法
    data["mastery"] = {"活动图": {"name": "活动图", "mastered": False, "changed_at": cp.now()}}  # 完整合法掌握备份
    data["mastery"]["活动图"][field] = value  # 单字段故障注入
    with pytest.raises(cp.HintError):  # 保存时拒绝覆盖有效旧清单
        cp.save_course(root, data)  # 复用清单严格类型校验
