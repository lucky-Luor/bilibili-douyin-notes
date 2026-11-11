#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图候选：先按画面切段，再按小节讲解时间段挑出各段的代表帧（供 bili_screenshot.py --candidates 使用）。

为什么不直接按讲话时刻截图（2026-11 盲评，见 docs/verification/W4-截图重设计.md）：
    讲话和画面不同步——板书讲完才写完、讲师会挡住、PPT 可能提前或延后翻页；只看像素又分不清
    “哪张是图注说的那张”。所以分工是：本模块负责剔除空画面、找出写完且没被挡住的画面，
    拼成带编号的候选网格；写笔记的 Agent 按图注语义挑编号。

做法（只用 PyAV / Pillow / numpy）：
    1. 每秒解码 1 帧，缩成 320×180 灰度小图
    2. 前后 10 秒取逐像素中位数得到“干净底板”，滤掉走来走去的讲师
    3. 底板上的墨迹（像素与 15×15 局部均值相差 > 20）按 8×8 格统计，只算持续 ≥ 6 秒的墨迹，
       再减去该格整集的低位基线（讲台、显示器等固定结构）；整集有 15% 以上时间在动的格子
       （讲师常驻区）不计
    4. 内容消失 35% 以上（擦黑板 / 翻页）或一秒内 40% 以上像素大变（镜头切换）就切出新段
    5. 每个与小节时间段相交的画面段，在“讲解期间”和“讲解结束后 30 秒内”各挑一帧：
       讲解期间新增、且这一帧没被挡住的内容为主，总内容为辅，同分偏晚
    6. 内容量低于门槛的段不出候选；一个候选都没有就判定“这一段没有可截的画面”

参数在 2026-11 开发集（W4 原 10 个教室录像，30 条）上确定，留出集只验证、未回调。
"""
import numpy as np  # 数值计算

W, H = 320, 180  # 分析用小图尺寸（16:9）
FPS = 1.0  # 分析采样率（帧/秒）
INK_RADIUS = 7  # 墨迹判定的局部窗口半径（15×15）
INK_DIFF = 20  # 像素与局部均值相差超过此值算墨迹（不依赖背景深浅：黑板、白底 PPT、黑底手写板通用）
PLATE_HALF = 10  # 干净底板的时间半窗（秒）
OCC_DIFF = 18  # 帧与底板相差超过此值的像素算被挡住
CELL = 8  # 统计格边长（像素）：320×180 → 40×22 格
HOLD = 6  # 墨迹至少持续的秒数（短暂出现的讲师残影不算；后期叠加的 PPT 小窗能保留）
BASE_PCT = 10  # 每格整集基线分位数（固定结构）
MAX_MOTION = 0.15  # 整集在动时间占比超过此值的格子（讲师常驻区）不计内容
MOTION_DIFF = 20  # 相邻两秒像素差超过此值算“在动”
MOTION_CELL = 0.1  # 格内在动像素占比达到此值算这一格在动
CELL_GAIN = 0.02  # 逐格持续墨迹增益超过此值才算内容格
MIN_CONTENT = 0.0115  # 无内容门槛（内容格墨迹占全图比例）：开发集空黑板最高 1.02%，有板书最低 1.29%
ERASE_FRAC = 0.35  # 与 ERASE_LAG 秒前相比内容消失超过此比例：擦除 / 翻页
ERASE_LAG = 6  # 擦除比较间隔（秒），也是两个切点的最小间隔
CUT_DIFF = 30  # 镜头切换判定的像素差
CUT_FRAC = 0.4  # 相邻两秒超过此比例的像素大变：镜头切换
MIN_SEG = 8  # 短于此秒数的画面段不出候选（片头标题卡、转场）
PRE, POST = 10, 30  # 小节时间段向前 / 向后扩展的秒数（板书常常讲完才写完）
TOTAL_W = 0.25  # 打分里总内容的权重（主权重给讲解期间新增的内容）
MAX_CANDS = 9  # 每张候选网格最多几格（含按时刻截的那一帧）


def to_gray_small(img) -> np.ndarray:
    """PIL 图 → W×H 灰度小图（uint8 数组）。"""
    from PIL import Image  # 惰性导入（pillow 为截图必需依赖）
    return np.asarray(img.convert("L").resize((W, H), Image.BILINEAR), dtype=np.uint8)  # 缩小 + 灰度


def decode_gray(video, fps: float = FPS) -> tuple[np.ndarray, np.ndarray]:
    """按 fps 解码整段视频，返回 (采样时刻数组, 灰度小图栈 T×H×W)。"""
    import av  # 惰性导入（PyAV 随 faster-whisper 安装）
    times, frames = [], []  # 结果
    with av.open(str(video)) as c:  # 打开视频
        nxt = 0.0  # 下一个采样时刻
        for f in c.decode(c.streams.video[0]):  # 顺序解码
            if f.time is None or f.time < nxt:  # 未到采样点
                continue  # 跳过
            times.append(nxt)  # 记录采样时刻（按网格对齐）
            frames.append(to_gray_small(f.to_image()))  # 记录小图
            nxt += 1.0 / fps  # 下一个采样点
    if not frames:  # 一帧都没解出来
        return np.zeros(0), np.zeros((0, H, W), np.uint8)  # 空结果
    return np.array(times), np.stack(frames)  # 返回


def box_mean(g: np.ndarray, r: int = INK_RADIUS) -> np.ndarray:
    """(2r+1)² 盒式均值（积分图实现，边缘复制填充）。"""
    p = np.pad(g.astype(np.float32), ((r + 1, r), (r + 1, r)), mode="edge")  # 填充，多一行一列给积分图差分
    ii = p.cumsum(0).cumsum(1)  # 积分图
    k = 2 * r + 1  # 窗口边长
    s = ii[k:, k:] - ii[:-k, k:] - ii[k:, :-k] + ii[:-k, :-k]  # 每个窗口的像素和
    return s / (k * k)  # 均值


def ink(g: np.ndarray) -> np.ndarray:
    """墨迹图：像素与局部均值相差超过 INK_DIFF 的位置（布尔数组）。"""
    return np.abs(g.astype(np.float32) - box_mean(g)) > INK_DIFF  # 局部对比度高 = 笔画 / 文字 / 图形边缘


def cells(a: np.ndarray) -> np.ndarray:
    """(..., H, W) → (..., gy, gx)：按 CELL×CELL 格求平均（裁掉不足一格的边）。"""
    gy, gx = a.shape[-2] // CELL, a.shape[-1] // CELL  # 格数
    a = a[..., : gy * CELL, : gx * CELL]  # 裁到整格
    return a.reshape(a.shape[:-2] + (gy, CELL, gx, CELL)).mean(axis=(-3, -1))  # 格内平均


class Analysis:
    """一个分P视频的逐秒画面分析结果（整段算一次，供该分P的多条清单共用）。"""

    def __init__(self, times: np.ndarray, frames: np.ndarray):
        self.t = np.asarray(times, dtype=float)  # 采样时刻（秒）
        T = len(frames)  # 帧数
        plates = np.empty_like(frames)  # 逐秒干净底板
        for i in range(T):  # 逐秒
            lo, hi = max(0, i - PLATE_HALF), min(T, i + PLATE_HALF + 1)  # 时间半窗
            plates[i] = np.median(frames[lo:hi], axis=0).astype(np.uint8)  # 中位数滤掉移动物体
        dens = np.stack([cells(ink(p).astype(np.float32)) for p in plates]) if T else np.zeros((0, H // CELL, W // CELL))  # 逐秒逐格墨迹密度
        base = np.percentile(dens, BASE_PCT, axis=0) if T else 0  # 每格整集低位基线（固定结构）
        run = np.empty_like(dens)  # 过去 HOLD 秒的最小值（只留持续的墨迹）
        for i in range(T):  # 逐秒
            run[i] = dens[max(0, i - HOLD + 1): i + 1].min(axis=0)  # 滑动最小
        moving = cells((np.abs(np.diff(frames.astype(np.int16), axis=0)) > MOTION_DIFF).astype(np.float32)) >= MOTION_CELL  # 每秒每格是否在动
        mfreq = moving.mean(axis=0) if T > 1 else np.zeros(dens.shape[1:])  # 每格整集在动的时间占比
        self.gain = np.clip(run - base, 0, None) * (mfreq <= MAX_MOTION)  # 持续墨迹增益（排除讲师常驻区）
        self.content = self.gain.reshape(T, -1).mean(axis=1) if T else np.zeros(0)  # 逐秒内容量（占全图比例）
        occ = cells((np.abs(frames.astype(np.int16) - plates.astype(np.int16)) > OCC_DIFF).astype(np.float32))  # 逐秒逐格被挡比例
        self.cells = self.gain > CELL_GAIN  # 内容格
        self.vis_w = 1 - np.clip(occ * 3, 0, 1)  # 逐格可见权重：被挡 1/3 以上的格子不算
        self.visible = (self.gain * self.cells * self.vis_w).reshape(T, -1).mean(axis=1) if T else np.zeros(0)  # 逐秒可见内容量
        self.diff1 = np.r_[0.0, (np.abs(np.diff(frames.astype(np.int16), axis=0)) > CUT_DIFF).mean(axis=(1, 2))] if T else np.zeros(0)  # 相邻两秒大变比例
        self.cuts = self._cuts()  # 切点

    def _cuts(self) -> list[int]:
        """切点下标：镜头切换，或与 ERASE_LAG 秒前相比内容消失 ERASE_FRAC 以上（擦除 / 翻页）。"""
        cuts, last = [], -10 ** 9  # 结果与上一个切点
        tot = self.gain.reshape(len(self.t), -1).sum(axis=1)  # 每秒内容总量
        for i in range(len(self.t)):  # 逐秒
            j = i - ERASE_LAG  # 比较对象
            erase = j >= 0 and tot[j] > 0 and np.clip(self.gain[j] - self.gain[i], 0, None).sum() / tot[j] > ERASE_FRAC  # 内容消失
            if (self.diff1[i] > CUT_FRAC or erase) and i - last > ERASE_LAG:  # 新切点（离上一个够远）
                cuts.append(i)  # 记录
                last = i  # 更新
        return cuts  # 返回

    def segments(self) -> list[tuple[int, int]]:
        """画面段 [起, 止) 下标列表。"""
        b = [0] + self.cuts + [len(self.t)]  # 段边界
        return [(b[k], b[k + 1]) for k in range(len(b) - 1) if b[k + 1] > b[k]]  # 非空段

    def candidates(self, ra: float, rb: float) -> list[dict]:
        """小节讲解时间段 [ra, rb] 的画面段代表帧：[{t, ov, score, seg}]，重叠多的段在前。

        每个与 [ra-PRE, rb+POST] 相交、不太短、内容达标的段，在“讲解期间”和“讲解结束后”
        两个子窗口各挑一帧；返回空列表表示这一段没有可截的画面。
        """
        segs = self.segments()  # 全部画面段
        out = []  # 结果
        for s0, s1 in segs:  # 逐段
            t0, t1 = self.t[s0], self.t[s1 - 1]  # 段起止时刻
            if t1 - t0 < MIN_SEG and len(segs) > 1:  # 太短（标题卡、转场）
                continue  # 跳过
            idx = [i for i in range(s0, s1) if ra - PRE <= self.t[i] <= rb + POST]  # 段内落在窗口里的秒
            if not idx or self.content[idx].max() < MIN_CONTENT:  # 不相交或没有内容
                continue  # 跳过
            ov = max(0.0, min(t1, rb) - max(t0, ra)) / max(1.0, rb - ra)  # 与讲解时间段的重叠比例
            ref = max(s0, int(np.searchsorted(self.t, ra - PRE)))  # “新增内容”的参照时刻：讲解开始前（不早于本段开头）
            for part in ([i for i in idx if self.t[i] <= rb], [i for i in idx if self.t[i] > rb]):  # 讲解期间 / 讲解结束后
                best, bscore = None, -1.0  # 子窗口最优
                for i in part:  # 逐秒打分
                    new = np.clip(self.gain[i] - self.gain[ref], 0, None) * self.cells[i] * self.vis_w[i]  # 讲解期间新增、且这一帧看得见的内容
                    score = float(new.mean()) + TOTAL_W * float(self.visible[i]) + 1e-7 * i  # 新增为主、总量为辅，同分偏晚
                    if score > bscore:  # 更好
                        best, bscore = i, score  # 记录
                if best is not None and self.content[best] >= MIN_CONTENT:  # 子窗口有内容才出候选
                    out.append({"t": float(self.t[best]), "ov": round(ov, 3), "score": bscore,  # 候选
                                "seg": [float(t0), float(t1)]})  # 所在画面段
        out.sort(key=lambda c: (-c["ov"], -c["score"]))  # 重叠多、分高的在前
        return out[: MAX_CANDS - 1]  # 给“按时刻截的一帧”留一个位置


def analyse_video(video) -> Analysis:
    """解码整段视频并分析。"""
    return Analysis(*decode_gray(video))  # 一步完成


def make_grid(images: list, cols: int = 3, thumb: tuple[int, int] = (426, 240)):
    """把候选图拼成带编号（1 起，从左到右、从上到下）的网格，返回 PIL 图。"""
    from PIL import Image, ImageDraw, ImageFont  # 惰性导入
    font = None  # 编号字体
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"):  # Linux / Windows / macOS 常见字体
        try:  # 尝试加载
            font = ImageFont.truetype(name, 28)  # 成功
            break  # 停止
        except OSError:  # 找不到
            continue  # 下一个
    font = font or ImageFont.load_default()  # 兜底默认字体
    rows = max(1, (len(images) + cols - 1) // cols)  # 行数
    grid = Image.new("RGB", (thumb[0] * cols, thumb[1] * rows), (30, 30, 30))  # 深灰底画布
    draw = ImageDraw.Draw(grid)  # 画笔
    for k, im in enumerate(images, 1):  # 逐张贴图
        x, y = ((k - 1) % cols) * thumb[0], ((k - 1) // cols) * thumb[1]  # 位置
        grid.paste(im.convert("RGB").resize(thumb), (x, y))  # 缩略图
        draw.rectangle([x, y, x + 54, y + 38], fill=(255, 220, 0))  # 黄底编号框
        draw.text((x + 10, y + 3), str(k), fill=(0, 0, 0), font=font)  # 黑字编号
    return grid  # 返回
