#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按需下载抖音 a_bogus 签名算法脚本 abogus.py（本仓库不含该文件）。

为什么 abogus.py 不入库？
    abogus.py 是第三方 GPLv3 代码（抖音 a_bogus / 风控签名算法），GPL 的
    "传染性"（copyleft）要求：只要仓库里分发这份代码，整个合并作品就必须
    整体按 GPL-3.0 分发。为了让本仓库主体保持宽松的 MIT 协议，我们把
    abogus.py 移出仓库，改为首次使用抖音功能时由本脚本按需下载到本地。
    本地下载后该文件遵循其自身的 GPLv3 条款，与仓库主体的 MIT 协议互不影响
   （"聚合作品 aggregate"关系，不构成 GPL 传染）。

上游文件头的许可自相矛盾：
    上游 abogus.py 的文件头第 4 行声称 "GNU General Public License v3.0"，
    第 9 行又出现 "Licensed under the Apache License, Version 2.0"。
    两个强 copyleft/弱许可声明互相矛盾，无法确认原作者真实意图；
    在法律上无法按宽松的 Apache-2.0 处理时，只能按更严格的 GPLv3 从严处理。

上游溯源链：
    JoeanAmier/TikTokDownloader（原始实现，GPLv3）
      -> Evil0ctal/Douyin_TikTok_Download_API（移植）
        -> JefferyHcool/BiliNote（简化移植，本脚本采用的下载源）

手动安装（网络不通时的兜底方案）：
    1. 浏览器打开下方 SOURCES 中的任一地址，另存为 abogus.py；
    2. 把文件放到 scripts/abogus.py（与本脚本同目录）；
    3. 重新运行即可，本脚本检测到文件有效后会跳过下载。
"""
import py_compile
import sys
import tempfile
import urllib.request
from pathlib import Path

# 下载源，按序尝试（第 1 个为官方 raw 地址，第 2 个为国内加速镜像兜底）
SOURCES = [
    "https://raw.githubusercontent.com/JefferyHcool/BiliNote/master/"
    "backend/app/downloaders/douyin_helper/abogus.py",
    "https://ghproxy.net/https://raw.githubusercontent.com/JefferyHcool/"
    "BiliNote/master/backend/app/downloaders/douyin_helper/abogus.py",
]

TARGET = Path(__file__).resolve().parent / "abogus.py"

# 下载后内容校验标记：缺少任一关键字即视为无效文件
REQUIRED_MARKERS = ("class ABogus", "def get_value")

RETRIES = 3       # 每个下载源的重试次数
TIMEOUT = 30      # 单次请求超时（秒）


def _valid(path: Path) -> bool:
    """检查 abogus.py 是否存在、可读且内容有效。"""
    try:
        if not path.is_file():
            return False
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return False
    return all(marker in text for marker in REQUIRED_MARKERS)


def _download(url: str) -> str:
    """从单个源下载内容（带重试），返回文本；全部失败抛出最后一个异常。"""
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001 网络/解码异常统一重试
            last_err = e
            if attempt < RETRIES:
                print(f"    retry {attempt}/{RETRIES - 1} after error: {e}",
                      file=sys.stderr)
    raise last_err


def _content_ok(text: str) -> bool:
    return all(marker in text for marker in REQUIRED_MARKERS)


def ensure(quiet: bool = False) -> bool:
    """确保 scripts/abogus.py 存在且有效，必要时从上游下载。

    返回 True 表示文件已就绪；无法获得有效文件时抛出 RuntimeError。
    quiet=True 时静默模式（成功时不打印进度信息）。
    """
    if _valid(TARGET):
        if not quiet:
            print(f"[ensure_abogus] OK: {TARGET} 已存在且有效")
        return True

    if not quiet:
        print(f"[ensure_abogus] {TARGET} 缺失或无效，开始从上游下载 ...",
              file=sys.stderr)
    errors = []
    for url in SOURCES:
        try:
            text = _download(url)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url} -> {e}")
            continue
        if not _content_ok(text):
            errors.append(f"{url} -> 内容校验失败（缺少必要标记）")
            continue
        # 先写临时文件并编译校验，全部通过后再原子替换到目标位置
        fd, tmp_name = tempfile.mkstemp(dir=str(TARGET.parent), suffix=".py.tmp")
        tmp_path = Path(tmp_name)
        try:
            with open(fd, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            py_compile.compile(str(tmp_path), doraise=True)
            tmp_path.replace(TARGET)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        if not quiet:
            print(f"[ensure_abogus] 已下载并校验通过: {TARGET}", file=sys.stderr)
        return True

    manual = (
        "无法自动下载 abogus.py（抖音签名算法，GPLv3，不入本仓库）。\n"
        "请手动下载：\n"
        f"  1. 打开 {SOURCES[0]}\n"
        "  2. 将文件另存为: " + str(TARGET) + "\n"
        "  3. 重新运行本 skill 的抖音流程\n"
        "（网络受限环境可尝试镜像: " + SOURCES[1] + "）\n"
        "自动下载失败详情:\n  " + "\n  ".join(errors)
    )
    raise RuntimeError(manual)


if __name__ == "__main__":
    try:
        ensure()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
