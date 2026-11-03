import json  # 构造完全离线的课程与媒体元数据
import socket  # 封锁意外网络连接
from pathlib import Path  # 操作隔离的临时课程目录
import pytest  # 参数化索引与路径安全用例
import course_plan as cp  # 验证课程公开业务与命令行接口
import clean_media  # 观察既有媒体候选收集器的复用
from test_course_execution import make_note  # 复用PR2真实合法短笔记及对应元数据夹具


@pytest.fixture(autouse=True)  # 所有新增测试均不允许网络或真实配置访问
def offline(monkeypatch):  # 使用直接构造的课程避开规划接口
    def blocked(*args, **kwargs):  # 明确暴露意外外部访问
        raise AssertionError("测试禁止网络与真实登录配置访问")  # 保持失败原因可诊断
    monkeypatch.setattr(socket.socket, "connect", blocked)  # 封锁底层网络连接
    monkeypatch.setattr(cp.bili_fetch, "load_sessdata", blocked)  # 禁止读取本机登录配置
    monkeypatch.setattr(cp.bili_fetch, "api", blocked)  # 禁止真实视频接口请求
    monkeypatch.setattr(cp, "now", lambda: "2026-11-03T08:00:00+00:00")  # 固定索引更新时间


@pytest.fixture  # 每个用例使用独立且完整的课程数据
def course(tmp_path):  # 无网络且不需要读取现有用户产物
    root = tmp_path / "中文 课程"  # 自然覆盖中文和空格根目录
    root.mkdir()  # 仅创建测试隔离目录
    items = [cp._item(index, "BV1offline", {"page": index, "cid": 100 + index, "part": f"第{index}讲", "duration": duration}, "课程", "基础" if index < 4 else "进阶") for index, duration in enumerate((60, 3661, 59, 600, 300, 120), 1)]  # 构造六集和两种时长格式
    data = {"version": 1, "kind": "pages", "title": "离线 | 课程", "owner": "测试老师", "source_url": "https://www.bilibili.com/video/BV1offline", "scope": {"mode": "all"}, "items": items}  # 保持现有课程清单协议
    cp.save_course(root, data)  # 使用真实持久化接口
    return root, data  # 允许按用例注入范围和执行结果


def cache_folder(root, name="BV1offline"):  # 准备真实单视频缓存目录
    folder = root / "videos" / name  # 与抓取产物目录保持一致
    folder.mkdir(parents=True, exist_ok=True)  # 支持多视频缓存测试
    return folder  # 返回后续媒体路径基准


def media_metadata(folder, paths):  # 为现有收集器生成候选文件引用
    (folder / "metadata.json").write_text(json.dumps({"pages": [{"media_path": str(path)} for path in paths]}), encoding="utf-8")  # 不导入或修改清理器


def invoke(capsys, *args):  # 同时检查真实命令分发及输出协议
    result = cp.main([str(arg) for arg in args])  # 用与用户一致的参数解析入口
    output = capsys.readouterr()  # 分开获取机器摘要和人类进度
    assert json.loads(output.out) == result  # 标准输出保持单个可解析回执
    return result  # 交给用例检查新增字段


@pytest.mark.parametrize("path,expected", [("notes/中文 空格+(第一讲).md", "notes/中文%20空格%2B%28第一讲%29.md"), (Path("notes/百分%号#问?.md"), "notes/百分%25号%23问%3F.md"), ("notes\\子目录\\a[b]!&.md", "notes/子目录/a%5Bb%5D%21%26.md"), ("notes/é_~-.md", "notes/%C3%A9_~-.md")])  # 中文保留并按路径编码其余非保留字符
def test_md_link_path_rfc3986(path, expected):  # 独立验证链接函数而非间接依赖索引
    assert cp._md_link_path(path) == expected  # 空格不得变成表单编码的加号


def test_index_markdown_scope_status_links_and_atomic_write(course, monkeypatch):  # 完整约束索引格式及当前范围
    root, data = course  # 直接使用隔离课程
    data["scope"] = {"mode": "range", "range": [1, 5]}  # 第六集必须不出现在目录中
    states = ("done", "failed", "running", "pending", "failed", "done")  # 同时覆盖所有展示状态及范围外完成集
    for item, state in zip(data["items"], states):  # 注入不同结果而不运行笔记模型
        item["status"] = state  # 保持执行状态与显示状态独立
    data["items"][0].update(title="导论 | 实践", note="notes/中文 +(第一讲).md")  # 特殊标题及路径必须可点击
    data["items"][1].update(reason="下载失败", next_step="检查音频", rerun="重跑第二集")  # 失败恢复字段不得丢失
    data["items"][3].update(reason="笔记门禁未通过：缺少摘要", next_step="修改摘要")  # 仅待办门禁恢复项显示待修改
    data["items"][4].update(reason="笔记门禁未通过：缺少摘要", next_step="修改摘要", rerun="重跑第五集")  # 失败仍显示失败而非待修改
    writes = []  # 观察必须复用的原子写边界
    atomic = cp.bili_fetch.atomic_write_text  # 保留真实落盘行为
    def record(path, text):  # 包装已有写入器
        writes.append(Path(path))  # 记录目标而不替代原子落盘
        return atomic(path, text)  # 使用原有实现
    monkeypatch.setattr(cp.bili_fetch, "atomic_write_text", record)  # 不允许索引绕过原子写函数
    path = cp.write_index(root, data)  # 调用正式索引接口
    text = path.read_text(encoding="utf-8")  # 观察用户实际获得的纯文本
    assert path == root / "课程索引.md" and writes == [path]  # 根目录固定名称且仅原子写一次
    assert text.startswith("> 课程：离线 \\| 课程（B站，UP主：测试老师）\n> 来源：") and not text.startswith("---")  # 索引没有前置元数据
    assert data["source_url"] in text and "更新时间：2026-11-03T08:00:00+00:00" in text  # 来源及更新时间完整可追溯
    assert "完成 1/5" in text and "失败 2" in text and "待做 1" in text and "总集 6" in text and "小时" in text  # 进度只计算所选但保留整课总规模
    assert "## 目录\n" in text and "| 序号 | 标题 | 时长 | 状态 | 笔记 | 视频 |" in text  # 表格列顺序固定
    assert "导论 \\| 实践" in text and "[笔记](notes/中文%20%2B%28第一讲%29.md)" in text  # 标题竖线与路径特殊字符分别处理
    assert "01:00" in text and "1:01:01" in text and "00:59" in text  # 小时边界前后格式正确
    assert all(label in text for label in ("✅ 完成", "❌ 失败", "🔄 进行中", "⏳ 待修改"))  # 待办门禁恢复显示待修改其余状态保持原义
    assert "❌ 失败" in next(line for line in text.splitlines() if line.startswith("| 5 |"))  # 门禁前缀不能覆盖失败状态
    assert "https://www.bilibili.com/video/BV1offline?p=2" in text  # 链接保留正确分集页码
    assert "第6讲" not in text and "### 基础" not in text  # 分P不分组且不展开范围外标题
    assert "## 失败与补跑" in text and all(value in text for value in ("下载失败", "检查音频", "原因：", "下一步：", "\n  补跑：`重跑第二集`", "+ "))  # 失败恢复采用中文列表及独立行命令
    assert all(field not in text for field in ("reason", "next_step", "rerun"))  # 索引不暴露内部字段名
    tail = text.split("## 未选择的集", 1)[1]  # 单独检查范围外章节
    assert "1 集" in tail and "start" in tail and "--range A-B" in tail and "第6讲" not in tail  # 未选部分只报告数量与选择方式


def test_season_index_groups_selected_items_in_section_order(course):  # 合集按章节标题分组
    root, data = course  # 获取六集两章课程
    data["kind"] = "season"  # 同一条目结构切换为合集展示
    data["scope"] = {"mode": "range", "range": [2, 4]}  # 两章各保留选中条目
    text = cp.write_index(root, data).read_text(encoding="utf-8")  # 不需要任何网络元数据
    assert "（合集，UP主：测试老师）" in text  # 课程类型清楚区分
    assert text.index("### 基础") < text.index("第2讲") < text.index("### 进阶") < text.index("第4讲")  # 章节及条目均保持课程序
    assert text.count("| 序号 |") == 2 and "第1讲" not in text and "第5讲" not in text  # 每个章节表格独立且只展示所选


def test_mark_refreshes_every_state_status_is_readonly_and_index_cli_rebuilds(course, capsys):  # 所有回报路径都刷新索引
    root, data = course  # 使用真实课程清单
    note = make_note(root, "notes/测试.md")  # 使用真实门禁可通过的笔记与元数据
    for state, options, label in (("failed", {"reason": "暂时失败"}, "❌ 失败"), ("pending", {}, "⏳ 待做"), ("done", {"note": str(note)}, "✅ 完成")):  # 三种可标记状态都不能遗漏刷新
        cp.mark(root, 1, state, **options)  # 走现有真实状态更新接口
        text = (root / "课程索引.md").read_text(encoding="utf-8")  # 检查每次写入后的结果
        assert label in next(line for line in text.splitlines() if line.startswith("| 1 |"))  # 首行状态与本次回报一致
    index = root / "课程索引.md"  # 状态接口返回固定索引路径
    before = index.read_bytes(), index.stat().st_mtime_ns  # 状态查询不得刷新索引
    result = cp.status(root)  # 保持原有只读语义
    assert Path(result["index_file"]) == index.resolve() and (index.read_bytes(), index.stat().st_mtime_ns) == before  # 路径绝对且文件不变
    capsys.readouterr()  # 清空先前回报的用户进度
    index.write_text("旧索引", encoding="utf-8")  # 模拟用户请求重建过期索引
    result = invoke(capsys, "index", root)  # 新增命令必须通过真实分发
    assert Path(result["index_file"]) == index.resolve() and "✅ 完成" in index.read_text(encoding="utf-8")  # 重建使用当前清单


@pytest.mark.parametrize("failed", [False, True])  # 无失败时不得出现失败补跑段
@pytest.mark.parametrize("size", [0, 1572864])  # 同时覆盖零缓存及小数兆字节

def test_finished_receipt_exact_scope_paths_failures_and_cache(course, capsys, failed, size):  # 完成回执准确说明本轮结果
    root, data = course  # 范围外结果不应污染回执
    data["scope"] = {"mode": "range", "range": [2, 3]}  # 仅处理两集
    data["items"][0].update(status="failed", reason="范围外失败")  # 排除历史失败
    note = make_note(root, "notes/第二讲.md")  # 为范围内完成集准备真实可通过门禁的文件
    data["items"][1].update(status="done", note=note.relative_to(root).as_posix())  # 范围内至少一集完成
    data["items"][2].update(status="failed" if failed else "done", note=note.relative_to(root).as_posix(), reason="目标失败", next_step="检查后补跑", rerun="重跑第三集")  # 精确检查失败详情
    cp.save_course(root, data)  # 持久化本轮结束状态
    if size:  # 无缓存场景不创建视频目录
        folder = cache_folder(root)  # 媒体只位于课程缓存目录
        (folder / ".audio_1.m4s").write_bytes(b"x" * size)  # 已知临时缓存无需元数据
    result = invoke(capsys, "next", root)  # 真正触发结束分支
    assert result["status"] == "finished" and result["done"] == (1 if failed else 2) and result["failed"] == int(failed)  # 完成失败不能误用总课程
    assert result["failed_items"] == ([3] if failed else [])  # 返回失败集序号保留现有协议
    assert Path(result["index_file"]) == (root / "课程索引.md").resolve() and Path(result["index_file"]).is_file()  # 回执前已写索引
    assert Path(result["notes_dir"]) == (root / "notes").resolve()  # 笔记位置始终使用绝对路径
    assert result["cache"] == {"files": int(bool(size)), "bytes": size}  # 统计真实可清理媒体数量与字节
    text = result["receipt_text"]  # 验证可直接转述给用户的文案
    assert text.startswith(f"课程《离线 | 课程》本次范围 2 集，完成 {result['done']} 篇笔记，失败 {int(failed)} 集。") and str(root.resolve()) in text  # 首句说明课程范围结果并定位产物
    assert ("失败集补跑" in text) is failed and "范围外失败" not in text  # 没失败就省略整段且不夹带历史失败
    if failed:  # 每个本轮失败都给出恢复所需信息
        assert all(value in text for value in ("第 3 集", "目标失败", "检查后补跑", "重跑第三集"))  # 不能只报失败数
    if size:  # 采用一位小数并明确询问用户
        assert text.endswith("缓存的视频和音频共 1.5 MB，要清理吗？（清理不影响笔记和转写稿）")  # 询问本身不触发删除
        assert (folder / ".audio_1.m4s").stat().st_size == size  # 回执与干跑都不删除文件
    else:  # 没有缓存时不发起无意义询问
        assert text.endswith("没有需要清理的缓存。")  # 零字节结尾严格符合要求


def test_clean_reuses_collect_all_folders_preserves_artifacts_and_reports_bytes(course, monkeypatch, capsys):  # 媒体清理只作用于可靠候选
    root, data = course  # 清理不依赖执行结束状态
    first, second = cache_folder(root), cache_folder(root, "BV2offline")  # 遍历多个视频目录
    media = first / "音频.m4a"  # 元数据引用的真实媒体
    media.write_bytes(b"a" * 7)  # 精确控制统计字节
    temp = second / ".video_2.m4s"  # 已知临时媒体无需元数据
    temp.write_bytes(b"b" * 11)  # 第二目录候选必须被收集
    partial = second / "download.part"  # 复用现有任意分片临时文件规则
    partial.write_bytes(b"c" * 3)  # 分片计入总量
    products = [first / "字幕.txt", first / "笔记.md", first / "subtitle.txt.done", first / "metadata.json", first / "字幕.txt.part", first / "metadata.json.part"]  # 元数据恶意指定产物也不能删除
    for path in products:  # 准备所有受保护产物
        path.write_text("保留", encoding="utf-8")  # 各文件必须真实存在
    orphan = first / "未引用.mp4"  # 不扩展成全目录媒体扫描
    orphan.write_bytes(b"safe")  # 只有收集器认识的候选才清理
    media_metadata(first, [media.name, *(path.name for path in products)])  # 元数据故意引用自身及多种文本
    media_metadata(second, [])  # 第二目录仅使用临时规则
    calls = []  # 验证复用而非重写收集逻辑
    original = clean_media.collect  # 保存现有收集函数
    def collect(folder):  # 记录每个视频子目录
        calls.append(folder)  # 返回结果仍由现有实现决定
        return original(folder)  # 使用真实候选规则
    monkeypatch.setattr(clean_media, "collect", collect)  # 验证要求指定的复用边界
    result = invoke(capsys, "clean", root)  # 默认干跑
    assert result["files"] == 3 and result["bytes"] == 21 and result["deleted"] == 0  # 仅统计实际可删除候选
    assert {Path(path) for path in result["paths"]} == {media, temp, partial}  # 干跑返回完整候选清单供调用方确认
    assert set(calls) == {first, second} and all(path.exists() for path in [media, temp, partial, *products])  # 干跑不删除且遍历全部视频目录
    result = invoke(capsys, "clean", root, "--apply")  # 显式请求才逐一删除
    assert result["deleted"] == 3 and result["freed_bytes"] == 21  # 返回真实删除数量及字节
    assert not any(path.exists() for path in (media, temp, partial))  # 媒体缓存已经清理
    assert orphan.exists() and all(path.exists() for path in products)  # 所有文本及元数据产物均被保留
    assert invoke(capsys, "clean", root)["files"] == 0  # 清理后再次干跑为空


def test_clean_deduplicates_resolved_references_and_protects_recorded_notes(course):  # 点点别名不重复统计且笔记不因异常扩展名被删除
    root, data = course  # 使用真实课程清单中的产物路径
    folder = cache_folder(root)  # 所有候选都处于合法视频边界内
    (folder / "sub").mkdir()  # 让点点别名经过真实存在的目录
    media = folder / "缓存.mp4"  # 同一文件由多个media_path引用
    media.write_bytes(b"media")  # 缓存统计必须只计算这五个字节
    note, draft = folder / "正式笔记.mp4", folder / "草稿.m4a"  # 即使扩展名不正常也要保护已分配笔记
    note.write_bytes(b"note")  # 模拟已有正式产物
    draft.write_bytes(b"draft")  # 模拟中断后需要恢复的草稿
    data["items"][0].update(note=note.relative_to(root).as_posix(), planned_note=draft.relative_to(root).as_posix())  # 由清单而非文件扩展名识别笔记身份
    cp.save_course(root, data)  # 保存受保护路径供清理接口读取
    media_metadata(folder, [media.name, "sub/../缓存.mp4", note.name, draft.name])  # 同时覆盖同一媒体的别名及伪装成媒体的笔记
    result = cp.clean(root, apply=True)  # 整批预检后只允许删除一次真实媒体
    assert result["files"] == result["deleted"] == 1 and result["freed_bytes"] == result["bytes"] == 5  # 去重后统计和实际删除严格一致
    assert note.read_bytes() == b"note" and draft.read_bytes() == b"draft" and not media.exists()  # 正式笔记及草稿内容保持完整


@pytest.mark.parametrize("relative", [False, True])  # 目录外绝对路径和点点逃逸均明确拒绝
@pytest.mark.parametrize("apply", [False, True])  # 干跑与实际清理采用同一安全规则

def test_clean_refuses_escape_and_preflights_entire_batch(course, tmp_path, relative, apply):  # 后一个候选违规不能先删前一个合法候选
    root, data = course  # 当前课程根目录
    valid_folder = cache_folder(root, "AAA")  # 排在恶意视频之前
    valid = valid_folder / ".audio_1.m4s"  # 合法媒体候选
    valid.write_bytes(b"valid")  # 预检失败必须完整保留
    bad_folder = cache_folder(root, "ZZZ")  # 后续目录注入逃逸引用
    outside = tmp_path / "外部.mp4"  # 真实存在的目录外文件
    outside.write_bytes(b"outside")  # 不允许任何形式删除
    media_metadata(bad_folder, ["../../../外部.mp4" if relative else outside.resolve()])  # 元数据使用两种逃逸路径
    with pytest.raises(cp.HintError, match="拒绝|越界|目录外") as exc:  # 不能静默跳过或冒泡路径异常
        cp.clean(root, apply=apply)  # 调用业务接口测试整批预检
    assert "videos" in str(exc.value) and valid.read_bytes() == b"valid" and outside.read_bytes() == b"outside"  # 明确边界并且没有部分删除


@pytest.mark.parametrize("target", ["videos", "folder", "media"])  # 覆盖整个视频根目录子目录及文件符号链接

def test_clean_refuses_symlink_escape_before_any_deletion(course, tmp_path, target):  # Windows无权限创建符号链接时允许跳过
    root, data = course  # 当前安全边界
    outside = tmp_path / "外部缓存"  # 不属于课程的真实目录
    outside.mkdir()  # 只在测试临时目录创建
    external = outside / ".audio_external.m4s"  # 外部媒体必须始终存在
    external.write_bytes(b"external")  # 保存实际内容用于安全断言
    try:  # Windows开发者模式或权限不足时跳过
        if target == "videos":  # 整个缓存根目录不能重定向到外部
            (root / "videos").symlink_to(outside, target_is_directory=True)  # 构造根目录逃逸
        elif target == "folder":  # 单个视频目录也不能逃逸
            (root / "videos").mkdir()  # 准备内部父目录
            (root / "videos" / "BVexternal").symlink_to(outside, target_is_directory=True)  # 收集前就必须拒绝
        else:  # 缓存文件本身可能链接到外部媒体
            folder = cache_folder(root)  # 使用合法视频目录
            (folder / ".audio_link.m4s").symlink_to(external)  # resolve后明确越界
    except (OSError, NotImplementedError):  # 平台不支持时不伪造测试成功
        pytest.skip("当前平台无法创建符号链接")  # 按用户要求允许Windows跳过
    with pytest.raises(cp.HintError, match="拒绝|越界|目录外"):  # 必须拒绝而不是删除链接或目标
        cp.clean(root, apply=True)  # 预检覆盖符号链接
    assert external.read_bytes() == b"external"  # 外部媒体及内容完整保留


def test_clean_empty_and_zero_byte_cache_receipt(course, capsys):  # 空目录以及零字节缓存都不要求用户清理
    root, data = course  # 未产生任何视频文件
    assert invoke(capsys, "clean", root)["bytes"] == 0  # 不需要强制先抓取视频
    folder = cache_folder(root)  # 后续只生成空缓存
    (folder / ".audio_empty.m4s").touch()  # 文件数与字节数明确区分
    note = make_note(root, "notes/完成笔记.md")  # 为所有已完成集保留真实门禁输入
    for item in data["items"]:  # 所有集完成使下一步返回回执
        item.update(status="done", note=note.relative_to(root).as_posix())  # PR2重检可以验证真实文件
    cp.save_course(root, data)  # 保存结束状态
    result = cp.course_next(root)  # 使用与clean相同的干跑统计
    assert result["cache"] == {"files": 1, "bytes": 0} and result["receipt_text"].endswith("没有需要清理的缓存。")  # 零字节以字节量而非文件数决定询问
