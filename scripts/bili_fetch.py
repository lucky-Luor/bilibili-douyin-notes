#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes 第一步（B站）：通过视频链接获取元数据与官方字幕（含AI字幕，需SESSDATA）。

用法:
    python bili_fetch.py <视频链接或BV号> [输出目录]
    python bili_fetch.py --probe <视频链接或BV号>   # 只探元数据（不抓字幕/不写文件）
    python bili_fetch.py --login                    # 扫码登录，SESSDATA 自动写入 config.json

输出:
    <输出目录>/metadata.json   视频元数据（标题、UP主、分P列表等）
    <输出目录>/<页码>_<分P名>.txt  每个分P的字幕文本（有时间戳），无字幕则跳过
    stdout 最后一行输出 JSON 摘要（供调用方解析）

字幕状态（每个分P的 subtitle_detail 字段）:
    cc / ai       拿到字幕（subtitle=true）；AI 字幕另做内容与标题的交叉校验
    ai_mismatch   AI 字幕整体错位（返回了 ai 状态但内容是别的音频，实测案例
                  BV1GKaG6zEtr 配的是无关电影对白）→ 不可信，存疑字幕另存
                  *.ai字幕存疑.txt 备查，本分P按无字幕处理直接走 ASR 兜底
    api_empty     任一接口出现了字幕轨但全无可用 URL（/x/player/v2 对部分视频
                  只给元信息不给 URL 的已知问题，已自动尝试 wbi/v2 兜底）
                  → 提示用户填 SESSDATA 后重跑，仍失败再走 ASR
    none          所有字幕接口都完全没有字幕轨 → 确实无字幕，直接走 ASR，
                  不必提示 SESSDATA
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CONFIG = Path(__file__).resolve().parent.parent / "config.json"
RETRIES = 3  # 与抖音侧一致的失败重试次数


def load_sessdata() -> str:
    try:
        return (json.loads(CONFIG.read_text(encoding="utf-8")).get("SESSDATA") or "").strip()
    except Exception:
        return ""


LOGIN_DOC = """B站扫码登录：生成二维码 PNG 图片（路径打印在下方），用 B站 App 扫描，
SESSDATA 自动写入 config.json。二维码过期会自动换新并覆盖同一路径；180 秒未完成则超时。
无 B站 App 时按 README「获取 SESSDATA」手动填写。"""

PASSPORT = "https://passport.bilibili.com"


def _cookies_from_set_cookie(raw_list) -> dict:
    """从 Set-Cookie 头列表解析 {name: value}（仅取首对 k=v，丢弃属性）。"""
    cookies = {}
    for raw in raw_list or []:
        k, _, v = raw.partition("=")
        cookies[k.strip()] = v.split(";", 1)[0]
    return cookies


def _qrcode_generate() -> tuple[str, str]:
    data = api(f"{PASSPORT}/x/passport-login/web/qrcode/generate")["data"]
    return data["qrcode_key"], data["url"]


def _poll_qrcode(qrcode_key: str) -> tuple[int, dict]:
    """轮询扫码状态。返回 (code, cookies)。
    code: 0=成功(cookies 含 SESSDATA) / 86101=未扫 / 86090=已扫未确认 / 86038=过期"""
    req = urllib.request.Request(
        f"{PASSPORT}/x/passport-login/web/qrcode/poll?qrcode_key={qrcode_key}",
        headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return (body.get("data") or {}).get("code"), _cookies_from_set_cookie(
        resp.headers.get_all("Set-Cookie"))


def _render_qr(url: str) -> str | None:
    """生成登录二维码，返回展示方式（PNG 文件路径 / "ascii"），失败返回 None。

    主路径是 PNG 图片文件而非终端 ASCII：ASCII 二维码经聊天工具/非等宽字体
    转发极易错位变形，用户根本扫不出来（2026-10-04 实测案例）；图片文件让
    Agent 能直接把路径递给用户打开扫码。优先写当前工作目录（Agent 好找），
    不可写退回临时目录，再不行退回终端 ASCII，全失败返回 None。
    """
    try:
        import qrcode
    except ImportError:
        print("提示: 未安装 qrcode 库（pip install qrcode pillow），无法生成二维码",
              file=sys.stderr)
        return None
    candidates = [Path.cwd() / "bilibili-login-qr.png"]
    import tempfile
    candidates.append(Path(tempfile.gettempdir()) / "bdn-login-qr.png")
    for out in candidates:
        try:
            qrcode.make(url).save(out)  # PNG 需要 pillow
            print(f"二维码图片: {out}（用B站App扫一扫；过期自动刷新后覆盖同一路径）",
                  file=sys.stderr)
            return str(out)
        except Exception:
            continue
    try:  # 兜底：终端 ASCII 渲染（非等宽环境下可能变形）
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.print_ascii(invert=True)
        return "ascii"
    except Exception:
        return None


def _save_sessdata(sessdata: str) -> None:
    """只更新 SESSDATA 字段，保留 config.json 其他配置；原子写。"""
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg["SESSDATA"] = sessdata
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG)


def _nav_name(sessdata: str) -> str:
    """登录态回显账号昵称；失败返回空串不阻塞。"""
    try:
        headers = {"User-Agent": UA}
        if sessdata:
            headers["Cookie"] = f"SESSDATA={sessdata}"
        req = urllib.request.Request("https://api.bilibili.com/x/web-interface/nav",
                                     headers=headers)
        data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
        return (data.get("data") or {}).get("uname") or ""
    except Exception:
        return ""


def login() -> None:
    """扫码登录：二维码 → 轮询 → SESSDATA 自动写入 config.json。"""
    _reconfigure_stdout()
    print(LOGIN_DOC, file=sys.stderr)
    key, url = _qrcode_generate()
    if not _render_qr(url):
        raise SystemExit("二维码渲染失败：pip install qrcode pillow 后重试，"
                         "或按 README「获取 SESSDATA」手动填写 config.json")
    state, deadline = "", time.time() + 180
    while time.time() < deadline:
        code, cookies = _poll_qrcode(key)
        if code == 0:
            sess = cookies.get("SESSDATA", "")
            if not sess:
                raise SystemExit("登录成功但未取到 SESSDATA cookie，请重试或走手动路径")
            _save_sessdata(sess)
            who = _nav_name(sess)
            print(f"登录成功：{who or 'B站账号'}，SESSDATA 已写入 {CONFIG}", file=sys.stderr)
            print("提醒: config.json 含登录凭据，请勿分享；B站重新登录后旧值会失效",
                  file=sys.stderr)
            print_json_summary({"status": "ok", "action": "login", "user": who})
            return
        if code == 86038:  # 二维码过期：自动换新并重渲染（同一路径覆盖），无需重跑
            key, url = _qrcode_generate()
            print("二维码已过期，已自动刷新，请重新扫码", file=sys.stderr)
            if not _render_qr(url):
                print("警告: 刷新后的二维码渲染失败，建议重跑 --login", file=sys.stderr)
        elif code == 86090 and state != "scanned":
            state = "scanned"
            print("已扫码，请在手机上确认登录…", file=sys.stderr)
        time.sleep(2)
    raise SystemExit("超时：180 秒内未完成扫码登录，可重新运行本命令")


def http_get(url: str, cookie: str = "", retries: int = RETRIES) -> bytes:
    """带重试的 GET：网络类错误按 1s/2s 退避重试，与抖音侧对称。"""
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


def api(url: str, cookie: str = "") -> dict:
    """请求B站 API 并校验业务 code，失效视频/风控给出可读报错而非裸 KeyError。"""
    data = json.loads(http_get(url, cookie).decode("utf-8"))
    if data.get("code") != 0:
        raise RuntimeError(f"接口失败 code={data.get('code')}: {data.get('message')} ({url[:80]})")
    return data


def extract_bvid(text: str) -> str:
    m = re.search(r"(BV[0-9A-Za-z]{10})", text)
    if m:
        return m.group(1)
    if "b23.tv" in text:  # 短链：跟随重定向解析
        req = urllib.request.Request(text.strip(), headers={"User-Agent": UA})
        final_url = urllib.request.urlopen(req, timeout=30).geturl()
        m = re.search(r"(BV[0-9A-Za-z]{10})", final_url)
        if m:
            return m.group(1)
    raise SystemExit(f"无法从输入中解析BV号: {text}")


def fmt_ts(sec) -> str:
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def note_type_for(duration_sec, page_count) -> str:
    """建议笔记型别（规格 §4.4 坑③）：short 当且仅当 总时长 ≤600s 且 分P数 == 1，
    否则 lecture。分P数只做 tiebreak，不作主判据。脚本只给建议，
    Agent 可在笔记 frontmatter 的 note_type 覆盖，门禁只读 frontmatter。"""
    return "short" if (int(duration_sec) <= 600 and int(page_count) == 1) else "lecture"


def subtitle_to_text(sub_json: dict) -> str:
    return "\n".join(f"[{fmt_ts(it['from'])}] {it['content']}"
                     for it in sub_json.get("body", []))


def atomic_write_text(path: Path, text: str):
    """先写 .tmp 临时文件再 os.replace，避免中断留下半个文件。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def fetch_subtitles(bvid: str, cid: int, cookie: str) -> tuple[list, bool]:
    """依次尝试 player/v2 与 player/wbi/v2，返回 (可用字幕条目, 是否出现过字幕轨)。

    /x/player/v2 对大量视频只返回 ai-zh 元信息而 subtitle_url 为空（已知问题），
    wbi/v2 是实测更可能给出真实 URL 的兜底接口。

    返回值语义（调用方据此三态分发）:
        usable 非空           -> 有可用字幕 URL，直接走字幕
        usable 空 & saw_track -> 有字幕轨但全无可用 URL（api_empty，
                                 多半是 SESSDATA 过期/无权限，值得提示填写）
        usable 空 & 无轨      -> 所有接口都完全没有字幕轨（none，直接走 ASR）
    单个接口异常/失败视为"未探测到"，continue 尝试下一个接口，不算 none 的依据；
    只要任一接口出现了字幕轨（哪怕无可用 URL），saw_track 即为 True。
    """
    usable: list = []
    saw_track = False
    for path in ("x/player/v2", "x/player/wbi/v2"):
        try:
            player = api(f"https://api.bilibili.com/{path}?bvid={bvid}&cid={cid}", cookie)
        except Exception:
            continue  # 接口异常不置 saw_track，也不算"确认无字幕"
        subs = (player.get("data") or {}).get("subtitle", {}).get("subtitles", []) or []
        if subs:
            saw_track = True
        # 过滤空 URL：有 URL 的条目才可用；取第一个给出可用字幕的接口
        usable = [s for s in subs if (s.get("subtitle_url") or "").strip()]
        if usable:
            return usable, True
    return [], saw_track


def _reconfigure_stdout() -> None:
    """stdout 兜底为 UTF-8：Agent 管道调用时编码常是 gbk/cp936，
    emoji 标题会让 print 直接 UnicodeEncodeError（第三轮复检 M3）。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def print_json_summary(obj: dict, **kw) -> None:
    """stdout 机器可读 JSON 摘要行。ensure_ascii=True：即便兜底失效、
    遇到 GBK 管道也不会崩——JSON 转义无损，下游 json.loads 照样还原 emoji。"""
    print(json.dumps(obj, ensure_ascii=True, **kw))


def probe(bvid: str):
    """--probe 模式：只调 view 接口拿元数据并输出 JSON，不抓字幕、不建目录、不写文件。"""
    info = api(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")["data"]
    pages = [{"page": p["page"], "part": p["part"], "cid": p["cid"],
              "duration": p["duration"]} for p in info["pages"]]
    total = info.get("duration") or sum(p["duration"] for p in pages)
    print_json_summary({
        "bvid": bvid,
        "title": info["title"],
        "owner": info["owner"]["name"],
        "pages": pages,
        "duration": total,
        "suggested_note_type": note_type_for(total, len(pages)),
        # faster-whisper CPU int8 转写约为音频时长的 2 倍（实测经验值）
        "estimated_asr_minutes": round(total * 2 / 60, 1),
    })


def pick_subtitle(subtitles: list) -> dict | None:
    """优先中文字幕（CC > AI），其次任意一条。调用前应已过滤空URL。"""
    def rank(s):
        lan = s.get("lan", "")
        ai = s.get("ai_type", 0) != 0 or lan.startswith("ai")
        zh = "zh" in lan
        return (not zh, ai)  # CC中文(0,0) > AI中文(0,1) > 其他
    return sorted(subtitles, key=rank)[0] if subtitles else None


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()[:60]


AI_MISMATCH_SUFFIX = ".ai字幕存疑"


def title_tokens(title: str) -> list[str]:
    """提取标题里的 ASCII 词（≥2 字符），作为 AI 字幕交叉校验的锚点词。"""
    return re.findall(r"[A-Za-z][A-Za-z0-9]{1,}", title)


def subtitle_relevant(title: str, text: str) -> bool | None:
    """AI 字幕内容与标题的交叉校验（防 AI 字幕整体错位）。

    实测案例（2026-10，BV1GKaG6zEtr）：接口返回 ai 状态、内容却是完全不相关的
    电影对白，AI 字幕整体错位时内容依然"通顺"，只有拿标题做锚点才能识破。

    锚点只用标题里的 ASCII 词：技术词在 AI 字幕里几乎总按原样出现；中文词
    （"为什么""教程"之类）太容易和无关对白撞车，做不得锚点。

        标题无 ASCII 词 -> None（无从校验，信任字幕）
        任一锚点词命中   -> True
        全部未命中      -> False（判为错位，调用方应走 ASR 兜底）
    """
    tokens = title_tokens(title)
    if not tokens:
        return None
    low = text.lower()
    return any(t.lower() in low for t in tokens)


def main():
    _reconfigure_stdout()
    argv = [a for a in sys.argv[1:] if a not in ("--probe", "--login")]
    probe_only = len(argv) != len(sys.argv) - 1
    if "--login" in sys.argv[1:]:
        login()
        return
    if not argv:
        raise SystemExit(__doc__)
    source = argv[0]
    sessdata = load_sessdata()

    bvid = extract_bvid(source)
    if probe_only:  # 只探元数据：不建输出目录、不写任何文件
        probe(bvid)
        return

    outdir = Path(argv[1]) if len(argv) > 1 else Path(f"bili-notes-{bvid}")
    outdir.mkdir(parents=True, exist_ok=True)

    info = api(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")["data"]
    pages = info["pages"]
    # 总时长：view 接口已给则直接用；缺失时取各分P时长之和
    total_duration = info.get("duration") or sum(p.get("duration", 0) for p in pages)
    meta = {
        "bvid": bvid,
        "aid": info["aid"],
        "title": info["title"],
        "owner": info["owner"]["name"],
        "desc": info["desc"],
        "duration": total_duration,
        "suggested_note_type": note_type_for(total_duration, len(pages)),
        "pages": [{"page": p["page"], "part": p["part"], "cid": p["cid"],
                   "duration": p["duration"]} for p in pages],
    }
    meta_path = outdir / "metadata.json"
    atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))

    report = {"bvid": bvid, "title": meta["title"], "owner": meta["owner"],
              "outdir": str(outdir), "sessdata_loaded": bool(sessdata),
              "duration": total_duration,
              "suggested_note_type": meta["suggested_note_type"], "pages": []}

    for p in pages:
        page_no, part, cid = p["page"], p["part"], p["cid"]
        entry = {"page": page_no, "part": part, "cid": cid,
                 "subtitle": False, "subtitle_detail": "none"}
        try:
            subs, saw_track = fetch_subtitles(bvid, cid, sessdata)
            if subs:
                sub = pick_subtitle(subs)
                sub_url = sub["subtitle_url"]
                if sub_url.startswith("//"):
                    sub_url = "https:" + sub_url
                entry["subtitle_url"] = sub_url
                text = subtitle_to_text(json.loads(http_get(sub_url, sessdata).decode("utf-8")))
                if text.strip():
                    lan = sub.get("lan", "")
                    is_ai = bool(sub.get("ai_type")) or lan.startswith("ai")
                    if is_ai and subtitle_relevant(meta["title"], text) is False:
                        # AI 字幕与标题交叉校验未命中：多半是字幕整体错位（配的是
                        # 别的音频），内容看着"通顺"但不可信——存疑字幕另存备查，
                        # 本分P按无字幕处理，转写脚本接手 ASR 兜底
                        suspect = outdir / f"{page_no:02d}_{safe_name(part)}{AI_MISMATCH_SUFFIX}.txt"
                        atomic_write_text(suspect, text)
                        entry.update(subtitle=False, lan=lan, ai=True,
                                     subtitle_detail="ai_mismatch", file=suspect.name)
                    else:
                        fname = f"{page_no:02d}_{safe_name(part)}.txt"
                        atomic_write_text(outdir / fname, text)
                        entry.update(subtitle=True, lan=lan, ai=is_ai,
                                     subtitle_detail="ai" if is_ai else "cc", file=fname)
                else:
                    entry["subtitle_detail"] = "api_empty"  # 字幕内容体为空
            else:
                # 有字幕轨但全无可用URL -> api_empty（SESSDATA 过期/无权限的典型症状）；
                # 所有接口都完全没有字幕轨 -> none，直接走 ASR，不必折腾 SESSDATA
                entry["subtitle_detail"] = "api_empty" if saw_track else "none"
        except Exception as e:  # 单个分P失败不阻塞整体
            entry["subtitle_detail"] = "error"
            entry["error"] = str(e)
        report["pages"].append(entry)

    # 字幕详情回写进 metadata.json 的 pages[]，供后续步骤直接读取（不依赖 stdout 解析）
    by_page = {e["page"]: e for e in report["pages"]}
    for mp in meta["pages"]:
        e = by_page.get(mp["page"], {})
        mp["subtitle_detail"] = e.get("subtitle_detail")
        mp["subtitle_lang"] = e.get("lan")
        mp["subtitle_url"] = e.get("subtitle_url")
    atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))

    # 字幕轨在但拿不到 URL：几乎总是登录态问题——给出可执行的下一步（--login 扫码）
    if any(e.get("subtitle_detail") == "ai_mismatch" for e in report["pages"]):
        report["hint_mismatch"] = ("有分P的 AI 字幕内容与标题交叉校验未命中（字幕可能整体错位，"
                                   "如配成了别的音频），已按无字幕处理、将走语音识别兜底；"
                                   f"存疑字幕另存 *{AI_MISMATCH_SUFFIX}.txt 备查")
        print("提示: " + report["hint_mismatch"], file=sys.stderr)
    if any(e.get("subtitle_detail") == "api_empty" for e in report["pages"]):
        report["hint"] = ("有分P出现字幕轨但拿不到URL，通常需要登录态。"
                          "推荐：python scripts/bili_fetch.py --login 扫码登录后重跑；"
                          "无 B站 App 时按 README「获取 SESSDATA」手动填写 config.json")
        print("提示: " + report["hint"], file=sys.stderr)
    elif not sessdata and any(e.get("subtitle_detail") == "none" for e in report["pages"]):
        # 盲区修补：未登录时 B站接口连字幕轨信息都不返回，全部判 none（可能真没字幕，
        # 也可能只是没登录看不到 AI 字幕轨）——给轻提示，要不要扫码交给用户判断
        report["hint_optional"] = ("本次未配置登录态，字幕轨信息可能不完整；若该视频本应有"
                                   " AI 字幕，可运行 python scripts/bili_fetch.py --login"
                                   " 扫码后重跑，通常能直接跳过语音识别（快 10 倍以上）")
        print("提示: " + report["hint_optional"], file=sys.stderr)

    print_json_summary(report)


if __name__ == "__main__":
    main()
