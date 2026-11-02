#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真实链接冒烟探针：四个平台各取几条公开链接，只拿元数据和字幕，不下载视频。

用法:
    python smoke_probe.py [--urls 链接清单.json] [--platform bilibili,youtube] [--out 结果.json] [--strict]

说明:
    - 链接清单默认读 references/smoke_urls.json，格式见该文件的 "_说明" 字段
    - 抖音、小红书只解析到视频地址；抖音额外对地址发一次 64KB 的范围请求，确认地址可用
    - 每条链接单独计时、单独捕获异常，一条失败不影响其他链接
    - stdout 输出 Markdown 汇总表；--out 另存完整 JSON；--strict 时有失败则退出码为 1
"""
import argparse  # 命令行参数解析
import json  # 读写 JSON
import sys  # 退出码、模块搜索路径
import time  # 计时
from pathlib import Path  # 路径处理

SCRIPTS = Path(__file__).resolve().parent  # 本脚本所在的 scripts 目录
if str(SCRIPTS) not in sys.path:  # 确保能导入同目录的各平台脚本
    sys.path.insert(0, str(SCRIPTS))  # 加入模块搜索路径
DEFAULT_URLS = SCRIPTS.parent / "references" / "smoke_urls.json"  # 默认链接清单位置
RANGE_BYTES = 65536  # 抖音视频地址可用性检查只读这么多字节
ERROR_MAX = 300  # 错误信息最多保留的字符数


def _reconfigure_stdout() -> None:  # Windows 管道下统一用 UTF-8 输出
    for stream in (sys.stdout, sys.stderr):  # 标准输出和标准错误都处理
        try:  # 老版本 Python 或被替换的流可能没有该方法
            stream.reconfigure(encoding="utf-8", errors="replace")  # 改为 UTF-8，无法编码的字符替换
        except (AttributeError, ValueError):  # 不支持时保持原样
            pass  # 忽略


def probe_bilibili(url: str) -> dict:  # B站：view 元数据 + 合集字段 + 第 1 个分P的字幕
    import bili_fetch as b  # 惰性导入，避免未用到的平台拖慢启动
    bvid = b.extract_bvid(url)  # 解析 BV 号
    info = b.api(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")["data"]  # 视频详情
    pages = info.get("pages") or []  # 分P列表
    season = info.get("ugc_season") or {}  # 合集信息，没有时为空
    episodes = sum(len(s.get("episodes") or []) for s in season.get("sections") or [])  # 合集里的视频总数
    subs, saw_track = (b.fetch_subtitles(bvid, pages[0]["cid"], "", rounds=1)  # 免登录取一次字幕
                       if pages else ([], False))  # 没有分P时跳过
    return {"title": info.get("title", ""),  # 标题
            "pages": len(pages),  # 分P数
            "season_id": season.get("id"),  # 合集 ID
            "season_episodes": episodes,  # 合集视频数
            "subtitle": bool(subs),  # 是否拿到可用字幕
            "subtitle_track": saw_track}  # 是否出现过字幕轨


def probe_youtube(url: str) -> dict:  # YouTube：yt-dlp 元数据 + 字幕两级回退
    import yt_fetch as y  # 惰性导入
    vid = y.extract_video_id(url)  # 解析视频 ID
    proxy = y.resolve_proxy(None, y.load_config())  # 读取代理配置
    opts = y.ydl_base_opts(proxy, None, None)  # yt-dlp 公共参数
    info = y.fetch_info(vid, opts)  # 元数据，被拦截时这里会抛错
    sub, sub_error = None, ""  # 字幕结果与字幕阶段的错误
    try:  # 一级：youtube-transcript-api
        sub = y.fetch_via_transcript_api(vid, y.DEFAULT_LANGS, proxy)  # 被封 IP 时抛 RuntimeError
    except RuntimeError as e:  # 一级被拦截
        sub_error = f"transcript-api: {e}"  # 记下原因，继续走二级
    if not sub:  # 一级没拿到
        sub = y.fetch_via_ytdlp(info, y.DEFAULT_LANGS, opts)  # 二级：yt-dlp 字幕
    if not sub and not info.get("duration"):  # 没字幕也没时长：被风控时 yt-dlp 只返回带标题的空壳
        raise RuntimeError(f"只拿到标题，没有时长和字幕，视为被风控拦截（{sub_error or '字幕为空'}）")  # 判为失败，避免假成功
    return {"title": info.get("title", ""),  # 标题
            "duration": int(info.get("duration") or 0),  # 时长（秒）
            "subtitle": bool(sub),  # 是否拿到字幕
            "subtitle_kind": sub[1] if sub else "",  # 字幕来源
            "subtitle_error": sub_error}  # 一级字幕的错误信息


def probe_douyin(url: str) -> dict:  # 抖音：详情接口 + 视频地址 + 64KB 范围请求
    import douyin_fetch as d  # 惰性导入（需要 gmssl 与 abogus.py）
    aweme_id = d.extract_video_id(url)  # 解析作品 ID，短链会先跟随跳转
    detail = d.fetch_aweme_detail(aweme_id)  # 详情接口
    d.check_not_album(detail)  # 图集帖直接退出
    vurl, kind = d.pick_video_url(detail)  # 按接口原样选视频地址
    resp = d.http(vurl, headers={"User-Agent": d.UA,  # 只读开头一小段，验证地址可用
                                 "Referer": "https://www.douyin.com/",  # 抖音 CDN 需要来源页
                                 "Range": f"bytes=0-{RANGE_BYTES - 1}"})  # 范围请求
    got = len(resp.read(RANGE_BYTES + 1))  # 实际读到的字节数
    return {"title": (detail.get("desc") or "")[:60],  # 标题（截断）
            "duration": round((detail.get("video", {}).get("duration") or 0) / 1000),  # 时长（秒）
            "video_source": kind,  # 地址来源
            "range_bytes": got}  # 范围请求读到的字节数


def probe_xiaohongshu(url: str) -> dict:  # 小红书：解析分享页 + 视频地址
    import xhs_fetch as x  # 惰性导入
    note_id, token, source = x.resolve_link(url)  # 解析笔记 ID 与 xsec_token
    note = x.fetch_note(note_id, token, source, x.load_cookie())  # 请求并解析笔记页
    x.check_is_video(note)  # 图文笔记直接退出
    _, kind = x.pick_video_url(note)  # 选视频地址
    return {"title": (note.get("title") or "")[:60],  # 标题（截断）
            "duration": x.duration_of(note),  # 时长（秒）
            "video_source": kind}  # 地址来源


PROBES = {"bilibili": probe_bilibili,  # 平台名 → 探测函数
          "youtube": probe_youtube,  # YouTube
          "douyin": probe_douyin,  # 抖音
          "xiaohongshu": probe_xiaohongshu}  # 小红书


def run_one(platform: str, url: str) -> dict:  # 探测单条链接，任何异常都转成失败记录
    start = time.monotonic()  # 开始计时
    detail, error = {}, ""  # 结果与错误
    try:  # 平台脚本可能抛普通异常，也可能用 SystemExit 给人话提示
        detail = PROBES[platform](url)  # 调用对应平台的探测函数
    except SystemExit as e:  # 平台脚本的人话退出
        error = str(e.code)  # 退出信息就是提示文案
    except Exception as e:  # 网络错误、风控、接口变动等
        error = f"{type(e).__name__}: {e}"  # 记录异常类型和信息
    return {"platform": platform,  # 平台
            "url": url,  # 链接
            "ok": not error,  # 没有错误即成功
            "seconds": round(time.monotonic() - start, 1),  # 耗时（秒）
            "error": error[:ERROR_MAX],  # 截断后的错误信息
            "detail": detail}  # 平台返回的摘要


def load_urls(path: Path, platforms: list[str] | None) -> list[tuple[str, str]]:  # 读取链接清单
    data = json.loads(path.read_text(encoding="utf-8"))  # 解析 JSON
    jobs = []  # (平台, 链接) 列表
    for platform in PROBES:  # 按固定顺序遍历平台
        if platforms and platform not in platforms:  # 只跑指定平台
            continue  # 跳过
        for item in data.get(platform) or []:  # 该平台的链接条目
            url = item.get("url") if isinstance(item, dict) else item  # 兼容字符串和对象两种写法
            if url:  # 空链接跳过（清单里允许先留空占位）
                jobs.append((platform, url))  # 加入任务
    return jobs  # 返回任务列表


def summarize(results: list[dict]) -> dict:  # 按平台统计成功率
    summary = {}  # 平台 → 统计
    for r in results:  # 遍历每条结果
        s = summary.setdefault(r["platform"], {"total": 0, "ok": 0})  # 取或建该平台的计数
        s["total"] += 1  # 总数加一
        s["ok"] += 1 if r["ok"] else 0  # 成功数加一
    for s in summary.values():  # 计算成功率
        s["rate"] = round(s["ok"] / s["total"], 3) if s["total"] else 0.0  # 保留三位小数
    return summary  # 返回统计


def render_markdown(results: list[dict], summary: dict) -> str:  # 生成 Markdown 汇总
    lines = ["| 平台 | 成功 / 总数 | 成功率 |", "|---|---|---|"]  # 汇总表表头
    for platform, s in summary.items():  # 每个平台一行
        lines.append(f"| {platform} | {s['ok']} / {s['total']} | {s['rate']:.0%} |")  # 汇总行
    lines += ["", "| 平台 | 结果 | 耗时(s) | 链接 | 摘要 / 错误 |", "|---|---|---|---|---|"]  # 明细表表头
    for r in results:  # 每条链接一行
        info = r["error"] if not r["ok"] else json.dumps(r["detail"], ensure_ascii=False)  # 失败写错误，成功写摘要
        info = info.replace("|", "\\|").replace("\n", " ")  # 转义表格分隔符、去掉换行
        lines.append(f"| {r['platform']} | {'✅' if r['ok'] else '❌'} | {r['seconds']} | {r['url']} | {info} |")  # 明细行
    return "\n".join(lines)  # 拼成文本


def parse_args(argv=None):  # 解析命令行参数
    ap = argparse.ArgumentParser(description="四平台真实链接冒烟探针（不下载视频）")  # 参数解析器
    ap.add_argument("--urls", default=str(DEFAULT_URLS), help="链接清单 JSON 路径")  # 清单路径
    ap.add_argument("--platform", default="", help="只跑这些平台，逗号分隔")  # 平台过滤
    ap.add_argument("--out", default="", help="另存完整 JSON 结果的路径")  # JSON 输出路径
    ap.add_argument("--strict", action="store_true", help="有失败时退出码为 1")  # 严格模式
    return ap.parse_args(argv)  # 返回解析结果


def main(argv=None) -> int:  # 主流程：读清单 → 逐条探测 → 汇总输出
    _reconfigure_stdout()  # 统一输出编码
    args = parse_args(argv)  # 解析参数
    platforms = [p.strip() for p in args.platform.split(",") if p.strip()]  # 平台过滤列表
    unknown = [p for p in platforms if p not in PROBES]  # 不认识的平台名
    if unknown:  # 有拼错的平台名
        raise SystemExit(f"未知平台: {', '.join(unknown)}；可选: {', '.join(PROBES)}")  # 人话退出
    jobs = load_urls(Path(args.urls), platforms or None)  # 读取任务
    if not jobs:  # 清单为空
        raise SystemExit(f"链接清单里没有可用链接: {args.urls}")  # 人话退出
    results = []  # 全部结果
    for platform, url in jobs:  # 逐条探测
        print(f"[smoke] {platform} {url}", file=sys.stderr, flush=True)  # 进度走 stderr
        results.append(run_one(platform, url))  # 记录结果
    summary = summarize(results)  # 统计
    print(render_markdown(results, summary))  # stdout 输出 Markdown
    if args.out:  # 需要另存 JSON
        Path(args.out).write_text(json.dumps({"summary": summary, "results": results},  # 写入完整结果
                                             ensure_ascii=False, indent=2), encoding="utf-8")  # UTF-8 落盘
    failed = sum(1 for r in results if not r["ok"])  # 失败条数
    return 1 if args.strict and failed else 0  # 严格模式下有失败返回 1


if __name__ == "__main__":  # 脚本入口
    sys.exit(main())  # 以主流程返回值作为退出码
