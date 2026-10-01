#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes 第三步（可选）：按转写稿时间戳抽取视频重点截图。

用法:
    python bili_screenshot.py <metadata.json路径> <转写txt所在目录> <截图输出目录> [页码,页码]

原理:
    1. 对每个分P，在转写稿中搜索"锚点关键词"（如 @Component、循环依赖、log4j），
       取其首次出现的时间戳（减去约3秒的ASR滞后）。
       锚点表外置在 references/anchors.json（{页码: [[label, 中文说明, [正则...]], ...]}）；
       该文件不存在或该页无条目时，自动用 auto_anchors() 从转写稿提取中英文关键词。
    2. 通过 playurl API 下载该分P的 480p DASH 视频流（无需登录/ffmpeg）
    3. 用 PyAV（faster-whisper 已自带）定位时间点解码抽帧，存为 PNG
    4. 本地质检（QC）：每帧计算平均亮度（<18 判 dark，>240 判 bright）与
       灰度拉普拉斯方差清晰度（<10 判 blurry）；判为非 ok 的帧自动在 +30s
       重抽一次（仍在该分P时长内才重试），重抽后仍非 ok 则保留该帧并标记 qc。
       本地初筛只是自动兜底，最终复核由调用方（Agent）视觉完成。

输出契约:
    stdout 每个分P输出一行 JSON 进度：
        {"page":.., "part":.., "frames":[{"file","caption","t","qc"}],
         "failed":[{"label","t"}], "status":"ok"|"partial"|...}
    frames 与锚点一一对应，任一帧取不到（None）时该锚点不生成 png、
    进入 failed 列表，其余截图的图注不受影响；全部成功 status=ok，
    有失败帧 status=partial。

平台差异:
    - B站: 按需下载视频流，用完即删；锚点优先查 references/anchors.json
    - 抖音（metadata 带 media_path）: 直接复用 douyin_fetch.py 已下载的本地视频，
      不再联网；无锚点条目时自动用 auto_anchors() 从转写稿提取关键词
"""
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CONFIG = Path(__file__).resolve().parent.parent / "config.json"
ANCHORS_PATH = Path(__file__).resolve().parent.parent / "references" / "anchors.json"

# QC 阈值与重抽间隔
QC_DARK = 18.0      # 平均亮度低于此 → dark
QC_BRIGHT = 240.0   # 平均亮度高于此 → bright
QC_BLURRY = 10.0    # 灰度拉普拉斯方差低于此 → blurry
QC_RETRY_AFTER = 30.0  # QC 不过时向后重抽的秒数


def load_anchors() -> dict[int, list]:
    """加载外置锚点表 references/anchors.json；文件不存在/损坏时返回空表。"""
    try:
        data = json.loads(ANCHORS_PATH.read_text(encoding="utf-8"))
        return {int(k): [(e[0], e[1], e[2]) for e in v] for k, v in data.items()}
    except Exception:
        return {}


def load_sessdata() -> str:
    try:
        return (json.loads(CONFIG.read_text(encoding="utf-8")).get("SESSDATA") or "").strip()
    except Exception:
        return ""


def http_get(url: str, cookie: str = "") -> bytes:
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    if cookie:
        headers["Cookie"] = f"SESSDATA={cookie}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


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

    返回 [(label, caption, 抽帧秒), ...]。
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
        jobs.append((label, caption, t))
        kept_t.append(t)
        kept_tok.append(tok)
    return jobs


def download_video(bvid: str, cid: int, dest: Path, cookie: str = "") -> Path:
    url = f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&fnval=16"
    data = json.loads(http_get(url, cookie).decode("utf-8"))
    videos = data["data"]["dash"]["video"]
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
    """本地初筛一帧：返回 "ok" | "dark" | "bright" | "blurry"。

    亮度 = 灰度均值；清晰度 = 灰度图拉普拉斯（3x3 卷积核）方差，纯 numpy 计算。
    亮度不达标优先判定（黑屏/白屏时清晰度无意义）。
    """
    import numpy as np
    g = np.asarray(img.convert("L"), dtype=np.float64)
    if g.shape[0] < 3 or g.shape[1] < 3:
        return "ok"
    mean = float(g.mean())
    if mean < QC_DARK:
        return "dark"
    if mean > QC_BRIGHT:
        return "bright"
    lap = (-4.0 * g[1:-1, 1:-1]
           + g[:-2, 1:-1] + g[2:, 1:-1]
           + g[1:-1, :-2] + g[1:-1, 2:])
    return "blurry" if float(lap.var()) < QC_BLURRY else "ok"


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()[:60]


def main():
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    meta_path, txt_dir, img_dir = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    only_pages = ({int(x) for x in sys.argv[4].split(",")}
                  if len(sys.argv) > 4 else None)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    img_dir.mkdir(parents=True, exist_ok=True)
    cookie = load_sessdata()
    anchors = load_anchors()

    for p in meta["pages"]:
        page_no, part, cid = p["page"], p["part"], p.get("cid")
        if only_pages and page_no not in only_pages:
            continue
        txt_file = txt_dir / f"{page_no:02d}_{safe_name(part)}.txt"
        if not txt_file.exists():
            print(json.dumps({"page": page_no, "status": "no_transcript"}), flush=True)
            continue
        lines = parse_ts_seconds(txt_file.read_text(encoding="utf-8"))
        jobs = []
        for label, caption, patterns in anchors.get(page_no, []):
            t = find_anchor_times(lines, patterns)
            if t is not None:
                jobs.append((label, caption, t))
        if not jobs:  # 无锚点表条目（如抖音平台）时自动提取
            jobs = auto_anchors(lines)
        if not jobs:
            print(json.dumps({"page": page_no, "status": "no_anchor"}), flush=True)
            continue

        local_media = p.get("media_path") and (meta_path.parent / p["media_path"])
        video = local_media if (local_media and local_media.exists()) \
            else img_dir / f".video_{page_no:02d}.m4s"
        try:
            if video is not local_media:
                download_video(meta["bvid"], cid, video, cookie)
            duration = float(p.get("duration") or 0)
            times = [t for _, _, t in jobs]
            frames = grab_frames(video, times)  # 与 times 等长，失败位为 None
            results, failed = [], []
            for (label, caption, t), img in zip(jobs, frames):
                if img is None:
                    failed.append({"label": label, "t": round(t, 1)})
                    continue
                qc = qc_check(img)
                if qc != "ok":  # 本地初筛不过 → +30s 重抽一次（仍在时长内才重试）
                    retry_t = t + QC_RETRY_AFTER
                    if duration <= 0 or retry_t < duration - 1:
                        retry_img = grab_frames(video, [retry_t])[0]
                        if retry_img is not None:
                            img, t = retry_img, retry_t
                            qc = qc_check(img)  # 仍非 ok 则保留并标记
                fname = f"p{page_no:02d}_{label}.png"
                img.save(img_dir / fname)
                results.append({"file": fname, "caption": caption,
                                "t": round(t, 1), "qc": qc})
            status = "ok" if not failed else "partial"
            print(json.dumps({"page": page_no, "part": part, "frames": results,
                              "failed": failed, "status": status},
                             ensure_ascii=False), flush=True)
        except Exception as e:
            print(json.dumps({"page": page_no, "status": "error", "error": str(e)},
                             ensure_ascii=False), flush=True)
        finally:
            if video is not local_media:
                video.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
