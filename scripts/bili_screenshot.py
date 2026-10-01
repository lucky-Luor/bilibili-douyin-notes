#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes 第三步（可选）：按转写稿时间戳抽取视频重点截图。

用法:
    python bili_screenshot.py <metadata.json路径> <转写txt所在目录> <截图输出目录>

原理:
    1. 对每个分P，在转写稿中搜索"锚点关键词"（如 @Component、循环依赖、log4j），
       取其首次出现的时间戳（减去约3秒的ASR滞后）
    2. 通过 playurl API 下载该分P的 480p DASH 视频流（无需登录/ffmpeg）
    3. 用 PyAV（faster-whisper 已自带）定位时间点解码抽帧，存为 PNG
    4. 截图是否有效（非黑屏/转场）由调用方另行视觉校验

平台差异:
    - B站: 按需下载视频流，用完即删；锚点优先查硬编码 ANCHORS 表
    - 抖音（metadata 带 media_path）: 直接复用 douyin_fetch.py 已下载的本地视频，
      不再联网；无 ANCHORS 条目时自动用 auto_anchors() 从转写稿提取关键词

stdout 每个分P输出一行 JSON 进度。
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

# 锚点表: {页码: [(label, 中文说明, [正则...]), ...]}，每页最多取前几个命中项。
# 可选配置：表里没有的页码（或抖音等平台）会自动走 auto_anchors() 关键词提取。
ANCHORS = {
    1: [("fenleijiaogou", "前后端分工与冰山比喻", ["冰山", "前端.*工程师|后端.*工程师"]),
        ("ai-frontend", "AI对前端的冲击", ["AI.*前端|前端.*AI|全栈"]),
        ("ai-quanstack", "全栈工程师趋势", ["全栈"])],
    2: [("j2ee-history", "J2EE到JavaEE的课程沿革", ["J2EE|Y211|加瓦一"]),
        ("tomcat-netty", "两大技术栈Tomcat与Netty", ["Tomcat|Netty|Latty"]),
        ("reactive-quanxian", "Reactive判断权限例子", ["全线|权限|角色"])],
    3: [("three-features", "Spring三大特性", ["三大|控制反转.*依赖注入|IOC|IoC"]),
        ("static-oop", "静态方法不是面向对象", ["静态"]),
        ("simplejava", "SimpleJava组装代码", ["SimpleJava|Simple|new"])],
    4: [("git-clone", "IDEA克隆仓库", ["克龙|Clone|clone"]),
        ("git-install", "git安装与配置", ["windows.*git|Git|git"])],
    5: [("maven-idea", "IDEA内置Maven", ["Maven|maven"]),
        ("add-maven", "Add as Maven Project", ["add as maven|Add as Maven|import"])],
    6: [("component", "@Component注解方式", ["art component|[Cc]omponent"]),
        ("qualifier", "@Qualifier指定注入", ["Qualifier|qualify|quali"]),
        ("lombok", "Lombok生成构造函数", ["Lombok|lombok|plug"])],
    7: [("jdk-setting", "项目JDK设置", ["Project Structure|project structure|JDK"]),
        ("delete-jdk", "删掉外部JDK", ["删|清除|清理"])],
    8: [("maven-dir", "Maven标准目录", ["src|SRC|目录"]),
        ("m2-repo", "本地仓库m2", ["m2|M2|仓库"])],
    9: [("lifecycle3", "三个生命周期", ["生命周期|life"]),
        ("compile-target", "compile生成target", ["target|class|comp"]),
        ("package", "打包package", ["package|包"])],
    10: [("plugin", "插件机制", ["插件|plug"]),
         ("run-goal", "spring-boot:run运行", ["run|goal|目标"]),
         ("started", "Started启动成功标志", ["Started|started|seconds"])],
    11: [("embed-tomcat", "内嵌Tomcat", ["Tomcat|war|挖包|哇包|哇巴"]),
         ("starter", "Starter起步依赖", ["Stutter|[Ss]tarter"]),
         ("deps-view", "依赖展开视图", ["Dependencies|Dependence|依赖"])],
    12: [("actuator", "Actuator监控", ["actuator|Accurate|accurate"]),
         ("beans-endpoint", "beans端点看容器", ["beans|兵队下|冰对象"]),
         ("metrics", "metrics监控指标", ["metrics|matrix|内存"])],
    13: [("beanfactory", "BeanFactory继承体系", ["BeanFactory|BinFacture|bingfactory|Bin Factory"]),
         ("appcontext", "ApplicationContext四特性", ["Application Context|ApplicationContacts|ApplicationContext|上下文"]),
         ("i18n-feature", "国际化特性", ["国际|语言"])],
    14: [("lifecycle-map", "生命周期全景图", ["生命"]),
         ("aware", "Aware接口", ["Aware|aware|awell|奥特瓦德"]),
         ("prop-set", "afterPropertiesSet", ["property set|propertyset|propertysset|properties"])],
    15: [("no-sysout", "禁用System.out.println", ["print line|printline|System"]),
         ("slf4j", "SLF4J门面", ["facade|佛界|slf|SLF|搬运工"]),
         ("log4j", "log4j漏洞案例", ["漏洞|log4j|log 4j"])],
    16: [("loop-dep", "循环依赖问题", ["循环依赖|循环"]),
         ("build-order", "实例化顺序", ["顺序|先.*构建|依赖.*关系"]),
         ("log-level", "日志级别切换", ["debug|级别|忘的级别|warn"])],
    17: [("logback-xml", "logback.xml配置", ["logback|Logback|xml"]),
         ("i18n-files", "i18n资源文件", ["messages|message|bundle|国际"]),
         ("java-go", "Java与Go竞争", ["Go|购语言|购语|原生"]),
         ("ali-aot", "阿里AOT实测", ["阿里|原生代码"])],
}


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


def auto_anchors(lines: list[tuple[float, str]], max_n: int = 3) -> list[tuple[str, str, float]]:
    """无硬编码锚点时的兜底：提取转写稿里的英文/代码样关键词，取首次出现时间。

    返回 [(label, caption, 抽帧秒), ...]，截图时间彼此至少错开15秒，避免扎堆。
    """
    from collections import Counter
    cnt, first = Counter(), {}
    for t, txt in lines:
        for m in re.finditer(r"@[A-Za-z]\w+|[A-Za-z][A-Za-z0-9_]{3,}", txt):
            tok = m.group(0)
            cnt[tok] += 1
            first.setdefault(tok, t)
    cands = sorted(((c, tok) for tok, c in cnt.items()
                    if tok.lower().lstrip("@") not in _STOP_WORDS),
                   key=lambda x: (-x[0], -len(x[1])))
    jobs, last_t = [], -999.0
    for c, tok in cands:
        if len(jobs) >= max_n:
            break
        label = re.sub(r"\W+", "-", tok.lstrip("@").lower()).strip("-")
        if not label:
            continue
        t = max(2.0, first[tok] - 3.0)
        if t - last_t < 15:
            continue
        jobs.append((label, f"关键词「{tok}」出现的画面", t))
        last_t = t
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


def grab_frames(video: Path, times: list[float]) -> list[object]:
    """在指定时间点各取一帧（PIL Image）。"""
    import av
    frames = []
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        for t in times:
            got = None
            container.seek(int(t / float(stream.time_base)), stream=stream, backward=True)
            for frame in container.decode(stream):
                if frame.time is not None and frame.time >= t - 0.4:
                    got = frame
                    break
            if got is not None:
                frames.append(got.to_image())
    return frames


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
        if page_no in ANCHORS:
            for label, caption, patterns in ANCHORS[page_no]:
                t = find_anchor_times(lines, patterns)
                if t is not None:
                    jobs.append((label, caption, t))
        if not jobs:  # 无硬编码锚点（如抖音平台）时自动提取
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
            times = [t for _, _, t in jobs]
            frames = grab_frames(video, times)
            results = []
            for (label, caption, t), img in zip(jobs, frames):
                if img is None:
                    continue
                fname = f"p{page_no:02d}_{label}.png"
                img.save(img_dir / fname)
                results.append({"file": fname, "caption": caption, "t": round(t, 1)})
            print(json.dumps({"page": page_no, "part": part,
                              "frames": results, "status": "ok"}, ensure_ascii=False), flush=True)
        except Exception as e:
            print(json.dumps({"page": page_no, "status": "error", "error": str(e)},
                             ensure_ascii=False), flush=True)
        finally:
            if video is not local_media:
                video.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
