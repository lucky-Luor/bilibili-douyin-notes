#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes ASR 术语校正 + 用户术语偏好档案（报告式，可审计，不静默替换）。

用法:
    # 校正（默认 dry-run 只报告；--apply 才替换并写 .bak）
    python apply_glossary.py <文件.md|.txt> [更多文件...] [--glossary <词表路径>] [--categories 编程,通用] [--topic 编程]
    # 查看词表（按类别列正式+候选，带 hits）后退出
    python apply_glossary.py --list
    # 人工入库 / 候选转正
    python apply_glossary.py --add "错法=正确词" [--category 编程] [--candidate] [--user]
    python apply_glossary.py --promote "错法" [--category 编程]

词表 schema v2（用户术语偏好档案）:
    {"schema_version": 2,
     "categories": {"编程": {"错法": {"correct": "正确词", "hits": 12, "source": "BV1..p3"}},
                    "美食": {}, "游戏": {}, "通用": {...}},
     "candidates": {"编程": {"错法": {...}}}}          # 候选区：命中只报告，永不替换
    - 词条值兼容旧字符串（读取时自动归一化为对象；写盘永远 v2 对象格式）；
    - hits: --apply 替换命中自动 +1，供 initial_prompt 注入排序（top_terms）；
    - source: 来源可追溯；disabled: true 停用（保留历史，读取时跳过，无 CLI）。
    旧扁平 map（schema v1）自动迁移：技术类词条（@Component/Spring/Bean/Java…）进
    「编程」，其余进「通用」；迁移只发生在内存，原文件在被写回时才升级为 v2。

两层词表:
    仓库层 references/asr-glossary.json（--glossary 可显式指定，显式时即为全部词表）
    + 用户层（查找顺序: <输出目录>/glossary.user.json > ~/.zcode/bdn-glossary.user.json，
    同键用户层优先；--glossary 显式指定时不叠加用户层，保持旧行为）。

类别与主题:
    --categories 编程,通用 只启用所选类别（正式替换与候选报告同时收窄）；
    --topic 编程 只影响候选披露的积极性（候选只报同类别），正式替换不受影响。

模式:
    默认 --dry-run：只输出报告行（按类别分组，候选标 [候选]），不改任何文件。
    --apply：替换前先备份 <文件>.bak，再原子写（.tmp → os.replace）；命中词条
    hits 自动 +1 并原子写回词表（dry-run 不写）。替换是否成立依赖语境，必须先看
    dry-run 报告逐条确认；本工具不做任何静默改写。

匹配规则:
    词表键按长度降序合并进同一个正则交替（长键优先），单趟扫描、大小写不敏感；
    替换产生的文本不会被再次匹配（幂等）；候选命中永不替换。

输出: 报告行（按类别分组，候选殿后带 [候选]）+ 末尾一行 JSON 摘要
{"file","hits","replaced","backup","by_category","candidate_hits",...}，
末尾候选命中时提示 --promote。JSON 摘要 ensure_ascii=True（GBK 管道无损）。
正常 exit 0；文件/词表读取失败 exit 1。
"""
import argparse
import bisect
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

TS_RE = re.compile(r"\[(\d{1,2}:\d{2})\]")
MD_PAREN_RE = re.compile(r"[-*\d.、\s]*([^\s→（(]+)\s*[（(]([^）)]+)[）)]")
MD_ARROW_RE = re.compile(r"[-*\s]*([^\s→]+)\s*→\s*(\S+)")

SCHEMA_VERSION = 2
DEFAULT_CATEGORIES = ("编程", "美食", "游戏", "通用")
DEFAULT_CATEGORY = "通用"
USER_GLOSSARY_FILENAME = "glossary.user.json"

# 迁移启发式：键/正确词命中任一关键词 → 编程类，否则进通用（用户可事后 --add --category 调整）
TECH_KEYWORDS = (
    "@component", "component", "starter", "spring", "bean", "java", "netty",
    "war包", "slf4j", "log4j", "facade", "aware", "actuator", "metric",
    "beanfactory", "applicationcontext", "clone", "go语言", "golang", "warn",
    "println", "gpt", "gemini", "codex", "openai", "reactive",
    "python", "json", "sql", "docker", "k8s", "linux", "maven", "gradle",
    "redis", "mysql", "http", "jvm", "api", "sdk",
    "软件工程", "编程", "注解", "编译", "部署", "框架", "算法", "数据库",
    "缓存", "代码", "程序",
)


def default_glossary_path() -> Path:
    return Path(__file__).resolve().parent.parent / "references" / "asr-glossary.json"


def user_glossary_home() -> Path:
    return Path.home() / ".zcode" / "bdn-glossary.user.json"


# ---------- schema v2：归一化 / 迁移 / 读写 ----------

def normalize_entry(value) -> dict | None:
    """词条值归一化：旧字符串 → 对象；缺 correct 的非法值返回 None（读取时忽略）。"""
    if isinstance(value, str):
        return {"correct": value, "hits": 0, "source": "", "disabled": False}
    if isinstance(value, dict):
        correct = value.get("correct")
        if not isinstance(correct, str) or not correct.strip():
            return None
        try:
            hits = int(value.get("hits", 0))
        except (TypeError, ValueError):
            hits = 0
        return {"correct": correct, "hits": max(0, hits),
                "source": str(value.get("source", "")),
                "disabled": bool(value.get("disabled", False))}
    return None


def main_word(correct: str) -> str:
    """正确词取主词：split("（")[0]（括号内仅是语境说明）。"""
    return correct.split("（")[0].split("(")[0].strip()


def classify_category(wrong: str, correct: str) -> str:
    """迁移启发式：技术类词条进「编程」，其余进「通用」。"""
    blob = f"{wrong} {correct}".lower()
    return "编程" if any(kw in blob for kw in TECH_KEYWORDS) else DEFAULT_CATEGORY


def migrate_v1_to_v2(data: dict) -> dict:
    """旧扁平 map（schema v1）自动迁移进类别区：技术类→「编程」，其余→「通用」。"""
    mapping = data.get("map")
    if not isinstance(mapping, dict):  # 宽松兜底：无 map 的顶层键即词条
        mapping = {k: v for k, v in data.items() if not str(k).startswith("_")}
    cats = {c: {} for c in DEFAULT_CATEGORIES}
    for wrong, val in mapping.items():
        entry = normalize_entry(val)
        wrong = str(wrong)
        if not entry or not wrong.strip():
            continue
        cats.setdefault(classify_category(wrong, entry["correct"]), {})[wrong] = entry
    out: dict = {"schema_version": SCHEMA_VERSION}
    for k, v in data.items():  # 保留 _说明 等 meta 键
        if str(k).startswith("_"):
            out[k] = v
    out["categories"] = cats
    out["candidates"] = {}
    return out


def _normalize_area(data: dict, name: str) -> dict:
    area: dict = {}
    raw = data.get(name)
    if not isinstance(raw, dict):
        return area
    for cat, entries in raw.items():
        cat = str(cat)
        if cat.startswith("_") or not isinstance(entries, dict):
            continue
        bucket: dict = {}
        for key, val in entries.items():
            key = str(key)
            if key.startswith("_"):
                continue
            entry = normalize_entry(val)
            if entry and main_word(entry["correct"]):
                bucket[key] = entry
        area[cat] = bucket
    return area


def normalize_v2(data: dict) -> dict:
    """v2 结构补全/清洗：词条统一为对象格式，缺类别不自动补。"""
    out: dict = {"schema_version": SCHEMA_VERSION}
    for k, v in data.items():
        if str(k).startswith("_"):
            out[k] = v
    out["categories"] = _normalize_area(data, "categories")
    out["candidates"] = _normalize_area(data, "candidates")
    return out


def load_glossary_data(path: Path) -> dict:
    """读入词表并归一化为 v2 结构。旧格式（v1 map / .md 行式）只在内存迁移，不写盘。"""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() != ".json":
        raw: dict = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("|"):
                continue
            m = MD_PAREN_RE.match(line) or MD_ARROW_RE.match(line)
            if m:
                raw[m.group(1)] = m.group(2)
        cats = {DEFAULT_CATEGORY: {}}
        for wrong, correct in raw.items():
            entry = normalize_entry(correct)
            if entry and main_word(correct):
                cats[DEFAULT_CATEGORY][wrong] = entry
        return {"schema_version": SCHEMA_VERSION, "categories": cats, "candidates": {}}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"词表 {path} 顶层必须是 JSON 对象")
    if data.get("schema_version") == 2 or "categories" in data:
        return normalize_v2(data)
    return migrate_v1_to_v2(data)


def write_glossary_data(path: Path, data: dict) -> None:
    """写盘永远 v2 对象格式，原子写（.tmp → os.replace）。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def resolve_user_glossary_path(files, create: bool = False) -> Path | None:
    """用户层词表解析：<输出目录>/glossary.user.json > ~/.zcode/bdn-glossary.user.json。

    create=True（--add --user 落盘用）时返回首个可创建位置并确保父目录存在；
    create=False（读取用）时返回首个已存在文件，都不存在则 None。
    """
    if files:
        p = Path(files[0]).resolve().parent / USER_GLOSSARY_FILENAME
        if p.exists() or create:
            return p
    home = user_glossary_home()
    if home.exists() or create:
        home.parent.mkdir(parents=True, exist_ok=True)
        return home
    return None


def flatten_data(data: dict, categories=None) -> dict[str, str]:
    """v2 数据扁平化为 {错法: 正确词(主词)}（disabled 跳过），供兼容入口/测试。"""
    official, _, _ = collect_entries([{"path": None, "data": data}], categories)
    return {info["wrong"]: info["right"] for info in official.values()}


def merge_glossary_data(base: dict, override: dict | None) -> dict:
    """两层合并展示视图：同 (区域, 类别, 键) 用户层（override）优先。"""
    if not override:
        return base
    out: dict = {"schema_version": SCHEMA_VERSION}
    for k, v in base.items():
        if str(k).startswith("_"):
            out[k] = v
    for area in ("categories", "candidates"):
        merged = {cat: dict(entries) for cat, entries in (base.get(area) or {}).items()}
        for cat, entries in (override.get(area) or {}).items():
            merged.setdefault(cat, {}).update(entries)
        out[area] = merged
    return out


def collect_entries(layers: list, categories=None) -> tuple[dict, dict, list]:
    """收集正式/候选词条。layers=[{"path","data"},...]，后层（用户层）同键优先。

    返回 (official, candidates, category_order)，每条 info 含
    wrong/right/category/key/layer/hits，供扫描、hits 写回与 --list 使用。
    disabled: true 的词条读取时跳过。
    """
    official: dict = {}
    candidates: dict = {}
    order: list = []
    for idx, layer in enumerate(layers):
        data = layer["data"]
        for area_name, target in (("categories", official), ("candidates", candidates)):
            for cat, entries in (data.get(area_name) or {}).items():
                if categories is not None and cat not in categories:
                    continue
                if area_name == "categories" and cat not in order:
                    order.append(cat)
                for key, entry in entries.items():
                    if entry.get("disabled"):
                        continue
                    right = main_word(entry["correct"])
                    if not right:
                        continue
                    target[key.lower()] = {"wrong": key, "right": right,
                                           "category": cat, "key": key,
                                           "layer": idx,
                                           "hits": entry.get("hits", 0)}
    return official, candidates, order


# ---------- 匹配 / 扫描 ----------

def build_pattern_from_keys(keys) -> re.Pattern:
    """键按长度降序进同一个正则交替（长键优先），单趟扫描、大小写不敏感。"""
    keys = sorted(keys, key=len, reverse=True)
    if not keys:
        raise ValueError("词表（按所选类别过滤后）为空")
    return re.compile("|".join(re.escape(k) for k in keys), re.IGNORECASE)


def build_pattern(glossary: dict[str, str]) -> re.Pattern:
    """兼容入口：扁平 {错法: 正确词} 构造匹配正则。"""
    return build_pattern_from_keys(glossary.keys())


def compile_glossary(layers: list, categories=None, topic=None) -> dict:
    """把两层词表编译为可扫描结构。--topic 只收窄候选披露（同类别才积极）。"""
    official, candidates, order = collect_entries(layers, categories)
    if topic is not None:
        candidates = {k: v for k, v in candidates.items()
                      if v["category"] == topic}
    return {"pattern": build_pattern_from_keys(set(official) | set(candidates)),
            "official": official, "candidates": candidates,
            "category_order": order}


def build_ts_index(text: str) -> tuple[list[int], list[str]]:
    """[mm:ss] 时间戳位置索引，供二分查找每个命中左侧最近的时间戳。"""
    positions, stamps = [], []
    for m in TS_RE.finditer(text):
        positions.append(m.start())
        stamps.append(m.group(1))
    return positions, stamps


def nearest_ts(positions: list[int], stamps: list[str], pos: int) -> str:
    """命中位置左侧最近的 [mm:ss]；前方没有时间戳时返回占位符。"""
    i = bisect.bisect_right(positions, pos) - 1
    return stamps[i] if i >= 0 else "--:--"


def scan_text_v2(text: str, compiled: dict) -> tuple[list[dict], str]:
    """扫描全部命中。正式命中（替换）+ 候选命中（只报告，永不替换）。

    返回 (hits, new_text)：每条 hit 含 ts/wrong/right/category/candidate/context。
    """
    pat = compiled["pattern"]
    official, candidates = compiled["official"], compiled["candidates"]
    positions, stamps = build_ts_index(text)
    hits = []
    for m in pat.finditer(text):
        key = m.group(0).lower()
        if key in official:
            info, is_cand = official[key], False
        elif key in candidates:
            info, is_cand = candidates[key], True
        else:
            continue
        s, e = m.span()
        wrong = m.group(0)
        ctx = text[max(0, s - 20):s] + "【" + wrong + "】" + text[e:e + 20]
        hits.append({"ts": nearest_ts(positions, stamps, s),
                     "wrong": wrong, "right": info["right"],
                     "category": info["category"], "candidate": is_cand,
                     "context": ctx.replace("\n", " ").replace("\r", " ")})

    def _sub(m: re.Match) -> str:
        info = official.get(m.group(0).lower())
        return info["right"] if info else m.group(0)  # 候选永不替换

    return hits, pat.sub(_sub, text)


def process_file_v2(path: Path, compiled: dict, apply: bool) -> tuple[dict, set]:
    """处理单个文件：按类别分组打印报告（候选殿后标 [候选]）；--apply 时
    先 .bak 备份再原子写替换。返回 (摘要, 被替换的词条键集合)。"""
    text = path.read_text(encoding="utf-8")
    hits, new_text = scan_text_v2(text, compiled)
    official_hits = [h for h in hits if not h["candidate"]]
    cand_hits = [h for h in hits if h["candidate"]]
    present = [c for c in compiled["category_order"]
               if any(h["category"] == c for h in official_hits)]
    for cat in present:
        print(f"== {cat} ==")
        for h in (h for h in official_hits if h["category"] == cat):
            print(f'[{h["ts"]}] "{h["wrong"]}" -> {h["right"]} | …{h["context"]}…')
    if cand_hits:
        print("== 候选（只报告，不替换）==")
        for h in cand_hits:
            print(f'[{h["ts"]}] [候选] "{h["wrong"]}" -> {h["right"]} | …{h["context"]}…')
    backup = None
    replaced = set()
    if apply and official_hits:
        backup = str(path) + ".bak"
        Path(backup).write_text(text, encoding="utf-8")  # 备份原文（替换前）
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, path)  # 原子写：.tmp → os.replace
        replaced = {h["wrong"].lower() for h in official_hits}
    by_category: dict = {}
    for h in official_hits:
        by_category[h["category"]] = by_category.get(h["category"], 0) + 1
    summary = {"file": str(path), "hits": len(official_hits),
               "replaced": len(official_hits) if apply else 0, "backup": backup,
               "by_category": by_category, "candidate_hits": len(cand_hits)}
    return summary, replaced


# ---------- 兼容入口（旧扁平用法，v1 语义保留） ----------

def load_glossary(path: Path) -> dict[str, str]:
    """加载词表为 {错法: 正确词(主词)}；内部走 v2 加载+扁平化，旧格式自动迁移。"""
    data = load_glossary_data(path)
    official, _, _ = collect_entries([{"path": path, "data": data}])
    return {info["wrong"]: info["right"] for info in official.values()}


def scan_text(text: str, glossary: dict[str, str],
              pat: re.Pattern) -> tuple[list[dict], str]:
    """兼容入口：扁平 {错法: 正确词} 扫描（无类别/候选语义）。"""
    repl = {k.lower(): v for k, v in glossary.items()}
    positions, stamps = build_ts_index(text)
    hits = []
    for m in pat.finditer(text):
        wrong, right = m.group(0), repl.get(m.group(0).lower(), "")
        s, e = m.span()
        ctx = text[max(0, s - 20):s] + "【" + wrong + "】" + text[e:e + 20]
        hits.append({"ts": nearest_ts(positions, stamps, s),
                     "wrong": wrong, "right": right,
                     "context": ctx.replace("\n", " ").replace("\r", " ")})
    new_text = pat.sub(lambda m: repl[m.group(0).lower()], text)
    return hits, new_text


def process_file(path: Path, glossary: dict[str, str], pat: re.Pattern,
                 apply: bool) -> dict:
    """兼容入口：扁平词表处理单个文件（.bak 备份 + 原子写）。"""
    text = path.read_text(encoding="utf-8")
    hits, new_text = scan_text(text, glossary, pat)
    for h in hits:
        print(f'[{h["ts"]}] "{h["wrong"]}" -> {h["right"]} | …{h["context"]}…')
    backup = None
    if apply and hits:
        backup = str(path) + ".bak"
        Path(backup).write_text(text, encoding="utf-8")  # 备份原文（替换前）
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, path)  # 原子写：.tmp → os.replace
    return {"file": str(path), "hits": len(hits),
            "replaced": len(hits) if apply else 0, "backup": backup}


# ---------- 供管线调用的接口 ----------

def top_terms(category: str | None = None, limit: int = 20) -> list[str]:
    """按 hits 降序返回正式词条的正确词（去重），供 bili_transcribe 注入
    faster-whisper initial_prompt（按视频主题取词）。读仓库层+用户层默认词表。"""
    layers = []
    repo = default_glossary_path()
    if repo.exists():
        layers.append({"path": repo, "data": load_glossary_data(repo)})
    user = user_glossary_home()
    if user.exists():
        layers.append({"path": user, "data": load_glossary_data(user)})
    official, _, _ = collect_entries(layers,
                                     {category} if category else None)
    best: dict = {}
    for info in official.values():
        best[info["right"]] = max(best.get(info["right"], 0), info["hits"])
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    return [word for word, _ in ranked[:limit]]


# ---------- 输出 ----------

def _reconfigure_stdout() -> None:
    """stdout 兜底为 UTF-8：Agent 管道调用时编码常是 gbk/cp936，
    emoji 会让 print 直接 UnicodeEncodeError（第三轮复检 M3）。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def print_json_summary(obj: dict, **kw) -> None:
    """stdout 机器可读 JSON 摘要行。ensure_ascii=True：即便兜底失效、
    遇到 GBK 管道也不会崩——JSON 转义无损，下游 json.loads 照样还原 emoji。"""
    print(json.dumps(obj, ensure_ascii=True, **kw))


def _load_layers(glossary_arg: str | None, files) -> list:
    """加载词表层：仓库层（--glossary 显式或默认）+ 用户层（显式时不叠加）。"""
    repo_path = Path(glossary_arg) if glossary_arg else default_glossary_path()
    layers = [{"path": repo_path, "data": load_glossary_data(repo_path)}]
    user_path = None if glossary_arg else resolve_user_glossary_path(files)
    if user_path and user_path != repo_path:
        layers.append({"path": user_path, "data": load_glossary_data(user_path)})
    return layers


def _add_target_path(args) -> Path:
    """--add/--promote 的落盘目标：--user 用户层 > --glossary 显式 > 仓库默认。"""
    if args.user:
        return resolve_user_glossary_path(args.files, create=True)
    if args.glossary:
        return Path(args.glossary)
    return default_glossary_path()


def handle_add(args) -> None:
    """--add "错法=正确词"：入库（--candidate 进候选区；类别不存在自动建）。"""
    m = re.split("[=→]", args.add, maxsplit=1)
    if len(m) != 2 or not m[0].strip() or not m[1].strip():
        print_json_summary({"error": '--add 需要 "错法=正确词"（或 错法→正确词）格式'})
        sys.exit(1)
    wrong, correct = m[0].strip(), m[1].strip()
    path = _add_target_path(args)
    try:
        data = load_glossary_data(path) if path.exists() else {
            "schema_version": SCHEMA_VERSION, "categories": {}, "candidates": {}}
    except (OSError, ValueError) as e:
        print_json_summary({"error": f"词表加载失败: {e}"})
        sys.exit(1)
    area = data["candidates"] if args.candidate else data["categories"]
    cat = args.category or DEFAULT_CATEGORY
    bucket = area.setdefault(cat, {})  # 类别不存在自动建
    old = bucket.get(wrong)
    old_hits = old.get("hits", 0) if isinstance(old, dict) else 0
    bucket[wrong] = {"correct": correct, "hits": old_hits,
                     "source": f"manual:{date.today():%Y-%m-%d}", "disabled": False}
    if not args.candidate:  # 转正式新增：清理同类别同键候选，避免双份
        data["candidates"].get(cat, {}).pop(wrong, None)
    write_glossary_data(path, data)
    print_json_summary({"added": wrong, "correct": correct, "category": cat,
                        "candidate": bool(args.candidate),
                        "user_layer": bool(args.user), "file": str(path)})


def handle_promote(args) -> None:
    """--promote "错法"：候选转正（同类别搬入 categories，保留 hits/source）。

    查找顺序：--user 用户层 > --glossary 显式文件 > 用户层 → 仓库层（写回候选所在的层）。
    """
    if args.user:
        targets = [resolve_user_glossary_path(args.files, create=True)]
    elif args.glossary:
        targets = [Path(args.glossary)]
    else:
        user = resolve_user_glossary_path(args.files)
        targets = ([user] if user else []) + [default_glossary_path()]
    searched = []
    for path in targets:
        if not path.exists():
            searched.append(str(path))
            continue
        try:
            data = load_glossary_data(path)
        except (OSError, ValueError) as e:
            print_json_summary({"error": f"词表加载失败: {e}"})
            sys.exit(1)
        matches = [(cat, key) for cat, entries in data["candidates"].items()
                   for key in entries
                   if key == args.promote or key.lower() == args.promote.lower()]
        if not matches:
            searched.append(str(path))
            continue
        if args.category:
            matches = [m for m in matches if m[0] == args.category]
            if not matches:
                print_json_summary({"error": f"类别 {args.category} 的候选区未找到: "
                                             f"{args.promote}"})
                sys.exit(1)
        if len(matches) > 1:
            print_json_summary({"error": f"{args.promote} 存在于多个类别的候选区: "
                                         f"{[c for c, _ in matches]}，请用 --category 指定"})
            sys.exit(1)
        cat, key = matches[0]
        entry = data["candidates"][cat].pop(key)
        entry["source"] = f"{entry.get('source', '')} | promoted:{date.today():%Y-%m-%d}".lstrip(" |")
        data["categories"].setdefault(cat, {})[key] = entry
        write_glossary_data(path, data)
        print_json_summary({"promoted": key, "correct": entry["correct"],
                            "category": cat, "hits": entry["hits"],
                            "file": str(path)})
        return
    print_json_summary({"error": f"候选区未找到: {args.promote}"
                                 f"（--list 可查看候选；已查找: {searched}）"})
    sys.exit(1)


def handle_list(layers: list, categories) -> None:
    """--list：按类别列正式+候选词条（带 hits），列完退出。"""
    merged = merge_glossary_data(layers[0]["data"],
                                 layers[1]["data"] if len(layers) > 1 else None)

    def area_cats(area):
        return [c for c in (merged.get(area) or {})
                if categories is None or c in categories]

    cats = list(dict.fromkeys(area_cats("categories") + area_cats("candidates")))
    summary = {}
    for cat in cats:
        print(f"== {cat} ==")
        summary[cat] = {"official": 0, "candidates": 0}
        for key, entry in (merged.get("categories") or {}).get(cat, {}).items():
            mark = " [停用]" if entry.get("disabled") else ""
            print(f'  {key} -> {entry["correct"]}    (hits={entry.get("hits", 0)}){mark}')
            summary[cat]["official"] += 1
        cand = (merged.get("candidates") or {}).get(cat, {})
        if cand:
            print("  -- 候选（只报告，不替换）--")
        for key, entry in cand.items():
            mark = " [停用]" if entry.get("disabled") else ""
            print(f'  {key} -> {entry["correct"]}    (hits={entry.get("hits", 0)}){mark}')
            summary[cat]["candidates"] += 1
    print_json_summary({"glossary": str(layers[0]["path"]),
                        "user_glossary": str(layers[1]["path"]) if len(layers) > 1 else None,
                        "categories": summary})


def main():
    _reconfigure_stdout()
    ap = argparse.ArgumentParser(
        description="ASR 术语校正 + 用户术语偏好档案（报告式，可审计；--apply 才落盘）",
        epilog="示例: python apply_glossary.py 转写.txt --dry-run   # 只看报告不改文件\n"
               "      python apply_glossary.py 转写.txt --apply          # 确认后替换\n"
               "      python apply_glossary.py --list                   # 按类别查看词表\n"
               "      python apply_glossary.py --add \"错法=正确词\" --category 编程 --user")
    ap.add_argument("files", nargs="*",
                    help="要校正的 .md / .txt 文件（可多个）；--list/--add/--promote 时可省略")
    ap.add_argument("--glossary", help="词表路径（默认 <skill目录>/references/asr-glossary.json；显式指定时不叠加用户层）")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true",
                      help="只输出报告不改任何文件（与默认行为相同；显式传参便于 Agent 编排时明确意图）")
    mode.add_argument("--apply", action="store_true",
                      help="实际替换（先写 .bak 备份，再原子写）+ 命中词条 hits 自动累加写回词表；缺省为 dry-run 只报告")
    ap.add_argument("--categories", help="只启用所选类别（逗号分隔，如 编程,通用）")
    ap.add_argument("--topic", help="笔记主题（编程/美食/游戏…）：候选披露只对同类别积极，正式替换不受影响")
    ap.add_argument("--list", action="store_true",
                    help="按类别列出正式+候选词条（带 hits）后退出")
    ap.add_argument("--add", metavar="错法=正确词",
                    help="新增词条（配合 --category/--candidate/--user；类别不存在自动建）")
    ap.add_argument("--promote", metavar="错法", help="把候选词条转正（候选区 → 正式区）")
    ap.add_argument("--category", help="类别（--add/--promote 用，默认 通用）")
    ap.add_argument("--candidate", action="store_true",
                    help="--add 时进候选区（命中只报告，永不替换）")
    ap.add_argument("--user", action="store_true",
                    help="--add/--promote 时写用户层词表（<输出目录>/glossary.user.json 或 ~/.zcode/bdn-glossary.user.json）")
    args = ap.parse_args()

    if args.add:
        handle_add(args)
        return
    if args.promote:
        handle_promote(args)
        return

    categories = None
    if args.categories:
        categories = {c.strip() for c in args.categories.split(",") if c.strip()}

    try:
        layers = _load_layers(args.glossary, args.files)
        if args.list:
            handle_list(layers, categories)
            return
        compiled = compile_glossary(layers, categories, args.topic)
    except (OSError, ValueError) as e:
        print_json_summary({"error": f"词表加载失败: {e}"})
        sys.exit(1)

    if not args.files:
        ap.error("需要提供要校正的文件，或使用 --list / --add / --promote")

    summaries, replaced = [], set()
    for f in args.files:
        path = Path(f)
        if not path.exists():
            print_json_summary({"error": f"文件不存在: {path}"})
            sys.exit(1)
        summary, file_replaced = process_file_v2(path, compiled, args.apply)
        summaries.append(summary)
        replaced |= file_replaced

    if args.apply and replaced:  # hits 自动 +1 并原子写回所属层（dry-run 不写）
        for key in replaced:
            info = compiled["official"].get(key)
            if not info:
                continue
            layer = layers[info["layer"]]
            entry = layer["data"]["categories"][info["category"]][info["key"]]
            entry["hits"] = int(entry.get("hits", 0)) + 1
            layer["changed"] = True
        for layer in layers:
            if layer.get("changed"):
                write_glossary_data(layer["path"], layer["data"])

    by_category: dict = {}
    for s in summaries:
        for cat, n in s["by_category"].items():
            by_category[cat] = by_category.get(cat, 0) + n
    candidate_total = sum(s["candidate_hits"] for s in summaries)
    if candidate_total:
        print(f'提示: 候选命中 {candidate_total} 处（未替换）。确认无误后运行 '
              f'--promote "错法" 转正，或 --add 覆盖入库。')
    print_json_summary({"glossary": str(layers[0]["path"]),
                        "user_glossary": str(layers[1]["path"]) if len(layers) > 1 else None,
                        "mode": "apply" if args.apply else "dry-run",
                        "by_category": by_category,
                        "candidate_hits": candidate_total,
                        "results": summaries})


if __name__ == "__main__":
    main()
