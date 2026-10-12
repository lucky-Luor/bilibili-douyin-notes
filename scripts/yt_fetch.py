#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes 第一步（YouTube 平台）：获取元数据 + 字幕，按需下载音频。

用法:
    python yt_fetch.py <YouTube链接或11位视频ID> [输出目录] [选项]

支持链接: youtube.com/watch?v= / youtu.be/ / /shorts/ / /live/ / /embed/ / 纯 11 位 ID

选项:
    --lang zh-Hans,en      字幕语言优先级（逗号分隔，默认 zh-Hans,zh,zh-CN,zh-TW,zh-HK,en,en-US,en-GB）
    --download             无字幕时下载音频到 media_p01.m4a（供第二步 ASR）；有字幕时忽略
    --cookies <文件>       Netscape 格式 cookies.txt（年龄限制 / "not a bot" 风控时用）
    --cookies-from-browser <浏览器>  直接读本机浏览器 cookie（如 chrome / edge / firefox）
    --proxy <URL>          代理（优先级：本参数 > config.json 的 youtube_proxy > HTTPS_PROXY 环境变量）

输出（与 bili_fetch.py / douyin_fetch.py 同一份 metadata.json 约定）:
    <输出目录>/metadata.json        platform=youtube / video_id / 标题 / 作者 / 时长 / 分P列表
    <输出目录>/01_<标题>.txt        有字幕时写出（[mm:ss] 时间戳格式）+ .done 完成标记
    <输出目录>/media_p01.m4a        --download 且无字幕时的音频（供 bili_transcribe.py 转写）
    stdout 最后一行输出 JSON 摘要，进度信息走 stderr

字幕获取三级回退（参考 BiliNote youtube_subtitle.py 的做法）:
  1. youtube-transcript-api：人工字幕 → 自动字幕（不下载视频，最快）
  2. yt-dlp 的 subtitles / automatic_captions（json3 格式）：一级被封 IP / 年龄限制时，
     可配合 cookies 拿到
  3. 都没有 → subtitle_detail=none，交第二步 ASR（加 --download 下载音频）

依赖: pip install yt-dlp youtube-transcript-api ；YouTube 2025 年起要求 yt-dlp 配合 JS 运行时
（推荐安装 deno），否则可能缺格式或下载失败。
"""
import argparse  # 命令行参数解析
import json  # metadata / 摘要 / json3 字幕的序列化
import os  # 原子替换文件、读取环境变量
import re  # 链接与文件名正则处理
import sys  # stderr 日志与 stdout 编码设置
from pathlib import Path  # 路径操作

# 本 skill 安装目录下的 config.json（与 bili_fetch.py 共用同一份配置）
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

# 默认字幕语言优先级：中文简体 → 中文其他变体 → 英文（与 BiliNote 的优先级思路一致）
DEFAULT_LANGS = ["zh-Hans", "zh", "zh-CN", "zh-TW", "zh-HK", "en", "en-US", "en-GB"]

# YouTube 视频 ID 固定为 11 位 [A-Za-z0-9_-]
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# 从各种链接形态中抠出 ID：watch?v= / youtu.be/ / shorts/ / live/ / embed/ / v/
URL_ID_RE = re.compile(
    r"(?:[?&]v=|youtu\.be/|/shorts/|/live/|/embed/|/v/)([A-Za-z0-9_-]{11})")  # 统一捕获 11 位 ID

# 被封 IP / 风控时 youtube-transcript-api 抛出的异常类名（按名字判断，避免强依赖具体版本）
BLOCKED_ERROR_NAMES = {"RequestBlocked", "IpBlocked", "TooManyRequests"}


def log(msg: str):
    """进度日志统一走 stderr，stdout 只留最后一行 JSON 摘要。"""
    print(msg, file=sys.stderr, flush=True)  # flush 保证管道调用时实时可见


def _reconfigure_stdout() -> None:
    """stdout 兜底为 UTF-8：Agent 管道调用时编码常是 gbk/cp936（与 douyin_fetch 一致）。"""
    try:  # 某些宿主的 stdout 不支持 reconfigure
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 无法编码的字符替换而不是崩溃
    except Exception:  # 不支持时保持原样
        pass  # 静默降级，摘要本身用 ensure_ascii=True 兜底


def extract_video_id(text: str) -> str:
    """从链接 / 分享文本 / 纯 ID 中解析 11 位视频 ID，解析不出直接退出（人话提示）。"""
    text = (text or "").strip()  # 去掉首尾空白，兼容粘贴带换行
    if VIDEO_ID_RE.match(text):  # 本身就是 11 位 ID
        return text  # 直接返回
    m = URL_ID_RE.search(text)  # 在链接 / 整段文本中查找 ID
    if m:  # 命中任意一种链接形态
        return m.group(1)  # 返回捕获到的 ID
    raise SystemExit(f"无法从输入中解析 YouTube 视频ID: {text[:100]}")  # 统一的人话报错


def load_config() -> dict:
    """读取 config.json（不存在或损坏时返回空字典，不影响主流程）。"""
    try:  # 配置文件是可选的
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))  # 正常读取
    except (OSError, ValueError):  # 文件不存在或 JSON 损坏
        return {}  # 按无配置处理


def resolve_proxy(cli_proxy: str | None, config: dict) -> str | None:
    """代理优先级：命令行 > config.json 的 youtube_proxy > HTTPS_PROXY / https_proxy 环境变量。"""
    return (cli_proxy  # 1. 命令行显式指定
            or config.get("youtube_proxy")  # 2. 配置文件
            or os.environ.get("HTTPS_PROXY")  # 3. 大写环境变量
            or os.environ.get("https_proxy")  # 4. 小写环境变量
            or None)  # 都没有则直连


def ydl_base_opts(proxy: str | None, cookies: str | None, browser: str | None) -> dict:
    """yt-dlp 公共参数：静默、代理、cookie（字幕与下载共用同一套）。"""
    opts = {"quiet": True, "no_warnings": True, "noprogress": True,  # 不刷屏、不打印进度条（保护 stdout 摘要）
            "noplaylist": True}  # 只处理单个视频
    if proxy:  # 配置了代理
        opts["proxy"] = proxy  # 交给 yt-dlp
    if cookies:  # 指定了 cookies.txt
        opts["cookiefile"] = cookies  # 用文件 cookie
    if browser:  # 指定从浏览器读 cookie
        opts["cookiesfrombrowser"] = (browser,)  # yt-dlp 要求元组形式
    return opts  # 返回参数字典


def fetch_info(video_id: str, base_opts: dict) -> dict:
    """只取元数据不下载（借鉴 BiliNote：ignore_no_formats_error 避免格式问题拖垮元数据）。"""
    import yt_dlp  # 惰性导入：不用 YouTube 流程的用户无需安装

    opts = dict(base_opts, skip_download=True, ignore_no_formats_error=True)  # 只要信息、格式缺失不报错
    with yt_dlp.YoutubeDL(opts) as ydl:  # 创建下载器实例
        return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}",
                                download=False)  # 只解析不下载


def check_not_live(info: dict):
    """直播中 / 尚未开播的视频没有可用字幕与完整音频，直接人话退出。"""
    status = info.get("live_status")  # yt-dlp 的直播状态字段
    if status in ("is_live", "is_upcoming"):  # 正在直播或预告
        raise SystemExit("这是正在进行或尚未开始的直播，暂无法生成笔记，请等直播结束（回放生成后）再试。")  # 人话提示


def dedupe_rolling(segments: list[dict]) -> list[dict]:
    """自动字幕去重：滚动显示会让相邻片段重复上一句，合并后只保留新增部分。

    规则：当前文本与上一条完全相同 → 丢弃；当前文本以上一条为前缀 → 只保留新增尾巴；
    去完后为空的片段丢弃。
    """
    out: list[dict] = []  # 去重后的结果
    prev = ""  # 上一条原始文本
    for seg in segments:  # 按时间顺序遍历
        text = " ".join(seg["text"].split())  # 压缩空白与换行
        if not text or text == prev:  # 空行或完全重复
            continue  # 丢弃
        new = text[len(prev):].strip() if prev and text.startswith(prev) else text  # 前缀重复时只取新增部分
        prev = text  # 记录本条原文，供下一条比对
        if new:  # 新增部分非空才保留
            out.append({"start": seg["start"], "text": new})  # 保留开始时间与新文本
    return out  # 返回去重结果


def parse_json3(data: dict) -> list[dict]:
    """解析 YouTube json3 字幕：events[].tStartMs + segs[].utf8 拼成片段。"""
    segments: list[dict] = []  # 解析结果
    for ev in data.get("events") or []:  # 遍历事件
        segs = ev.get("segs")  # 文本分片
        if not segs:  # 纯窗口控制事件，无文本
            continue  # 跳过
        text = "".join(s.get("utf8", "") for s in segs).strip()  # 拼接分片文本
        if not text:  # 只有换行符的事件
            continue  # 跳过
        segments.append({"start": ev.get("tStartMs", 0) / 1000.0, "text": text})  # 毫秒转秒
    return segments  # 返回片段列表


def fmt_ts(sec: float) -> str:
    """秒转 [mm:ss] 中的 mm:ss（分钟可超过 59，与 bili_fetch.fmt_ts 一致的写法）。"""
    s = int(sec)  # 向下取整到秒
    return f"{s // 60:02d}:{s % 60:02d}"  # 补零格式化


def segments_to_txt(segments: list[dict]) -> str:
    """片段转为转写稿文本：每行 [mm:ss] 文本（与 B站字幕 txt 同格式）。

    片段内的换行压成空格，避免产生无时间戳的孤行（transcript-api 的人工字幕常带换行）。
    """
    return "\n".join(f"[{fmt_ts(s['start'])}] {' '.join(s['text'].split())}"  # 压缩换行与多余空白
                     for s in segments if s["text"].strip())  # 跳过空片段


def pick_lang(available: list[str], langs: list[str]) -> str | None:
    """按优先级从可用语言中挑一个：先精确匹配，再按主语言前缀（如 zh-Hans-xx → zh）匹配。"""
    for lang in langs:  # 先精确匹配
        if lang in available:  # 命中
            return lang  # 返回
    for lang in langs:  # 再按主语言前缀匹配
        base = lang.split("-")[0]  # 主语言代码
        for a in available:  # 遍历可用语言
            if a.split("-")[0] == base:  # 主语言相同
                return a  # 返回该轨道
    return None  # 都不匹配


def fetch_via_transcript_api(video_id: str, langs: list[str], proxy: str | None):
    """一级：youtube-transcript-api 拿字幕。返回 (片段, 类型, 语言)；无字幕返回 None。

    被封 IP 时抛 RuntimeError("blocked")，交由调用方回退到 yt-dlp。
    """
    try:  # 库是可选依赖
        from youtube_transcript_api import YouTubeTranscriptApi  # 惰性导入
    except ImportError:  # 未安装
        log("[2] 未安装 youtube-transcript-api，跳过一级字幕获取")  # 提示
        return None  # 直接走下一级

    kwargs = {}  # 构造参数
    if proxy:  # 需要代理
        from youtube_transcript_api.proxies import GenericProxyConfig  # 通用代理配置
        kwargs["proxy_config"] = GenericProxyConfig(http_url=proxy, https_url=proxy)  # http/https 同一代理
    api = YouTubeTranscriptApi(**kwargs)  # 新版 API 为实例方法
    try:  # 网络与风控异常
        tlist = api.list(video_id)  # 列出所有字幕轨道
        manual = [t.language_code for t in tlist if not t.is_generated]  # 人工字幕语言
        auto = [t.language_code for t in tlist if t.is_generated]  # 自动字幕语言
        lang = pick_lang(manual, langs)  # 先挑人工字幕
        kind = "manual"  # 默认类型
        if lang is None:  # 没有匹配的人工字幕
            lang = pick_lang(auto, langs)  # 再挑自动字幕
            kind = "auto"  # 类型改为自动
        if lang is None and (manual or auto):  # 优先级里都没有，但有别的语言
            lang, kind = (manual[0], "manual") if manual else (auto[0], "auto")  # 退而取第一条（原语言）
        if lang is None:  # 完全没有字幕
            return None  # 交给下一级
        fetched = tlist.find_transcript([lang]).fetch()  # 拉取该语言字幕
    except Exception as e:  # 风控或其他错误
        if type(e).__name__ in BLOCKED_ERROR_NAMES:  # 被封 IP
            raise RuntimeError("blocked") from e  # 通知调用方
        if type(e).__name__ in ("TranscriptsDisabled", "NoTranscriptFound"):  # 明确无字幕
            return None  # 正常交下一级
        log(f"[2] youtube-transcript-api 失败: {type(e).__name__}: {str(e)[:120]}")  # 其他错误记日志
        return None  # 交下一级
    # 新版返回 FetchedTranscriptSnippet 数据类，按属性读（避开 BiliNote 按 dict 读的坑）
    segs = [{"start": float(s.start), "text": s.text} for s in fetched]  # 转为统一片段
    return segs, kind, lang  # 返回结果


def fetch_via_ytdlp(info: dict, langs: list[str], base_opts: dict):
    """二级：从 yt-dlp 元数据的 subtitles / automatic_captions 取 json3 字幕。"""
    import yt_dlp  # 惰性导入

    for field, kind in (("subtitles", "ytdlp_manual"), ("automatic_captions", "ytdlp_auto")):  # 人工优先
        tracks = info.get(field) or {}  # 语言 -> 格式列表
        tracks = {k: v for k, v in tracks.items() if k != "live_chat"}  # 排除直播聊天轨道
        lang = pick_lang(list(tracks), langs)  # 按优先级挑语言
        if lang is None and field == "subtitles" and tracks:  # 人工字幕存在但不在优先级里
            lang = next(iter(tracks))  # 取第一条（原语言）
        if lang is None:  # 该来源无可用语言
            continue  # 看下一个来源
        fmt = next((f for f in tracks[lang] if f.get("ext") == "json3"), None)  # 找 json3 格式
        if not fmt:  # 没有 json3
            continue  # 看下一个来源
        with yt_dlp.YoutubeDL(base_opts) as ydl:  # 复用代理 / cookie
            raw = ydl.urlopen(fmt["url"]).read().decode("utf-8")  # 下载字幕内容
        segs = parse_json3(json.loads(raw))  # 解析 json3
        if segs:  # 有内容才算成功
            return segs, kind, lang  # 返回结果
    return None  # 两个来源都没拿到


def download_audio(video_id: str, outdir: Path, base_opts: dict) -> Path:
    """下载音频到 media_p01.m4a（借鉴 BiliNote 的 bestaudio[ext=m4a] 优先格式）。"""
    import yt_dlp  # 惰性导入

    dest = outdir / "media_p01.m4a"  # 目标文件
    if dest.exists() and dest.stat().st_size > 100 * 1024:  # 已有完整缓存
        log("[3] 本地已有缓存音频，跳过下载")  # 提示
        return dest  # 直接复用
    opts = dict(base_opts,  # 公共参数
                format="bestaudio[ext=m4a]/bestaudio",  # 优先 m4a 原始音轨，无需转码
                outtmpl=str(outdir / "media_p01.%(ext)s"))  # 输出文件名模板
    log("[3] downloading audio ...")  # 进度提示
    with yt_dlp.YoutubeDL(opts) as ydl:  # 创建下载器（yt-dlp 自带 .part 断点与原子改名）
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)  # 下载
        real = Path(ydl.prepare_filename(info))  # 实际落盘文件（扩展名可能是 webm）
    if real != dest and real.exists():  # 非 m4a 扩展名
        dest = real  # 以实际文件为准（PyAV 可直接解码 webm/opus）
    if not dest.exists() or dest.stat().st_size < 10 * 1024:  # 文件缺失或过小
        raise RuntimeError("音频下载失败或文件过小，可能被风控，请配置 cookies / 代理后重试")  # 人话报错
    return dest  # 返回音频路径


def safe_name(name: str) -> str:
    """文件名清洗：与 bili_transcribe.safe_name 同规则，保证 txt 文件名两边一致。"""
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()[:60]  # 替换非法字符并截断


def note_type_for(duration_sec, page_count) -> str:
    """建议笔记型别：与 bili_fetch / douyin_fetch 同一套判据（≤600s 且单分P → short）。"""
    return "short" if (int(duration_sec) <= 600 and int(page_count) == 1) else "lecture"  # 判据


def atomic_write(path: Path, text: str):
    """原子写：先写 .tmp 再 os.replace，中断不留半个文件（全仓落盘惯例）。"""
    tmp = path.with_name(path.name + ".tmp")  # 临时文件
    tmp.write_text(text, encoding="utf-8")  # 写入内容
    os.replace(tmp, path)  # 原子替换


def parse_args(argv=None):
    """命令行参数定义。"""
    ap = argparse.ArgumentParser(description="YouTube 视频 → metadata.json + 字幕 txt")  # 解析器
    ap.add_argument("source", help="YouTube 链接或 11 位视频 ID")  # 输入
    ap.add_argument("outdir", nargs="?", help="输出目录（默认 yt-notes-<视频ID>）")  # 输出目录
    ap.add_argument("--lang", help="字幕语言优先级，逗号分隔")  # 语言优先级
    ap.add_argument("--download", action="store_true", help="无字幕时下载音频供 ASR")  # 下载开关
    ap.add_argument("--cookies", help="Netscape 格式 cookies.txt 路径")  # cookie 文件
    ap.add_argument("--cookies-from-browser", dest="browser", help="从浏览器读取 cookie")  # 浏览器 cookie
    ap.add_argument("--proxy", help="代理 URL，如 http://127.0.0.1:7890")  # 代理
    return ap.parse_args(argv)  # 解析


def main(argv=None):
    """主流程：元数据 → 字幕三级回退 → 按需下载音频 → 写 metadata.json → 输出摘要。"""
    _reconfigure_stdout()  # 管道编码兜底
    args = parse_args(argv)  # 解析参数
    video_id = extract_video_id(args.source)  # 解析视频 ID
    outdir = Path(args.outdir) if args.outdir else Path(f"yt-notes-{video_id}")  # 输出目录
    outdir.mkdir(parents=True, exist_ok=True)  # 确保目录存在
    langs = [x.strip() for x in args.lang.split(",") if x.strip()] if args.lang else DEFAULT_LANGS  # 语言列表
    proxy = resolve_proxy(args.proxy, load_config())  # 代理
    base_opts = ydl_base_opts(proxy, args.cookies, args.browser)  # yt-dlp 公共参数

    log(f"[1] 获取元数据 video_id={video_id}")  # 进度
    try:  # 元数据失败通常是风控 / 视频不可用
        info = fetch_info(video_id, base_opts)  # 取元数据
    except Exception as e:  # yt-dlp 抛 DownloadError 等
        msg = str(e)  # 错误信息
        hint = ("（被要求登录验证：请加 --cookies-from-browser chrome 或 --cookies 后重试，"
                "必要时配置代理并安装 deno）") if "not a bot" in msg or "Sign in" in msg else ""  # 风控提示
        raise SystemExit(f"获取 YouTube 元数据失败: {msg[:200]}{hint}")  # 人话退出
    check_not_live(info)  # 直播中直接退出
    title = (info.get("title") or "YouTube视频").strip()  # 标题
    owner = info.get("uploader") or info.get("channel") or ""  # 作者
    duration_sec = int(info.get("duration") or 0)  # 时长（秒）
    part = safe_name(title) or "正片"  # 分P名（单分P即标题）

    log("[2] 获取字幕（transcript-api → yt-dlp）")  # 进度
    detail, sub_lang, segs = "none", None, None  # 默认无字幕
    try:  # 一级可能报被封
        got = fetch_via_transcript_api(video_id, langs, proxy)  # 一级
    except RuntimeError:  # 被封 IP
        log("[2] transcript-api 被封 IP，回退 yt-dlp")  # 提示
        got, detail = None, "blocked"  # 标记被封，继续回退
    if got is None:  # 一级没拿到
        try:  # 二级同样可能失败
            got = fetch_via_ytdlp(info, langs, base_opts)  # 二级
        except Exception as e:  # 网络 / 风控
            log(f"[2] yt-dlp 字幕获取失败: {str(e)[:120]}")  # 记日志
            got, detail = None, detail if detail == "blocked" else "error"  # 保留被封标记
    if got:  # 拿到字幕
        segs, detail, sub_lang = got  # 解包
        if detail.endswith("auto"):  # 自动字幕存在滚动重复
            segs = dedupe_rolling(segs)  # 去重
    txt_name = f"01_{part}.txt"  # 转写稿文件名（与 bili_transcribe 的命名一致）
    if segs:  # 有字幕则写 txt
        txt_path = outdir / txt_name  # 路径
        atomic_write(txt_path, segments_to_txt(segs))  # 原子写字幕
        (outdir / (txt_name + ".done")).write_text("ok", encoding="utf-8")  # 完成标记，转写脚本据此跳过
        log(f"[2] 字幕已写入 {txt_name}（{detail}, {sub_lang}, {len(segs)} 行）")  # 进度

    page = {"page": 1, "part": part, "duration": duration_sec,  # 分P基础信息
            "subtitle": bool(segs), "subtitle_detail": detail}  # 字幕状态
    if sub_lang:  # 记录字幕语言
        page["subtitle_lang"] = sub_lang  # 便于笔记判断是否需要翻译
    if not segs and args.download:  # 无字幕且要求下载
        audio = download_audio(video_id, outdir, base_opts)  # 下载音频
        page["media_path"] = audio.name  # 供 bili_transcribe 直接读取本地文件

    meta = {  # metadata.json 内容（字段与抖音一致）
        "platform": "youtube",  # 平台
        "video_id": video_id,  # 视频 ID（深链与门禁使用）
        "title": title,  # 标题
        "owner": owner,  # 作者
        "desc": info.get("description") or "",  # 简介（可能含代码仓库链接）
        "duration": duration_sec,  # 时长
        "suggested_note_type": note_type_for(duration_sec, 1),  # 建议笔记型别
        "pages": [page],  # 单分P
    }
    atomic_write(outdir / "metadata.json", json.dumps(meta, ensure_ascii=False, indent=2))  # 原子写元数据

    summary = {"platform": "youtube", "video_id": video_id, "title": title,  # 摘要基础字段
               "owner": owner, "outdir": str(outdir), "duration_sec": duration_sec,  # 作者 / 目录 / 时长
               "suggested_note_type": meta["suggested_note_type"], "pages": [page]}  # 型别与分P
    if detail == "blocked" and not segs:  # 被封且没拿到字幕
        summary["hint"] = "YouTube 拒绝了当前 IP：可配置代理（--proxy）或 cookies 后重跑，或直接加 --download 走 ASR"  # 引导
    elif not segs and not args.download:  # 无字幕且没下载
        summary["hint"] = "该视频无可用字幕：加 --download 重跑以下载音频，再进入第二步 ASR"  # 引导
    print(json.dumps(summary, ensure_ascii=True))  # ensure_ascii 保证 GBK 管道不崩


if __name__ == "__main__":  # 命令行入口
    main()  # 执行主流程
