# -*- coding: utf-8 -*-
"""note_nav.py v3 模式的离线测试：时间索引填链 / 幂等 / 不配对不改文件 / 分P归属 /
抖音、小红书纯文本 / YouTube 深链 / 不支持的模板版本报错（v4 见 test_note_nav_v4.py）。"""
import hashlib
import json

import note_nav

META = {
    "platform": "bilibili",
    "bvid": "BV1xx411c7mD",
    "pages": [{"page": 1, "part": "P1", "cid": 1, "duration": 600},
              {"page": 2, "part": "P2", "cid": 2, "duration": 600}],
}

DOUYIN_META = {"platform": "douyin", "video_id": "7123456789",
               "pages": [{"page": 1, "part": "视频", "duration": 60}]}

# v3 笔记：正文去时间戳，文末 <details> 内由模型写好 NAV 标记对与无链接条目
V3_NOTE = """---
template_version: v3
note_type: lecture
---

# 课堂笔记

（v3 正文：全面去时间戳）

<details>
<summary>视频时间索引</summary>

<!-- NAV:BEGIN 由 scripts/note_nav.py 生成，请勿手工编辑 -->
- [00:05] 1. 开场与课程介绍
- [08:45] 2. 控制反转与容器
- [00:22] 3. ApplicationContext 预实例化
<!-- NAV:END -->

</details>
"""


def setup_files(tmp_path, note=None, meta=META):
    (tmp_path / "note.md").write_text(V3_NOTE if note is None else note, encoding="utf-8")  # 默认用 v3 笔记夹具
    (tmp_path / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False),
                                            encoding="utf-8")
    txt = tmp_path / "txt"
    txt.mkdir(exist_ok=True)
    (txt / "01_P1.txt").write_text("[00:05] 开场\n[08:45] 控制反转\n",
                                   encoding="utf-8")
    (txt / "02_P2.txt").write_text("[00:22] ApplicationContext 预实例化\n",
                                   encoding="utf-8")
    return tmp_path


def run(tmp_path, meta_name="metadata.json", note_name="note.md"):
    return note_nav.main([str(tmp_path / note_name),
                          "--meta", str(tmp_path / meta_name),
                          "--txt-dir", str(tmp_path / "txt")])


def read(tmp_path, note_name="note.md"):
    return (tmp_path / note_name).read_text(encoding="utf-8")


def digest(tmp_path, note_name="note.md"):
    return hashlib.sha256((tmp_path / note_name).read_bytes()).hexdigest()


def test_missing_bvid_for_bilibili_is_error(tmp_path):
    setup_files(tmp_path, meta={"platform": "bilibili", "pages": []})
    assert run(tmp_path) == 1


# ---------- v3 模式：文末时间索引条目机械填链 ----------

def test_v3_fill_links_single_and_multipage(tmp_path, capsys):
    setup_files(tmp_path, note=V3_NOTE)
    assert run(tmp_path) == 0
    text = read(tmp_path)
    block = text.split("<!-- NAV:BEGIN")[1]
    # 单P（P1）与跨P（P2）条目均填链，t 为分P内相对秒
    assert ("- [00:05](https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=5)"
            " 1. 开场与课程介绍") in block
    assert ("- [08:45](https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=525)"
            " 2. 控制反转与容器") in block
    assert ("- [00:22](https://www.bilibili.com/video/BV1xx411c7mD?p=2&t=22)"
            " 3. ApplicationContext 预实例化") in block
    # 条目原位重写：不排序、不增删行，标记对与 <details> 外部结构原样保留
    assert block.index("00:05") < block.index("08:45") < block.index("00:22")
    assert text.startswith(V3_NOTE.split("<!-- NAV:BEGIN")[0])
    assert "</details>" in text
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary == {"status": "ok", "mode": "v3", "written": 3, "skipped": 0,
                       "platform": "bilibili", "note_type": "lecture"}


def test_v3_multipage_relative_seconds(tmp_path):
    """多P：t 必须是分P内相对秒（U1 语义），而非全片累计秒。"""
    note = ("---\ntemplate_version: v3\n---\n\n# 笔记\n\n"
            "<!-- NAV:BEGIN 由脚本生成 -->\n- [00:40] P2 中段\n<!-- NAV:END -->\n")
    setup_files(tmp_path, note=note)
    (tmp_path / "txt" / "02_P2.txt").write_text("[00:40] P2 中段\n",
                                               encoding="utf-8")
    assert run(tmp_path) == 0
    block = read(tmp_path).split("<!-- NAV:BEGIN")[1]
    assert ("- [00:40](https://www.bilibili.com/video/BV1xx411c7mD?p=2&t=40)"
            " P2 中段") in block
    assert "?p=1" not in block  # [00:40] 只在 P2 命中，不得误归属 P1


def test_v3_idempotent_second_run_byte_identical(tmp_path, capsys):
    setup_files(tmp_path, note=V3_NOTE)
    run(tmp_path)
    first = (tmp_path / "note.md").read_bytes()
    assert run(tmp_path) == 0
    second = (tmp_path / "note.md").read_bytes()
    assert first == second  # 已带链接条目跳过，二次运行不改文件
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["status"] == "ok" and summary["mode"] == "v3"
    assert summary["written"] == 0 and summary["skipped"] == 0


def test_v3_skips_already_linked_entries(tmp_path):
    """混合块：已带链接条目原样保留，只填无链接条目。"""
    note = ("---\ntemplate_version: v3\n---\n\n# 笔记\n\n"
            "<!-- NAV:BEGIN 由脚本生成 -->\n"
            "- [00:05](https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=5) 已填\n"
            "- [00:22] 未填\n"
            "<!-- NAV:END -->\n")
    setup_files(tmp_path, note=note)
    assert run(tmp_path) == 0
    block = read(tmp_path).split("<!-- NAV:BEGIN")[1]
    assert ("- [00:05](https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=5) 已填"
            in block)
    assert ("- [00:22](https://www.bilibili.com/video/BV1xx411c7mD?p=2&t=22) 未填"
            in block)


def test_v3_douyin_keeps_plain_text_without_warning(tmp_path, capsys):
    setup_files(tmp_path, note=V3_NOTE, meta=DOUYIN_META)
    assert run(tmp_path) == 0
    captured = capsys.readouterr()
    assert captured.err == ""  # 抖音不填链属正常，不告警
    block = read(tmp_path).split("<!-- NAV:BEGIN")[1]
    assert "http" not in block
    assert "- [00:05] 1. 开场与课程介绍" in block  # 条目保持纯文本
    summary = json.loads(captured.out.strip().splitlines()[-1])
    assert summary["platform"] == "douyin" and summary["written"] == 0


def test_v3_short_note_type_skips(tmp_path, capsys):
    note = "---\ntemplate_version: v3\nnote_type: short\n---\n\n# 笔记\n"
    setup_files(tmp_path, note=note)
    before = digest(tmp_path)
    assert run(tmp_path) == 0
    assert digest(tmp_path) == before  # short 维持现有跳过行为，文件不动
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["status"] == "skipped"


def test_v3_missing_markers_warn_and_no_write(tmp_path, capsys):
    note = "---\ntemplate_version: v3\n---\n\n# 笔记\n\n正文无 NAV 标记。\n"
    setup_files(tmp_path, note=note)
    before = digest(tmp_path)
    assert run(tmp_path) == 0  # 存在性由门禁管，脚本正常退出
    captured = capsys.readouterr()
    assert "NAV" in captured.err  # stderr warning
    assert digest(tmp_path) == before  # 不改文件
    summary = json.loads(captured.out.strip().splitlines()[-1])
    assert summary["status"] == "skipped" and summary["written"] == 0


def test_v3_empty_entry_area_warn_and_no_write(tmp_path, capsys):
    note = ("---\ntemplate_version: v3\n---\n\n# 笔记\n\n"
            "<!-- NAV:BEGIN 由脚本生成 -->\n<!-- NAV:END -->\n")
    setup_files(tmp_path, note=note)
    before = digest(tmp_path)
    assert run(tmp_path) == 0
    captured = capsys.readouterr()
    assert "条目区为空" in captured.err
    assert digest(tmp_path) == before
    summary = json.loads(captured.out.strip().splitlines()[-1])
    assert summary["status"] == "skipped"


def test_v3_entry_without_matching_page_warned_and_kept(tmp_path, capsys):
    note = ("---\ntemplate_version: v3\n---\n\n# 笔记\n\n"
            "<!-- NAV:BEGIN 由脚本生成 -->\n- [99:99] 凭空章节\n<!-- NAV:END -->\n")
    setup_files(tmp_path, note=note)
    assert run(tmp_path) == 0
    captured = capsys.readouterr()
    assert "未找到归属分P" in captured.err
    block = read(tmp_path).split("<!-- NAV:BEGIN")[1]
    assert "- [99:99] 凭空章节" in block  # 该行保持原样（不填链）
    summary = json.loads(captured.out.strip().splitlines()[-1])
    assert summary["skipped"] == 1 and summary["written"] == 0


def test_v3_unpaired_markers_error_and_no_write(tmp_path):
    note = ("---\ntemplate_version: v3\n---\n\n# 笔记\n\n"
            "<!-- NAV:BEGIN 由脚本生成 -->\n- [00:05] 开场\n")  # 缺 END
    setup_files(tmp_path, note=note)
    before = digest(tmp_path)
    assert run(tmp_path) == 1
    assert digest(tmp_path) == before


def test_unsupported_template_version_error_and_no_write(tmp_path, capsys):
    """frontmatter 写 v2 等已移除的版本 → 报错退出 1，文件不改。"""
    note = "---\ntemplate_version: v2\nnote_type: lecture\n---\n\n# 笔记\n"  # v2 老笔记
    setup_files(tmp_path, note=note)  # 准备文件
    before = digest(tmp_path)  # 运行前指纹
    assert run(tmp_path) == 1  # 报错退出
    assert digest(tmp_path) == before  # 文件不变
    assert "不支持的模板版本" in capsys.readouterr().err  # 提示原因


# ---------- YouTube：单分P，watch?v=<id>&t=<秒>s 深链 ----------

YT_META = {"platform": "youtube", "video_id": "aircAruvnKk",  # YouTube 元数据（用 video_id）
           "pages": [{"page": 1, "part": "视频", "duration": 1120}]}  # 单分P


def test_v3_youtube_deep_link(tmp_path, capsys):
    setup_files(tmp_path, note=V3_NOTE, meta=YT_META)  # 复用 v3 笔记夹具
    assert run(tmp_path) == 0  # 正常退出
    block = read(tmp_path).split("<!-- NAV:BEGIN")[1]  # 取 NAV 区
    assert ("- [00:05](https://www.youtube.com/watch?v=aircAruvnKk&t=5s) 1. 开场与课程介绍"
            in block)  # 生成 YouTube 深链
    assert "bilibili.com" not in block  # 不残留 B站链接
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])  # 摘要
    assert summary["platform"] == "youtube"  # 平台透传


def test_deep_link_helper():
    assert (note_nav.deep_link("youtube", "aircAruvnKk", 1, 65.4)
            == "https://www.youtube.com/watch?v=aircAruvnKk&t=65s")  # YouTube 格式，秒数四舍五入
    assert (note_nav.deep_link("bilibili", "BV1xx411c7mD", 2, 22)
            == "https://www.bilibili.com/video/BV1xx411c7mD?p=2&t=22")  # B站格式不变


# ---------- 小红书：与抖音一致，无深链保持纯文本 ----------

XHS_META = {"platform": "xiaohongshu", "video_id": "6a963ece000000001203cfb4",  # 小红书元数据
            "pages": [{"page": 1, "part": "视频", "duration": 1979}]}  # 单分P


def test_v3_xiaohongshu_keeps_plain_text(tmp_path, capsys):
    setup_files(tmp_path, note=V3_NOTE, meta=XHS_META)  # 复用 v3 笔记夹具
    assert run(tmp_path) == 0  # 正常退出
    captured = capsys.readouterr()  # 捕获输出
    assert captured.err == ""  # 无深链属正常，不告警
    block = read(tmp_path).split("<!-- NAV:BEGIN")[1]  # 取 NAV 区
    assert "http" not in block  # 不生成任何链接
    assert "- [00:05] 1. 开场与课程介绍" in block  # 条目保持纯文本
    assert json.loads(captured.out.strip().splitlines()[-1])["platform"] == "xiaohongshu"  # 平台透传
