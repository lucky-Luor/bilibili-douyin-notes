# -*- coding: utf-8 -*-
"""ensure_abogus.py 的离线测试：SHA-256 固定校验、env 覆盖、ghproxy 兜底（下载一律 mock）。"""
import hashlib

import pytest

import ensure_abogus


FAKE_OK = ("# fake\n"
           "class ABogus:\n"
           "    def get_value(self):\n"
           "        return 'ok'\n")  # 含必要标记，哈希在测试里现算


# ---------- 常量与真实文件的一致性 ----------

def test_pinned_commit_and_hash_constants():
    assert len(ensure_abogus.PINNED_COMMIT) == 40
    assert len(ensure_abogus.EXPECTED_SHA256) == 64
    assert ensure_abogus.EXPECTED_SHA256 == ensure_abogus.EXPECTED_SHA256.lower()


def test_expected_sha256_matches_local_abogus_file():
    """本地 scripts/abogus.py（若存在）必须与固定 commit 的哈希真值一致。"""
    local = ensure_abogus.TARGET
    if not local.is_file():
        pytest.skip("本地尚未下载 scripts/abogus.py")
    assert ensure_abogus.sha256_file(local) == ensure_abogus.EXPECTED_SHA256


# ---------- sources() / effective_commit() ----------

def test_sources_contain_pinned_commit_and_ghproxy():
    urls = ensure_abogus.sources(ensure_abogus.PINNED_COMMIT)
    assert len(urls) == 2
    assert urls[0].startswith("https://raw.githubusercontent.com/")
    assert urls[1].startswith("https://ghproxy.net/https://raw.githubusercontent.com/")
    for u in urls:
        assert ensure_abogus.PINNED_COMMIT in u          # commit 固定进 URL
        assert ensure_abogus.UPSTREAM_PATH in u


def test_effective_commit_default_is_pinned(monkeypatch):
    monkeypatch.delenv(ensure_abogus.ENV_COMMIT, raising=False)
    assert ensure_abogus.effective_commit() == ensure_abogus.PINNED_COMMIT


def test_effective_commit_env_override(monkeypatch):
    monkeypatch.setenv(ensure_abogus.ENV_COMMIT, "deadbeef" * 5)
    assert ensure_abogus.effective_commit() == "deadbeef" * 5
    for u in ensure_abogus.sources(ensure_abogus.effective_commit()):
        assert "deadbeef" in u


# ---------- 正向：哈希相符 → 落盘 ----------

def _mock_env(monkeypatch, tmp_path):
    monkeypatch.setattr(ensure_abogus, "TARGET", tmp_path / "abogus.py")
    monkeypatch.delenv(ensure_abogus.ENV_COMMIT, raising=False)


def test_ensure_accepts_content_matching_expected_hash(monkeypatch, tmp_path):
    _mock_env(monkeypatch, tmp_path)
    monkeypatch.setattr(ensure_abogus, "EXPECTED_SHA256",
                        ensure_abogus.sha256_text(FAKE_OK))
    monkeypatch.setattr(ensure_abogus, "_download", lambda url: FAKE_OK)
    assert ensure_abogus.ensure(quiet=True) is True
    assert (tmp_path / "abogus.py").read_text(encoding="utf-8") == FAKE_OK


def test_ensure_falls_back_to_ghproxy_mirror(monkeypatch, tmp_path):
    """ghproxy 只是传输通道：镜像下载的内容同样必须过同一哈希校验。"""
    _mock_env(monkeypatch, tmp_path)
    monkeypatch.setattr(ensure_abogus, "EXPECTED_SHA256",
                        ensure_abogus.sha256_text(FAKE_OK))
    used_urls = []

    def fake_download(url):
        used_urls.append(url)
        if len(used_urls) == 1:  # 第 1 个源（官方 raw）网络失败
            raise OSError("network down")
        return FAKE_OK

    monkeypatch.setattr(ensure_abogus, "_download", fake_download)
    assert ensure_abogus.ensure(quiet=True) is True
    assert len(used_urls) == 2
    assert used_urls[1].startswith("https://ghproxy.net/")
    assert ensure_abogus.sha256_text(FAKE_OK) == ensure_abogus.EXPECTED_SHA256


def test_ensure_existing_file_with_valid_hash_short_circuits(monkeypatch, tmp_path):
    _mock_env(monkeypatch, tmp_path)
    target = tmp_path / "abogus.py"
    target.write_text(FAKE_OK, encoding="utf-8")
    monkeypatch.setattr(ensure_abogus, "EXPECTED_SHA256",
                        hashlib.sha256(FAKE_OK.encode()).hexdigest())

    def fail_download(url):  # 不应触发下载
        raise AssertionError("不应下载")

    monkeypatch.setattr(ensure_abogus, "_download", fail_download)
    assert ensure_abogus.ensure(quiet=True) is True


# ---------- 反向：哈希不符 → 拒绝并打印实测值 ----------

def test_ensure_rejects_wrong_hash_and_prints_actual(monkeypatch, tmp_path):
    _mock_env(monkeypatch, tmp_path)
    wrong = FAKE_OK + "tampered"
    actual = ensure_abogus.sha256_text(wrong)
    assert actual != ensure_abogus.EXPECTED_SHA256
    monkeypatch.setattr(ensure_abogus, "_download", lambda url: wrong)

    with pytest.raises(RuntimeError) as ei:
        ensure_abogus.ensure(quiet=True)
    msg = str(ei.value)
    assert "SHA-256" in msg
    assert "实测" in msg and actual in msg          # 打印实测哈希
    assert ensure_abogus.EXPECTED_SHA256 in msg     # 同时给出期望值
    assert not (tmp_path / "abogus.py").exists()    # 拒绝落盘


def test_ensure_existing_file_with_bad_hash_triggers_redownload(
        monkeypatch, tmp_path):
    _mock_env(monkeypatch, tmp_path)
    target = tmp_path / "abogus.py"
    target.write_text(FAKE_OK + "tampered", encoding="utf-8")  # 标记内但哈希不符
    monkeypatch.setattr(ensure_abogus, "EXPECTED_SHA256",
                        ensure_abogus.sha256_text(FAKE_OK))
    monkeypatch.setattr(ensure_abogus, "_download", lambda url: FAKE_OK)
    assert ensure_abogus.ensure(quiet=True) is True
    assert target.read_text(encoding="utf-8") == FAKE_OK  # 已被重新下载替换


# ---------- env 覆盖模式：无法预知哈希，只做标记+编译校验 ----------

def test_env_override_skips_hash_check_but_requires_markers(monkeypatch, tmp_path):
    monkeypatch.setattr(ensure_abogus, "TARGET", tmp_path / "abogus.py")
    monkeypatch.setenv(ensure_abogus.ENV_COMMIT, "deadbeef" * 5)
    other = "# other revision\nclass ABogus:\n    def get_value(self):\n        pass\n"
    monkeypatch.setattr(ensure_abogus, "_download", lambda url: other)
    assert ensure_abogus.ensure(quiet=True) is True  # 哈希不同也接受
    assert (tmp_path / "abogus.py").exists()


def test_env_override_still_rejects_markerless_content(monkeypatch, tmp_path):
    monkeypatch.setattr(ensure_abogus, "TARGET", tmp_path / "abogus.py")
    monkeypatch.setenv(ensure_abogus.ENV_COMMIT, "deadbeef" * 5)
    monkeypatch.setattr(ensure_abogus, "_download",
                        lambda url: "# 垃圾内容，缺少必要标记\n")
    with pytest.raises(RuntimeError):
        ensure_abogus.ensure(quiet=True)
    assert not (tmp_path / "abogus.py").exists()
