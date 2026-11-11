# -*- coding: utf-8 -*-
"""_frames.py 画面分析的离线测试：用合成灰度画面（不解码视频）覆盖切段、内容判定、遮挡与选帧。"""
import numpy as np  # 合成画面
from PIL import Image  # 网格测试

import _frames as F  # 被测模块（conftest 已把 scripts/ 加入 sys.path）

H, W = F.H, F.W  # 分析小图尺寸
BOARD = 40  # 黑板底色（深色）
CHALK = 230  # 粉笔颜色（亮色）


def blank(T: int, value: int = BOARD) -> np.ndarray:
    """T 秒纯色画面。"""
    return np.full((T, H, W), value, np.uint8)  # 全部同色


def stroke(frame: np.ndarray, k: int) -> None:
    """在画面上画第 k 条“板书”：黑板区域内的一条横线（3 像素粗）。"""
    y = 30 + (k % 10) * 12  # 行位置
    x0 = 40 + (k // 10) * 120  # 列起点
    frame[y: y + 3, x0: x0 + 100] = CHALK  # 画线


def person(frame: np.ndarray, x: int) -> None:
    """在横坐标 x 处画一个“讲师”（亮色竖条，带边缘）。"""
    frame[60:180, x: x + 40] = 150  # 身体
    frame[40:60, x + 10: x + 30] = 200  # 头


def times(T: int) -> np.ndarray:
    """0..T-1 秒。"""
    return np.arange(T, dtype=float)  # 采样时刻


def progressive(T: int = 120, start: int = 20, end: int = 80) -> np.ndarray:
    """边讲边写：start 秒起每 3 秒多写一条板书，写到 end 秒为止；讲师在画面右侧来回走动。"""
    fr = blank(T)  # 空黑板
    for i in range(T):  # 逐秒
        for k in range(max(0, min(i, end) - start) // 3):  # 到这一秒为止写了几条
            stroke(fr[i], k)  # 画板书
        person(fr[i], 250 + (i * 7) % 30)  # 讲师在右侧小范围走动
    return fr  # 返回


def test_empty_board_with_lecturer_has_no_candidates():
    fr = blank(120)  # 空黑板
    for i in range(120):  # 逐秒
        person(fr[i], 100 + (i * 13) % 150)  # 讲师来回走动
    a = F.Analysis(times(120), fr)  # 分析
    assert a.content.max() < F.MIN_CONTENT  # 内容量不过门槛
    assert a.candidates(30, 90) == []  # 没有可截的画面


def test_progressive_writing_prefers_complete_board():
    a = F.Analysis(times(120), progressive())  # 分析
    cands = a.candidates(30, 60)  # 讲解时间段
    assert cands, "有板书时应给出候选"  # 有候选
    latest = max(c["t"] for c in cands)  # 最晚的候选
    assert latest > 60  # 讲解结束后写完的板书也会成为候选
    assert a.content[int(latest)] > a.content[35]  # 更晚的候选内容更多


def test_slide_change_creates_new_segment():
    fr = blank(120, 235)  # 白底幻灯片
    for i in range(120):  # 逐秒
        for k in (range(0, 6) if i < 60 else range(10, 16)):  # 前后两页内容不同
            stroke(fr[i], k)  # 画内容
        fr[i][fr[i] == CHALK] = 20  # 白底黑字
    a = F.Analysis(times(120), fr)  # 分析
    assert any(55 <= a.t[c] <= 68 for c in a.cuts)  # 翻页处切段
    cands = a.candidates(75, 100)  # 第二页的讲解时间段
    assert cands and all(c["seg"][0] >= 55 for c in cands[:1])  # 重叠最多的候选来自第二页


def test_camera_cut_is_a_segment_boundary():
    fr = progressive()  # 板书画面
    fr[70:] = 255 - fr[70:]  # 第 70 秒起整幅画面大变（模拟机位切换）
    a = F.Analysis(times(120), fr)  # 分析
    assert any(68 <= a.t[c] <= 72 for c in a.cuts)  # 切换处切段


def test_occluded_frames_are_avoided():
    fr = blank(120)  # 黑板
    for i in range(20, 120):  # 第 20 秒写上板书（整集一直在的内容按设计算固定背景）
        for k in range(8):  # 8 条板书
            stroke(fr[i], k)  # 画
    for i in range(30, 45):  # 这段时间讲师每秒换位置挡在板书前
        person(fr[i], 40 + (i * 23) % 90)  # 挡住板书区域
    a = F.Analysis(times(120), fr)  # 分析
    assert a.visible[35] < a.visible[60]  # 被挡时可见内容更少
    picked = [c["t"] for c in a.candidates(28, 46)]  # 讲解时间段正好覆盖遮挡期
    assert picked and all(not (30 <= t < 45) for t in picked)  # 候选避开被挡的帧


def test_short_title_card_segment_is_skipped():
    fr = progressive()  # 板书画面
    fr[:5] = 240  # 开头 5 秒白色标题卡
    for i in range(5):  # 标题卡上的大字
        for k in range(6):  # 几行
            stroke(fr[i], k)  # 画
        fr[i][fr[i] == CHALK] = 0  # 黑字
    a = F.Analysis(times(120), fr)  # 分析
    assert all(c["t"] >= 5 for c in a.candidates(0, 40))  # 不选标题卡


def test_candidates_are_capped():
    a = F.Analysis(times(120), progressive())  # 分析
    assert len(a.candidates(0, 119)) <= F.MAX_CANDS - 1  # 给“按时刻截的一帧”留位置


def test_cells_shape():
    assert F.cells(np.ones((2, H, W))).shape == (2, H // F.CELL, W // F.CELL)  # 按格求平均


def test_make_grid_layout():
    imgs = [Image.new("RGB", (852, 480), (k * 40, 0, 0)) for k in range(4)]  # 4 张候选
    g = F.make_grid(imgs)  # 拼网格
    assert g.size == (426 * 3, 240 * 2) and g.mode == "RGB"  # 3 列 2 行
    assert g.getpixel((5, 5)) == (255, 220, 0)  # 第 1 格左上角是黄底编号框


def test_content_present_whole_video_counts_as_background():
    """整集一直不变的内容（如上节课留下、始终没擦的板书）按设计算固定背景，不作为本节候选。"""
    fr = blank(120)  # 黑板
    for i in range(120):  # 从头到尾
        for k in range(8):  # 同样 8 条板书
            stroke(fr[i], k)  # 画
    a = F.Analysis(times(120), fr)  # 分析
    assert a.candidates(30, 90) == []  # 不出候选（2026-11 盲评：这类画面多与图注不符）
