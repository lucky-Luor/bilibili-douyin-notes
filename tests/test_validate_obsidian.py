import json  # 解析命令回执和构造双引号标量
import re  # 转换现有真实有效样例的目录
import subprocess  # 验证真实命令行入口
import sys  # 使用当前指定测试解释器
from pathlib import Path  # 管理临时笔记路径
import pytest  # 参数化错误场景
import note_nav  # 导航被测入口
import validate_note as vn  # 校验被测入口
from test_validate_note_v4 import GOOD, make_env  # 复用完整合规讲义而非简化正文


FM = "\n".join([  # 保留显式顺序以验证原文不重序列化
    "---",  # 起始分隔线
    "template_version: v4-obsidian",  # 模板声明
    'title: "业务流程：建模 # 基础"',  # 含特殊字符时必须引用
    "note_type: lecture",  # 笔记类型
    "platform: bilibili",  # 来源平台
    'source: "https://www.bilibili.com/video/BV1zMpaeTEJa?p=13"',  # 带冒号链接引用
    "duration: 1560",  # 时长标量
    "created: 2026-11-03",  # 有效日期
    "tags:",  # 块列表
    "  - UML",  # 第一标签
    '  - "活动图: 基础"',  # 带冒号标签
    "bvid: BV1zMpaeTEJa",  # 视频标识
    "page: 13",  # 分页标识
    "---",  # 结束分隔线
]) + "\n"  # 正文紧接引语
BODY = re.sub(r"\[([^\]]+)\]\(#[^)]+\)", r"[[#\1]]", GOOD)  # 使用与正文完全一致的维基目录
OBS = FM + BODY  # 完整有效黑曜石样例


def check(tmp_path, text=OBS, **kwargs):  # 统一经公开分发接口测试
    note, meta, txt = make_env(tmp_path, text)  # 临时目录内准备所有输入
    return vn.check_note_dispatch(text, note, meta, txt, only_page=13, **kwargs)  # 默认通过声明自动路由


def test_obsidian_valid_and_direct_variant(tmp_path):  # 完整样例须零错误并复用正文规则
    result = check(tmp_path)  # 自动检测声明
    assert result["ok"] and result["template_version"] == "v4-obsidian", result  # 有效笔记必须通过
    note, meta, txt = make_env(tmp_path, OBS)  # 直接变体入口使用相同输入
    assert vn.check_note_v4(OBS, note, meta, txt, only_page=13, variant="obsidian")["ok"]  # 显式变体也通过


def test_parser_decodes_json_and_list():  # 不借助旧版解析器解析双引号转义
    text = FM.replace('"业务流程：建模 # 基础"', json.dumps('标题 "引号"\n第二行', ensure_ascii=False))  # JSON 转义覆盖
    fm, errors, end_line = vn.parse_frontmatter_obsidian(text + BODY)  # 返回结束分隔线的一基行号
    assert not errors and fm["title"] == '标题 "引号"\n第二行'  # 解码不能直接剥引号
    assert fm["tags"] == ["UML", "活动图: 基础"] and end_line == len(FM.splitlines())  # 列表及正文起点


@pytest.mark.parametrize("old,new", [  # 独立覆盖坏语法与字段约束
    ("created: 2026-11-03", "created: 2026-02-30"),  # 无效日历日期
    ("created: 2026-11-03", "created: 20261103"),  # 非标准日期格式
    ("tags:\n  - UML\n  - \"活动图: 基础\"", "tags: UML"),  # 标量不能充当标签列表
    ("tags:\n  - UML\n  - \"活动图: 基础\"", "tags:"),  # 空标签列表
    ('title: "业务流程：建模 # 基础"', "title: bad: value"),  # 未引用冒号
    ('title: "业务流程：建模 # 基础"', "title: bad#value"),  # 未引用井号
    ('title: "业务流程：建模 # 基础"', "title: [value"),  # 未引用方括号
    ('title: "业务流程：建模 # 基础"', "title: {value"),  # 未引用花括号
    ('title: "业务流程：建模 # 基础"', "title:  leading"),  # 前导空格未引用
    ('title: "业务流程：建模 # 基础"', 'title: "broken\\q"'),  # 错误双引号转义
    ("  - UML", "    - UML"),  # 错误列表缩进
    ("bvid: BV1zMpaeTEJa\n", ""),  # B站缺视频号
    ("page: 13\n", ""),  # B站缺页码
    ("duration: 1560\n", ""),  # 缺必需字段
    ("note_type: lecture", "note_type: unknown"),  # 非法笔记类型
])  # 所有失败均走新检查编号

def test_bad_frontmatter(tmp_path, old, new):  # 每种失败必须定位元信息错误
    result = check(tmp_path, OBS.replace(old, new))  # 只修改目标输入
    assert any(e["check"] == "V4O-FM" for e in result["errors"]), result  # 新版独立错误编号


@pytest.mark.parametrize("text", [BODY, FM[:-4] + BODY, FM + "\n" + BODY])  # 缺元信息未闭合及正文不紧接

def test_explicit_obsidian_requires_closed_frontmatter_and_immediate_quote(tmp_path, text):  # 显式选项仍须完整校验
    result = check(tmp_path, text, template="v4-obsidian")  # 强制新模板
    assert any(e["check"] in ("V4O-FM", "V4-HEAD") for e in result["errors"])  # 不静默回退普通四版


def test_explicit_v4_keeps_frontmatter_ban(tmp_path):  # 原模板显式参数优先
    result = check(tmp_path, template="v4")  # 即使声明黑曜石也强制普通四版
    assert result["template_version"] == "v4" and any(e["check"] == "V4-FM" for e in result["errors"])  # 保持原门禁


def test_fm_note_type_wins_and_conflicts_warn(tmp_path):  # 元信息优先于参数及建议
    result = check(tmp_path, note_type="short")  # 参数与元信息冲突
    assert result["note_type"] == "lecture" and result["ok"]  # 元信息生效
    assert any(e["check"] == "V4O-FM" for e in result["warnings"])  # 冲突可观察


def test_toc_exact_match_and_legacy_warning(tmp_path):  # 维基标题必须保留编号及标点
    bad = OBS.replace("[[#1. 从用例到活动图]]", "[[#从用例到活动图]]")  # 去编号也不能视为完全相同
    assert any(e["check"] == "V4O-TOC" for e in check(tmp_path, bad)["errors"])  # 无效标题报错
    result = check(tmp_path, FM + GOOD)  # 传统目录尚未转换
    assert any(e["check"] == "V4O-TOC" for e in result["warnings"])  # 传统锚点必须提示
    assert any(e["check"] == "V4O-TOC" for e in result["errors"])  # 无维基条目也须拒绝


def test_toc_ignores_fenced_fake_headings(tmp_path):  # 代码中的标题不能作为目录目标
    text = OBS.replace("## 目录", "```text\n## 假标题\n```\n\n## 目录")  # 插入围栏伪标题
    result = check(tmp_path, text)  # 原有目录仍有效
    assert not any(e["check"] == "V4O-TOC" for e in result["errors"])  # 不要求伪标题条目
    result = check(tmp_path, text.replace("+ [[#1. 从用例到活动图]]", "+ [[#假标题]]"))  # 指向伪标题
    assert any(e["check"] == "V4O-TOC" for e in result["errors"])  # 不能把围栏标题当真实目标


def test_body_line_coordinates_in_errors_messages_and_checks(tmp_path):  # 正文全部坐标须包含元信息偏移
    text = OBS.replace("+ 业务流程讲的是", "- [03:12] 业务流程讲的是")  # 同时触发时间戳及列表明细
    line = next(i for i, value in enumerate(text.splitlines(), 1) if "[03:12]" in value)  # 原文件真实行号
    result = check(tmp_path, text)  # 校验输入
    error = next(e for e in result["errors"] if e["check"] == "V4-E1")  # 获取正文错误
    assert error["line"] == line and f"第{line}行" in error["message"]  # 错误字段及嵌入坐标一致
    assert result["checks"]["body_timestamp_ban"]["hits"] == [(line, "[03:12]")]  # 元组坐标正确
    assert result["checks"]["list_marker"]["lines"] == [line]  # 嵌套行号列表正确


def test_youtube_requires_video_id(tmp_path):  # 平台不同必需标识不同
    text = OBS.replace("platform: bilibili", "platform: youtube")  # 缺少油管标识
    assert any(e["check"] == "V4O-FM" and "video_id" in e["message"] for e in check(tmp_path, text)["errors"])  # 不接受仅有站内标识


def test_validate_cli_obsidian(tmp_path):  # 命令行选择新模板真实端到端
    note, meta, txt = make_env(tmp_path, OBS)  # 准备离线输入
    proc = subprocess.run([sys.executable, str(Path(vn.__file__)), str(note), "--meta", str(meta), "--txt-dir", str(txt), "--page", "13", "--template", "v4-obsidian"], capture_output=True, text=True, encoding="utf-8")  # 不经网络运行命令
    assert proc.returncode == 0 and json.loads(proc.stdout)["template_version"] == "v4-obsidian", proc.stdout + proc.stderr  # 参数与报告一致


@pytest.mark.parametrize("explicit", [True, False])  # 显式选择和自动识别均覆盖

def test_nav_preserves_frontmatter_and_idempotence(tmp_path, capsys, explicit):  # 原位填链不重排元信息
    text = OBS.replace("](https://www.bilibili.com/video/BV1zMpaeTEJa?p=13&t=0)", "]").replace("](https://www.bilibili.com/video/BV1zMpaeTEJa?p=13&t=305)", "]")  # 清除索引深链
    note, meta, txt = make_env(tmp_path, text)  # 准备导航输入
    args = [str(note), "--meta", str(meta), "--txt-dir", str(txt), "--page", "13", "--note-type", "short"]  # 参数冲突必须使用元信息
    args += ["--template", "v4-obsidian"] if explicit else []  # 两条分派路径
    assert note_nav.main(args) == 0  # 填链成功
    summary = json.loads(capsys.readouterr().out)  # 获取唯一摘要
    first = note.read_text(encoding="utf-8")  # 观察修改结果
    assert first.startswith(FM) and "?p=13&t=305" in first  # 原文元信息与分P深链正确
    assert summary["mode"] == "v4-obsidian" and summary["note_type"] == "lecture"  # 模式及优先级正确
    assert note_nav.main(args) == 0 and note.read_text(encoding="utf-8") == first  # 重跑严格幂等
