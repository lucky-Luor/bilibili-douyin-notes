#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本机定时冒烟：拉最新代码 → 跑真实链接探针 → 按时间戳存报告。

W1 结论：B站、YouTube 在 GitHub Actions 的机房 IP 上会被风控，冒烟只能在家用网络的本机跑。

用法:
    python smoke_local.py run [--platform bilibili,douyin,xiaohongshu] [--no-pull] [--keep 40]
    python smoke_local.py install [--days TUE,FRI] [--time 20:00] [--platform ...]   # 注册 Windows 定时任务
    python smoke_local.py uninstall                                                    # 删除定时任务
说明:
    - 报告写到仓库根目录的 reports/（已在 .gitignore），最新状态另存 reports/last-status.json
    - 建议单独克隆一份仓库专门跑冒烟，避免 git pull 影响日常使用的那一份
    - 非 Windows 系统的 install 只打印 crontab 配置，不改系统
"""
import argparse  # 命令行参数解析
import json  # 读写状态文件
import os  # 判断操作系统
import subprocess  # 调用 git、探针、schtasks
import sys  # 解释器路径与退出码
import time  # 时间戳
from pathlib import Path  # 路径处理

SCRIPTS = Path(__file__).resolve().parent  # scripts 目录
ROOT = SCRIPTS.parent  # 仓库根目录
REPORTS = ROOT / "reports"  # 报告目录
TASK_NAME = "bdn-smoke"  # Windows 定时任务名
DEFAULT_PLATFORMS = "bilibili,douyin,xiaohongshu"  # 默认平台（YouTube 需要代理，暂不跑）
DEFAULT_KEEP = 40  # 最多保留的报告文件数（.md + .json 各算一个，约 20 次）
WEEKDAYS = {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}  # schtasks 认识的星期写法
IS_WINDOWS = os.name == "nt"  # 是否 Windows（测试里可替换）
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0  # Windows 上子进程不弹控制台窗口（CREATE_NO_WINDOW）


def log(msg: str) -> None:  # 进度输出到 stderr
    print(f"[smoke-local] {msg}", file=sys.stderr, flush=True)  # 带前缀


def git_pull(root: Path) -> str:  # 拉最新代码；失败只告警，继续用本地代码跑
    try:  # git 可能没装或网络不通
        r = subprocess.run(["git", "-C", str(root), "pull", "--ff-only"],  # 只快进，不产生合并
                           capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW)  # 最多等 2 分钟，不弹窗
    except (OSError, subprocess.TimeoutExpired) as e:  # 找不到 git 或超时
        return f"git pull 未执行：{e}"  # 返回告警
    return "" if r.returncode == 0 else f"git pull 失败：{(r.stderr or r.stdout).strip()[:200]}"  # 成功返回空串


def prune(report_dir: Path, keep: int) -> list[Path]:  # 只保留最新的 keep 个报告文件
    files = sorted(report_dir.glob("smoke-*.*"), key=lambda p: p.name, reverse=True)  # 文件名含时间戳，按名倒序即按时间倒序
    old = files[keep:] if keep > 0 else []  # 超出部分
    for p in old:  # 逐个删除
        p.unlink(missing_ok=True)  # 删除旧报告
    return old  # 返回删除清单


def latest_report(report_dir: Path) -> Path | None:  # 本次运行生成的 JSON 报告
    files = sorted(report_dir.glob("smoke-*.json"), key=lambda p: p.name)  # 按时间排序
    return files[-1] if files else None  # 最新一份


def run(platforms: str, pull: bool, keep: int, root: Path = ROOT) -> int:  # 跑一次冒烟
    report_dir = root / "reports"  # 报告目录
    warn = git_pull(root) if pull else ""  # 拉代码
    if warn:  # 拉取失败
        log(warn)  # 告警但继续
    cmd = [console_python(), str(root / "scripts" / "smoke_probe.py"),  # 用子进程跑，保证用到刚拉下来的代码
           "--platform", platforms, "--report-dir", str(report_dir), "--strict"]  # 平台、报告目录、严格模式
    log("运行：" + " ".join(cmd))  # 记录命令
    report_dir.mkdir(parents=True, exist_ok=True)  # 确保目录存在
    with open(report_dir / "last-run.log", "w", encoding="utf-8") as f:  # 定时任务没有控制台，输出写日志
        code = subprocess.run(cmd, cwd=str(root), stdout=f, stderr=subprocess.STDOUT,  # 执行探针，输出进日志
                              env=dict(os.environ, PYTHONIOENCODING="utf-8"),  # 子进程统一 UTF-8 输出
                              creationflags=NO_WINDOW).returncode  # 不弹窗
    report = latest_report(report_dir)  # 本次报告
    overall = {}  # 合计成功率
    if report:  # 有报告
        overall = json.loads(report.read_text(encoding="utf-8")).get("overall", {})  # 读合计
    status = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),  # 运行时间
              "exit_code": code,  # 0 = 没有平台故障
              "overall": overall,  # 合计成功率
              "report": report.name if report else "",  # 报告文件名
              "git_warning": warn}  # 拉代码的告警
    (report_dir / "last-status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2),  # 最新状态
                                                 encoding="utf-8")  # UTF-8 落盘
    removed = prune(report_dir, keep)  # 清理旧报告
    if removed:  # 有删除
        log(f"清理旧报告 {len(removed)} 个")  # 告知
    log(f"完成：退出码 {code}，整体成功率 {overall.get('rate', 0):.0%}，报告 {status['report']}")  # 结果
    return code  # 返回探针退出码


def console_python() -> str:  # 子进程用控制台版 python.exe（pythonw 下 stdout 可能不可用）
    exe = Path(sys.executable)  # 当前解释器
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():  # 定时任务里是 pythonw
        return str(exe.with_name("python.exe"))  # 换成同目录的 python.exe
    return str(exe)  # 其他情况原样使用


def pythonw() -> str:  # 定时任务用无窗口的 pythonw.exe，避免到点弹黑框
    exe = Path(sys.executable)  # 当前解释器
    w = exe.with_name("pythonw.exe")  # 同目录的 pythonw
    return str(w if w.exists() else exe)  # 没有就用原解释器


def task_command(platforms: str) -> str:  # 定时任务执行的命令行
    return f'"{pythonw()}" "{Path(__file__).resolve()}" run --platform {platforms}'  # 路径加引号


def install(days: str, at: str, platforms: str) -> int:  # 注册定时任务
    day_list = [d.strip().upper() for d in days.split(",") if d.strip()]  # 星期列表
    bad = [d for d in day_list if d not in WEEKDAYS]  # 不认识的写法
    if bad or not day_list:  # 参数有误
        raise SystemExit(f"--days 只能用 {','.join(sorted(WEEKDAYS))}，收到：{days}")  # 人话退出
    if not IS_WINDOWS:  # 非 Windows：只打印 crontab 配置
        hh, mm = at.split(":")  # 时、分
        cron_days = ",".join(d[:3].lower() for d in day_list)  # crontab 星期写法
        print(f"{int(mm)} {int(hh)} * * {cron_days} {sys.executable} {Path(__file__).resolve()} run --platform {platforms}")  # crontab 行
        log("非 Windows 系统：把上面这行加进 crontab -e 即可，本命令不改系统")  # 说明
        return 0  # 不报错
    cmd = ["schtasks", "/Create", "/TN", TASK_NAME, "/SC", "WEEKLY", "/D", ",".join(day_list),  # 每周指定几天
           "/ST", at, "/TR", task_command(platforms), "/F"]  # 时间、命令、已存在则覆盖
    r = subprocess.run(cmd, capture_output=True, text=True)  # 注册
    log((r.stdout or r.stderr).strip())  # 输出 schtasks 的结果
    return r.returncode  # 返回退出码


def uninstall() -> int:  # 删除定时任务
    if not IS_WINDOWS:  # 非 Windows
        log("非 Windows 系统：请在 crontab -e 里删掉对应的行")  # 说明
        return 0  # 不报错
    r = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True, text=True)  # 删除
    log((r.stdout or r.stderr).strip())  # 输出结果
    return r.returncode  # 返回退出码


def parse_args(argv=None):  # 命令行参数
    ap = argparse.ArgumentParser(description="本机定时冒烟（拉代码 → 探针 → 报告）")  # 解析器
    sub = ap.add_subparsers(dest="cmd", required=True)  # 子命令
    r = sub.add_parser("run", help="跑一次冒烟")  # run
    r.add_argument("--platform", default=DEFAULT_PLATFORMS, help="平台，逗号分隔")  # 平台
    r.add_argument("--no-pull", action="store_true", help="不拉最新代码")  # 跳过 git pull
    r.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="最多保留的报告文件数")  # 保留数
    i = sub.add_parser("install", help="注册 Windows 定时任务")  # install
    i.add_argument("--days", default="TUE,FRI", help="每周哪几天，如 TUE,FRI")  # 星期
    i.add_argument("--time", default="20:00", help="几点跑，24 小时制 HH:MM")  # 时间
    i.add_argument("--platform", default=DEFAULT_PLATFORMS, help="平台，逗号分隔")  # 平台
    sub.add_parser("uninstall", help="删除定时任务")  # uninstall
    return ap.parse_args(argv)  # 解析


def main(argv=None) -> int:  # 入口
    for stream in (sys.stdout, sys.stderr):  # Windows 管道下统一 UTF-8
        try:  # 老版本或被替换的流可能不支持
            stream.reconfigure(encoding="utf-8", errors="replace")  # 改编码
        except (AttributeError, ValueError):  # 不支持
            pass  # 忽略
    args = parse_args(argv)  # 解析参数
    if args.cmd == "run":  # 跑一次
        return run(args.platform, not args.no_pull, args.keep)  # 执行
    if args.cmd == "install":  # 注册
        return install(args.days, args.time, args.platform)  # 执行
    return uninstall()  # 删除


if __name__ == "__main__":  # 脚本入口
    sys.exit(main())  # 以返回值作为退出码
