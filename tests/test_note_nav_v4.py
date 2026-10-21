# -*- coding: utf-8 -*-
"""note_nav.py v4 模式（--template v4，语雀直导版式）的离线测试。"""
import hashlib  # 幂等校验用
import json  # 写 metadata.json / 解析摘要

import note_nav  # 被测模块（conftest 已把 scripts/ 加入 sys.path）

# v4 笔记：无 frontmatter；时间索引节内为无链接条目
NOTE = """> 核心主线：示例。
> 课程来源：示例课程

## 1. 第一节
**核心思想：** 示例。

+ [00:05] 正文里这行不在索引节内，不应被填链

---

## 视频时间索引
+ [00:05] 1. 第一节
+ [08:45] 2. 第二节
+ [09:59] 3. 找不到归属的条目
"""

META = {"platform": "bilibili", "bvid": "BV1xx411c7mD",  # B站元数据
        "suggested_note_type": "lecture"}  # 建议类型


def setup(tmp_path, note=NOTE, meta=META):
    """写出笔记、metadata.json 与两个分P的转写稿，返回 CLI 参数列表。"""
    (tmp_path / "note.md").write_text(note, encoding="utf-8")  # 笔记
    (tmp_path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")  # 元数据
    txt = tmp_path / "txt"  # 转写稿目录
    txt.mkdir(exist_ok=True)  # 创建
    (txt / "01_P1.txt").write_text("[00:05] 开场\n", encoding="utf-8")  # 分P1
    (txt / "02_P2.txt").write_text("[08:45] 第二节\n", encoding="utf-8")  # 分P2
    return [str(tmp_path / "note.md"), "--template", "v4",  # 笔记与模板
            "--meta", str(tmp_path / "meta.json"), "--txt-dir", str(txt)]  # 元数据与转写稿


def test_fills_entries_in_place(tmp_path, capsys):
    assert note_nav.main(setup(tmp_path)) == 0  # 正常退出
    out = (tmp_path / "note.md").read_text(encoding="utf-8")  # 读回
    assert "+ [00:05](https://www.bilibili.com/video/BV1xx411c7mD?p=1&t=5) 1. 第一节" in out  # 分P1
    assert "+ [08:45](https://www.bilibili.com/video/BV1xx411c7mD?p=2&t=525) 2. 第二节" in out  # 分P2
    assert "+ [09:59] 3. 找不到归属的条目" in out  # 找不到归属保持原样
    assert "+ [00:05] 正文里这行" in out  # 索引节外的行不动
    summary = json.loads(capsys.readouterr().out.strip())  # 摘要
    assert summary["mode"] == "v4" and summary["written"] == 2 and summary["skipped"] == 1  # 计数


def test_idempotent(tmp_path):
    args = setup(tmp_path)  # 准备
    note_nav.main(args)  # 第一次
    first = hashlib.md5((tmp_path / "note.md").read_bytes()).hexdigest()  # 指纹
    note_nav.main(args)  # 第二次
    assert hashlib.md5((tmp_path / "note.md").read_bytes()).hexdigest() == first  # 不变


def test_missing_index_section_skips(tmp_path, capsys):
    note = NOTE.split("## 视频时间索引")[0]  # 去掉索引节
    assert note_nav.main(setup(tmp_path, note=note)) == 0  # 正常退出
    assert (tmp_path / "note.md").read_text(encoding="utf-8") == note  # 文件不变
    assert json.loads(capsys.readouterr().out.strip())["status"] == "skipped"  # 跳过


def test_short_from_metadata_skips(tmp_path, capsys):
    meta = dict(META, suggested_note_type="short")  # metadata 建议 short
    assert note_nav.main(setup(tmp_path, meta=meta)) == 0  # 正常退出
    assert (tmp_path / "note.md").read_text(encoding="utf-8") == NOTE  # 文件不变
    assert json.loads(capsys.readouterr().out.strip())["status"] == "skipped"  # 跳过


def test_douyin_keeps_plain_text(tmp_path):
    meta = {"platform": "douyin", "video_id": "7123456789"}  # 抖音无深链
    note_nav.main(setup(tmp_path, meta=meta))  # 运行
    assert (tmp_path / "note.md").read_text(encoding="utf-8") == NOTE  # 保持纯文本


def test_youtube_link(tmp_path):
    meta = {"platform": "youtube", "video_id": "dQw4w9WgXcQ"}  # YouTube
    note_nav.main(setup(tmp_path, meta=meta) + ["--page", "2"])  # 限定分P2
    out = (tmp_path / "note.md").read_text(encoding="utf-8")  # 读回
    assert "+ [08:45](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=525s) 2. 第二节" in out  # YouTube 深链
