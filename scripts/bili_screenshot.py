#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes 第二步半（可选）：按转写稿时间戳抽取视频重点截图。

用法:
    python bili_screenshot.py <metadata.json路径> <转写txt所在目录> <截图输出目录> [页码,页码]
                              [--shots shots.json] [--ascii-names]

选项:
    --shots        截图清单（推荐）：Agent 写完正文后按小节产出，脚本逐条抽帧，
                   格式见下方「截图清单」；给了清单就不再走关键词锚点。
    --ascii-names  把中文锚点 label 转写为 zh-<md5(关键词)前6位>，文件名全 ASCII
                   （英文 label 不变）；默认关闭，保留中文文件名。

截图清单（shots.json）:
    {"shots": [{"page": 1, "section": 2, "t": "05:12",
                "caption": "开闭原则两层含义", "name": "two-meanings"}, ...]}
    - page    分P号（单P视频写 1）；section 笔记里 `## N.` 小节号（门禁据此校验位置）
    - t       该分P转写稿里真实存在的时刻 mm:ss（±5s 容差，否则该条进 failed）
    - caption 图注，笔记里 alt 文本须以它开头；name 可选，文件名用（缺省取 caption）
    输出文件名 p<页>_s<小节>_<name>.png。

原理:
    1. 定时刻：有 --shots 时直接用清单时刻（截什么由理解了内容的 Agent 决定）；
       否则在转写稿中搜索"锚点关键词"，取首次出现时间戳（减去约3秒的ASR滞后）。
       锚点表外置在 references/anchors.json，**按视频 ID 隔离**
       （{视频ID: {页码: [[label, 中文说明, [正则...]], ...]}}，以 _ 开头的键忽略）；
       该视频无条目时，自动用 auto_anchors() 从转写稿提取中英文关键词。
    1.5 多帧择优：每个时刻向后取 CAND_COUNT 个候选（间隔 CAND_STEP 秒），优先 QC
       合格帧，其中挑与相邻候选差异最小（画面已静止、非翻页过渡）的一帧，
       同分比清晰度；同一分P内与已选帧感知哈希（dHash）汉明距离 ≤ DUP_HAMMING
       且缩略图平均差 < DUP_DIFF 的判为重复画面，不落盘，记入 stdout 的 duplicates。
    2. 通过 playurl API 下载该分P的 480p DASH 视频流（无需登录/ffmpeg）
    3. 用 PyAV（faster-whisper 已自带）定位时间点解码抽帧，存为 PNG
    4. 本地质检（QC）：按像素占比判亮度（近黑像素占比>97% 判 dark，
       近白像素占比>98.5% 判 bright——白底 PPT 少量文字不会误判）与灰度
       拉普拉斯方差清晰度（<10 判 blurry）；判为非 ok 的帧自动在 +30s
       重抽一次（仍在该分P时长内才重试），重抽后仍非 ok 则保留该帧并标记 qc。
       numpy 未安装时跳过初筛（qc="skipped"），流程继续。
       本地初筛只是自动兜底，最终复核由调用方（Agent）视觉完成。
    5. 运行结束把全部截图元数据写入 <img_dir>/manifest.json（跨分P累积、
       同页重跑时该页旧记录覆盖，被覆盖页不再被引用的旧 PNG 一并删除），
       供 validate_note.py 交叉校验图注与画面。

输出契约:
    stdout 每个分P输出一行 JSON 进度：
        {"page":.., "part":..,
         "frames":[{"file","page","label","keyword","caption","t","qc","section"}],
         "failed":[{"page","label","t","reason"}],
         "duplicates":[{"page","label","t","duplicate_of"}], "status":"ok"|"partial"|...}
    section 仅清单模式有值（关键词锚点模式为 null）；清单模式 keyword = caption。
    frames 里的 keyword 为锚点原文（匹配转写稿用的关键词），与 manifest
    同构；--ascii-names 只改写 label（文件名），keyword 保留原文。
    frames 与锚点一一对应，任一帧取不到（None）时该锚点不生成 png、
    进入 failed 列表，其余截图的图注不受影响；全部成功 status=ok，
    有失败帧 status=partial。

平台差异:
    - B站: 按需下载视频流，用完即删；锚点查 references/anchors.json 里本视频的条目
    - 抖音（metadata 带 media_path）: 直接复用 douyin_fetch.py 已下载的本地视频，
      不再联网；无锚点条目时自动用 auto_anchors() 从转写稿提取关键词
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CONFIG = Path(__file__).resolve().parent.parent / "config.json"
ANCHORS_PATH = Path(__file__).resolve().parent.parent / "references" / "anchors.json"

# QC 阈值与重抽间隔（亮度用像素占比法，避免白底PPT被误判为空屏）
QC_DARK_FRAC = 0.97    # 近黑像素（灰度<25）占比高于此 → dark
QC_WHITE_FRAC = 0.985  # 近白像素（灰度>=250）占比高于此 → bright（真正空屏）
QC_BLURRY = 10.0       # 灰度拉普拉斯方差低于此 → blurry
QC_RETRY_AFTER = 30.0  # QC 不过时向后重抽的秒数
HTTP_RETRIES = 3       # http_get 网络类错误重试次数（1s/2s 退避）
CAND_COUNT = 3         # 每个截图时刻抽取的候选帧数（含时刻本身）
CAND_STEP = 4.0        # 候选帧之间的间隔秒数（覆盖 PPT 翻页后画面静止的那几秒）
DUP_HAMMING = 16       # dHash 汉明距离 ≤ 此值（256 位哈希，约 6%）才可能是同一画面
DUP_DIFF = 3.0         # 且缩略灰度图平均差 < 此值（0~255）才判重复；两条件同时满足，防止同模板不同内容的 PPT 被误删
SHOT_TS_TOLERANCE = 5  # 清单时刻须在转写稿时间行 ±5s 内（与门禁时间索引同口径）


def load_anchors(video_id: str | None = None) -> dict[int, list]:
    """加载外置锚点表中**本视频**的条目：{页码: [(label, 中文说明, [正则])]}。

    锚点表按视频 ID 隔离（{视频ID: {页码: [...]}}），以 _ 开头的键（说明/示例）
    一律忽略；没有本视频条目、文件不存在或损坏时返回空表（走 auto_anchors 兜底）。
    """
    try:  # 读文件或解析失败都按空表处理
        data = json.loads(ANCHORS_PATH.read_text(encoding="utf-8"))  # 整张锚点表
        pages = data.get(video_id) if (video_id and not video_id.startswith("_")) else None  # 只取本视频
        if not isinstance(pages, dict):  # 本视频无条目（或格式不对）
            return {}  # 空表 → 调用方自动提取关键词
        return {int(k): [(e[0], e[1], e[2]) for e in v] for k, v in pages.items()}  # 页码转 int
    except Exception:  # 文件缺失 / JSON 损坏
        return {}  # 空表


def parse_mmss(ts: str) -> float | None:
    """把 "mm:ss" / "h:mm:ss" 转成秒；格式不对返回 None。"""
    parts = str(ts).strip().split(":")  # 按冒号拆分
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):  # 只接受 2~3 段纯数字
        return None  # 非法格式
    sec = 0  # 累加秒数
    for p in parts:  # 逐段进位（时→分→秒）
        sec = sec * 60 + int(p)  # 60 进制累加
    return float(sec)  # 返回秒


def load_shots(path: Path) -> dict[int, list[dict]]:
    """读取截图清单 shots.json，按分P分组：{页码: [{label, keyword, caption, t, section}]}。

    清单项缺 page 时按 1 处理；t 非法的项保留 t=None，由 main 记入 failed。
    """
    data = json.loads(path.read_text(encoding="utf-8"))  # 解析清单文件
    items = data.get("shots", []) if isinstance(data, dict) else data  # 兼容顶层直接是数组
    out: dict[int, list[dict]] = {}  # 分P → 截图任务
    for it in items:  # 逐条清单项
        page = int(it.get("page") or 1)  # 分P号，缺省 1
        section = it.get("section")  # 笔记小节号
        caption = str(it.get("caption") or "").strip()  # 图注
        name = str(it.get("name") or caption or "shot").strip()  # 文件名主体
        label = f"s{section}_{safe_name(name)}" if section is not None else safe_name(name)  # 带小节前缀
        label = re.sub(r"\s+", "-", label)  # 文件名去空白
        out.setdefault(page, []).append({  # 按分P收集
            "label": label, "keyword": caption, "caption": caption,  # 清单模式 keyword 即图注
            "t": parse_mmss(it.get("t", "")), "raw_t": it.get("t"),  # 秒数与原文（报错用）
            "section": int(section) if section is not None else None})  # 小节号
    return out  # 分组结果


def load_sessdata() -> str:
    try:
        return (json.loads(CONFIG.read_text(encoding="utf-8")).get("SESSDATA") or "").strip()
    except Exception:
        return ""


def http_get(url: str, cookie: str = "", retries: int = HTTP_RETRIES) -> bytes:
    """带重试的 GET：网络类错误按 1s/2s 退避重试（与 bili_fetch.py 相同模式）。"""
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    if cookie:
        headers["Cookie"] = f"SESSDATA={cookie}"
    last_exc = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_exc = e
            if attempt < retries - 1:
                time.sleep(attempt + 1)
    raise last_exc


def parse_ts_seconds(text: str) -> list[tuple[float, str]]:
    """返回 [(秒, 该时刻的行文本), ...]，按时间排序。"""
    out = []
    for line in text.splitlines():
        m = re.match(r"\[(\d+):(\d+)\] ?(.*)", line)
        if m:
            out.append((int(m.group(1)) * 60 + int(m.group(2)), m.group(3)))
    return out


def find_anchor_times(lines: list[tuple[float, str]], patterns: list[str]) -> float | None:
    for t, txt in lines:
        for pat in patterns:
            if re.search(pat, txt):
                return max(2.0, t - 3.0)  # 减去ASR滞后
    return None


# 自动锚点时排除的英文常见口语词
_STOP_WORDS = {
    "this", "that", "with", "from", "have", "what", "when", "your", "then",
    "them", "they", "will", "would", "there", "these", "those", "which",
    "about", "into", "just", "like", "make", "made", "need", "want", "here",
    "very", "much", "more", "some", "time", "know", "think", "also",
    "because", "video", "today", "start", "okay",
}

# 中文虚词停用词：单字（出现在片段中即过滤）+ 多字词（作为子串过滤）
_ZH_STOP_CHARS = set("的了呢吗是在有和与就不都也还又把被让给从向对为以等")
_ZH_STOP_WORDS = {
    "并且", "但是", "因为", "所以", "如果", "这个", "那个", "什么", "怎么",
    "我们", "你们", "他们", "没有", "已经", "还是", "而且", "因此", "然后",
    "可以", "自己", "一样", "这么", "那么", "怎样", "如何", "以及", "或者",
    "关于", "通过", "进行", "开始", "继续", "一直",
}
_EN_WEIGHT = 1.5  # 英文 token 天然信息量大，排序权重稍高


def auto_anchors(lines: list[tuple[float, str]], max_n: int = 3) -> list[tuple[str, str, float]]:
    """无锚点表条目时的兜底：从转写稿提取中英文关键词，取首次出现时间。

    英文/代码 token：@注解、驼峰词、≥4 字母普通词（沿用原规则）。
    中文候选：对连续中文串做 2~4 字 n-gram 频次统计（无 jieba，纯计数），
    过滤含虚词停用词的片段，只保留出现 ≥2 次的 n-gram；按 (次数, 长度)
    融合为 次数×长度 打分优选（频次与长度兼顾），已选关键词的子串不再重复选。
    中英混合排序（英文权重 1.5），总量限 max_n，截图时间彼此至少错开15秒。

    返回 [(label, keyword, caption, 抽帧秒), ...]，keyword 为锚点原文
    （英文 token 含 @ 前缀的原始形态，中文与 label 相同）。
    """
    from collections import Counter
    en_cnt, zh_cnt = Counter(), Counter()
    en_first, zh_first = {}, {}
    for t, txt in lines:
        for m in re.finditer(r"@[A-Za-z]\w+|[A-Za-z][A-Za-z0-9_]{3,}", txt):
            tok = m.group(0)
            en_cnt[tok] += 1
            en_first.setdefault(tok, t)
        for m in re.finditer(r"[\u4e00-\u9fff]{2,8}", txt):
            s = m.group(0)
            for n in (2, 3, 4):
                for i in range(len(s) - n + 1):
                    g = s[i:i + n]
                    zh_cnt[g] += 1
                    zh_first.setdefault(g, t)

    def zh_bad(g: str) -> bool:
        return (any(c in _ZH_STOP_CHARS for c in g)
                or any(w in g for w in _ZH_STOP_WORDS))

    # 候选: (分数, 长度, token, 首现时间, 是否中文)
    cands = []
    for tok, c in en_cnt.items():
        if tok.lower().lstrip("@") not in _STOP_WORDS:
            cands.append((_EN_WEIGHT * c * len(tok), len(tok), tok, en_first[tok], False))
    for g, c in zh_cnt.items():
        if c >= 2 and not zh_bad(g):
            cands.append((c * len(g), len(g), g, zh_first[g], True))
    cands.sort(key=lambda x: (-x[0], -x[1], x[2]))

    jobs, kept_t, kept_tok = [], [], []
    for _score, _ln, tok, ft, is_zh in cands:
        if len(jobs) >= max_n:
            break
        if any(tok in k for k in kept_tok):  # 已选关键词的子串不再重复选
            continue
        label = tok if is_zh else re.sub(r"\W+", "-", tok.lstrip("@").lower()).strip("-")
        if not label:
            continue
        t = max(2.0, ft - 3.0)
        if any(abs(t - st) < 15 for st in kept_t):  # 与已选锚点至少错开15秒
            continue
        caption = (f"关键词“{tok}”出现的画面" if is_zh
                   else f"关键词「{tok}」出现的画面")
        jobs.append((label, tok, caption, t))
        kept_t.append(t)
        kept_tok.append(tok)
    return jobs


def download_video(bvid: str, cid: int, dest: Path, cookie: str = "") -> Path:
    url = f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&fnval=16"
    data = json.loads(http_get(url, cookie).decode("utf-8"))
    if data.get("code") != 0:  # 失效视频/风控/需要登录等，给出可读报错而非裸 KeyError
        raise RuntimeError(
            f"playurl API 返回 code={data.get('code')}: "
            f"{data.get('message') or data.get('msg') or '未知错误'}（视频可能已失效、需要登录或风控拦截）")
    try:
        videos = data["data"]["dash"]["video"]
    except (KeyError, TypeError) as e:
        raise RuntimeError(
            f"playurl 响应缺少 dash.video 字段（bvid={bvid} cid={cid}，"
            "可能未登录无 DASH 流权限）") from e
    best = max(videos, key=lambda v: v["id"])  # 未登录最高480p
    req = urllib.request.Request(best["baseUrl"] or best["base_url"], headers={
        "User-Agent": UA, "Referer": "https://www.bilibili.com/",
        **({"Cookie": f"SESSDATA={cookie}"} if cookie else {})})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)
    return dest


def grab_frames(video: Path, times: list[float]) -> list:
    """在指定时间点各取一帧（PIL Image）。

    始终返回与 times 等长的列表：某时刻取不到帧（越界/解码失败）时
    对应位置为 None，调用方按位置与锚点配对，保证图注不错位。
    """
    import av
    frames: list = []
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        for t in times:
            got = None
            try:
                container.seek(int(t / float(stream.time_base)), stream=stream, backward=True)
                for frame in container.decode(stream):
                    if frame.time is not None and frame.time >= t - 0.4:
                        got = frame
                        break
            except Exception:
                got = None
            frames.append(got.to_image() if got is not None else None)
    return frames


def qc_check(img) -> str:
    """本地初筛一帧：返回 "ok" | "dark" | "bright" | "blurry" | "skipped"。

    亮度用像素占比法：近黑像素（灰度<25）占比、近白像素（灰度>=250）占比，
    纯白空屏近白占比≈100% 判 bright，白底PPT（文字占比<8%）近白占比≈95%
    仍判 ok——均值法（>240）会把后者误判。清晰度 = 灰度图拉普拉斯
    （3x3 卷积核）方差，纯 numpy 计算。亮度不达标优先判定（黑屏/白屏时
    清晰度无意义）。numpy 未安装时返回 "skipped"，不阻塞流程。
    """
    try:
        import numpy as np
    except ImportError:
        print("warning: numpy 未安装，跳过画质初筛", file=sys.stderr)
        return "skipped"
    g = np.asarray(img.convert("L"), dtype=np.float64)
    if g.shape[0] < 3 or g.shape[1] < 3:
        return "ok"
    dark_frac = float((g < 25).mean())
    white_frac = float((g >= 250).mean())
    if dark_frac > QC_DARK_FRAC:
        return "dark"
    if white_frac > QC_WHITE_FRAC:
        return "bright"
    return "blurry" if _laplacian_var(g) < QC_BLURRY else "ok"


def _laplacian_var(g) -> float:
    """灰度 numpy 数组的拉普拉斯（3x3 卷积核）方差，越大越清晰。"""
    lap = (-4.0 * g[1:-1, 1:-1]  # 中心像素权重 -4
           + g[:-2, 1:-1] + g[2:, 1:-1]  # 上下邻居
           + g[1:-1, :-2] + g[1:-1, 2:])  # 左右邻居
    return float(lap.var())  # 方差


def frame_sharpness(img) -> float:
    """一帧的清晰度分数（拉普拉斯方差）；numpy 缺失或图太小时返回 0。"""
    try:  # numpy 为可选依赖
        import numpy as np  # 惰性导入
    except ImportError:  # 未安装
        return 0.0  # 不参与比较
    g = np.asarray(img.convert("L"), dtype=np.float64)  # 转灰度数组
    if g.shape[0] < 3 or g.shape[1] < 3:  # 卷积核放不下
        return 0.0  # 视为无清晰度信息
    return _laplacian_var(g)  # 清晰度


def dhash(img, size: int = 16) -> int:
    """感知差值哈希（dHash，size² 位，默认 256 位）：缩到 (size+1)×size 灰度，比较水平相邻像素。"""
    small = img.convert("L").resize((size + 1, size))  # 缩小 + 灰度，抹掉细节噪声
    px = small.tobytes()  # 行优先像素（L 模式每像素 1 字节）
    bits = 0  # 哈希值
    for row in range(size):  # 逐行
        for col in range(size):  # 逐列比较左右像素
            left = px[row * (size + 1) + col]  # 左像素
            right = px[row * (size + 1) + col + 1]  # 右像素
            bits = (bits << 1) | (1 if left > right else 0)  # 左亮于右记 1
    return bits  # size² 位整数


def hamming(a: int, b: int) -> int:
    """两个哈希的汉明距离（不同比特数）。"""
    return bin(a ^ b).count("1")  # 异或后数 1


def frame_diff(a, b) -> float:
    """两帧缩略灰度图的平均绝对差（0~255），越小越像；用来判断画面是否已静止。"""
    from PIL import ImageChops, ImageStat  # 惰性导入（pillow 为截图必需依赖）
    sa = a.convert("L").resize((64, 36))  # 缩成 16:9 小图，降低噪声与计算量
    sb = b.convert("L").resize((64, 36))  # 同尺寸
    return float(ImageStat.Stat(ImageChops.difference(sa, sb)).mean[0])  # 平均差


def pick_best(cands: list[tuple[float, object]]) -> tuple[float, object, str] | None:
    """从同一时刻的候选帧 [(秒, PIL 图)] 中挑最合适的一帧，返回 (秒, 图, qc)。

    规则：先保留 QC 合格（ok/skipped）的候选，全部不合格则在全体中挑；
    稳定度 = 与相邻候选差异的最小值（越小说明画面已静止，避开翻页过渡），
    稳定度相同（取整后）再比清晰度。没有任何可用帧时返回 None。
    """
    valid = [(t, img) for t, img in cands if img is not None]  # 去掉取不到的帧
    if not valid:  # 一帧都没有
        return None  # 交给调用方记 failed
    qcs = [qc_check(img) for _, img in valid]  # 每帧 QC 结果
    good = [i for i, q in enumerate(qcs) if q in ("ok", "skipped")]  # 合格候选下标
    pool = good or list(range(len(valid)))  # 无合格帧则全部参与

    def stability(i: int) -> float:  # 与相邻候选的最小差异
        diffs = [frame_diff(valid[i][1], valid[j][1])  # 和前后邻居比较
                 for j in (i - 1, i + 1) if 0 <= j < len(valid)]  # 越界的邻居跳过
        return min(diffs) if diffs else 0.0  # 只有一帧时视为稳定

    best = min(pool, key=lambda i: (round(stability(i)), -frame_sharpness(valid[i][1]), i))  # 稳定优先、清晰次之、时间早再次
    return valid[best][0], valid[best][1], qcs[best]  # (秒, 图, qc)


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()[:60]


def ascii_label(label: str) -> str:
    """--ascii-names 开启时：含非 ASCII 字符的 label 转为 zh-<md5(关键词)前6位>。"""
    if label.isascii():
        return label
    return "zh-" + hashlib.md5(label.encode("utf-8")).hexdigest()[:6]


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="按转写稿时间戳抽取B站/抖音视频重点截图",
        epilog="示例: python bili_screenshot.py meta.json txt img 2,6,11 --ascii-names")
    ap.add_argument("meta", type=Path, help="metadata.json 路径")
    ap.add_argument("txt_dir", type=Path, help="转写txt所在目录")
    ap.add_argument("img_dir", type=Path, help="截图输出目录")
    ap.add_argument("pages", nargs="?", default=None,
                    help="可选页码列表，逗号分隔，如 2,6,11（缺省处理全部分P）")
    ap.add_argument("--ascii-names", action="store_true",
                    help="中文锚点label转写为 zh-<md5前6位>，文件名全ASCII（默认保留中文）")
    ap.add_argument("--shots", type=Path, default=None,  # 截图清单（推荐）
                    help="截图清单 shots.json（按小节给出时刻与图注），给了就不走关键词锚点")
    return ap.parse_args(argv)


def write_manifest(img_dir: Path, frames: list, failed: list, touched: set):
    """把截图元数据落盘 manifest.json：本次处理的页覆盖旧记录，未触及的页保留。

    M9：被覆盖页（touched）不再被新记录引用的旧 PNG 视为孤儿文件删除；
    跨分P合并逻辑不变，只清被覆盖页的孤儿（以新记录 file 集合为准，
    未触及页的 file 不受影响）。删除清单输出到 stderr。
    """
    mpath = img_dir / "manifest.json"
    old_frames, old_failed = [], []
    if mpath.exists():
        try:
            old = json.loads(mpath.read_text(encoding="utf-8"))
        except Exception:
            old = None
        if isinstance(old, dict):
            old_frames = [f for f in old.get("frames", []) if f.get("page") not in touched]
            old_failed = [f for f in old.get("failed", []) if f.get("page") not in touched]
            if touched:
                new_files = {f.get("file") for f in frames
                             if isinstance(f, dict) and f.get("file")}
                keep_files = new_files | {f.get("file") for f in old_frames
                                          if isinstance(f, dict) and f.get("file")}
                orphans = []
                for f in old.get("frames", []):
                    fn = f.get("file") if isinstance(f, dict) else None
                    if fn and f.get("page") in touched and fn not in keep_files:
                        orphan = img_dir / fn
                        if orphan.is_file():
                            orphan.unlink(missing_ok=True)
                            orphans.append(fn)
                if orphans:
                    print(f"cleaned orphan pngs (overwritten pages): {orphans}",
                          file=sys.stderr)
    manifest = {"frames": old_frames + frames, "failed": old_failed + failed}
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                     encoding="utf-8")


def main(argv=None):
    try:  # Agent 经管道调用时 stdout 可能是 gbk，emoji 标题会炸在最后一行 print
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args(argv)
    meta_path, txt_dir, img_dir = args.meta, args.txt_dir, args.img_dir
    only_pages = ({int(x) for x in args.pages.split(",")}
                  if args.pages else None)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    img_dir.mkdir(parents=True, exist_ok=True)
    cookie = load_sessdata()
    video_id = meta.get("bvid") or meta.get("video_id")  # 各平台视频 ID（锚点表按它隔离）
    shots = load_shots(args.shots) if args.shots else None  # 清单模式：{页码: [任务]}
    anchors = {} if shots is not None else load_anchors(video_id)  # 清单模式不查锚点表
    all_frames, all_failed, touched_pages = [], [], set()

    try:
        for p in meta["pages"]:
            page_no, part, cid = p["page"], p["part"], p.get("cid")
            if only_pages and page_no not in only_pages:
                continue
            if shots is not None and page_no not in shots:  # 清单模式：清单没列的分P不处理
                continue  # 跳过
            txt_file = txt_dir / f"{page_no:02d}_{safe_name(part)}.txt"
            if not txt_file.exists():
                print(json.dumps({"page": page_no, "status": "no_transcript"}), flush=True)
                continue
            lines = parse_ts_seconds(txt_file.read_text(encoding="utf-8"))
            pre_failed = []  # 抽帧前就判定失败的清单项（时刻非法 / 不在转写稿里）
            if shots is not None:  # 清单模式：只处理清单里列出的分P
                legal = [t for t, _ in lines]  # 转写稿真实时间行（秒）
                jobs = []  # 本分P的截图任务
                for job in shots.get(page_no, []):  # 逐条清单项
                    t = job["t"]  # 清单时刻（秒）
                    if t is None or not any(abs(t - lt) <= SHOT_TS_TOLERANCE for lt in legal):  # 时刻非法或虚构
                        pre_failed.append({"page": page_no, "label": job["label"],  # 记录失败项
                                           "t": job["raw_t"], "reason": "t_not_in_transcript"})  # 原因
                        continue  # 不抽帧
                    jobs.append(job)  # 合法任务
                if not jobs and not pre_failed:  # 清单里没有本分P
                    continue  # 静默跳过，不输出进度
            else:  # 关键词锚点模式
                jobs = []  # 本分P的截图任务
                for label, caption, patterns in anchors.get(page_no, []):
                    t = find_anchor_times(lines, patterns)
                    if t is not None:
                        # keyword=锚点原文（label 即原文）
                        jobs.append({"label": label, "keyword": label, "caption": caption,
                                     "t": t, "section": None})  # 锚点模式无小节号
                if not jobs:  # 无锚点表条目（如抖音平台）时自动提取
                    jobs = [{"label": l, "keyword": k, "caption": c, "t": t, "section": None}  # 统一成字典
                            for l, k, c, t in auto_anchors(lines)]  # 自动关键词
            if not jobs:
                if pre_failed:  # 清单项全部时刻非法
                    all_failed.extend(pre_failed)  # 计入 manifest
                    touched_pages.add(page_no)  # 本页视为已处理
                    print(json.dumps({"page": page_no, "part": part, "frames": [],  # 输出进度
                                      "failed": pre_failed, "duplicates": [], "status": "partial"},
                                     ensure_ascii=True), flush=True)  # 与正常进度同构
                    continue  # 下一个分P
                print(json.dumps({"page": page_no, "status": "no_anchor"}), flush=True)
                continue
            if args.ascii_names:  # 中文label → zh-<md5前6位>，文件名全ASCII；keyword 保留原文
                for job in jobs:  # 逐个改写
                    job["label"] = ascii_label(job["label"])  # 只改 label

            local_media = p.get("media_path") and (meta_path.parent / p["media_path"])
            video = local_media if (local_media and local_media.exists()) \
                else img_dir / f".video_{page_no:02d}.m4s"
            try:
                if video is not local_media:
                    download_video(meta["bvid"], cid, video, cookie)
                duration = float(p.get("duration") or 0)

                def cand_times(t0: float) -> list[float]:  # 某时刻的候选抽帧时间
                    ts = [t0 + k * CAND_STEP for k in range(CAND_COUNT)]  # 向后等间隔
                    return [x for x in ts if duration <= 0 or x < duration - 1] or [t0]  # 不越过分P末尾

                cand_lists = [cand_times(job["t"]) for job in jobs]  # 每个任务的候选时间
                flat = [x for ts in cand_lists for x in ts]  # 摊平后一次性抽帧
                imgs = grab_frames(video, flat)  # 与 flat 等长，失败位为 None
                results, failed, dups = [], list(pre_failed), []  # 成功 / 失败 / 重复
                kept: list[tuple[int, object, str]] = []  # 本分P已落盘帧的 (dHash, 图, 文件名)
                pos = 0  # imgs 读取游标
                for job, ts in zip(jobs, cand_lists):  # 逐任务
                    cands = list(zip(ts, imgs[pos:pos + len(ts)]))  # 本任务候选 [(秒, 图)]
                    pos += len(ts)  # 游标前移
                    picked = pick_best(cands)  # 多帧择优
                    if picked is None:  # 一帧都取不到
                        failed.append({"page": page_no, "label": job["label"],
                                       "t": round(job["t"], 1), "reason": "no_frame"})  # 记失败
                        continue  # 下一个任务
                    t, img, qc = picked  # 选中的帧
                    if qc not in ("ok", "skipped") and job["section"] is None:  # 锚点模式全不合格 → +30s 重抽一次
                        retry_t = job["t"] + QC_RETRY_AFTER  # 重抽时刻
                        if duration <= 0 or retry_t < duration - 1:  # 仍在分P时长内
                            retry_img = grab_frames(video, [retry_t])[0]  # 重抽
                            if retry_img is not None:  # 取到了
                                img, t = retry_img, retry_t  # 替换
                                qc = qc_check(img)  # 仍非 ok 则保留并标记
                    fname = f"p{page_no:02d}_{job['label']}.png"  # 输出文件名
                    h = dhash(img)  # 感知哈希
                    twin = next((f for hv, kimg, f in kept  # 找重复画面：哈希接近且像素差也小
                                 if hamming(hv, h) <= DUP_HAMMING and frame_diff(kimg, img) < DUP_DIFF), None)
                    if twin is not None:  # 与已选帧是同一画面
                        dups.append({"page": page_no, "label": job["label"],
                                     "t": round(t, 1), "duplicate_of": twin})  # 记重复，不落盘
                        continue  # 下一个任务
                    kept.append((h, img, fname))  # 登记已选帧
                    img.save(img_dir / fname)  # 落盘
                    results.append({"file": fname, "page": page_no,
                                    "label": job["label"], "keyword": job["keyword"],
                                    "caption": job["caption"],
                                    "t": round(t, 1), "qc": qc,
                                    "section": job["section"]})  # 清单模式记录小节号
                status = "ok" if not failed else "partial"
                all_frames.extend(results)
                all_failed.extend(failed)
                touched_pages.add(page_no)
                print(json.dumps({"page": page_no, "part": part, "frames": results,
                                  "failed": failed, "duplicates": dups, "status": status},
                                 ensure_ascii=True), flush=True)
            except Exception as e:
                print(json.dumps({"page": page_no, "status": "error", "error": str(e)},
                                 ensure_ascii=True), flush=True)
            finally:
                if video is not local_media:
                    video.unlink(missing_ok=True)
    finally:
        write_manifest(img_dir, all_frames, all_failed, touched_pages)


if __name__ == "__main__":
    main()
