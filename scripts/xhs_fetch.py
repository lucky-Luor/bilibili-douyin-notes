#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes 第一步（小红书平台）：获取视频笔记元数据并下载视频到本地。

用法:
    python xhs_fetch.py <小红书链接或分享文本> [输出目录] [--origin]

支持链接: xhslink.com 短链 / www.xiaohongshu.com/explore/{id}?xsec_token=...
          / www.xiaohongshu.com/discovery/item/{id}?xsec_token=... / 整段分享文本（自动抠出链接）
          注意：必须保留链接里的 xsec_token（直接复制 App / 网页的分享链接即可），
          只有笔记 ID 无法访问。

选项:
    --origin    下载原片（originVideoKey，体积可达默认流的十几倍）；默认下载 h264 转码流

输出（与 bili_fetch.py / douyin_fetch.py 同一份 metadata.json 约定，方便共用第二/三步脚本）:
    <输出目录>/metadata.json   平台/标题/作者/时长/分P列表（page=1, media_path=本地视频）
    <输出目录>/media_p01.mp4   视频（已存在且>1MB 且无 .part 残片时跳过下载；
                               下载先写 .part 临时文件，完成后原子替换）
    stdout 最后一行输出 JSON 摘要，进度信息走 stderr

原理（免登录、无签名，思路参考 ReaJason/xhs 的 get_note_by_id_from_html（MIT）
与 JoeanAmier/XHS-Downloader 的页面解析思路，代码为本项目独立实现）:
  1. 短链跟随 302 得到完整链接，保留 xsec_token / xsec_source
  2. 以手机浏览器 UA 请求笔记页（实测桌面 UA 未登录会被重定向登录页，手机 UA 不会）
  3. 解析 HTML 中的 window.__INITIAL_STATE__（undefined → null 后按 JSON 读）
  4. 数据位于 noteData.data.noteData（手机页）或 note.noteDetailMap[id].note（桌面页）
  5. type=normal 为图文笔记 → 拒绝；type=video 时按 h264 流 → h265 流 → 原片 选择下载地址

小红书视频一般没有字幕，第二步固定走 ASR（bili_transcribe.py 直接读本地 media_path）。
"""
import argparse  # 命令行参数解析
import json  # metadata / 摘要 / 页面状态的序列化
import os  # 原子替换文件
import re  # 链接与页面解析
import sys  # stderr 日志与 stdout 编码设置
import urllib.error  # HTTP 错误类型
import urllib.request  # 纯标准库 HTTP（与 douyin_fetch 一致，不引入新依赖）
from pathlib import Path  # 路径操作
from urllib.parse import parse_qs, quote, urlparse  # 解析 / 重建查询参数

# 手机浏览器 UA：实测未登录时桌面 UA 会被 302 到登录页，手机 UA 可直接拿到笔记数据
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")  # iOS Safari

# 本 skill 安装目录下的 config.json（可选 xhs_cookie，用于提高清晰度或缓解风控）
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

# 小红书原片 CDN（originVideoKey 拼接用）
ORIGIN_CDN = "https://sns-video-bd.xhscdn.com/"

# 风控验证码状态码（参考 MediaCrawler xhs/client.py 的处理）
CAPTCHA_CODES = {461, 471}

# 直连国内接口，不走系统代理（与 douyin_fetch 一致）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 链接识别：短链 / explore / discovery/item
SHORT_RE = re.compile(r"https?://xhslink\.com/[\w/]+")  # 分享短链
NOTE_URL_RE = re.compile(  # 完整笔记链接（含查询参数）
    r"https?://(?:www\.)?xiaohongshu\.com/(?:explore|discovery/item)/([0-9a-f]{24})[^\s，。]*")
STATE_RE = re.compile(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", re.S)  # 页面状态脚本


def log(msg: str):
    """进度日志统一走 stderr，stdout 只留最后一行 JSON 摘要。"""
    print(msg, file=sys.stderr, flush=True)  # flush 保证管道调用时实时可见


def _reconfigure_stdout() -> None:
    """stdout 兜底为 UTF-8：Agent 管道调用时编码常是 gbk/cp936（与 douyin_fetch 一致）。"""
    try:  # 某些宿主的 stdout 不支持 reconfigure
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 无法编码的字符替换而不是崩溃
    except Exception:  # 不支持时保持原样
        pass  # 静默降级，摘要本身用 ensure_ascii=True 兜底


def load_cookie() -> str:
    """读取 config.json 的 xhs_cookie（可选，不存在或损坏时返回空串）。"""
    try:  # 配置文件是可选的
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("xhs_cookie", "") or ""  # 正常读取
    except (OSError, ValueError):  # 文件不存在或 JSON 损坏
        return ""  # 按无 Cookie 处理


def http(url: str, cookie: str = ""):
    """统一 GET 请求：手机 UA + Referer，可选 Cookie。"""
    headers = {"User-Agent": UA,  # 手机 UA
               "Accept": "text/html,application/xhtml+xml,*/*",  # 接受 HTML
               "Accept-Language": "zh-CN,zh;q=0.9",  # 中文
               "Referer": "https://www.xiaohongshu.com/"}  # 来源页
    if cookie:  # 配置了 Cookie
        headers["Cookie"] = cookie  # 附带
    return _OPENER.open(urllib.request.Request(url, headers=headers), timeout=60)  # 发起请求


def resolve_link(text: str) -> tuple[str, str, str]:
    """从链接 / 分享文本解析 (笔记ID, xsec_token, xsec_source)；短链先跟随跳转。"""
    m = SHORT_RE.search(text or "")  # 先找短链
    if m:  # 是短链
        text = http(m.group(0)).geturl()  # 跟随 302 拿到完整链接
    m = NOTE_URL_RE.search(text or "")  # 找完整笔记链接
    if not m:  # 解析不到
        raise SystemExit(f"无法从输入中解析小红书笔记链接: {(text or '')[:100]}")  # 人话退出
    qs = parse_qs(urlparse(m.group(0)).query)  # 解析查询参数（会自动 URL 解码）
    token = (qs.get("xsec_token") or [""])[0]  # 访问令牌
    if not token:  # 没有令牌无法访问
        raise SystemExit("链接缺少 xsec_token：请在小红书 App / 网页点「分享 → 复制链接」，"
                         "把完整链接（或整段分享文本）发过来，只有笔记 ID 无法访问。")  # 引导用户
    source = (qs.get("xsec_source") or ["app_share"])[0]  # 来源，缺省按 App 分享
    return m.group(1), token, source  # 返回三元组


def note_page_url(note_id: str, token: str, source: str) -> str:
    """重建笔记页地址（只保留访问必需的参数，去掉分享追踪参数）。"""
    return (f"https://www.xiaohongshu.com/discovery/item/{note_id}"  # 手机页路径
            f"?xsec_token={quote(token, safe='')}&xsec_source={quote(source, safe='')}")  # 必需参数


def parse_state(html: str) -> dict:
    """解析页面中的 window.__INITIAL_STATE__（JS 字面量 undefined 替换为 null 后按 JSON 读）。"""
    m = STATE_RE.search(html)  # 定位状态脚本
    if not m:  # 页面结构变化或被拦截
        raise RuntimeError("页面中没有 __INITIAL_STATE__（可能被风控拦截或页面结构已变化）")  # 报错
    return json.loads(re.sub(r"\bundefined\b", "null", m.group(1)))  # 替换后解析


def extract_note(state: dict, note_id: str) -> dict:
    """从页面状态中取笔记对象：兼容手机页与桌面页两种结构；取不到返回空字典。"""
    mobile = ((state.get("noteData") or {}).get("data") or {}).get("noteData")  # 手机页结构
    if mobile:  # 命中手机页
        return mobile  # 直接返回
    detail = (state.get("note") or {}).get("noteDetailMap") or {}  # 桌面页结构
    item = detail.get(note_id) or (list(detail.values())[-1] if detail else {})  # 优先按 ID 取
    return (item or {}).get("note") or {}  # 返回笔记对象


def check_is_video(note: dict):
    """图文笔记直接人话退出：本工具只处理视频笔记（与抖音图集处理一致）。"""
    if note.get("type") != "video" or not note.get("video"):  # 不是视频笔记
        raise SystemExit("这是图文笔记，本工具只处理视频笔记，请换一个视频笔记链接再试。")  # 人话退出


def pick_video_url(note: dict, origin: bool = False) -> tuple[str, str]:
    """选择下载地址，返回 (URL, 来源说明)。

    默认 h264 流（PyAV 解码兼容最好、体积适中）→ h265 流 → 原片；
    --origin 时原片优先（实测 33 分钟视频原片约 3GB，720p h264 流约 220MB）。
    """
    video = note.get("video") or {}  # 视频对象
    key = ((video.get("consumer") or {}).get("originVideoKey") or "").strip()  # 原片 key
    origin_url = (ORIGIN_CDN + key) if key else ""  # 拼接原片地址
    if origin and origin_url:  # 用户要求原片
        return origin_url, "originVideoKey (原片)"  # 直接返回
    stream = (video.get("media") or {}).get("stream") or {}  # 转码流集合
    for codec in ("h264", "h265", "av1"):  # 按兼容性优先级遍历编码
        items = [s for s in stream.get(codec) or [] if s.get("masterUrl") or s.get("backupUrls")]  # 有地址的流
        if not items:  # 该编码没有可用流
            continue  # 看下一个编码
        best = max(items, key=lambda s: s.get("height") or 0)  # 同编码取最高清晰度
        url = best.get("masterUrl") or (best.get("backupUrls") or [""])[0]  # 主地址优先、备用兜底
        return url.replace("http://", "https://", 1), f"stream.{codec} {best.get('height')}p"  # 统一 https
    if origin_url:  # 没有转码流时用原片兜底
        return origin_url, "originVideoKey (原片)"  # 返回
    raise RuntimeError("笔记数据里没有视频下载地址")  # 都没有


def download(url: str, dest: Path, cookie: str = "") -> int:
    """先写 .part 临时文件，下载完成后原子替换为正式文件名（与 douyin_fetch 一致）。"""
    part = dest.with_name(dest.name + ".part")  # 临时文件
    log(f"[3] downloading -> {dest.name}")  # 进度
    n = 0  # 已下载字节数
    resp = http(url, cookie)  # 发起下载
    with open(part, "wb") as f:  # 写临时文件
        while chunk := resp.read(1 << 16):  # 64KB 分块读取
            f.write(chunk)  # 写入
            n += len(chunk)  # 累计
    resp.close()  # 关闭连接
    os.replace(part, dest)  # 原子替换
    return n  # 返回字节数


def safe_name(name: str) -> str:
    """文件名清洗：与 bili_transcribe.safe_name 同规则，保证 txt 文件名两边一致。"""
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()[:60]  # 替换非法字符并截断


def note_type_for(duration_sec, page_count) -> str:
    """建议笔记型别：与 bili_fetch / douyin_fetch 同一套判据（≤600s 且单分P → short）。"""
    return "short" if (int(duration_sec) <= 600 and int(page_count) == 1) else "lecture"  # 判据


def duration_of(note: dict) -> int:
    """视频时长（秒）：优先 capa.duration，其次 media.video.duration。"""
    video = note.get("video") or {}  # 视频对象
    return int((video.get("capa") or {}).get("duration")  # 创作端记录的时长
               or ((video.get("media") or {}).get("video") or {}).get("duration")  # 媒体信息时长
               or 0)  # 都没有记 0


def fetch_note(note_id: str, token: str, source: str, cookie: str) -> dict:
    """请求笔记页并解析出笔记对象；风控 / 跳登录 / 无数据时给出人话错误。"""
    url = note_page_url(note_id, token, source)  # 页面地址
    log(f"[1] GET note page {note_id}")  # 进度
    try:  # HTTP 层错误
        resp = http(url, cookie)  # 请求
    except urllib.error.HTTPError as e:  # 非 2xx
        if e.code in CAPTCHA_CODES:  # 风控验证码
            raise SystemExit(f"小红书触发风控验证（HTTP {e.code}）：请稍后再试，"
                             "或在 config.json 配置 xhs_cookie 后重跑。")  # 人话退出
        raise  # 其他错误原样抛出
    final_url = resp.geturl()  # 跟随跳转后的地址
    html = resp.read().decode("utf-8", "replace")  # 页面内容
    if "/login" in urlparse(final_url).path or "/404" in urlparse(final_url).path:  # 被跳登录页 / 404
        raise SystemExit("笔记页被重定向（登录页或 404）：xsec_token 可能已过期或笔记不可见，"
                         "请重新复制分享链接再试。")  # 人话退出
    note = extract_note(parse_state(html), note_id)  # 解析笔记
    if not note:  # 页面里没有笔记数据
        raise SystemExit("页面里没有笔记数据：xsec_token 可能已过期或笔记已删除/仅自己可见，"
                         "请重新复制分享链接再试。")  # 人话退出
    return note  # 返回笔记对象


def parse_args(argv=None):
    """命令行参数定义。"""
    ap = argparse.ArgumentParser(description="小红书视频笔记 → metadata.json + 本地视频")  # 解析器
    ap.add_argument("source", help="小红书链接或整段分享文本（需含 xsec_token）")  # 输入
    ap.add_argument("outdir", nargs="?", help="输出目录（默认 xhs-notes-<笔记ID>）")  # 输出目录
    ap.add_argument("--origin", action="store_true", help="下载原片（体积很大）")  # 原片开关
    return ap.parse_args(argv)  # 解析


def main(argv=None):
    """主流程：解析链接 → 抓页面 → 校验视频 → 下载 → 写 metadata.json → 输出摘要。"""
    _reconfigure_stdout()  # 管道编码兜底
    args = parse_args(argv)  # 解析参数
    cookie = load_cookie()  # 可选 Cookie
    note_id, token, source = resolve_link(args.source)  # 解析链接
    outdir = Path(args.outdir) if args.outdir else Path(f"xhs-notes-{note_id}")  # 输出目录
    outdir.mkdir(parents=True, exist_ok=True)  # 确保目录存在

    note = fetch_note(note_id, token, source, cookie)  # 抓取并解析笔记
    check_is_video(note)  # 图文笔记直接退出
    title = (note.get("title") or note.get("desc") or "小红书视频").strip()  # 标题（无标题用正文）
    user = note.get("user") or {}  # 作者对象
    owner = user.get("nickName") or user.get("nickname") or ""  # 手机页 / 桌面页字段名不同
    duration_sec = duration_of(note)  # 时长
    log(f"[2] {title[:40]} / {owner} / {duration_sec}s")  # 进度

    media = outdir / "media_p01.mp4"  # 本地视频路径
    part = outdir / "media_p01.mp4.part"  # 临时文件
    if part.exists():  # 上次下载中断
        log("[3] 发现上次中断的 .part 残片，删除后重新下载")  # 提示
        part.unlink()  # 删除残片
    if media.exists() and media.stat().st_size > 1024 * 1024:  # 已有完整缓存
        log("[3] 本地已有缓存视频，跳过下载")  # 提示
    else:  # 需要下载
        vurl, kind = pick_video_url(note, args.origin)  # 选择地址
        n = download(vurl, media, cookie)  # 下载
        if n < 100 * 1024:  # 文件过小
            raise RuntimeError(f"下载的文件过小({n}字节)，可能被风控，请重试")  # 报错
        log(f"[3] downloaded {n} bytes ({kind})")  # 进度

    meta = {  # metadata.json 内容（字段与抖音一致）
        "platform": "xiaohongshu",  # 平台
        "video_id": note_id,  # 笔记 ID
        "title": title,  # 标题
        "owner": owner,  # 作者
        "desc": note.get("desc", "") or "",  # 正文（含话题标签）
        "duration": duration_sec,  # 时长
        "suggested_note_type": note_type_for(duration_sec, 1),  # 建议笔记型别
        "pages": [{"page": 1, "part": safe_name(title) or "正片",  # 单分P
                   "duration": duration_sec,  # 分P时长
                   "media_path": media.name, "subtitle": False}],  # 本地视频、无字幕
    }
    meta_path = outdir / "metadata.json"  # 元数据路径
    tmp = meta_path.with_name(meta_path.name + ".tmp")  # 原子写临时文件
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")  # 写入
    os.replace(tmp, meta_path)  # 原子替换

    print(json.dumps({  # stdout 摘要（ensure_ascii=True：GBK 管道下也不崩）
        "platform": "xiaohongshu", "video_id": note_id, "title": title, "owner": owner,  # 基础字段
        "outdir": str(outdir), "media": str(media), "duration_sec": duration_sec,  # 目录 / 媒体 / 时长
        "suggested_note_type": meta["suggested_note_type"],  # 型别
        "pages": [{"page": 1, "part": meta["pages"][0]["part"],  # 分P摘要
                   "subtitle": False, "media_path": media.name}],  # 无字幕、本地视频
    }, ensure_ascii=True))  # 输出


if __name__ == "__main__":  # 命令行入口
    main()  # 执行主流程
