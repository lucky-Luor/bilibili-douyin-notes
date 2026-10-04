#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes 第一步（抖音平台）：获取元数据并下载无水印视频到本地。

用法:
    python douyin_fetch.py <抖音链接或分享口令文本> [输出目录]

支持链接: www.douyin.com/video/{id} / v.douyin.com 短链 / iesdouyin.com/share/video/{id}
          或直接粘贴整段分享口令文本（自动正则抠出链接）

输出（与 bili_fetch.py 同一份 metadata.json 约定，方便共用第二/三步脚本）:
    <输出目录>/metadata.json   平台/标题/作者/时长/分P列表（page=1, media_path=本地视频）
    <输出目录>/media_p01.mp4   无水印视频（已存在且>1MB 且无 .part 残片时跳过下载，
                               直接复用；下载先写 .part 临时文件，完成后原子替换）
    stdout 最后一行输出 JSON 摘要（供调用方解析），进度信息走 stderr

原理（全部游客身份，无需登录、无需用户 Cookie）:
  1. POST ttwid.bytedance.com/ttwid/union/register/     -> 游客 ttwid
  2. msToken 本地伪造即可（实测服务端不校验）
  3. 业务参数 + abogus.py 计算 a_bogus（SM3，依赖 gmssl）
  4. 追加 Argus 风控参数: verifyFp/fp(本地生成) + uifid(本地伪造) +
     x-secsdk-web-signature = md5(uifid_ts_SALT_query)
  5. GET /aweme/v1/web/aweme/detail/ -> aweme_detail
  6. play_addr 把 /playwm/ 替换为 /play/ 得无水印地址，download_addr 兜底

依赖: pip install gmssl ；abogus.py（抖音签名算法，GPLv3）由 ensure_abogus.py
首次使用时自动下载，不入本仓库（保持仓库主体 MIT）。
若接口返回 403/Signature 错误，多半是抖音升级了签名算法，
参考上游开源项目（TikTokDownloader / Douyin_TikTok_Download_API / BiliNote）
更新 abogus.py 或 WEB_SIGN_SALT。
"""
import hashlib
import http.client
import json
import os
import random
import re
import secrets
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from abogus import ABogus
except ImportError:
    import ensure_abogus
    ensure_abogus.ensure()
    from abogus import ABogus

# 此 UA 必须与 abogus.py 中硬编码的 ua_code 配套，不能随意更换
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36")

TTWID_REQ = ('{"region":"cn","aid":1768,"needFid":false,'
             '"service":"www.ixigua.com","migrate_info":{"ticket":"","source":"node"},'
             '"cbUrlProtocol":"https","union":true}')

WEB_SIGN_SALT = "A96D855A08C0A9707F8BEF0D9A527E4E"

# 直连国内接口，不走系统代理
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def log(msg: str):
    print(msg, file=sys.stderr, flush=True)


def _reconfigure_stdout() -> None:
    """stdout 兜底为 UTF-8：Agent 管道调用时编码常是 gbk/cp936，
    emoji 标题会让 print 直接 UnicodeEncodeError（第三轮复检 M3）。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def http(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    return _OPENER.open(req, timeout=60)


def extract_video_id(text: str) -> str:
    """从链接/分享口令文本中解析 19 位左右的 aweme_id。"""
    m = re.search(r"https?://v\.douyin\.com/[\w-]+", text)
    if m:  # 短链：跟随 302 重定向（与主请求共用 _OPENER，保持代理/UA 一致）
        req = urllib.request.Request(m.group(0), headers={"User-Agent": UA})
        text = _OPENER.open(req, timeout=30).geturl()
    m = re.search(r"(?:video/|aweme_id=|/note/)(\d{15,})", text) or \
        re.search(r"^(\d{15,})$", text.strip())
    if m:
        return m.group(1)
    raise SystemExit(f"无法从输入中解析抖音视频ID: {text[:100]}")


def get_ttwid() -> str:
    resp = http("https://ttwid.bytedance.com/ttwid/union/register/",
                data=TTWID_REQ.encode(),
                headers={"Content-Type": "application/json"})
    body = resp.read().decode()
    m = re.search(r"ttwid=([^;]+)", resp.headers.get("Set-Cookie", "")) or \
        re.search(r'"ttwid":"?([^",}]+)', body)
    if not m:
        raise RuntimeError(f"ttwid 注册失败: {body[:200]}")
    log("[1] ttwid OK")
    return m.group(1)


def gen_verify_fp() -> str:
    """本地生成 s_v_web_id / verify_fp（uuid4 形状）。"""
    alpha = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

    def b36(v):
        d = ""
        while v > 0:
            d = (chr(97 + v % 36 - 10) if v % 36 >= 10 else str(v % 36)) + d
            v //= 36
        return d or "0"

    tail = [""] * 36
    for i in (8, 13, 18, 23):
        tail[i] = "_"
    tail[14] = "4"
    for i in range(36):
        if tail[i]:
            continue
        p = int(random.random() * len(alpha))
        if i == 19:
            p = 3 & p | 8
        tail[i] = alpha[p]
    return "verify_" + b36(round(time.time() * 1000)) + "_" + "".join(tail)


def encode_pairs(pairs) -> str:
    """仿 JS URLSearchParams.toString() 的序列化（与签名预映像逐字节一致）。"""
    return "&".join(f"{quote(k, safe='*-._')}={quote(v, safe='*-._')}" for k, v in pairs)


def fetch_aweme_detail(aweme_id: str) -> dict:
    ttwid = get_ttwid()
    ms_token = "".join(random.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(38)) + "=="
    verify_fp = gen_verify_fp()
    uifid = secrets.token_hex(24) + secrets.token_hex(1)

    params = {
        "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
        "pc_client_type": "1", "version_code": "290100", "version_name": "29.1.0",
        "cookie_enabled": "true", "screen_width": "1920", "screen_height": "1080",
        "browser_language": "zh-CN", "browser_platform": "Win32",
        "browser_name": "Chrome", "browser_version": "130.0.0.0",
        "browser_online": "true", "engine_name": "Blink", "engine_version": "130.0.0.0",
        "os_name": "Windows", "os_version": "10", "cpu_core_num": "12",
        "device_memory": "8", "platform": "PC", "downlink": "10",
        "effective_type": "4g", "round_trip_time": "0",
        "msToken": ms_token, "aweme_id": aweme_id,
    }
    bogus = ABogus().get_value(params)
    pairs = list(params.items()) + [("a_bogus", bogus),
                                    ("verifyFp", verify_fp), ("fp", verify_fp)]
    stamp = str(int(time.time()))
    pairs += [("uifid", uifid), ("timestamp", stamp)]
    preimage = encode_pairs(pairs)
    sig = hashlib.md5(f"{uifid}_{stamp}_{WEB_SIGN_SALT}_{preimage}".encode()).hexdigest()
    query = f"{preimage}&x-secsdk-web-signature={sig}"

    url = f"https://www.douyin.com/aweme/v1/web/aweme/detail/?{query}"
    headers = {
        "User-Agent": UA,
        "Referer": "https://www.douyin.com/",
        "Accept": "application/json, text/plain, */*",
        "Cookie": f"ttwid={ttwid}; msToken={ms_token}; "
                  f"s_v_web_id={verify_fp}; UIFID_TEMP={uifid}",
        "uifid": uifid,
        "x-secsdk-web-signature": sig,
        "x-secsdk-web-expire": stamp,
    }
    log("[2] GET detail API (a_bogus + x-secsdk-web-signature) ...")
    data = None
    for attempt in range(3):
        try:
            resp = http(url, headers=headers)
            data = json.loads(resp.read().decode())
            break
        except (http.client.IncompleteRead, urllib.error.URLError,
                json.JSONDecodeError, TimeoutError) as e:
            if attempt == 2:
                raise
            log(f"[2] transient error ({e}), retry {attempt + 2}/3")
            time.sleep(2)
    if not (data or {}).get("aweme_detail"):
        raise RuntimeError(f"detail 接口无 aweme_detail（可能签名算法已失效，见文件头说明）: "
                           f"{json.dumps(data, ensure_ascii=True)[:300]}")
    return data["aweme_detail"]


def pick_video_url(detail: dict):
    """优先无水印 play_addr（playwm->play），兜底带水印 download_addr。"""
    play = detail["video"].get("play_addr", {}).get("url_list") or []
    if play:
        return play[0].replace("/playwm/", "/play/"), "play_addr (no watermark)"
    dl = detail["video"].get("download_addr", {}).get("url_list") or []
    if dl:
        return dl[0], "download_addr (watermarked)"
    raise RuntimeError("detail 里没有视频下载地址")


# 图集/图文类 aweme_type：68=图集(多图)，150=图文
ALBUM_AWEME_TYPES = {68, 150}


def check_not_album(detail: dict):
    """图集/图文帖直接退出（人话提示，不 traceback）：本工具只处理视频。"""
    images = detail.get("images") or []
    video = detail.get("video") or {}
    has_video_addr = bool((video.get("play_addr") or {}).get("url_list")
                          or (video.get("download_addr") or {}).get("url_list"))
    if (images and not has_video_addr) or detail.get("aweme_type") in ALBUM_AWEME_TYPES:
        raise SystemExit("这是图集帖（图文），本工具只处理视频，请换一个视频链接再试。")


def download(url: str, dest: Path) -> int:
    """先写 .part 临时文件，下载完成后原子替换为正式文件名（中断不产生半个正式文件）。"""
    part = dest.with_name(dest.name + ".part")
    log(f"[3] downloading -> {dest.name}")
    n = 0
    resp = http(url, headers={"User-Agent": UA, "Referer": "https://www.douyin.com/"})
    with open(part, "wb") as f:
        while chunk := resp.read(1 << 16):
            f.write(chunk)
            n += len(chunk)
    resp.close()
    os.replace(part, dest)
    return n


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()[:60]


def note_type_for(duration_sec, page_count) -> str:
    """建议笔记型别（判据见 references/note-template.md）：short 当且仅当
    总时长 ≤600s 且 分P数 == 1，否则 lecture。分P数只做 tiebreak，不作主判据。
    脚本只给建议，Agent 可在笔记 frontmatter 的 note_type 覆盖，门禁只读 frontmatter。
    （与 bili_fetch.note_type_for 同一套判据，抖音视频恒为单分P。）"""
    return "short" if (int(duration_sec) <= 600 and int(page_count) == 1) else "lecture"


def main():
    _reconfigure_stdout()
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    source = sys.argv[1]
    aweme_id = extract_video_id(source)
    outdir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(f"douyin-notes-{aweme_id}")
    outdir.mkdir(parents=True, exist_ok=True)

    detail = fetch_aweme_detail(aweme_id)
    check_not_album(detail)
    title = (detail.get("desc") or detail.get("item_title") or "抖音视频").strip()
    owner = detail.get("author", {}).get("nickname", "")
    duration_ms = detail.get("video", {}).get("duration") or 0
    duration_sec = round(duration_ms / 1000)

    media = outdir / "media_p01.mp4"
    part = outdir / "media_p01.mp4.part"
    if part.exists():
        # .part 残留说明上次下载中断：删残片重新下载，不复用残片也不复用旧缓存
        log("[3] 发现上次中断的 .part 残片，删除后重新下载")
        part.unlink()
    if media.exists() and media.stat().st_size > 1024 * 1024:
        log("[3] 本地已有缓存视频，跳过下载")
    else:
        vurl, source_kind = pick_video_url(detail)
        n = download(vurl, media)
        if n < 100 * 1024:
            raise RuntimeError(f"下载的文件过小({n}字节)，可能被风控，请重试")
        log(f"[3] downloaded {n} bytes ({source_kind})")

    meta = {
        "platform": "douyin",
        "video_id": aweme_id,
        "title": title,
        "owner": owner,
        "desc": detail.get("desc", ""),
        "duration": duration_sec,
        "suggested_note_type": note_type_for(duration_sec, 1),
        "pages": [{"page": 1, "part": safe_name(title) or "正片",
                   "duration": duration_sec,
                   "media_path": media.name, "subtitle": False}],
    }
    meta_path = outdir / "metadata.json"
    # 原子写：先写 .tmp 再 os.replace，中断不留半个 metadata.json（与 bili_fetch 一致）
    tmp = meta_path.with_name(meta_path.name + ".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, meta_path)

    # ensure_ascii=True：GBK 管道下也不崩，json.loads 后 emoji 照样还原（M3）
    print(json.dumps({
        "platform": "douyin", "video_id": aweme_id, "title": title, "owner": owner,
        "outdir": str(outdir), "media": str(media),
        "duration_sec": duration_sec,
        "suggested_note_type": meta["suggested_note_type"],
        "pages": [{"page": 1, "part": meta["pages"][0]["part"],
                   "subtitle": False, "media_path": media.name}],
    }, ensure_ascii=True))


if __name__ == "__main__":
    main()
