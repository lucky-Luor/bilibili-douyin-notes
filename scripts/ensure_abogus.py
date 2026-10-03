#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按需下载抖音 a_bogus 签名算法脚本 abogus.py（本仓库不包含、也不分发该文件）。

法律地位：
    abogus.py 是第三方 GPLv3 代码（抖音 a_bogus / 风控签名算法）。
    本仓库不包含、也不分发该组件；由使用者在自己的机器上按需获取，
    按其自身条款使用。下载到本地后，该文件遵循其自身的 GPLv3 条款。

    上游文件头的许可声明自相矛盾（同文件第 4 行 GPLv3、第 9 行 Apache-2.0），
    无法确认原作者真实意图；在无法按宽松许可处理时，只能按更严格的
    GPLv3 从严对待。

上游溯源链：
    JoeanAmier/TikTokDownloader（原始实现，GPLv3）
      -> Evil0ctal/Douyin_TikTok_Download_API（移植）
        -> JefferyHcool/BiliNote（简化移植，本脚本采用的下载源）

供应链固定（supply-chain pinning）：
    下载 URL 固定到上游仓库的具体 commit（PINNED_COMMIT），不跟随分支漂移；
    下载内容必须通过 SHA-256 校验（EXPECTED_SHA256，为该 commit 文件的实测真值）
    与关键字标记校验，两者任一不符即拒绝落盘并打印实测值。
    ghproxy 镜像仅作为传输通道保留，镜像下载的内容同样必须通过全部校验。
    设置环境变量 BILINOTE_ABOGUS_SHA 可把 commit 指向其他 revision
    （用于上游更新后的临时跟进）；此时本脚本无法预知其哈希，只做
    标记 + 编译校验并打印醒目警告。

手动安装（网络不通时的兜底方案）：
    1. 浏览器打开下方 SOURCES 中的任一地址，另存为 abogus.py；
    2. 把文件放到 scripts/abogus.py（与本脚本同目录）；
    3. 重新运行即可，本脚本检测到文件有效后会跳过下载。
"""
import hashlib
import os
import py_compile
import sys
import tempfile
import urllib.request
from pathlib import Path

# 上游仓库中 abogus.py 的定位（commit 固定，不用分支名，防止上游漂移）
REPO = "JefferyHcool/BiliNote"
UPSTREAM_PATH = "backend/app/downloaders/douyin_helper/abogus.py"

# 固定的上游 commit（abogus.py 最后一次变动的 commit，2025-05-02）
PINNED_COMMIT = "04dad3b72aa13e1550a241bc0318348f2a665ce7"

# 该 commit 的 abogus.py 内容 SHA-256 实测真值（17980 字节，utf-8）
EXPECTED_SHA256 = "848733908c7ae97b91f9569784cf22255cff90a8c426787cee350c692011fe4e"

# 环境变量：覆盖固定的 commit SHA（上游更新后的临时跟进手段）
ENV_COMMIT = "BILINOTE_ABOGUS_SHA"


def effective_commit() -> str:
    """返回生效的 commit SHA：环境变量优先，否则用固定值。"""
    return (os.environ.get(ENV_COMMIT, "") or "").strip() or PINNED_COMMIT


def sources(commit: str) -> list[str]:
    """给定 commit 的下载源列表：官方 raw 优先，ghproxy 镜像兜底（仅传输通道）。"""
    base = f"https://raw.githubusercontent.com/{REPO}/{commit}/{UPSTREAM_PATH}"
    return [base, f"https://ghproxy.net/{base}"]


# 兼容旧名字（只读展示用）；实际下载一律走 sources(effective_commit())
SOURCES = sources(PINNED_COMMIT)

TARGET = Path(__file__).resolve().parent / "abogus.py"

# 下载后内容校验标记：缺少任一关键字即视为无效文件
REQUIRED_MARKERS = ("class ABogus", "def get_value")

RETRIES = 3       # 每个下载源的重试次数
TIMEOUT = 30      # 单次请求超时（秒）


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _markers_ok(text: str) -> bool:
    return all(marker in text for marker in REQUIRED_MARKERS)


def _valid(path: Path, strict_hash: bool = True) -> bool:
    """检查 abogus.py 是否存在、可读、含必要标记，且（固定 commit 模式下）哈希相符。"""
    try:
        if not path.is_file():
            return False
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return False
    if not _markers_ok(text):
        return False
    if strict_hash and effective_commit() == PINNED_COMMIT \
            and sha256_text(text) != EXPECTED_SHA256:
        return False
    return True


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


def ensure(quiet: bool = False) -> bool:
    """确保 scripts/abogus.py 存在且有效，必要时从上游固定 commit 下载。

    返回 True 表示文件已就绪；无法获得通过校验的文件时抛出 RuntimeError。
    quiet=True 时静默模式（成功时不打印进度信息）。
    """
    commit = effective_commit()
    strict_hash = commit == PINNED_COMMIT
    if not strict_hash and not quiet:
        print(f"[ensure_abogus] 警告：{ENV_COMMIT} 已把 commit 覆盖为 {commit}，"
              "无法预知该 revision 的 SHA-256，仅做标记+编译校验。", file=sys.stderr)

    if _valid(TARGET, strict_hash=strict_hash):
        if not quiet:
            print(f"[ensure_abogus] OK: {TARGET} 已存在且通过校验")
        return True

    if not quiet:
        print(f"[ensure_abogus] {TARGET} 缺失或未通过校验，"
              f"开始从上游 commit {commit[:12]} 下载 ...", file=sys.stderr)
    errors = []
    for url in sources(commit):
        try:
            text = _download(url)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url} -> {e}")
            continue
        if not _markers_ok(text):
            errors.append(f"{url} -> 内容校验失败（缺少必要标记）")
            continue
        if strict_hash:
            actual = sha256_text(text)
            if actual != EXPECTED_SHA256:
                # 镜像只是传输通道，下载内容同样必须过哈希校验
                errors.append(f"{url} -> SHA-256 校验失败：期望 {EXPECTED_SHA256}，"
                              f"实测 {actual}，拒绝使用")
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
            print(f"[ensure_abogus] 已下载并通过 SHA-256/标记/编译校验: {TARGET}",
                  file=sys.stderr)
        return True

    manual = (
        "无法自动下载 abogus.py（抖音签名算法，GPLv3，本仓库不分发该组件）。\n"
        "请手动下载：\n"
        f"  1. 打开 {sources(commit)[0]}\n"
        "  2. 将文件另存为: " + str(TARGET) + "\n"
        "  3. 重新运行本 skill 的抖音流程\n"
        "（网络受限环境可尝试镜像: " + sources(commit)[1] +
        "，镜像内容同样必须通过 SHA-256 校验）\n"
        "自动下载失败详情:\n  " + "\n  ".join(errors)
    )
    raise RuntimeError(manual)


if __name__ == "__main__":
    # stdout 兜底为 UTF-8：Agent 管道调用时编码常是 gbk/cp936，
    # 含 emoji/生僻字的消息会让 print 直接 UnicodeEncodeError（第三轮复检 M3）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        ensure()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
