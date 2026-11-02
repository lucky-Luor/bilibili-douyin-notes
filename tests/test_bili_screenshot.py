# -*- coding: utf-8 -*-
"""bili_screenshot.py 纯函数的离线测试：parse_ts_seconds / auto_anchors / qc_check / ascii_label。

qc_check 用四类合成图（PIL 生成，纯本地）：
    纯白 → bright、白底+少量字 → ok、纯黑 → dark、噪声 → ok。
"""
import hashlib
import json
import random

import pytest
from PIL import Image, ImageDraw

import bili_screenshot


# ---------- parse_ts_seconds ----------

def test_parse_ts_seconds_basic():
    text = "[00:05] 开场\n不是时间行\n[12:34] 讲 BeanFactory\n"
    assert bili_screenshot.parse_ts_seconds(text) == [
        (5, "开场"), (754, "讲 BeanFactory")]


def test_parse_ts_seconds_empty():
    assert bili_screenshot.parse_ts_seconds("没有时间行") == []


def test_parse_ts_seconds_parses_all_lines():
    text = "[05:00] b\n[01:00] a"
    out = bili_screenshot.parse_ts_seconds(text)
    assert sorted(t for t, _ in out) == [60, 300]
    assert dict((t, s) for t, s in out)[60] == "a"


# ---------- auto_anchors ----------

def _lines():
    return [
        (10.0, "今天讲 Spring 的循环依赖问题"),
        (20.0, "循环依赖是面试重点，@Component 注解注册 Bean"),
        (30.0, "循环依赖的三级缓存设计很巧妙"),
        (40.0, "Bean 生命周期分为实例化、属性填充、初始化"),
        (200.0, "最后总结一下 Bean 生命周期的核心流程"),
    ]


def test_auto_anchors_chinese_keywords():
    jobs = bili_screenshot.auto_anchors(_lines())
    assert 0 < len(jobs) <= 3
    for label, keyword, caption, t in jobs:
        assert isinstance(label, str) and label
        assert isinstance(keyword, str) and keyword  # M2: 锚点原文
        assert "出现的画面" in caption
        assert t >= 2.0


def test_auto_anchors_times_spread_and_valid():
    jobs = bili_screenshot.auto_anchors(_lines())
    times = [t for *_, t in jobs]
    assert all(t >= 2.0 for t in times)
    for i in range(len(times)):
        for j in range(i + 1, len(times)):
            assert abs(times[i] - times[j]) >= 15  # 彼此至少错开15秒


def test_auto_anchors_max_n():
    jobs = bili_screenshot.auto_anchors(_lines(), max_n=2)
    assert len(jobs) <= 2


def test_auto_anchors_empty_input():
    assert bili_screenshot.auto_anchors([]) == []


def test_auto_anchors_prefers_repeated_terms():
    lines = [(10.0, "事务"), (20.0, "事务传播"), (30.0, "事务隔离级别")]
    jobs = bili_screenshot.auto_anchors(lines)
    assert jobs and any("事务" in label for label, *_ in jobs)


# ---------- qc_check（四类合成图） ----------

def _solid(color, size=(64, 64)):
    return Image.new("RGB", size, color)


def _white_with_text(size=(120, 120)):
    """白底 + 少量黑色字形（模拟白底 PPT），近白占比 < 98.5%。"""
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    for y in (30, 60, 90):
        d.rectangle([10, y, 110, y + 4], fill=(10, 10, 10))  # 模拟文字行
    return img


def _noise(size=(80, 80), seed=42):
    import numpy as np
    rng = np.random.RandomState(seed)
    arr = rng.randint(0, 256, size=(size[1], size[0]), dtype="uint8")
    return Image.fromarray(arr, mode="L").convert("RGB")


def test_qc_pure_white_is_bright():
    assert bili_screenshot.qc_check(_solid((255, 255, 255))) == "bright"


def test_qc_white_bg_with_text_is_ok():
    assert bili_screenshot.qc_check(_white_with_text()) == "ok"


def test_qc_pure_black_is_dark():
    assert bili_screenshot.qc_check(_solid((0, 0, 0))) == "dark"


def test_qc_noise_is_ok():
    assert bili_screenshot.qc_check(_noise()) == "ok"


def test_qc_tiny_image_is_ok():
    assert bili_screenshot.qc_check(Image.new("RGB", (2, 2), (128, 128, 128))) == "ok"


def test_qc_qc_thresholds_unchanged():
    """QC 常量口径（像素占比法）不得被无意改动。"""
    assert bili_screenshot.QC_DARK_FRAC == 0.97
    assert bili_screenshot.QC_WHITE_FRAC == 0.985
    assert bili_screenshot.QC_BLURRY == 10.0


# ---------- ascii_label（--ascii-names 口径） ----------

def test_ascii_label_ascii_passthrough():
    assert bili_screenshot.ascii_label("spring-bean") == "spring-bean"


def test_ascii_label_chinese_becomes_zh_md5():
    out = bili_screenshot.ascii_label("循环依赖")
    assert out == "zh-" + hashlib.md5("循环依赖".encode("utf-8")).hexdigest()[:6]
    assert out.isascii()


# ---------- M2: manifest keyword 字段 / M9: 孤儿 PNG 清理（main 集成，全 mock） ----------

def _run_main(tmp_path, monkeypatch, capsys, old_manifest=None):
    """mock 网络/解码，跑 main()，返回 (manifest dict, stdout 末行 JSON)。"""
    import PIL.Image

    meta = {"bvid": "BV1xx411c7mD",
            "pages": [{"page": 1, "part": "P1", "cid": 1, "duration": 600}]}
    (tmp_path / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False),
                                            encoding="utf-8")
    txt_dir = tmp_path / "txt"
    txt_dir.mkdir()
    (txt_dir / "01_P1.txt").write_text("[00:10] 循环依赖的三级缓存\n", encoding="utf-8")
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    if old_manifest is not None:
        (img_dir / "manifest.json").write_text(
            json.dumps(old_manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bili_screenshot, "load_anchors",
                        lambda vid=None: {1: [("循环依赖", "循环依赖的图示", ["循环依赖"])]})
    monkeypatch.setattr(bili_screenshot, "load_sessdata", lambda: "")
    monkeypatch.setattr(bili_screenshot, "download_video",
                        lambda bvid, cid, dest, cookie="": dest.write_bytes(b"fake") or dest)
    monkeypatch.setattr(bili_screenshot, "grab_frames",
                        lambda video, times: [PIL.Image.new("RGB", (4, 4)) for _ in times])
    monkeypatch.setattr(bili_screenshot, "qc_check", lambda img: "ok")

    bili_screenshot.main([str(tmp_path / "metadata.json"), str(txt_dir), str(img_dir), "1"])
    manifest = json.loads((img_dir / "manifest.json").read_text(encoding="utf-8"))
    out = capsys.readouterr().out
    last = json.loads(out.strip().splitlines()[-1])
    return img_dir, manifest, last


def test_main_manifest_and_stdout_have_keyword(tmp_path, monkeypatch, capsys):
    img_dir, manifest, last = _run_main(tmp_path, monkeypatch, capsys)
    assert manifest["frames"], "应有成功帧"
    for f in manifest["frames"]:
        assert f["keyword"] == "循环依赖"  # M2: manifest 每条 frame 带 keyword
    # stdout 进度行 frames 与 manifest 同构，同样带 keyword
    assert last["frames"] and last["frames"][0]["keyword"] == "循环依赖"


def test_main_keyword_keeps_original_with_ascii_names(tmp_path, monkeypatch, capsys):
    import PIL.Image
    meta = {"bvid": "BV1xx411c7mD",
            "pages": [{"page": 1, "part": "P1", "cid": 1, "duration": 600}]}
    (tmp_path / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False),
                                            encoding="utf-8")
    txt_dir = tmp_path / "txt"
    txt_dir.mkdir()
    (txt_dir / "01_P1.txt").write_text("[00:10] 循环依赖的三级缓存\n", encoding="utf-8")
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    monkeypatch.setattr(bili_screenshot, "load_anchors",
                        lambda vid=None: {1: [("循环依赖", "循环依赖的图示", ["循环依赖"])]})
    monkeypatch.setattr(bili_screenshot, "load_sessdata", lambda: "")
    monkeypatch.setattr(bili_screenshot, "download_video",
                        lambda bvid, cid, dest, cookie="": dest.write_bytes(b"fake") or dest)
    monkeypatch.setattr(bili_screenshot, "grab_frames",
                        lambda video, times: [PIL.Image.new("RGB", (4, 4)) for _ in times])
    monkeypatch.setattr(bili_screenshot, "qc_check", lambda img: "ok")
    bili_screenshot.main([str(tmp_path / "metadata.json"), str(txt_dir), str(img_dir),
                          "1", "--ascii-names"])
    manifest = json.loads((img_dir / "manifest.json").read_text(encoding="utf-8"))
    f = manifest["frames"][0]
    assert f["file"].isascii()           # label 转 ASCII 文件名
    assert f["keyword"] == "循环依赖"     # keyword 保留锚点原文


def test_write_manifest_cleans_orphan_pngs_on_overwrite(tmp_path):
    """M9：重跑覆盖页时，该页不再被引用的旧 PNG 被删除；保留页与跨分P不受影响。"""
    old = {"frames": [
        {"file": "p01_keep.png", "page": 1, "t": 5},
        {"file": "p01_orphan.png", "page": 1, "t": 50},
        {"file": "p02_keep.png", "page": 2, "t": 5},
    ], "failed": []}
    for fn in ("p01_keep.png", "p01_orphan.png", "p02_keep.png"):
        (tmp_path / fn).write_bytes(b"png")
    (tmp_path / "manifest.json").write_text(json.dumps(old, ensure_ascii=False),
                                            encoding="utf-8")
    bili_screenshot.write_manifest(tmp_path, [{"file": "p01_keep.png", "page": 1}],
                                   [], touched={1})
    assert (tmp_path / "p01_keep.png").exists()   # 新记录仍引用
    assert not (tmp_path / "p01_orphan.png").exists()  # 被覆盖页的孤儿 → 删除
    assert (tmp_path / "p02_keep.png").exists()   # 跨分P合并逻辑不变
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    files = [f["file"] for f in manifest["frames"]]
    assert "p01_orphan.png" not in files
    assert "p02_keep.png" in files


def test_write_manifest_keeps_files_when_page_untouched(tmp_path):
    (tmp_path / "a.png").write_bytes(b"png")
    old = {"frames": [{"file": "a.png", "page": 3}], "failed": []}
    bili_screenshot.write_manifest(tmp_path, [{"file": "b.png", "page": 1}],
                                   [], touched={1})
    assert (tmp_path / "a.png").exists()


# ---------- 锚点表按视频隔离 / 截图清单 / 多帧择优 / 去重 ----------

def test_load_anchors_only_returns_current_video(tmp_path, monkeypatch):
    """锚点表按视频 ID 隔离：别的视频、以 _ 开头的示例键都不会被加载。"""
    table = {"_示例": {"1": [["x", "示例", ["删"]]]},  # 示例键应被忽略
             "BV1aaaaaaaaa": {"2": [["a", "说明A", ["循环"]]]}}  # 另一个视频
    path = tmp_path / "anchors.json"  # 临时锚点表
    path.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")  # 写入
    monkeypatch.setattr(bili_screenshot, "ANCHORS_PATH", path)  # 指向临时表
    assert bili_screenshot.load_anchors("BV1bbbbbbbbb") == {}  # 别的视频：空表，不串课
    assert bili_screenshot.load_anchors("_示例") == {}  # 示例键不可被当成视频
    assert bili_screenshot.load_anchors(None) == {}  # 无视频 ID：空表
    assert bili_screenshot.load_anchors("BV1aaaaaaaaa") == {2: [("a", "说明A", ["循环"])]}  # 本视频命中


def test_parse_mmss():
    assert bili_screenshot.parse_mmss("05:12") == 312.0  # mm:ss
    assert bili_screenshot.parse_mmss("1:00:05") == 3605.0  # h:mm:ss
    assert bili_screenshot.parse_mmss("5分12秒") is None  # 非法格式
    assert bili_screenshot.parse_mmss("") is None  # 空串


def _patterned(seed: int):
    """生成一张测试“幻灯片”：seed 不同 → 黑块位置不同（画面明显不同）。"""
    import PIL.Image, PIL.ImageDraw  # 惰性导入
    img = PIL.Image.new("RGB", (160, 90), "white")  # 白底 16:9
    d = PIL.ImageDraw.Draw(img)  # 画笔
    x0, y0 = (seed * 37) % 120, (seed * 23) % 60  # 黑块位置随 seed 变化
    d.rectangle([x0, y0, x0 + 40, y0 + 30], fill="black")  # 主体黑块
    d.rectangle([0, 80, 159, 84], fill="gray")  # 共同的页脚条（模拟同一 PPT 模板）
    return img  # 返回图片


def _run_shots(tmp_path, monkeypatch, capsys, shots, txt, frame_for, zero_exit=False):
    """清单模式跑 main()：frame_for(t) 决定每个抽帧时刻返回的图片。"""
    meta = {"bvid": "BV1xx411c7mD",
            "pages": [{"page": 1, "part": "P1", "cid": 1, "duration": 600}]}  # 单P元数据
    (tmp_path / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")  # 写 meta
    txt_dir = tmp_path / "txt"  # 转写稿目录
    txt_dir.mkdir()  # 创建
    (txt_dir / "01_P1.txt").write_text(txt, encoding="utf-8")  # 写转写稿
    img_dir = tmp_path / "images"  # 截图目录
    shots_path = tmp_path / "shots.json"  # 清单路径
    shots_path.write_text(json.dumps({"shots": shots}, ensure_ascii=False), encoding="utf-8")  # 写清单
    monkeypatch.setattr(bili_screenshot, "load_anchors",
                        lambda vid=None: pytest.fail("清单模式不应查锚点表"))  # 清单模式不查锚点
    monkeypatch.setattr(bili_screenshot, "load_sessdata", lambda: "")  # 无登录
    monkeypatch.setattr(bili_screenshot, "download_video",
                        lambda bvid, cid, dest, cookie="": dest.write_bytes(b"fake") or dest)  # 假下载
    monkeypatch.setattr(bili_screenshot, "grab_frames",
                        lambda video, times: [frame_for(t) for t in times])  # 按时刻给图
    argv = [str(tmp_path / "metadata.json"), str(txt_dir), str(img_dir), "--shots", str(shots_path)]  # 清单模式参数
    if zero_exit:  # 预期一张截图都没有：脚本应以三段式提示退出
        with pytest.raises(SystemExit) as ei:  # 期望退出
            bili_screenshot.main(argv)  # 跑清单模式
        assert "原因：" in str(ei.value.code) and "下一步：" in str(ei.value.code)  # 三段式提示
    else:  # 正常情况
        bili_screenshot.main(argv)  # 跑清单模式
    manifest = json.loads((img_dir / "manifest.json").read_text(encoding="utf-8"))  # 读 manifest
    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])  # 末行进度
    return img_dir, manifest, last  # 返回


def test_shots_mode_writes_section_and_caption(tmp_path, monkeypatch, capsys):
    """清单模式：文件名带小节号，manifest 记录 section，keyword 等于 caption。"""
    img_dir, manifest, last = _run_shots(
        tmp_path, monkeypatch, capsys,
        shots=[{"page": 1, "section": 2, "t": "01:00", "caption": "开闭原则两层含义", "name": "two"}],
        txt="[00:58] 开闭原则有两层含义\n", frame_for=lambda t: _patterned(1))  # 一条清单项
    f = manifest["frames"][0]  # 唯一一帧
    assert f["file"] == "p01_s2_two.png" and (img_dir / f["file"]).exists()  # 文件名与落盘
    assert f["section"] == 2 and f["keyword"] == f["caption"] == "开闭原则两层含义"  # 小节与图注
    assert last["status"] == "ok" and last["duplicates"] == []  # 进度


def test_shots_mode_rejects_invented_timestamp(tmp_path, monkeypatch, capsys):
    """清单时刻不在转写稿 ±5s 内：不抽帧，进 failed（reason=t_not_in_transcript）。"""
    _, manifest, last = _run_shots(
        tmp_path, monkeypatch, capsys,
        shots=[{"page": 1, "section": 1, "t": "09:00", "caption": "虚构时刻"}],
        txt="[00:10] 开场\n", frame_for=lambda t: _patterned(1), zero_exit=True)  # 09:00 转写稿里没有
    assert manifest["frames"] == []  # 没有截图
    assert last["failed"][0]["reason"] == "t_not_in_transcript"  # 原因明确
    assert last["status"] == "partial"  # 部分失败


def test_shots_mode_dedupes_same_slide(tmp_path, monkeypatch, capsys):
    """同一分P两条清单项截到同一页 PPT：第二张不落盘，记入 duplicates。"""
    img_dir, manifest, last = _run_shots(
        tmp_path, monkeypatch, capsys,
        shots=[{"page": 1, "section": 1, "t": "00:10", "caption": "图一", "name": "a"},
               {"page": 1, "section": 2, "t": "02:00", "caption": "图二", "name": "b"}],
        txt="[00:10] 一\n[02:00] 二\n", frame_for=lambda t: _patterned(1))  # 所有时刻同一画面
    assert [f["file"] for f in manifest["frames"]] == ["p01_s1_a.png"]  # 只保留第一张
    assert last["duplicates"][0]["duplicate_of"] == "p01_s1_a.png"  # 记录重复来源
    assert not (img_dir / "p01_s2_b.png").exists()  # 重复帧未落盘


def test_pick_best_prefers_stable_frame():
    """候选里第一帧是翻页过渡（与后面差异大），后两帧画面已静止 → 选静止帧。"""
    a, b = _patterned(1), _patterned(2)  # 两种画面
    t, img, qc = bili_screenshot.pick_best([(10.0, a), (14.0, b), (18.0, b)])  # a 是过渡帧
    assert t in (14.0, 18.0)  # 选到静止的画面
    assert bili_screenshot.pick_best([(10.0, None)]) is None  # 全部取不到 → None


def test_pick_best_prefers_qc_ok(monkeypatch):
    """QC 不合格的候选即使更稳定也不选。"""
    bad, good = _patterned(1), _patterned(2)  # 两帧
    monkeypatch.setattr(bili_screenshot, "qc_check",
                        lambda img: "dark" if img is bad else "ok")  # bad 判暗
    t, img, qc = bili_screenshot.pick_best([(1.0, bad), (5.0, good)])  # 择优
    assert img is good and qc == "ok"  # 选合格帧


def test_dhash_hamming_distinguishes_slides():
    a, b = _patterned(1), _patterned(2)  # 不同画面
    assert bili_screenshot.hamming(bili_screenshot.dhash(a), bili_screenshot.dhash(a)) == 0  # 同图距离 0
    assert bili_screenshot.hamming(bili_screenshot.dhash(a), bili_screenshot.dhash(b)) > bili_screenshot.DUP_HAMMING  # 不同图超阈值


# ---------- W4：一句话回执 / 0 张截图明确报错 ----------

def test_shot_summary_receipt_counts():
    # 回执口径：剔除 = 重复 + 失败；画质未通过的已保留，单列待复核；整集失败列出集号
    frames = [{"qc": "ok"}, {"qc": "dark"}]  # 2 张，其中 1 张待复核
    s = bili_screenshot.shot_summary(frames, [{"reason": "no_frame"}], [{"duplicate_of": "a"}], {1: "partial", 3: "error"})  # 汇总
    assert (s["frames"], s["dropped"], s["qc_review"], s["error_pages"]) == (2, 2, 1, [3])  # 数量
    assert s["receipt"].startswith("截图 2 张，剔除坏帧 2 张")  # 一句话回执
    assert "画质待复核 1 张" in s["receipt"] and "第 3 集" in s["receipt"]  # 附加说明


@pytest.mark.parametrize("status,word", [("no_transcript", "转写稿"), ("no_anchor", "--shots"),
                                         ("t_not_in_transcript", "grep"), ("partial", "换几个时刻")])
def test_zero_shot_hint_by_main_cause(status, word):
    # 0 张截图时按主要原因给出下一步
    reason, impact, next_step, _ = bili_screenshot.zero_shot_hint({1: status}, None)  # 生成提示
    assert reason and "没有截图" in impact and word in next_step  # 三段与下一步关键词


def test_zero_shot_hint_all_error_reuses_page_hint():
    # 全部整集失败：直接沿用单集失败的原因和下一步；一个分P都没处理时提示检查页码
    rec = {"reason": "请求被B站风控拦截", "next_step": "换到家用网络"}  # 单集失败记录
    assert bili_screenshot.zero_shot_hint({1: "error"}, rec)[0] == "请求被B站风控拦截"  # 沿用原因
    assert "页码" in bili_screenshot.zero_shot_hint({}, None)[2]  # 没有分P被处理


def test_main_writes_summary_and_prints_receipt(tmp_path, monkeypatch, capsys):
    # 正常出图：manifest 带 summary，stderr 打一句话回执（_run_main 会读掉 stdout，这里另取 stderr）
    _, manifest, _ = _run_main(tmp_path, monkeypatch, capsys)  # 跑一遍
    assert manifest["summary"]["frames"] == 1  # 汇总写入 manifest
    assert manifest["summary"]["receipt"].startswith("截图 1 张")  # 回执口径


def test_main_zero_shots_exits_with_hint(tmp_path, monkeypatch):
    # 要了截图却一张都没出（缺转写稿）：非 0 退出，给出原因 / 影响 / 下一步
    meta = {"bvid": "BV1xx411c7mD", "pages": [{"page": 1, "part": "P1", "cid": 1, "duration": 600}]}  # 单分P
    (tmp_path / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")  # 写 metadata
    monkeypatch.setattr(bili_screenshot, "load_sessdata", lambda: "")  # 无登录态
    with pytest.raises(SystemExit) as ei:  # 期望退出
        bili_screenshot.main([str(tmp_path / "metadata.json"), str(tmp_path), str(tmp_path / "images")])  # 没有转写稿
    text = str(ei.value.code)  # 退出文案
    assert "原因：要截图的分P还没有转写稿" in text and "bili_transcribe.py" in text  # 原因与下一步
