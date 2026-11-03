import copy  # 比较元数据更新范围
import io  # 模拟可重新配置的标准流
import json  # 解析机器回执和元数据
import socket  # 强制测试离线
import sys  # 模拟命令行参数和标准流
from types import SimpleNamespace  # 提供轻量模型返回值
import pytest  # 使用隔离夹具和参数化用例
import apply_glossary  # 屏蔽真实术语文件读取
import bili_fetch  # 观察共享原子写入边界
import bili_transcribe as bt  # 调用真实转写入口


@pytest.fixture(autouse=True)  # 所有测试禁止真实网络和配置读取
def offline(monkeypatch):  # 安装可控的模型和外部边界
    def blocked(*args, **kwargs):  # 意外联网立即失败
        raise AssertionError("离线测试禁止网络请求")  # 明确隔离失败原因
    monkeypatch.setattr(socket.socket, "connect", blocked)  # 封锁底层网络
    monkeypatch.setattr(bt, "load_sessdata", lambda: "")  # 不读真实登录配置
    monkeypatch.setattr(bt, "_cfg", lambda key, default: default)  # 不读真实模型配置
    monkeypatch.setattr(apply_glossary, "top_terms", lambda **kwargs: [])  # 不读用户词表
    state = {"model_used": "small", "device_used": "cpu", "batched": False, "fallback_reasons": []}  # 模拟共享模型缓存
    def transcriber(audio, language, prompt):  # 保留真实转写函数及语言信息流
        return iter([SimpleNamespace(start=1, text=" 实际转写内容 ")]), SimpleNamespace(language=language or "en")  # 自动语言来自模型信息
    state["transcriber"] = transcriber  # 复用模型但不保存每集语言
    monkeypatch.setattr(bt, "load_model", lambda *args: state)  # 避免加载重型依赖
    monkeypatch.setattr(bt, "_MODEL_STATE", state)  # 保持进度字段和加载状态一致
    monkeypatch.setattr(bt, "download_audio", lambda bvid, cid, dest, cookie: dest.write_bytes(b"audio"))  # 离线落盘音频
    return state  # 允许用例改变模型行为


def metadata(tmp_path, count=1):  # 构造独立完整的临时元数据
    path = tmp_path / "metadata.json"  # 元数据只在测试临时目录生成
    pages = [dict(page=index, part=f"第{index}集", cid=100 + index, duration=60, subtitle_detail="none") for index in range(1, count + 1)]  # 创建稳定的分集身份
    path.write_text(json.dumps(dict(bvid="BVoffline", title="中文标题", pages=pages, extra={"keep": True}), ensure_ascii=False), encoding="utf-8")  # 保留无关字段供更新范围检查
    return path  # 返回入口所需文件路径


def invoke(monkeypatch, capsys, path, *options):  # 将直接调用转换为可观察的进程退出语义
    monkeypatch.setattr(sys, "argv", ["bili_transcribe.py", str(path), *options])  # 使用真实参数解析
    code = 0  # 正常返回即进程成功
    try:  # 主入口允许显式退出
        bt.main()  # 执行真实文件写入与错误汇总
    except SystemExit as exc:  # 捕获退出码供断言
        code = exc.code  # 不把失败出口吞成成功
    captured = capsys.readouterr()  # 收集每集机器回执
    return code, [json.loads(line) for line in captured.out.splitlines()]  # 每行必须是有效JSON


@pytest.mark.parametrize("stage", ["download", "transcribe", "text", "marker", "metadata"])  # 每个持久化阶段失败都应影响退出码
def test_failure_exits_one_and_continues(tmp_path, monkeypatch, capsys, stage):  # 首集失败后继续处理第二集
    path = metadata(tmp_path, 2)  # 多集验证累计失败而非立即退出
    def fail(*args, **kwargs):  # 只让首集在指定阶段失败一次
        raise RuntimeError("模拟首集失败")  # 错误文案必须保留
    if stage == "download":  # 下载边界
        original = bt.download_audio  # 第二集仍执行离线下载
        monkeypatch.setattr(bt, "download_audio", lambda bvid, cid, dest, cookie: fail() if cid == 101 else original(bvid, cid, dest, cookie))  # 故障仅注入首集
    elif stage == "transcribe":  # 模型边界
        original = bt.transcribe  # 保留真实模型返回路径
        monkeypatch.setattr(bt, "transcribe", lambda audio, *args, **kwargs: fail() if audio.name == ".audio_01.m4s" else original(audio, *args, **kwargs))  # 第二集继续转写
    else:  # 文件写入边界
        target = bili_fetch if stage == "metadata" else bt  # 元数据必须走公共原子写入函数
        name = "mark_done" if stage == "marker" else "atomic_write_text"  # 按对应边界注入
        original = getattr(target, name)  # 保留后续写入实现
        calls = []  # 跟踪首轮故障
        def write(*args, **kwargs):  # 首次失败后恢复
            calls.append(args)  # 记录边界调用
            return fail() if len(calls) == 1 else original(*args, **kwargs)  # 每集故障不得影响后续执行
        monkeypatch.setattr(target, name, write)  # 安装文件故障
    code, records = invoke(monkeypatch, capsys, path)  # 使用入口及完整错误处理
    assert code == 1  # 单集错误最终必须返回非零
    assert [record["status"] for record in records] == ["error", "ok"]  # 失败不阻断第二集
    assert records[0]["reason"] and records[0]["next_step"] and records[0]["rerun"]  # 保留可补跑信息
    assert not list(tmp_path.glob(".audio_*.m4s"))  # 成功失败均清理临时音频
    if stage != "marker":  # 完成标记前元数据尚未成功写入时不得泄漏语言
        saved = json.loads(path.read_text(encoding="utf-8"))  # 从第二集最终写入结果观察首集隔离
        assert "asr_lang" not in saved["pages"][0]  # 失败页不得随下一集成功顺带落盘


@pytest.mark.parametrize("lang,expected", [("zh", "zh"), ("auto", "en")])  # 自动语言不能写成auto或沿用默认中文
def test_success_updates_only_target_language_atomically(tmp_path, monkeypatch, capsys, lang, expected):  # 元数据语言和原子持久化必须可观察
    path = metadata(tmp_path, 2)  # 仅处理第二页验证不会污染其它页
    before = json.loads(path.read_text(encoding="utf-8"))  # 保存完整输入
    calls = []  # 记录公共原子写入调用
    original = bili_fetch.atomic_write_text  # 仍执行真实原子替换
    def write(target, text):  # 在公共边界观察元数据
        calls.append(target)  # 目标必须是原元数据路径
        return original(target, text)  # 保留生产落盘语义
    monkeypatch.setattr(bili_fetch, "atomic_write_text", write)  # 验证复用指定公共工具
    code, records = invoke(monkeypatch, capsys, path, "--page", "2", "--lang", lang)  # 真实入口执行单页
    expected_meta = copy.deepcopy(before)  # 只允许增加目标字段
    expected_meta["pages"][1]["asr_lang"] = expected  # 实际语言写入对应页
    assert code == 0 and records[0]["status"] == "ok"  # 正常完成成功退出
    assert json.loads(path.read_text(encoding="utf-8")) == expected_meta  # 其它元数据完全不变
    assert calls == [path]  # 成功必须通过公共原子写一次元数据
    assert (tmp_path / "02_第2集.txt.done").is_file()  # 完成标记真实落盘


def test_elapsed_uses_monotonic_download_through_marker(tmp_path, monkeypatch, capsys):  # 精确覆盖计时区间和一位小数
    path = metadata(tmp_path)  # 使用下载而非已有媒体路径
    clock = SimpleNamespace(value=10.0)  # 模拟可移动单调时钟
    monkeypatch.setattr(bt.time, "monotonic", lambda: clock.value)  # 排除墙钟时间依赖
    def download(bvid, cid, dest, cookie):  # 下载时间必须计入
        clock.value += 2.0  # 模拟下载耗时
        dest.write_bytes(b"audio")  # 保留临时文件清理路径
    original = bt.mark_done  # 保留真实完成标记
    def mark(path):  # 完成标记时间必须计入
        original(path)  # 真正写完标记
        clock.value += 1.26  # 模拟写标记耗时
    monkeypatch.setattr(bt, "download_audio", download)  # 使用离线计时下载桩
    monkeypatch.setattr(bt, "mark_done", mark)  # 使用离线计时标记桩
    code, records = invoke(monkeypatch, capsys, path)  # 真实入口累计耗时
    assert code == 0 and records[0]["elapsed_seconds"] == 3.3  # 计时覆盖下载至标记并保留一位小数


def test_nothing_to_do_is_success(tmp_path, monkeypatch, capsys):  # 已完成时不进入模型
    path = metadata(tmp_path)  # 生成可识别的目标
    text = tmp_path / "01_第1集.txt"  # 对应正式文本
    text.write_bytes(b"x" * 100)  # 旧文本达到补标记阈值
    code, records = invoke(monkeypatch, capsys, path)  # 运行实际完成判断
    assert code == 0 and records == [{"status": "nothing_to_do"}]  # 无需处理成功返回
    assert text.with_name(text.name + ".done").is_file()  # 旧文本自动补写标记


def test_orphan_marker_is_not_done_and_is_preserved(tmp_path):  # 孤立标记不能替代正文也不应改名
    marker = tmp_path / "01_第1集.txt.done"  # 正文尚不存在
    marker.write_text("{}", encoding="utf-8")  # 保留异常状态证据
    assert bt.txt_done(tmp_path, dict(page=1, part="第1集")) is False  # 缺正式正文必须重新转写
    assert marker.read_text(encoding="utf-8") == "{}"  # 判据不得修改孤立标记
    assert not list(tmp_path.glob("*.orphan-*"))  # 不再通过改名实现恢复


def test_transcribe_keeps_string_and_per_call_auto_language(tmp_path, offline):  # 复用同一模型的两页语言必须隔离
    languages = iter(["en", "ja"])  # 连续两集真实检测语言不同
    def transcriber(*args):  # 返回每集独立模型信息
        return iter([SimpleNamespace(start=1, text=" 内容 ")]), SimpleNamespace(language=next(languages))  # 检测结果不写模型缓存
    offline["transcriber"] = transcriber  # 复用同一个状态字典
    before = offline.copy()  # 缓存不应增加语言字段
    for language in ("en", "ja"):  # 连续调用相同模型
        result = {}  # 每集独立的可选结果出参
        text = bt.transcribe(tmp_path / "audio", "small", None, result=result)  # 保持原字符串返回协议
        assert text == "[00:01] 内容" and result["asr_lang"] == language  # 语言来自当前模型info
    assert offline == before  # 每集检测结果不能污染跨页缓存


def test_main_reconfigures_both_streams(tmp_path, monkeypatch):  # 中文错误流与标准输出均使用UTF8
    path = metadata(tmp_path)  # 准备正常入口
    class Stream(io.StringIO):  # 内存流记录编码配置
        def reconfigure(self, **options):  # 模拟宿主标准流接口
            self.options = options  # 保留设置供断言
    stdout, stderr = Stream(), Stream()  # 两个流独立观察
    monkeypatch.setattr(sys, "stdout", stdout)  # 安装机器输出流
    monkeypatch.setattr(sys, "stderr", stderr)  # 安装人类错误流
    monkeypatch.setattr(sys, "argv", ["bili_transcribe.py", str(path)])  # 使用默认参数
    bt.main()  # 直接执行完整成功路径
    assert stdout.options["encoding"] == stderr.options["encoding"] == "utf-8"  # 两个流都必须重新配置
