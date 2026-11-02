#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境自检：开工前逐项检查运行环境，每项给出 OK / WARN / FAIL 和修复命令。

用法:
    python doctor.py [--platform bilibili,douyin,youtube,xiaohongshu] [--offline] [--json]

检查项:
    Python 版本、语音识别与解码依赖、各平台可选依赖、模型缓存、推理设备、
    控制台编码、skill 文件是否被加密软件处理过、各平台网络可达性（含风控 412）

退出码: 有 FAIL 时为 1，否则为 0。--json 输出供智能体解析，默认输出给人看的清单。
"""
import argparse  # 命令行参数解析
import importlib.metadata  # 读取已安装包的版本
import importlib.util  # 判断模块是否可导入（不真正导入，速度快）
import json  # 读配置、输出 JSON
import locale  # 系统默认编码
import os  # 环境变量、平台判断
import shutil  # 查找可执行文件
import sys  # Python 版本、输出流、退出码
import urllib.error  # HTTP 错误
import urllib.request  # 网络探测
from pathlib import Path  # 路径处理

SCRIPTS = Path(__file__).resolve().parent  # scripts 目录
ROOT = SCRIPTS.parent  # skill 根目录
CONFIG = ROOT / "config.json"  # 用户配置文件
PLATFORMS = ("bilibili", "douyin", "youtube", "xiaohongshu")  # 支持的平台
PIP = "python -m pip install"  # 修复命令前缀
TESTED_PY = ((3, 10), (3, 12))  # CI 覆盖的最低与最高 Python 版本
MODEL_SIZES_MB = {"tiny": 75, "base": 145, "small": 484, "medium": 1530, "large-v3": 3090}  # 模型约略大小（MB）
NET_TIMEOUT = 8  # 网络探测超时（秒）
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"  # 浏览器 UA

# 各平台网络探测目标：(平台, 名称, 地址)
NET_TARGETS = (("bilibili", "B站接口", "https://api.bilibili.com/x/web-interface/view?bvid=BV1Wv411h7kN"),  # 实际用到的详情接口（nav 不触发风控，测不出 412）
               ("douyin", "抖音", "https://www.douyin.com/"),  # 首页
               ("youtube", "YouTube", "https://www.youtube.com/generate_204"),  # 连通性探测地址
               ("xiaohongshu", "小红书", "https://www.xiaohongshu.com/"),  # 首页
               (None, "模型镜像 hf-mirror", "https://hf-mirror.com/"))  # 首次下载模型用，所有平台都需要

# 各平台的可选依赖：(模块名, pip 包名, 用途, 适用平台)
OPTIONAL_DEPS = (("gmssl", "gmssl", "抖音接口请求", ("douyin",)),  # 抖音
                 ("yt_dlp", "yt-dlp", "YouTube 元数据与字幕", ("youtube",)),  # YouTube
                 ("youtube_transcript_api", "youtube-transcript-api", "YouTube 字幕（一级）", ("youtube",)),  # YouTube
                 ("PIL", "pillow", "截图画质初筛", PLATFORMS),  # 截图
                 ("numpy", "numpy", "截图画质初筛", PLATFORMS))  # 截图


def _reconfigure_stdout() -> None:  # Windows 管道下统一用 UTF-8 输出
    for stream in (sys.stdout, sys.stderr):  # 标准输出和标准错误都处理
        try:  # 被替换的流可能没有该方法
            stream.reconfigure(encoding="utf-8", errors="replace")  # 改为 UTF-8
        except (AttributeError, ValueError):  # 不支持时保持原样
            pass  # 忽略


def item(name: str, status: str, detail: str, fix: str = "") -> dict:  # 构造一条检查结果
    return {"name": name, "status": status, "detail": detail, "fix": fix}  # 统一结构


def has_module(name: str) -> bool:  # 模块是否可导入
    try:  # 父包缺失时 find_spec 会抛错
        return importlib.util.find_spec(name) is not None  # 找得到规格即可导入
    except (ImportError, ValueError):  # 父包缺失或名字非法
        return False  # 视为不可导入


def pkg_version(name: str) -> str:  # 读取包版本，取不到返回空
    try:  # 未安装时抛 PackageNotFoundError
        return importlib.metadata.version(name)  # 版本号
    except importlib.metadata.PackageNotFoundError:  # 未安装
        return ""  # 空字符串


def load_config() -> dict:  # 读取 config.json，没有或损坏时返回空
    try:  # 文件可能不存在或不是合法 JSON
        return json.loads(CONFIG.read_text(encoding="utf-8"))  # 解析配置
    except (OSError, ValueError):  # 读不到或解析失败
        return {}  # 按默认配置处理


def check_python(version=None) -> dict:  # Python 版本
    v = tuple(version or sys.version_info[:2])  # 当前版本（主、次）
    text = f"{v[0]}.{v[1]}"  # 版本文本
    if v < TESTED_PY[0]:  # 低于最低支持版本
        return item("Python 版本", "FAIL", f"当前 {text}，需要 3.10 或更高",  # 失败
                    "安装 Python 3.12：https://www.python.org/downloads/")  # 修复建议
    if v > TESTED_PY[1]:  # 高于 CI 覆盖的版本
        return item("Python 版本", "WARN", f"当前 {text}，CI 只覆盖 3.10 和 3.12，更新的版本未经测试",  # 警告
                    "依赖装不上或运行报错时，改用 Python 3.12")  # 修复建议
    return item("Python 版本", "OK", f"当前 {text}")  # 通过


def check_core_deps() -> list[dict]:  # 语音识别与解码的核心依赖
    out = []  # 结果列表
    for mod, pkg, use in (("faster_whisper", "faster-whisper", "无字幕时本地语音识别"),  # 语音识别
                          ("av", "av", "音视频解码与截图抽帧（自带 FFmpeg 库，无需另装 ffmpeg）")):  # 解码
        if has_module(mod):  # 已安装
            out.append(item(f"依赖 {pkg}", "OK", f"{pkg_version(pkg) or '已安装'}：{use}"))  # 通过
        else:  # 未安装
            out.append(item(f"依赖 {pkg}", "FAIL", f"未安装：{use}", f"{PIP} {pkg}"))  # 失败
    return out  # 返回结果


def check_optional_deps(platforms) -> list[dict]:  # 各平台的可选依赖
    out = []  # 结果列表
    for mod, pkg, use, scope in OPTIONAL_DEPS:  # 遍历可选依赖
        if not set(scope) & set(platforms):  # 与本次关心的平台无关
            continue  # 跳过
        if has_module(mod):  # 已安装
            out.append(item(f"依赖 {pkg}", "OK", f"{pkg_version(pkg) or '已安装'}：{use}"))  # 通过
        else:  # 未安装
            out.append(item(f"依赖 {pkg}", "WARN", f"未安装：{use}需要", f"{PIP} {pkg}"))  # 警告
    if "youtube" in platforms and has_module("yt_dlp") and not shutil.which("deno"):  # yt-dlp 解 JS 挑战建议装 deno
        out.append(item("可执行文件 deno", "WARN", "未找到：YouTube 风控时 yt-dlp 需要它解 JS 挑战",  # 警告
                        "安装 deno：https://deno.com/"))  # 修复建议
    return out  # 返回结果


def model_cached(size: str) -> bool:  # 模型是否已在本地 HF 缓存（与 bili_transcribe.py 判断一致）
    name = f"models--Systran--faster-whisper-{size}"  # 缓存目录名
    bases = [Path.home() / ".cache" / "huggingface" / "hub"]  # 默认缓存位置
    if os.environ.get("HF_HOME"):  # 自定义了 HF_HOME
        bases.append(Path(os.environ["HF_HOME"]) / "hub")  # 加入自定义位置
    return any((b / name).exists() for b in bases)  # 任一位置存在即可


def check_model(config: dict) -> dict:  # 语音识别模型缓存
    size = config.get("asr_model") or "small"  # 配置的模型，默认 small
    if model_cached(size):  # 已缓存
        return item("语音识别模型", "OK", f"{size} 已在本地缓存，可离线使用")  # 通过
    mb = MODEL_SIZES_MB.get(size)  # 约略大小
    return item("语音识别模型", "WARN",  # 警告
                f"{size} 未缓存：首次语音识别会自动下载{f'约 {mb} MB' if mb else ''}（国内默认走 hf-mirror 镜像）",  # 说明
                "保持网络畅通即可；想提前下载可先转写一个短视频")  # 修复建议


def check_device(config: dict) -> dict:  # 推理设备
    want = config.get("asr_device") or "auto"  # 配置的设备
    cuda = False  # 是否有可用 CUDA
    try:  # ctranslate2 未安装或驱动异常时会抛错
        import ctranslate2  # 惰性导入
        cuda = ctranslate2.get_cuda_device_count() > 0  # 检测 CUDA 设备
    except Exception:  # 任何异常都视为没有 GPU
        cuda = False  # 回退 CPU
    if want == "cuda" and not cuda:  # 配置要 CUDA 但没有
        return item("推理设备", "WARN", "config.json 指定了 cuda，但没检测到可用的 NVIDIA 显卡，会退回 CPU",  # 警告
                    "把 config.json 的 asr_device 改成 auto")  # 修复建议
    if cuda and want != "cpu":  # 有 GPU 且没强制 CPU
        return item("推理设备", "OK", "检测到 NVIDIA 显卡：无字幕视频的语音识别快于视频时长")  # 通过
    return item("推理设备", "OK", "CPU：无字幕视频的语音识别实测约为视频时长的 0.2 倍（6 核笔记本 CPU），"  # 通过（附耗时预期）
                "更老的 CPU 会慢一些；有字幕的视频不受影响")  # W1 实测口径，见 docs/verification/W1-技术验证.md


def check_console(os_name=None, encoding=None) -> dict:  # 控制台编码
    os_name = os_name or os.name  # 当前系统
    encoding = (encoding or locale.getpreferredencoding(False) or "").lower()  # 系统默认编码
    if os_name == "nt" and encoding.replace("-", "") not in ("utf8", "cp65001"):  # Windows 且不是 UTF-8
        return item("控制台编码", "WARN", f"系统默认编码为 {encoding}：脚本已自动切换为 UTF-8 输出，个别终端仍可能显示乱码",  # 警告
                    "PowerShell 执行 $env:PYTHONIOENCODING='utf-8'，或 cmd 执行 set PYTHONIOENCODING=utf-8")  # 修复建议
    return item("控制台编码", "OK", f"{encoding or '未知'}")  # 通过


def is_encrypted(path: Path) -> bool:  # 文件是否被加密软件处理过（Python 读到的不是明文）
    try:  # 文件可能读不了
        head = path.read_bytes()[:4096]  # 只看文件头
    except OSError:  # 读取失败
        return True  # 读不了同样无法使用
    if b"\x00" in head:  # 文本文件不会有 NUL 字节，加密软件写入的二进制文件头一定有
        return True  # 已加密
    try:  # 明文文件应能按 UTF-8 解码
        head.decode("utf-8")  # 尝试解码
    except UnicodeDecodeError as e:  # 解码失败
        return e.start < len(head) - 4  # 只是截断在多字节字符中间时不算
    return False  # 明文


def check_skill_files(root: Path = ROOT) -> dict:  # skill 自身文件是否可读
    files = sorted(root.glob("scripts/*.py")) + sorted(root.glob("references/*.md")) + [root / "SKILL.md"]  # 要检查的文件
    bad = [str(f.relative_to(root)) for f in files if f.exists() and is_encrypted(f)]  # 读不出明文的文件
    if bad:  # 有被加密的文件
        shown = "、".join(bad[:5]) + (f" 等 {len(bad)} 个" if len(bad) > 5 else "")  # 列出前几个
        return item("skill 文件", "FAIL", f"{shown} 被加密 / 防泄密软件处理过，Python 读到的不是明文",  # 失败
                    "请 IT 把 python.exe 和你用的 AI Agent 加入加密软件白名单（重新 git clone 不一定有用："  # 修复建议
                    "实测 git 写出的 .md / .txt 同样会被加密）；requirements.txt 被加密时 pip install -r 会失败，"  # 依赖安装也受影响
                    "可改用 README「安装」里直接列出包名的 pip 命令")  # 绕开的安装办法
    return item("skill 文件", "OK", f"{len(files)} 个脚本与模板均可正常读取")  # 通过


def probe_url(url: str, timeout: int = NET_TIMEOUT) -> tuple[int, str]:  # 探测一个地址，返回 (HTTP 状态码, 错误)
    req = urllib.request.Request(url, headers={"User-Agent": UA})  # 带浏览器 UA
    try:  # 网络错误
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # 发请求
            return resp.status, ""  # 成功返回状态码
    except urllib.error.HTTPError as e:  # 非 2xx 也说明网络是通的
        return e.code, ""  # 返回状态码
    except Exception as e:  # 超时、DNS、TLS 等
        return 0, f"{type(e).__name__}: {e}"[:120]  # 返回错误


def check_network(platforms, prober=probe_url) -> list[dict]:  # 各平台网络可达性
    out = []  # 结果列表
    for platform, name, url in NET_TARGETS:  # 遍历目标
        if platform and platform not in platforms:  # 与本次关心的平台无关
            continue  # 跳过
        code, err = prober(url)  # 探测
        if err:  # 连不上
            fix = ("YouTube 需要能访问外网：在 config.json 配置 youtube_proxy，或加 --proxy"  # YouTube 的修复建议
                   if platform == "youtube" else "检查网络或代理设置后重试")  # 其他平台的修复建议
            out.append(item(f"网络 {name}", "WARN", f"连不上：{err}", fix))  # 警告
        elif code in (412, 403, 429, 461, 471):  # 风控拦截
            out.append(item(f"网络 {name}", "WARN", f"HTTP {code}：被平台风控拦截（云服务器 / 机房 IP 上常见）",  # 警告
                            "换到家用网络或本机运行；B站可配置 SESSDATA 后再试"))  # 修复建议
        else:  # 正常响应
            out.append(item(f"网络 {name}", "OK", f"HTTP {code}"))  # 通过
    return out  # 返回结果


def run_checks(platforms=PLATFORMS, offline: bool = False) -> list[dict]:  # 跑全部检查
    config = load_config()  # 读取配置
    checks = [check_python()]  # Python 版本
    checks += check_core_deps()  # 核心依赖
    checks += check_optional_deps(platforms)  # 可选依赖
    checks += [check_model(config), check_device(config), check_console(), check_skill_files()]  # 模型、设备、编码、文件
    if not offline:  # 未要求离线
        checks += check_network(platforms)  # 网络
    return checks  # 返回全部结果


ICON = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌"}  # 状态图标


def render(checks: list[dict]) -> str:  # 生成给人看的清单
    lines = []  # 输出行
    for c in checks:  # 每项一行
        lines.append(f"{ICON[c['status']]} {c['name']}：{c['detail']}")  # 结果行
        if c["fix"] and c["status"] != "OK":  # 有问题且有修复建议
            lines.append(f"    修复：{c['fix']}")  # 修复行
    n = {s: sum(1 for c in checks if c["status"] == s) for s in ICON}  # 各状态计数
    lines.append(f"\n共 {len(checks)} 项：{n['OK']} 项通过，{n['WARN']} 项警告，{n['FAIL']} 项失败")  # 汇总行
    if n["FAIL"]:  # 有失败
        lines.append("先按上面的“修复”处理失败项，再开始做笔记。")  # 下一步
    elif n["WARN"]:  # 只有警告
        lines.append("可以开始做笔记；警告项只影响对应的平台或功能。")  # 下一步
    return "\n".join(lines)  # 拼成文本


def parse_args(argv=None):  # 解析命令行参数
    ap = argparse.ArgumentParser(description="bilibili-douyin-notes 环境自检")  # 参数解析器
    ap.add_argument("--platform", default="", help="只检查这些平台相关的项，逗号分隔")  # 平台过滤
    ap.add_argument("--offline", action="store_true", help="跳过网络检查")  # 离线
    ap.add_argument("--json", action="store_true", help="输出 JSON（供智能体解析）")  # JSON 输出
    return ap.parse_args(argv)  # 返回解析结果


def main(argv=None) -> int:  # 主流程
    _reconfigure_stdout()  # 统一输出编码
    args = parse_args(argv)  # 解析参数
    platforms = [p.strip() for p in args.platform.split(",") if p.strip()] or list(PLATFORMS)  # 平台列表
    unknown = [p for p in platforms if p not in PLATFORMS]  # 不认识的平台名
    if unknown:  # 有拼错的
        raise SystemExit(f"未知平台: {', '.join(unknown)}；可选: {', '.join(PLATFORMS)}")  # 人话退出
    checks = run_checks(platforms, args.offline)  # 跑检查
    failed = any(c["status"] == "FAIL" for c in checks)  # 是否有失败
    if args.json:  # JSON 输出
        print(json.dumps({"ok": not failed, "checks": checks}, ensure_ascii=False, indent=2))  # 打印 JSON
    else:  # 给人看的输出
        print(render(checks))  # 打印清单
    return 1 if failed else 0  # 有失败返回 1


if __name__ == "__main__":  # 脚本入口
    sys.exit(main())  # 以主流程返回值作为退出码
