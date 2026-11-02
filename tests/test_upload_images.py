# -*- coding: utf-8 -*-
"""upload_images.py 离线测试：配置合并、预演、上传改写、幂等、缺图与密钥打码（全部用假 bucket，不联网）。"""
import json  # 解析报告

import pytest  # 测试框架

import upload_images  # 被测脚本


class FakeBucket:
    """假 OSS bucket：记录上传的对象键，object_exists 按已上传集合判断。"""

    def __init__(self, existing=()):
        self.objects = set(existing)  # 远端已有的对象键
        self.uploads = []  # 本次上传记录 (键, 本地路径, 头)

    def object_exists(self, key):
        return key in self.objects  # 是否已存在

    def put_object_from_file(self, key, path, headers=None):
        self.uploads.append((key, path, headers))  # 记录上传
        self.objects.add(key)  # 视为已存在


CFG = {"endpoint": "oss-cn-hangzhou.aliyuncs.com", "bucket": "my-notes",  # 测试配置
       "access_key_id": "AKID_TEST", "access_key_secret": "SECRET_TEST", "prefix": "notes/"}  # 假密钥


@pytest.fixture
def note(tmp_path):
    """造一份引用两张本地图（其中一张引用两次）和一张网络图的笔记。"""
    img = tmp_path / "images"  # 截图目录
    img.mkdir()  # 创建
    (img / "a.png").write_bytes(b"png-a")  # 图 a
    (img / "b.jpg").write_bytes(b"jpg-b")  # 图 b
    p = tmp_path / "9.1-开闭原则.md"  # 笔记
    p.write_text("## 1. 节\n![图A](images/a.png)\n![图B](images/b.jpg)\n"
                 "![图A again](images/a.png)\n![远程](https://x.com/c.png)\n", encoding="utf-8")  # 正文
    return p  # 返回笔记路径


def test_dry_run_plans_without_network(note):
    r = upload_images.run(note, apply=False, cfg=CFG, bucket=None)  # 预演不需要 bucket
    assert r["status"] == "ok" and r["mode"] == "dry-run"  # 成功预演
    assert [i["src"] for i in r["images"]] == ["images/a.png", "images/b.jpg"]  # 去重保序，跳过网络图
    assert all(i["status"] == "planned" for i in r["images"])  # 全部是计划
    assert r["images"][0]["url"].startswith("https://my-notes.oss-cn-hangzhou.aliyuncs.com/notes/")  # 默认域名
    assert "images/a.png" in note.read_text(encoding="utf-8")  # 预演不改文件


def test_apply_uploads_and_rewrites(note):
    bucket = FakeBucket()  # 空 bucket
    r = upload_images.run(note, apply=True, cfg=CFG, bucket=bucket)  # 实际执行
    assert r["status"] == "ok" and len(bucket.uploads) == 2  # 两张图各传一次
    assert bucket.uploads[0][2] == {"Content-Type": "image/png"}  # MIME 正确
    text = note.read_text(encoding="utf-8")  # 改写后的笔记
    assert "images/a.png" not in text and r["rewritten"] == 3  # 三处引用都改了
    assert "https://x.com/c.png" in text  # 网络图原样保留
    assert (note.parent / (note.name + ".bak")).exists()  # 留了备份


def test_apply_is_idempotent(note):
    bucket = FakeBucket()  # 空 bucket
    upload_images.run(note, apply=True, cfg=CFG, bucket=bucket)  # 第一次
    note.write_text(note.with_name(note.name + ".bak").read_text(encoding="utf-8"), encoding="utf-8")  # 还原笔记
    r = upload_images.run(note, apply=True, cfg=CFG, bucket=bucket)  # 第二次
    assert len(bucket.uploads) == 2  # 没有重复上传
    assert all(i["status"] == "exists" for i in r["images"])  # 都是远端已有


def test_missing_image_stops_without_changes(note):
    note.write_text(note.read_text(encoding="utf-8") + "![缺](images/none.png)\n", encoding="utf-8")  # 引用不存在的图
    bucket = FakeBucket()  # 空 bucket
    r = upload_images.run(note, apply=True, cfg=CFG, bucket=bucket)  # 执行
    assert r["status"] == "error" and r["missing"] == ["images/none.png"]  # 报缺图
    assert bucket.uploads == [] and "images/a.png" in note.read_text(encoding="utf-8")  # 不上传不改写


def test_apply_requires_credentials(note):
    r = upload_images.run(note, apply=True, cfg={"prefix": "notes/"}, bucket=FakeBucket())  # 无配置
    assert r["status"] == "error" and "access_key_secret" in r["error"]  # 只列缺的键名


def test_public_base_url_and_prefix(tmp_path, monkeypatch):
    for k in upload_images.ENV_KEYS:  # 清掉宿主环境里的 OSS 变量，避免干扰
        monkeypatch.delenv(k, raising=False)  # 删除
    cfg_file = tmp_path / "config.json"  # 假 config.json
    cfg_file.write_text(json.dumps({"oss": {"bucket": "b", "endpoint": "e.com", "prefix": "/x"}}),
                        encoding="utf-8")  # 写配置
    env = tmp_path / "oss.env"  # 假 .env
    env.write_text("# 注释\nexport OSS_PUBLIC_BASE_URL='https://img.example.com/'\n", encoding="utf-8")  # 写 .env
    monkeypatch.setenv("OSS_BUCKET", "from-env")  # 环境变量优先级最高
    cfg = upload_images.load_oss_config(env, cfg_file)  # 合并
    assert cfg["bucket"] == "from-env" and cfg["prefix"] == "x/"  # 覆盖与前缀归一
    assert upload_images.public_url(cfg, "x/k.png") == "https://img.example.com/x/k.png"  # 自定义域名


def test_main_masks_credentials_in_errors(note, monkeypatch, capsys):
    for k in upload_images.ENV_KEYS:  # 清环境
        monkeypatch.delenv(k, raising=False)  # 删除
    monkeypatch.setattr(upload_images, "load_oss_config", lambda env_file=None: dict(CFG))  # 注入配置

    def boom(cfg):  # 模拟 OSS 返回签名错误并回显 AccessKeyId
        raise RuntimeError("SignatureDoesNotMatch OSSAccessKeyId=AKID_TEST secret=SECRET_TEST")  # 异常

    monkeypatch.setattr(upload_images, "make_bucket", boom)  # 替换建 bucket
    code = upload_images.main([str(note), "--apply"])  # 执行
    out = capsys.readouterr().out  # 输出
    assert code == 1 and "AKID_TEST" not in out and "SECRET_TEST" not in out  # 密钥已打码
