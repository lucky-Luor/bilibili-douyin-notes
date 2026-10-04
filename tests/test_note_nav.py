# -*- coding: utf-8 -*-
"""note_nav.py 的离线测试：幂等替换 / 追加 / 不配对不改文件 / 分P归属 / 抖音纯文本
/ v3 时间索引填链 / v2 老笔记回归。"""
import hashlib
import json

import note_nav

META = {
    "platform": "bilibili",
    "bvid": "BV1xx411c7mD",
    "pages": [{"page": 1, "part": "P1", "cid": 1, "duration": 600},
              {"page": 2, "part": "P2", "cid": 2, "duration": 600}],
}

NOTE = """# 课堂笔记

## 第一节（00:05）

- ★★★ 控制反转把创建权交给容器 [08:45]
- ★★ 次要要点无锚点

## 第二节（00:22）

- ★★★ ApplicationContext 预实例化 [00:22]
"""

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


def setup_files(tmp_path, note=NOTE, meta=META):
    (tmp_path / "note.md").write_text(note, encoding="utf-8")
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


# ---------- 标记缺失 → 追加到末尾 ----------

def test_append_when_markers_missing(tmp_path, capsys):
    setup_files(tmp_path)
    assert run(tmp_path) == 0
    text = read(tmp_path)
    assert "<!-- NAV:BEGIN" in text and "<!-- NAV:END -->" in text
    assert text.rstrip().endswith("<!-- NAV:END -->")
    # 标记之外的内容不受影响
    assert text.startswith(NOTE)
    # 分P归属正确：[08:45]=525s 在 P1，t 用该分P内相对秒
    assert "https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=525" in text
    assert "https://www.bilibili.com/video/BV1xx411c7mD?p=2&t=22" in text
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["status"] == "ok" and summary["mode"] == "append"
    assert summary["written"] == 4  # 2 个小节标题 + 2 条有锚点的标注行


def test_lines_sorted_by_time(tmp_path):
    setup_files(tmp_path)
    run(tmp_path)
    text = read(tmp_path)
    block = text.split("<!-- NAV:BEGIN")[1]
    secs = ["00:05", "00:22", "08:45"]
    pos = [block.index(s) for s in secs]
    assert pos == sorted(pos)


# ---------- 幂等：连续运行两次字节一致 ----------

def test_idempotent_two_runs_byte_identical(tmp_path):
    setup_files(tmp_path)
    run(tmp_path)
    first = (tmp_path / "note.md").read_bytes()
    run(tmp_path)
    second = (tmp_path / "note.md").read_bytes()
    assert first == second


def test_replace_keeps_outside_content_byte_identical(tmp_path):
    setup_files(tmp_path)
    run(tmp_path)
    before = read(tmp_path)
    # 在 NAV 块之间手工塞入垃圾内容，再跑一次应被整体替换
    dirty = before.replace("## 参考时间戳\n\n- [00:05]",
                           "## 参考时间戳\n\n手写垃圾行\n- [00:05]")
    (tmp_path / "note.md").write_text(dirty, encoding="utf-8")
    run(tmp_path)
    assert read(tmp_path) == before  # 标记外内容与干净版字节一致


# ---------- 标记不配对 → 报错退出且不改文件 ----------

def test_unpaired_markers_error_and_no_write(tmp_path):
    setup_files(tmp_path, note=NOTE + "<!-- NAV:BEGIN 由脚本生成 -->\n")
    before = digest(tmp_path)
    assert run(tmp_path) == 1
    assert digest(tmp_path) == before  # 文件哈希不变


def test_only_end_marker_is_unpaired(tmp_path):
    setup_files(tmp_path, note=NOTE + "<!-- NAV:END -->\n")
    before = digest(tmp_path)
    assert run(tmp_path) == 1
    assert digest(tmp_path) == before


def test_two_begin_markers_is_unpaired(tmp_path):
    setup_files(tmp_path, note=NOTE + "<!-- NAV:BEGIN -->\n<!-- NAV:BEGIN -->\n"
                                     "<!-- NAV:END -->\n")
    before = digest(tmp_path)
    assert run(tmp_path) == 1
    assert digest(tmp_path) == before


# ---------- 分P归属 / 找不到归属跳过 ----------

def test_anchor_without_matching_page_skipped(tmp_path, capsys):
    setup_files(tmp_path, note=NOTE + "- ★★ 凭空锚点 [99:99]\n")
    assert run(tmp_path) == 0
    err = capsys.readouterr()
    assert "未找到归属分P" in err.err  # stderr warning
    text = read(tmp_path)
    assert "99:99" not in text.split("<!-- NAV:BEGIN")[1]
    summary = json.loads(err.out.strip().splitlines()[-1])
    assert summary["skipped"] == 1


def test_first_page_wins_on_ambiguous_match(tmp_path):
    """同一秒命中多个分P时按页码升序取首个，结果确定。"""
    setup_files(tmp_path)
    (tmp_path / "txt" / "03_P3.txt").write_text("[08:45] 重复时间点\n",
                                                encoding="utf-8")
    run(tmp_path)
    text = read(tmp_path)
    assert "?p=1&t=525" in text
    assert "?p=3&t=525" not in text


# ---------- 抖音：纯文本行，无链接 ----------

def test_douyin_plain_text_no_url(tmp_path):
    setup_files(tmp_path, meta=DOUYIN_META)
    assert run(tmp_path) == 0
    text = read(tmp_path)
    block = text.split("<!-- NAV:BEGIN")[1]
    assert "http" not in block
    assert "- [08:45] ★★★：控制反转把创建权交给容器" in block
    assert "- [00:22] ★★★：ApplicationContext 预实例化" in block


# ---------- v2 文字标签兼容 / short 跳过 ----------

def test_v2_tag_lines_supported(tmp_path):
    note = "# 笔记\n\n- **核心必考** 控制反转 [08:45]\n- （重点掌握）三级缓存 [08:45]\n"
    setup_files(tmp_path, note=note)
    run(tmp_path)
    block = read(tmp_path).split("<!-- NAV:BEGIN")[1]
    assert "核心必考：控制反转" in block
    assert "重点掌握：三级缓存" in block


def test_short_note_type_skips(tmp_path):
    note = "---\nnote_type: short\n---\n\n# 笔记\n\n- ★★ 要点 [08:45]\n"
    setup_files(tmp_path, note=note)
    before = digest(tmp_path)
    assert run(tmp_path) == 0
    assert digest(tmp_path) == before  # short 直接跳过，文件不动


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


def test_v2_note_regression_unchanged_dispatch(tmp_path, capsys):
    """无 template_version 的 v2 老笔记不走 v3 分支：仍由标注行重算 NAV 块。"""
    note = NOTE + "<!-- NAV:BEGIN 旧内容 -->\n手写垃圾行\n<!-- NAV:END -->\n"
    setup_files(tmp_path, note=note)
    assert run(tmp_path) == 0
    block = read(tmp_path).split("<!-- NAV:BEGIN")[1]
    assert "## 参考时间戳" in block  # v1/v2 语义：整块重新生成
    assert "★★★：ApplicationContext 预实例化" in block
    assert "手写垃圾行" not in block
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["mode"] == "replace"  # 非 v3 mode
