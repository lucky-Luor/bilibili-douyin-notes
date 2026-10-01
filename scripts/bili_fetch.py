#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-notes 第一步：通过视频链接获取元数据与官方字幕（含AI字幕，需SESSDATA）。

用法:
    python bili_fetch.py <视频链接或BV号> [输出目录]

输出:
    <输出目录>/metadata.json   视频元数据（标题、UP主、分P列表等）
    <输出目录>/<页码>_<分P名>.txt  每个分P的字幕文本（有时间戳），无字幕则跳过
    stdout 最后一行输出 JSON 摘要（供调用方解析）
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CONFIG = Path(__file__).resolve().parent.parent / "config.json"


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
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def api(url: str, cookie: str = "") -> dict:
    return json.loads(http_get(url, cookie).decode("utf-8"))


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


def subtitle_to_text(sub_json: dict) -> str:
    return "\n".join(f"[{fmt_ts(it['from'])}] {it['content']}"
                     for it in sub_json.get("body", []))


def pick_subtitle(subtitles: list) -> dict | None:
    """优先中文字幕（CC > AI），其次任意一条。"""
    def rank(s):
        lan = s.get("lan", "")
        ai = s.get("ai_type", 0) != 0 or lan.startswith("ai")
        zh = "zh" in lan
        return (not zh, ai)  # CC中文(0,0) > AI中文(0,1) > 其他
    return sorted(subtitles, key=rank)[0] if subtitles else None


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()[:60]


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    source = sys.argv[1]
    sessdata = load_sessdata()

    bvid = extract_bvid(source)
    outdir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(f"bili-notes-{bvid}")
    outdir.mkdir(parents=True, exist_ok=True)

    info = api(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")["data"]
    pages = info["pages"]
    meta = {
        "bvid": bvid,
        "aid": info["aid"],
        "title": info["title"],
        "owner": info["owner"]["name"],
        "desc": info["desc"],
        "duration": info["duration"],
        "pages": [{"page": p["page"], "part": p["part"], "cid": p["cid"],
                   "duration": p["duration"]} for p in pages],
    }
    meta_path = outdir / "metadata.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {"bvid": bvid, "title": meta["title"], "owner": meta["owner"],
              "outdir": str(outdir), "sessdata_loaded": bool(sessdata), "pages": []}

    for p in pages:
        page_no, part, cid = p["page"], p["part"], p["cid"]
        entry = {"page": page_no, "part": part, "cid": cid, "subtitle": False}
        try:
            player = api(f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}",
                         sessdata)
            subs = (player.get("data") or {}).get("subtitle", {}).get("subtitles", [])
            sub = pick_subtitle(subs)
            if sub:
                sub_url = sub["subtitle_url"]
                if sub_url.startswith("//"):
                    sub_url = "https:" + sub_url
                text = subtitle_to_text(json.loads(http_get(sub_url, sessdata).decode("utf-8")))
                if text.strip():
                    fname = f"{page_no:02d}_{safe_name(part)}.txt"
                    (outdir / fname).write_text(text, encoding="utf-8")
                    entry.update(subtitle=True, lan=sub.get("lan", ""),
                                 ai=bool(sub.get("ai_type")), file=fname)
        except Exception as e:  # 单个分P失败不阻塞整体
            entry["error"] = str(e)
        report["pages"].append(entry)

    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
