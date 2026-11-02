# -*- coding: utf-8 -*-
"""smoke_local.py 离线测试：报告清理、一次运行的状态文件、定时任务注册命令（全部打桩，不调用真实 git / schtasks）。"""
import json  # 读写 JSON
import subprocess  # CompletedProcess

import pytest  # 测试框架

import smoke_local  # 被测模块


def test_prune_keeps_latest(tmp_path):
    # 只保留最新的 keep 个报告文件，其他文件不动
    for stamp in ("20261101-200000", "20261104-200000", "20261108-200000"):  # 三次运行
        (tmp_path / f"smoke-{stamp}.md").write_text("x", encoding="utf-8")  # Markdown
        (tmp_path / f"smoke-{stamp}.json").write_text("{}", encoding="utf-8")  # JSON
    (tmp_path / "last-status.json").write_text("{}", encoding="utf-8")  # 状态文件
    removed = smoke_local.prune(tmp_path, keep=4)  # 保留 4 个
    assert sorted(p.name for p in removed) == ["smoke-20261101-200000.json", "smoke-20261101-200000.md"]  # 删最旧一次
    assert (tmp_path / "last-status.json").exists()  # 状态文件保留


def test_run_writes_status_and_log(tmp_path, monkeypatch):
    # 一次运行：git pull → 探针子进程（这里打桩为直接写报告）→ last-status.json
    calls = []  # 记录子进程命令
    def fake_run(cmd, **kw):  # 假的 subprocess.run
        calls.append(cmd)  # 记录
        if cmd[0] == "git":  # git pull
            return subprocess.CompletedProcess(cmd, 0, "Already up to date.", "")  # 成功
        rd = tmp_path / "reports"  # 探针的报告目录
        (rd / "smoke-20261102-200000.json").write_text(json.dumps({"overall": {"rate": 0.9, "link": 1}}), encoding="utf-8")  # 假报告
        kw["stdout"].write("探针输出\n")  # 写日志
        return subprocess.CompletedProcess(cmd, 1, None, None)  # 有平台故障
    monkeypatch.setattr(smoke_local.subprocess, "run", fake_run)  # 打桩
    code = smoke_local.run("bilibili,douyin", pull=True, keep=40, root=tmp_path)  # 运行
    status = json.loads((tmp_path / "reports" / "last-status.json").read_text(encoding="utf-8"))  # 状态
    assert code == 1 and status["exit_code"] == 1 and status["overall"]["rate"] == 0.9  # 退出码与成功率
    assert status["report"] == "smoke-20261102-200000.json" and status["git_warning"] == ""  # 报告名、无告警
    assert calls[0][:2] == ["git", "-C"] and "--strict" in calls[1]  # 先拉代码，再严格模式跑探针
    assert "探针输出" in (tmp_path / "reports" / "last-run.log").read_text(encoding="utf-8")  # 输出进日志


def test_git_pull_failure_is_warning_only(tmp_path, monkeypatch):
    # 没装 git：只告警，不中断
    def no_git(cmd, **kw):  # 模拟找不到 git
        raise FileNotFoundError("git")  # 抛错
    monkeypatch.setattr(smoke_local.subprocess, "run", no_git)  # 打桩
    assert smoke_local.git_pull(tmp_path).startswith("git pull 未执行")  # 返回告警


def test_install_windows_builds_schtasks(monkeypatch):
    # Windows：每周二、五 20:00，用无窗口解释器跑 run 子命令
    seen = {}  # 记录命令
    def fake_run(cmd, **kw):  # 假的 subprocess.run
        seen["cmd"] = cmd  # 记录
        return subprocess.CompletedProcess(cmd, 0, "成功", "")  # 成功
    monkeypatch.setattr(smoke_local, "IS_WINDOWS", True)  # 当作 Windows
    monkeypatch.setattr(smoke_local.subprocess, "run", fake_run)  # 打桩
    assert smoke_local.install("tue,fri", "20:00", "bilibili") == 0  # 注册
    cmd = seen["cmd"]  # 实际命令
    assert cmd[:3] == ["schtasks", "/Create", "/TN"] and cmd[cmd.index("/D") + 1] == "TUE,FRI"  # 任务与星期
    assert cmd[cmd.index("/ST") + 1] == "20:00" and "run --platform bilibili" in cmd[cmd.index("/TR") + 1]  # 时间与命令


def test_install_rejects_bad_days_and_prints_cron(monkeypatch, capsys):
    # 星期写错直接人话退出；非 Windows 只打印 crontab 行
    with pytest.raises(SystemExit, match="--days"):  # 写错星期
        smoke_local.install("周二", "20:00", "bilibili")  # 注册
    monkeypatch.setattr(smoke_local, "IS_WINDOWS", False)  # 当作 Linux / macOS
    assert smoke_local.install("TUE,FRI", "20:05", "bilibili") == 0  # 不报错
    assert capsys.readouterr().out.startswith("5 20 * * tue,fri ")  # crontab 行


def test_task_command_quotes_paths():
    # 定时任务命令里的路径加引号（Windows 路径可能有空格）
    cmd = smoke_local.task_command("bilibili,douyin")  # 生成命令
    assert cmd.startswith('"') and cmd.endswith("run --platform bilibili,douyin")  # 引号与子命令
