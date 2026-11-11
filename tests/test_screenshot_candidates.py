# -*- coding: utf-8 -*-
"""bili_screenshot.py 候选网格两步流程的离线测试：--candidates 出网格 → 清单写 pick → 按编号出图。"""
import json  # 读写清单与记录

import pytest  # 断言退出
from PIL import Image, ImageDraw  # 合成画面

import _frames  # 画面分析模块（打桩）
import bili_screenshot  # 被测模块（conftest 已把 scripts/ 加入 sys.path）

TXT = "[00:10] 开场\n[01:00] 开始讲类图\n[02:00] 类图讲完\n[03:00] 下一节\n"  # 转写稿（四个真实时间行）


def slide(seed: int) -> Image.Image:
    """一张合成画面：seed 不同，黑块位置不同（彼此不会被判成重复）。"""
    img = Image.new("RGB", (160, 90), "white")  # 白底
    d = ImageDraw.Draw(img)  # 画笔
    x0, y0 = (seed * 37) % 120, (seed * 23) % 60  # 位置随 seed 变化
    d.rectangle([x0, y0, x0 + 40, y0 + 30], fill="black")  # 主体黑块
    return img  # 返回


class FakeAnalysis:
    """打桩的画面分析：按给定列表返回画面段候选时刻。"""

    def __init__(self, seg_times):
        self.seg_times = seg_times  # 候选时刻
        self.calls = []  # 调用记录

    def candidates(self, ra, rb):
        self.calls.append((ra, rb))  # 记录讲解时间段
        return [{"t": t, "ov": 1.0, "score": 1.0, "seg": [0, 0]} for t in self.seg_times]  # 候选


def setup(tmp_path, monkeypatch, shots, seg_times=(70.0, 130.0), frame_for=None):
    """准备元数据、转写稿、清单，并打桩下载、画面分析与抽帧；返回 (argv 前缀, 截图目录, 抽帧调用记录)。"""
    meta = {"bvid": "BV1xx411c7mD", "pages": [{"page": 1, "part": "P1", "cid": 1, "duration": 600}]}  # 单P元数据
    (tmp_path / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")  # 写元数据
    txt_dir = tmp_path / "txt"; txt_dir.mkdir()  # 转写稿目录
    (txt_dir / "01_P1.txt").write_text(TXT, encoding="utf-8")  # 写转写稿
    (tmp_path / "shots.json").write_text(json.dumps({"shots": shots}, ensure_ascii=False), encoding="utf-8")  # 写清单
    grabbed = []  # 抽帧调用记录
    frame_for = frame_for or (lambda t: slide(int(t)))  # 默认：不同时刻不同画面
    monkeypatch.setattr(bili_screenshot, "load_sessdata", lambda: "")  # 无登录
    monkeypatch.setattr(bili_screenshot, "download_video", lambda bvid, cid, dest, cookie="": dest.write_bytes(b"x") or dest)  # 假下载
    monkeypatch.setattr(bili_screenshot, "grab_frames", lambda video, ts: grabbed.append(list(ts)) or [frame_for(t) for t in ts])  # 按时刻给图
    monkeypatch.setattr(bili_screenshot, "qc_check", lambda img: "ok")  # 画质都合格
    fake = FakeAnalysis(list(seg_times))  # 打桩分析
    monkeypatch.setattr(_frames, "analyse_video", lambda video: fake)  # 不解码视频
    argv = [str(tmp_path / "metadata.json"), str(txt_dir), str(tmp_path / "images"), "--shots", str(tmp_path / "shots.json")]  # 公共参数
    return argv, tmp_path / "images", grabbed, fake  # 返回


def write_shots(tmp_path, shots):
    """改写清单（模拟 Agent 看完网格后填 pick）。"""
    (tmp_path / "shots.json").write_text(json.dumps({"shots": shots}, ensure_ascii=False), encoding="utf-8")  # 覆盖


SHOT = {"page": 1, "section": 2, "range": ["01:00", "02:00"], "caption": "类图", "name": "cls"}  # 一条候选网格条目


def test_candidates_mode_writes_grid_and_record(tmp_path, monkeypatch, capsys):
    argv, img_dir, _, fake = setup(tmp_path, monkeypatch, [dict(SHOT, t="01:00")])  # 带 t 的条目
    bili_screenshot.main(argv + ["--candidates"])  # 生成候选网格
    rec = json.loads((img_dir / "_candidates" / "candidates.json").read_text(encoding="utf-8"))  # 候选记录
    hit = rec["p01_s2_cls"]  # 这条清单
    assert fake.calls == [(60.0, 120.0)]  # 按讲解时间段分析
    assert (img_dir / "_candidates" / hit["grid"]).exists()  # 网格落盘
    assert [c["no"] for c in hit["cands"]] == [1, 2, 3]  # 编号从 1 起
    assert [c["t"] for c in hit["cands"]] == sorted(c["t"] for c in hit["cands"])  # 按时间排序
    assert {c["src"] for c in hit["cands"]} == {"seg", "t"}  # 画面段代表帧 + 按 t 截的一帧
    assert hit["no_content"] is False  # 有画面内容
    assert not (img_dir / "manifest.json").exists()  # 这一步不出最终截图
    err = capsys.readouterr().err  # 回执
    assert "候选网格 1 张" in err and "pick" in err  # 告诉 Agent 下一步


def test_pick_outputs_exact_candidate_frame(tmp_path, monkeypatch, capsys):
    argv, img_dir, grabbed, _ = setup(tmp_path, monkeypatch, [SHOT])  # 不带 t
    bili_screenshot.main(argv + ["--candidates"])  # 第一步：网格
    write_shots(tmp_path, [dict(SHOT, pick=2)])  # Agent 选第 2 张
    grabbed.clear()  # 只看第二步的抽帧
    bili_screenshot.main(argv)  # 第二步：出图
    manifest = json.loads((img_dir / "manifest.json").read_text(encoding="utf-8"))  # 截图记录
    f = manifest["frames"][0]  # 唯一一张
    assert f["t"] == 130.0 and f["section"] == 2 and (img_dir / f["file"]).exists()  # 正是第 2 个候选的时刻
    assert grabbed == [[130.0]]  # 精确取这一帧，不再向后择优


def test_range_without_pick_fails_with_hint(tmp_path, monkeypatch, capsys):
    argv, img_dir, _, _ = setup(tmp_path, monkeypatch, [SHOT])  # 不带 pick
    bili_screenshot.main(argv + ["--candidates"])  # 生成网格
    with pytest.raises(SystemExit) as ei:  # 没选编号就出图
        bili_screenshot.main(argv)  # 第二步
    assert "pick" in str(ei.value.code) and "下一步" in str(ei.value.code)  # 提示先选编号
    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])  # 末行进度
    assert last["failed"][0]["reason"] == "need_pick"  # 原因码


def test_pick_before_candidates_fails(tmp_path, monkeypatch, capsys):
    argv, _, _, _ = setup(tmp_path, monkeypatch, [dict(SHOT, pick=1)])  # 还没生成网格就写了 pick
    with pytest.raises(SystemExit):  # 一张都出不了
        bili_screenshot.main(argv)  # 出图
    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])  # 末行进度
    assert last["failed"][0]["reason"] == "no_candidates"  # 先跑 --candidates


def test_pick_out_of_range(tmp_path, monkeypatch, capsys):
    argv, _, _, _ = setup(tmp_path, monkeypatch, [SHOT])  # 两个候选
    bili_screenshot.main(argv + ["--candidates"])  # 生成网格
    write_shots(tmp_path, [dict(SHOT, pick=9)])  # 编号超出
    with pytest.raises(SystemExit):  # 出不了图
        bili_screenshot.main(argv)  # 出图
    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])  # 末行进度
    assert last["failed"][0]["reason"] == "pick_not_found"  # 原因码


def test_range_must_come_from_transcript(tmp_path, monkeypatch, capsys):
    argv, _, _, _ = setup(tmp_path, monkeypatch, [dict(SHOT, range=["05:00", "06:00"])])  # 转写稿里没有的时刻
    with pytest.raises(SystemExit) as ei:  # 一张网格都没有
        bili_screenshot.main(argv + ["--candidates"])  # 生成网格
    assert "range" in str(ei.value.code)  # 提示检查 range
    out = capsys.readouterr().out  # 进度
    assert "range_not_in_transcript" in out  # 原因码


def test_no_content_is_recorded_not_an_error(tmp_path, monkeypatch, capsys):
    argv, img_dir, _, _ = setup(tmp_path, monkeypatch, [SHOT], seg_times=())  # 分析判定没有画面内容
    bili_screenshot.main(argv + ["--candidates"])  # 正常结束，不报错
    hit = json.loads((img_dir / "_candidates" / "candidates.json").read_text(encoding="utf-8"))["p01_s2_cls"]  # 记录
    assert hit["no_content"] is True and hit["grid"] is None and hit["cands"] == []  # 无内容、无网格
    assert "1 节判定没有可截的画面" in capsys.readouterr().err  # 回执说明


def test_duplicate_candidates_keep_later_frame(tmp_path, monkeypatch, capsys):
    argv, img_dir, _, _ = setup(tmp_path, monkeypatch, [dict(SHOT, t="01:00")], seg_times=(70.0, 130.0),  # 带 t
                                frame_for=lambda t: slide(1))  # 所有时刻同一画面
    bili_screenshot.main(argv + ["--candidates"])  # 生成网格
    hit = json.loads((img_dir / "_candidates" / "candidates.json").read_text(encoding="utf-8"))["p01_s2_cls"]  # 记录
    assert len(hit["cands"]) == 1 and hit["cands"][0]["t"] == 130.0  # 同一画面只留最晚的一张


def test_candidates_requires_shots(tmp_path, monkeypatch):
    argv, _, _, _ = setup(tmp_path, monkeypatch, [SHOT])  # 准备文件
    with pytest.raises(SystemExit) as ei:  # 没给清单
        bili_screenshot.main(argv[:3] + ["--candidates"])  # 去掉 --shots
    assert "--shots" in str(ei.value.code)  # 提示配合清单使用


def test_t_only_entries_keep_old_behaviour(tmp_path, monkeypatch, capsys):
    argv, img_dir, grabbed, fake = setup(tmp_path, monkeypatch, [{"page": 1, "section": 1, "t": "01:00", "caption": "旧清单"}])  # 只有 t
    bili_screenshot.main(argv)  # 出图
    assert fake.calls == []  # 不做画面分析
    assert grabbed and len(grabbed[0]) == bili_screenshot.CAND_COUNT  # 仍是 t 往后多帧择优
    assert json.loads((img_dir / "manifest.json").read_text(encoding="utf-8"))["frames"][0]["section"] == 1  # 照常出图
