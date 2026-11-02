# -*- coding: utf-8 -*-
"""doctor.py 离线测试：依赖、设备、网络全部打桩，不联网、不依赖本机实际环境。"""
import json  # 解析 JSON 输出

import pytest  # 测试框架

import doctor  # 被测模块


@pytest.mark.parametrize("ver,status", [((3, 9), "FAIL"), ((3, 10), "OK"), ((3, 12), "OK"), ((3, 14), "WARN")])
def test_check_python(ver, status):
    # 低于 3.10 失败；3.10~3.12 通过；高于 CI 覆盖版本警告
    assert doctor.check_python(ver)["status"] == status  # 状态符合预期


def test_core_deps_missing_is_fail(monkeypatch):
    # 核心依赖缺失为 FAIL，并给出 pip 安装命令
    monkeypatch.setattr(doctor, "has_module", lambda name: name != "faster_whisper")  # 只缺 faster-whisper
    res = {c["name"]: c for c in doctor.check_core_deps()}  # 按名字索引
    assert res["依赖 faster-whisper"]["status"] == "FAIL"  # 缺失为失败
    assert res["依赖 faster-whisper"]["fix"] == "python -m pip install faster-whisper"  # 修复命令
    assert res["依赖 av"]["status"] == "OK"  # 已安装为通过
    assert "无需另装 ffmpeg" in res["依赖 av"]["detail"]  # 说明不需要 ffmpeg 可执行文件


def test_optional_deps_filtered_by_platform(monkeypatch):
    # 可选依赖只检查相关平台；缺失为 WARN
    monkeypatch.setattr(doctor, "has_module", lambda name: False)  # 全部缺失
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)  # 没有 deno
    names = [c["name"] for c in doctor.check_optional_deps(["douyin"])]  # 只看抖音
    assert "依赖 gmssl" in names and "依赖 yt-dlp" not in names  # 抖音相关、YouTube 无关
    assert all(c["status"] == "WARN" for c in doctor.check_optional_deps(["douyin"]))  # 缺失为警告


def test_deno_warn_only_when_ytdlp_installed(monkeypatch):
    # 装了 yt-dlp 但没有 deno 时才提示 deno
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)  # 没有 deno
    monkeypatch.setattr(doctor, "has_module", lambda name: name == "yt_dlp")  # 只装了 yt-dlp
    assert any(c["name"] == "可执行文件 deno" for c in doctor.check_optional_deps(["youtube"]))  # 提示 deno
    monkeypatch.setattr(doctor, "has_module", lambda name: False)  # yt-dlp 也没装
    assert not any(c["name"] == "可执行文件 deno" for c in doctor.check_optional_deps(["youtube"]))  # 不重复提示


def test_check_model(monkeypatch):
    # 模型已缓存为 OK；未缓存为 WARN 并说明下载大小
    monkeypatch.setattr(doctor, "model_cached", lambda size: True)  # 已缓存
    assert doctor.check_model({"asr_model": "base"})["status"] == "OK"  # 通过
    monkeypatch.setattr(doctor, "model_cached", lambda size: False)  # 未缓存
    r = doctor.check_model({})  # 默认 small
    assert r["status"] == "WARN" and "small" in r["detail"] and "484 MB" in r["detail"]  # 警告并给出大小


def test_check_device_cuda_config_without_gpu(monkeypatch):
    # 配置了 cuda 但没有显卡时警告
    import sys  # 注入假模块
    import types  # 构造假模块
    fake = types.SimpleNamespace(get_cuda_device_count=lambda: 0)  # 没有 CUDA 设备
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)  # 注入
    assert doctor.check_device({"asr_device": "cuda"})["status"] == "WARN"  # 警告
    assert "1~3 倍" in doctor.check_device({})["detail"]  # CPU 时给出耗时预期


@pytest.mark.parametrize("os_name,enc,status", [("nt", "cp936", "WARN"), ("nt", "UTF-8", "OK"),
                                                ("nt", "cp65001", "OK"), ("posix", "ascii", "OK")])
def test_check_console(os_name, enc, status):
    # 只有 Windows 且非 UTF-8 时警告
    assert doctor.check_console(os_name, enc)["status"] == status  # 状态符合预期


def test_is_encrypted(tmp_path):
    # 明文（含中文、含加密软件名字面量）不算加密；含 NUL 或无法解码算加密
    plain = tmp_path / "a.py"  # 明文文件
    plain.write_text("# 注释 Esafenet\nprint('你好')\n", encoding="utf-8")  # 源码里出现软件名不应误判
    assert doctor.is_encrypted(plain) is False  # 明文
    enc = tmp_path / "b.py"  # 模拟加密文件
    enc.write_bytes(b"\xe0\xa8\x91\xe7\xd8\xf2\x05\xac\x00\x02\x00\x00Esafenet")  # 加密文件头特征
    assert doctor.is_encrypted(enc) is True  # 识别为加密
    bad = tmp_path / "c.py"  # 无法按 UTF-8 解码的文件
    bad.write_bytes(b"\xff\xfe" * 100)  # 非法 UTF-8
    assert doctor.is_encrypted(bad) is True  # 识别为不可读


def test_is_encrypted_tolerates_truncated_multibyte(tmp_path):
    # 文件头截断在多字节字符中间时不算加密
    f = tmp_path / "d.md"  # 明文文件
    f.write_bytes(("中" * 2000).encode("utf-8"))  # 6000 字节，4096 处截断在字符中间
    assert doctor.is_encrypted(f) is False  # 不误判


def test_check_skill_files(tmp_path):
    # 有加密文件时 FAIL 并列出文件；全部明文时 OK
    (tmp_path / "scripts").mkdir()  # 脚本目录
    (tmp_path / "references").mkdir()  # 模板目录
    (tmp_path / "SKILL.md").write_text("# skill\n", encoding="utf-8")  # 明文
    (tmp_path / "scripts" / "ok.py").write_text("x = 1\n", encoding="utf-8")  # 明文
    assert doctor.check_skill_files(tmp_path)["status"] == "OK"  # 全部明文
    (tmp_path / "scripts" / "bad.py").write_bytes(b"\x00\x01\x02")  # 加密文件
    r = doctor.check_skill_files(tmp_path)  # 再查一次
    assert r["status"] == "FAIL" and "bad.py" in r["detail"] and "git clone" in r["fix"]  # 失败并给出修复


def test_check_network_classifies(monkeypatch):
    # 正常为 OK；412 等风控为 WARN；连不上为 WARN 且 YouTube 提示代理
    answers = {"bilibili": (412, ""), "youtube": (0, "URLError: EOF"), "douyin": (200, "")}  # 每个平台的模拟结果
    def prober(url):  # 按地址返回模拟结果
        for platform, name, target in doctor.NET_TARGETS:  # 找到地址对应的平台
            if target == url:  # 命中
                return answers.get(platform, (200, ""))  # 返回模拟结果
        return 200, ""  # 兜底
    res = {c["name"]: c for c in doctor.check_network(["bilibili", "youtube", "douyin"], prober)}  # 跑检查
    assert res["网络 B站接口"]["status"] == "WARN" and "风控" in res["网络 B站接口"]["detail"]  # 412 识别为风控
    assert res["网络 YouTube"]["status"] == "WARN" and "youtube_proxy" in res["网络 YouTube"]["fix"]  # 提示代理
    assert res["网络 抖音"]["status"] == "OK"  # 正常
    assert "网络 小红书" not in res and "网络 模型镜像 hf-mirror" in res  # 无关平台跳过、镜像总是检查


def test_bilibili_probe_uses_view_endpoint():
    # B站要探测实际用到的 view 接口：nav 接口在机房 IP 上也返回 200，测不出风控
    url = next(t for p, n, t in doctor.NET_TARGETS if p == "bilibili")  # B站探测地址
    assert "/x/web-interface/view" in url  # 使用详情接口


def test_render_summary_and_next_step():
    # 清单带修复行与汇总；有失败时提示先修
    checks = [doctor.item("A", "OK", "好"), doctor.item("B", "WARN", "注意", "做点什么"),  # 一项通过、一项警告
              doctor.item("C", "FAIL", "坏了", "pip install x")]  # 一项失败
    text = doctor.render(checks)  # 生成清单
    assert "修复：pip install x" in text and "修复：做点什么" in text  # 修复行
    assert "1 项通过，1 项警告，1 项失败" in text and "先按上面" in text  # 汇总与下一步


def test_main_json_and_exit_code(monkeypatch, capsys):
    # --json 输出结构正确；有 FAIL 时退出码为 1
    monkeypatch.setattr(doctor, "run_checks", lambda platforms, offline: [doctor.item("X", "FAIL", "坏")])  # 打桩
    assert doctor.main(["--json", "--offline"]) == 1  # 有失败返回 1
    data = json.loads(capsys.readouterr().out)  # 解析输出
    assert data["ok"] is False and data["checks"][0]["name"] == "X"  # 结构正确
    monkeypatch.setattr(doctor, "run_checks", lambda platforms, offline: [doctor.item("X", "WARN", "注意")])  # 只有警告
    assert doctor.main(["--offline"]) == 0  # 只有警告返回 0


def test_main_rejects_unknown_platform():
    # 拼错平台名时人话退出
    with pytest.raises(SystemExit, match="未知平台"):  # 期望退出
        doctor.main(["--platform", "bilbili"])  # 拼错


def test_run_checks_offline_skips_network(monkeypatch):
    # --offline 时不做网络检查
    monkeypatch.setattr(doctor, "check_network", lambda platforms: pytest.fail("不应联网"))  # 一旦调用就失败
    names = [c["name"] for c in doctor.run_checks(["bilibili"], offline=True)]  # 离线跑
    assert "Python 版本" in names and not any(n.startswith("网络") for n in names)  # 没有网络项
