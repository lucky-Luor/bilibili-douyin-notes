import json  # 构造目标分集元数据
import subprocess  # 验证真实命令行参数
import sys  # 使用当前隔离解释器
import pytest  # 参数化术语边界
import validate_note as vn  # 被测门禁模块
from test_validate_note_v4 import GOOD, make_env, ROOT  # 复用合规笔记与离线环境
from test_validate_obsidian import OBS  # 复用真实黑曜石元信息与维基目录


def term_warnings(report):  # 仅筛选新增术语告警
    return [item for item in report["warnings"] if item["check"] == "V4-TERM"]  # 保持既有规则独立


def check(tmp_path, text=GOOD, source_lang="en", pages=None, note_type=None):  # 构造最小语言门禁环境
    note, meta, txt = make_env(tmp_path, text)  # 文件全部写入临时测试目录
    if pages is not None:  # 需要覆盖元数据语言时追加分集
        data = json.loads(meta.read_text(encoding="utf-8"))  # 保留样例其它字段
        data["pages"] = pages  # 测试目标分集选择
        meta.write_text(json.dumps(data), encoding="utf-8")  # 保存隔离元数据
    return vn.check_note_dispatch(text, note, meta, txt, 13, "v4", note_type, source_lang)  # 尾部可选参数兼容


def test_missing_original_and_first_mention_warn_only(tmp_path):  # 外语讲义两个中文卡都要求原文
    report = check(tmp_path)  # 合规旧笔记缺少术语原文
    warnings = term_warnings(report)  # 筛选新增规则
    assert report["ok"] and len(warnings) == 3  # 两卡缺原文且正文仅出现活动图因此共三条提示
    assert sum("原文" in item["message"] and "概念卡" in item["message"] for item in warnings) >= 2  # 告警指明卡片


@pytest.mark.parametrize("lang,nt", [(None, None), ("zh", None), ("zh-Hans", None), ("en", "short")])  # 未知中文与短笔记不检查
def test_terms_only_foreign_lecture(tmp_path, lang, nt):  # 新规则严格限定适用范围
    assert not term_warnings(check(tmp_path, source_lang=lang, note_type=nt))  # 不产生额外提示


@pytest.mark.parametrize("mention", ["活动图（Activity Diagram）", "**活动图（Activity Diagram）**", "**活动图**（Activity Diagram）", "活动图(Activity Diagram)"])  # 普通与粗体写法均支持
def test_valid_original_and_first_body_mention(tmp_path, mention):  # 不把目录标题或概念卡抢作正文首次
    text = GOOD.replace("### 活动图\n", "### 活动图\n+ 原文：Activity Diagram\n").replace("### 颜色语义\n", "### 颜色语义\n+ 原文：Color Semantics\n")  # 卡片增加原文
    text = text.replace("用例是散的，描述协作与先后顺序需要活动图。", "用例是散的，" + mention + "用于描述协作。")  # 正文首次紧跟原文
    text = text.replace("## 2. 活动图的颜色语义\n", "## 2. 活动图的颜色语义\n").replace("action 的语义由使用者定义", "颜色语义（Color Semantics）由使用者定义")  # 第二个中文概念首次标注
    assert not term_warnings(check(tmp_path, text))  # 目录与编号标题不算正文叙述


@pytest.mark.parametrize("suffix", ["", "（）", "（  ）", "( )", " 后面才补（Activity Diagram）"])  # 非空且紧跟要求
def test_empty_or_late_original_warns(tmp_path, suffix):  # 原文缺失及延后不能掩盖首次出现
    text = GOOD.replace("### 活动图\n", "### 活动图\n+ 原文：Activity Diagram\n")  # 卡片字段合规
    text = text.replace("需要活动图。", "需要活动图" + suffix + "。")  # 首次出现缺少紧邻原文
    warnings = term_warnings(check(tmp_path, text))  # 运行新规则
    assert any("首次" in item["message"] and "活动图" in item["message"] for item in warnings)  # 给出正文首次提示


def test_english_card_does_not_require_original(tmp_path):  # 纯英文概念无需重复保留原文
    text = GOOD.replace("活动图", "Activity Diagram").replace("颜色语义", "Color Semantics")  # 全英文卡名
    assert not term_warnings(check(tmp_path, text))  # 不要求额外原文字段


@pytest.mark.parametrize("page,explicit,want", [  # 显式值与目标分集字段优先级
    ({"asr_lang": "en", "audio_lang": "zh", "subtitle_lang": "zh"}, None, True),  # 实际识别优先
    ({"asr_lang": None, "audio_lang": "en", "subtitle_lang": "zh"}, None, True),  # 音频优先字幕
    ({"subtitle_lang": "ja"}, None, True),  # 字幕最终兜底
    ({"audio_lang": "en"}, "zh-CN", False),  # 显式中文覆盖元数据
    ({"audio_lang": "zh"}, "en", True),  # 显式外语覆盖元数据
    ({}, None, False),  # 无字段不推断其它分集语言
])  # 参数表结束
def test_target_page_language_precedence(tmp_path, page, explicit, want):  # 不能使用第一分集代替目标分集
    pages = [{"page": 1, "audio_lang": "en"}, {"page": 13, **page}]  # 目标为第十三页
    assert bool(term_warnings(check(tmp_path, source_lang=explicit, pages=pages))) is want  # 精确优先级


def test_obsidian_propagates_source_language(tmp_path):  # 真实变体必须保留显式来源语言与原文件坐标
    note, meta, txt = make_env(tmp_path, OBS)  # 复用完整有效黑曜石讲义
    report = vn.check_note_dispatch(OBS, note, meta, txt, only_page=13, template="v4-obsidian", note_type="short", source_lang="en")  # 元信息讲义类型仍优先且语言传入变体
    warnings = term_warnings(report)  # 只筛选术语提示
    assert report["ok"] and report["note_type"] == "lecture" and len(warnings) == 3  # 两条缺字段及一条正文首次提示
    first = next(item for item in warnings if "首次" in item["message"])  # 检查正文坐标已补元信息偏移
    assert "需要活动图" in OBS.splitlines()[first["line"] - 1]  # 必须定位原始文件而非剥离后的正文
    direct = vn.check_note_v4(OBS, note, meta, txt, 13, None, "obsidian", "en")  # 新旧尾部参数可以同时使用
    assert len(term_warnings(direct)) == 3  # 直接变体与分派行为一致


def test_direct_v4_optional_tail_and_cli(tmp_path):  # 直接接口与命令行都转发语言
    note, meta, txt = make_env(tmp_path)  # 临时合规笔记
    assert len(term_warnings(vn.check_note_v4(GOOD, note, meta, txt, 13, None, None, "en"))) == 3  # 保留变体参数位置并在尾部传递来源语言
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_note.py"), str(note), "--source-lang", "en"], capture_output=True, text=True, encoding="utf-8")  # 仅使用本地笔记
    assert proc.returncode == 0 and len(term_warnings(json.loads(proc.stdout))) == 3  # 告警不改变通过退出码
