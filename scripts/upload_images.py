#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes 交付前（可选）：把笔记引用的本地截图上传阿里云 OSS，并改写成 OSS 链接。

语雀导入 Markdown 时不会上传本地图片，`![](images/xxx.png)` 会变成裂图；
本脚本把这些图传到 OSS，再把笔记里的相对路径原位改成可公开访问的 URL。

用法:
    python upload_images.py <笔记.md>                 # 预演（默认）：只列出将上传的文件和目标链接，不联网不改文件
    python upload_images.py <笔记.md> --apply         # 实际上传并改写笔记（改写前留 <笔记>.md.bak）
    python upload_images.py <笔记.md> --apply --env-file <xxx.env>   # 从 .env 文件读取 OSS 配置

配置（优先级从高到低，同名键高优先级覆盖低优先级）:
    1. 环境变量 / --env-file：OSS_ENDPOINT、OSS_BUCKET、OSS_ACCESS_KEY_ID、OSS_ACCESS_KEY_SECRET、
       OSS_PREFIX、OSS_PUBLIC_BASE_URL
    2. config.json 的 "oss" 字段：{"endpoint", "bucket", "access_key_id", "access_key_secret",
       "prefix", "public_base_url"}
    - endpoint 形如 oss-cn-hangzhou.aliyuncs.com；prefix 为对象键前缀（默认 notes/）；
    - public_base_url 可选：绑定了 CDN / 自定义域名时填（如 https://img.example.com），
      缺省为 https://<bucket>.<endpoint>；Bucket 需公共读（或经 CDN 可公开访问），
      否则语雀拉不到图。

行为约定:
    - 对象键 = <prefix><sha256(文件内容)前16位>.<扩展名>：同一张图永远同一个键，
      远端已存在就跳过上传，重复执行安全（幂等）；
    - 只处理相对路径的本地图片，http(s) 链接原样保留；引用的文件不存在时报错并不改写笔记；
    - 密钥绝不打印：输出 JSON 只含文件、对象键、URL 与状态。

输出契约（stdout 一行 JSON）:
    {"note", "mode": "dry-run"|"apply", "images": [{"src", "key", "url", "status"}],
     "missing": [...], "rewritten": 改写处数, "backup": 备份路径|null, "status": "ok"|"error", "error"?}
    status 取值：planned（预演）/ uploaded（已上传）/ exists（远端已有，跳过）
"""
import argparse  # 命令行参数
import hashlib  # 内容哈希
import json  # 读配置 / 输出报告
import os  # 环境变量
import re  # 匹配图片语法
import shutil  # 备份笔记
import sys  # stdout 编码与退出码
from pathlib import Path  # 路径处理

CONFIG = Path(__file__).resolve().parent.parent / "config.json"  # skill 配置文件
IMG_MD_RE = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)(\))")  # ![alt](路径)：组1 前缀 / 组2 路径 / 组3 右括号
ENV_KEYS = {  # 环境变量名 → 配置键
    "OSS_ENDPOINT": "endpoint", "OSS_BUCKET": "bucket",  # 地域节点 / Bucket
    "OSS_ACCESS_KEY_ID": "access_key_id", "OSS_ACCESS_KEY_SECRET": "access_key_secret",  # 访问密钥
    "OSS_PREFIX": "prefix", "OSS_PUBLIC_BASE_URL": "public_base_url",  # 键前缀 / 公开域名
}
CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",  # 常见图片类型
                 ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}  # 上传时设置正确 MIME


def read_env_file(path: Path) -> dict:
    """解析简单 .env 文件（KEY=VALUE，# 开头为注释，值两侧引号会去掉）。"""
    out = {}  # 结果
    for line in path.read_text(encoding="utf-8").splitlines():  # 逐行
        line = line.strip()  # 去首尾空白
        if not line or line.startswith("#") or "=" not in line:  # 空行 / 注释 / 非赋值
            continue  # 跳过
        k, v = line.split("=", 1)  # 只按第一个等号切
        k = k.strip().removeprefix("export ").strip()  # 兼容 export KEY=VALUE
        out[k] = v.strip().strip("'\"")  # 去引号
    return out  # 键值表


def load_oss_config(env_file: Path | None = None, config_path: Path = CONFIG) -> dict:
    """合并 config.json 的 oss 字段、--env-file 与环境变量，返回 OSS 配置字典。"""
    cfg = {"prefix": "notes/"}  # 默认前缀
    try:  # config.json 可能不存在或损坏
        oss = json.loads(config_path.read_text(encoding="utf-8")).get("oss") or {}  # 取 oss 字段
        cfg.update({k: str(v).strip() for k, v in oss.items() if str(v).strip()})  # 只收非空值
    except (OSError, ValueError, AttributeError):  # 读不到就跳过
        pass  # 保持默认
    sources = [read_env_file(env_file)] if env_file else []  # .env 文件（较低）
    sources.append(dict(os.environ))  # 进程环境变量（最高）
    for src in sources:  # 按优先级从低到高覆盖
        for env_key, cfg_key in ENV_KEYS.items():  # 逐个已知变量
            if (src.get(env_key) or "").strip():  # 非空才覆盖
                cfg[cfg_key] = src[env_key].strip()  # 覆盖
    if cfg.get("prefix") and not cfg["prefix"].endswith("/"):  # 前缀统一以 / 结尾
        cfg["prefix"] += "/"  # 补斜杠
    cfg["prefix"] = cfg.get("prefix", "").lstrip("/")  # 对象键不能以 / 开头
    return cfg  # 合并后的配置


def public_url(cfg: dict, key: str) -> str | None:
    """拼对象的公开访问 URL；缺 bucket/endpoint 且无 public_base_url 时返回 None。"""
    if cfg.get("public_base_url"):  # 自定义域名 / CDN 优先
        return cfg["public_base_url"].rstrip("/") + "/" + key  # 直接拼键
    if cfg.get("bucket") and cfg.get("endpoint"):  # 默认外网域名
        host = re.sub(r"^https?://", "", cfg["endpoint"]).rstrip("/")  # endpoint 去协议
        return f"https://{cfg['bucket']}.{host}/{key}"  # https://<bucket>.<endpoint>/<key>
    return None  # 信息不足


def object_key(cfg: dict, file: Path) -> str:
    """对象键：<prefix><内容 sha256 前16位><扩展名>（同内容同键，天然幂等）。"""
    digest = hashlib.sha256(file.read_bytes()).hexdigest()[:16]  # 内容哈希
    return f"{cfg.get('prefix', '')}{digest}{file.suffix.lower()}"  # 拼键


def find_local_images(text: str, note_dir: Path) -> tuple[list[str], list[str]]:
    """找出笔记里的本地图片引用：返回 (存在的相对路径列表[去重保序], 不存在的路径列表)。"""
    found, missing = [], []  # 结果
    for m in IMG_MD_RE.finditer(text):  # 每处图片语法
        src = m.group(2)  # 路径
        if re.match(r"^(?:https?:)?//|^data:", src):  # 网络图 / 内嵌图
            continue  # 不处理
        if src in found or src in missing:  # 已登记
            continue  # 去重
        (found if (note_dir / src).is_file() else missing).append(src)  # 按存在与否分组
    return found, missing  # 返回


def make_bucket(cfg: dict):
    """创建 oss2.Bucket（惰性导入 oss2；未安装时给出可读报错）。"""
    try:  # oss2 为可选依赖
        import oss2  # 阿里云 OSS SDK
    except ImportError as e:  # 未安装
        raise RuntimeError("未安装 oss2，请先 pip install oss2") from e  # 可读报错
    auth = oss2.Auth(cfg["access_key_id"], cfg["access_key_secret"])  # 密钥鉴权
    endpoint = cfg["endpoint"] if cfg["endpoint"].startswith("http") else "https://" + cfg["endpoint"]  # 补协议
    return oss2.Bucket(auth, endpoint, cfg["bucket"])  # Bucket 句柄


def rewrite_note(text: str, mapping: dict[str, str]) -> tuple[str, int]:
    """把图片语法里的本地路径替换成 URL，返回 (新文本, 替换处数)；只改 ![..](..) 里的路径。"""
    count = 0  # 替换计数

    def repl(m: re.Match) -> str:  # 单处替换
        nonlocal count  # 修改外层计数
        url = mapping.get(m.group(2))  # 查映射
        if url is None:  # 不在映射里（网络图等）
            return m.group(0)  # 原样保留
        count += 1  # 计数
        return m.group(1) + url + m.group(3)  # 只换路径

    return IMG_MD_RE.sub(repl, text), count  # 新文本与计数


def run(note: Path, apply: bool, cfg: dict, bucket=None) -> dict:
    """主流程（便于测试注入 bucket）：返回输出契约字典。"""
    text = note.read_text(encoding="utf-8")  # 笔记全文
    found, missing = find_local_images(text, note.parent)  # 本地图片
    report = {"note": str(note), "mode": "apply" if apply else "dry-run",  # 基础字段
              "images": [], "missing": missing, "rewritten": 0, "backup": None, "status": "ok"}  # 默认值
    if missing:  # 有引用但文件不存在
        report.update(status="error", error="笔记引用的图片不存在，已停止（不上传、不改写）")  # 报错
        return report  # 直接返回
    plan = []  # (相对路径, 对象键, URL)
    for src in found:  # 逐张图
        key = object_key(cfg, note.parent / src)  # 对象键
        plan.append((src, key, public_url(cfg, key)))  # 记录计划
    if not apply:  # 预演
        report["images"] = [{"src": s, "key": k, "url": u, "status": "planned"} for s, k, u in plan]  # 计划清单
        if plan and plan[0][2] is None:  # 拼不出 URL
            report["note_hint"] = "未配置 bucket/endpoint（或 public_base_url），--apply 前请先补齐 OSS 配置"  # 提示
        return report  # 预演不联网
    if not plan:  # 没有本地图片
        return report  # 无事可做
    need = [k for k in ("endpoint", "bucket", "access_key_id", "access_key_secret") if not cfg.get(k)]  # 必填项
    if need:  # 缺配置
        report.update(status="error", error=f"OSS 配置缺少：{', '.join(need)}（见脚本说明的配置项）")  # 报错（只列键名）
        return report  # 返回
    bucket = bucket or make_bucket(cfg)  # 测试可注入假 bucket
    mapping = {}  # 本地路径 → URL
    for src, key, url in plan:  # 逐张上传
        if bucket.object_exists(key):  # 远端已有（同内容）
            status = "exists"  # 跳过上传
        else:  # 需要上传
            ctype = CONTENT_TYPES.get(Path(src).suffix.lower(), "application/octet-stream")  # MIME
            bucket.put_object_from_file(key, str(note.parent / src), headers={"Content-Type": ctype})  # 上传
            status = "uploaded"  # 已上传
        mapping[src] = url  # 记录映射
        report["images"].append({"src": src, "key": key, "url": url, "status": status})  # 记录结果
    new_text, count = rewrite_note(text, mapping)  # 改写笔记
    if count:  # 有改动才写
        backup = note.with_name(note.name + ".bak")  # 备份路径
        shutil.copy2(note, backup)  # 先备份
        note.write_text(new_text, encoding="utf-8")  # 再写回
        report.update(rewritten=count, backup=str(backup))  # 记录
    return report  # 返回报告


def parse_args(argv=None):
    """命令行参数。"""
    ap = argparse.ArgumentParser(description="把笔记里的本地截图上传 OSS 并改写为 OSS 链接（默认预演）")  # 说明
    ap.add_argument("note", type=Path, help="笔记 .md 路径")  # 笔记
    ap.add_argument("--apply", action="store_true", help="实际上传并改写笔记（默认只预演）")  # 实际执行
    ap.add_argument("--env-file", type=Path, default=None, help="从 .env 文件读取 OSS_* 配置")  # .env
    return ap.parse_args(argv)  # 解析


def main(argv=None) -> int:
    """入口：输出一行 JSON 报告，status=error 时退出码 1。"""
    try:  # 管道下 stdout 可能是 gbk
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 统一 utf-8
    except Exception:  # 老版本或非文本流
        pass  # 忽略
    args = parse_args(argv)  # 参数
    if not args.note.is_file():  # 笔记不存在
        print(json.dumps({"note": str(args.note), "status": "error", "error": "笔记文件不存在"},
                         ensure_ascii=False))  # 报错
        return 1  # 失败
    if args.env_file and not args.env_file.is_file():  # .env 不存在
        print(json.dumps({"note": str(args.note), "status": "error",
                          "error": f"--env-file 不存在：{args.env_file}"}, ensure_ascii=False))  # 报错
        return 1  # 失败
    cfg = load_oss_config(args.env_file)  # 合并配置
    try:  # 上传过程的网络/鉴权异常转成报告
        report = run(args.note, args.apply, cfg)  # 主流程
    except Exception as e:  # oss2 异常等
        msg = f"{type(e).__name__}: {e}"  # 类型与消息
        for secret in (cfg.get("access_key_id"), cfg.get("access_key_secret")):  # OSS 签名错误会回显 AccessKeyId
            if secret:  # 已配置才打码
                msg = msg.replace(secret, "***")  # 打码
        report = {"note": str(args.note), "status": "error", "error": msg}  # 报告
    print(json.dumps(report, ensure_ascii=False))  # 输出报告
    return 0 if report.get("status") == "ok" else 1  # 退出码


if __name__ == "__main__":  # 脚本入口
    sys.exit(main())  # 返回退出码
