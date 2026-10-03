# -*- coding: utf-8 -*-
"""apply_glossary.py 的离线测试：dry-run / --apply / .bak / 幂等 / 词表加载 /
S4 术语词表 v2（schema 迁移、两层词表、categories/topic、候选披露、hits 累加、
--add/--promote/--list、top_terms）。"""
import json
import sys

import pytest

import apply_glossary


def _run_main(argv, monkeypatch, capsys):
    """以给定 argv 运行 main()，返回 stdout 文本（末行固定为 JSON 摘要）。"""
    monkeypatch.setattr(sys, "argv", ["apply_glossary.py"] + argv)
    apply_glossary.main()
    return capsys.readouterr().out


GLOSSARY_JSON = {"map": {
    "全线": "权限（Reactive权限例子语境）",
    "art component": "@Component",
    "Stutter": "Starter",
}}


def _write_glossary(tmp_path):
    p = tmp_path / "glossary.json"
    p.write_text(json.dumps(GLOSSARY_JSON, ensure_ascii=False), encoding="utf-8")
    return p


def _write_doc(tmp_path, text="这一节讲 Reactive 的全线校验，用 art component 注解。\n"):
    p = tmp_path / "doc.txt"
    p.write_text(text, encoding="utf-8")
    return p


# ---------- 词表加载 ----------

def test_load_glossary_takes_main_word_before_paren(tmp_path):
    g = apply_glossary.load_glossary(_write_glossary(tmp_path))
    assert g["全线"] == "权限"  # 括号内只是语境说明


def test_load_glossary_md_arrow_format(tmp_path):
    p = tmp_path / "glossary.md"
    p.write_text("# 注释行\n错法A→正确B\n", encoding="utf-8")
    assert apply_glossary.load_glossary(p) == {"错法A": "正确B"}


def test_build_pattern_longest_key_first(tmp_path):
    g = {"购语言": "Java", "购语": "Ja"}
    pat = apply_glossary.build_pattern(g)
    m = pat.search("这里说购语言")
    assert m.group(0) == "购语言"  # 长键优先


# ---------- dry-run（默认，不改文件） ----------

def test_dry_run_reports_without_modifying(tmp_path, capsys):
    gp = _write_glossary(tmp_path)
    doc = _write_doc(tmp_path)
    original = doc.read_text(encoding="utf-8")
    g = apply_glossary.load_glossary(gp)
    pat = apply_glossary.build_pattern(g)
    summary = apply_glossary.process_file(doc, g, pat, apply=False)
    assert summary["hits"] == 2       # 全线 / art component
    assert summary["replaced"] == 0
    assert summary["backup"] is None
    assert doc.read_text(encoding="utf-8") == original  # 原文件未动
    out = capsys.readouterr().out
    assert '"全线" -> 权限' in out


# ---------- --apply（.bak 备份 + 原子替换） ----------

def test_apply_replaces_and_creates_bak(tmp_path):
    gp = _write_glossary(tmp_path)
    doc = _write_doc(tmp_path)
    original = doc.read_text(encoding="utf-8")
    g = apply_glossary.load_glossary(gp)
    pat = apply_glossary.build_pattern(g)
    summary = apply_glossary.process_file(doc, g, pat, apply=True)
    assert summary["replaced"] == 2
    bak = tmp_path / "doc.txt.bak"
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == original
    new_text = doc.read_text(encoding="utf-8")
    assert "权限" in new_text and "@Component" in new_text
    assert "全线" not in new_text and "art component" not in new_text
    assert not (tmp_path / "doc.txt.tmp").exists()  # 原子写无残留


def test_apply_is_idempotent(tmp_path):
    gp = _write_glossary(tmp_path)
    doc = _write_doc(tmp_path)
    g = apply_glossary.load_glossary(gp)
    pat = apply_glossary.build_pattern(g)
    apply_glossary.process_file(doc, g, pat, apply=True)
    summary2 = apply_glossary.process_file(doc, g, pat, apply=True)
    assert summary2["hits"] == 0  # 第二趟无命中（替换结果不再被匹配）


def test_case_insensitive_match(tmp_path):
    gp = tmp_path / "g.json"
    gp.write_text(json.dumps({"map": {"Stutter": "Starter"}}), encoding="utf-8")
    doc = tmp_path / "d.txt"
    doc.write_text("use the STUTTER dependency\n", encoding="utf-8")
    g = apply_glossary.load_glossary(gp)
    pat = apply_glossary.build_pattern(g)
    hits, new_text = apply_glossary.scan_text(doc.read_text(encoding="utf-8"), g, pat)
    assert len(hits) == 1
    assert "Starter" in new_text


def test_nearest_ts_placeholder_before_first_stamp():
    positions, stamps = apply_glossary.build_ts_index("前文 [00:10] 全线 后文")
    assert apply_glossary.nearest_ts(positions, stamps, 6) == "00:10"
    assert apply_glossary.nearest_ts(positions, stamps, 0) == "--:--"


# ---------- S4 术语词表 v2：schema 迁移 ----------

def _write_v2(tmp_path, data):
    p = tmp_path / "glossary.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_migrate_v1_classifies_programming_vs_general(tmp_path):
    """旧扁平 map 自动迁移：技术类词条进「编程」，其余进「通用」。"""
    p = tmp_path / "v1.json"
    p.write_text(json.dumps({"map": {
        "art component": "@Component", "Stutter": "Starter",
        "全线": "权限（Reactive权限例子语境）",
        "祸心": "和稀泥", "skew": "skill"}}, ensure_ascii=False), encoding="utf-8")
    data = apply_glossary.load_glossary_data(p)
    assert data["schema_version"] == 2
    cats = data["categories"]
    assert set(cats) >= {"编程", "通用"}          # 编程/美食/游戏/通用 初始化
    for c in ("美食", "游戏"):
        assert cats[c] == {}
    assert {"art component", "Stutter", "全线"} <= set(cats["编程"])
    assert {"祸心", "skew"} <= set(cats["通用"])
    # 字符串旧值读取时自动归一化为对象
    entry = cats["编程"]["art component"]
    assert entry == {"correct": "@Component", "hits": 0, "source": "", "disabled": False}
    # 迁移只发生在内存，原文件未被写
    assert "map" in json.loads(p.read_text(encoding="utf-8"))


def test_v2_load_normalizes_and_preserves_meta(tmp_path):
    data = {"schema_version": 2, "_说明": "meta 保留",
            "categories": {"编程": {"Stutter": {"correct": "Starter", "hits": 3,
                                                "source": "BV1", "disabled": False},
                                    "awell": {"correct": "Aware", "disabled": True},
                                    "bad": {"hits": 9}}},
            "candidates": {"编程": {"磨炸": {"correct": "mock 咋", "hits": 1}}}}
    loaded = apply_glossary.load_glossary_data(_write_v2(tmp_path, data))
    assert loaded["_说明"] == "meta 保留"
    assert loaded["categories"]["编程"]["Stutter"]["hits"] == 3
    assert "awell" not in apply_glossary.flatten_data(loaded)  # disabled 读取时跳过
    assert "bad" not in loaded["categories"]["编程"]           # 缺 correct 的词条忽略
    assert loaded["candidates"]["编程"]["磨炸"]["correct"] == "mock 咋"


def test_write_glossary_data_always_v2_and_atomic(tmp_path):
    p = tmp_path / "g.json"
    p.write_text(json.dumps({"map": {"Stutter": "Starter"}}), encoding="utf-8")
    data = apply_glossary.load_glossary_data(p)  # v1 读入
    data["categories"]["编程"]["Stutter"]["hits"] += 1
    apply_glossary.write_glossary_data(p, data)
    written = json.loads(p.read_text(encoding="utf-8"))
    assert written["schema_version"] == 2        # 写盘永远 v2 对象格式
    assert isinstance(written["categories"]["编程"]["Stutter"], dict)
    assert not (tmp_path / "g.json.tmp").exists()  # 原子写无残留


# ---------- S4：两层词表（用户层优先合并） ----------

def _layer(data):
    return {"path": None, "data": data}


def test_user_layer_overrides_repo_layer():
    repo = {"schema_version": 2, "categories": {"通用": {
        "平分": {"correct": "评分", "hits": 1, "source": "", "disabled": False}}},
        "candidates": {}}
    user = {"schema_version": 2, "categories": {"通用": {
        "平分": {"correct": "评分（用户口径）", "hits": 9, "source": "", "disabled": False},
        "祸心": {"correct": "和稀泥", "hits": 0, "source": "", "disabled": False}}},
        "candidates": {}}
    official, _, _ = apply_glossary.collect_entries([_layer(repo), _layer(user)])
    assert official["平分"]["hits"] == 9          # 同键用户层优先
    assert official["平分"]["layer"] == 1
    assert official["祸心"]["layer"] == 1         # 用户层新增词条生效


def test_main_merges_user_layer_from_home(tmp_path, monkeypatch, capsys):
    repo = _write_v2(tmp_path, {"schema_version": 2, "categories": {"通用": {
        "平分": {"correct": "评分", "hits": 0, "source": "", "disabled": False}}},
        "candidates": {}})
    user = tmp_path / "home" / ".zcode" / "bdn-glossary.user.json"
    user.parent.mkdir(parents=True)
    user.write_text(json.dumps({"schema_version": 2, "categories": {"通用": {
        "平分": {"correct": "评分", "hits": 7, "source": "", "disabled": False},
        "祸心": {"correct": "和稀泥", "hits": 0, "source": "", "disabled": False}}},
        "candidates": {}}, ensure_ascii=False), encoding="utf-8")
    doc = tmp_path / "d.txt"
    doc.write_text("平分 祸心\n", encoding="utf-8")
    monkeypatch.setattr(apply_glossary, "default_glossary_path", lambda: repo)
    monkeypatch.setattr(apply_glossary, "user_glossary_home", lambda: user)
    out = _run_main([str(doc), "--dry-run"], monkeypatch, capsys)
    assert '"平分" -> 评分' in out and '"祸心" -> 和稀泥' in out
    summary = json.loads(out.strip().splitlines()[-1])
    assert summary["user_glossary"] == str(user)  # 用户层被叠加（同目录无 glossary.user.json）


def test_resolve_user_glossary_path_prefers_outdir(tmp_path):
    home = tmp_path / "home" / ".zcode" / "bdn-glossary.user.json"
    outdir = tmp_path / "out"
    outdir.mkdir()
    monkey_target = apply_glossary.user_glossary_home
    try:
        apply_glossary.user_glossary_home = lambda: home
        # 都不存在：读取返回 None；落盘（create）优先建在输出目录
        assert apply_glossary.resolve_user_glossary_path([str(outdir / "d.txt")]) is None
        assert apply_glossary.resolve_user_glossary_path(
            [str(outdir / "d.txt")], create=True) == outdir / "glossary.user.json"
        # 无文件上下文（如 --add --user 单独运行）→ 落盘 ~/.zcode
        assert apply_glossary.resolve_user_glossary_path([], create=True) == home
        # 输出目录存在 glossary.user.json → 优先于 home
        out_user = outdir / "glossary.user.json"
        out_user.write_text("{}", encoding="utf-8")
        assert apply_glossary.resolve_user_glossary_path(
            [str(outdir / "d.txt")]) == out_user
    finally:
        apply_glossary.user_glossary_home = monkey_target


# ---------- S4：categories 过滤 / topic / 候选不替换 ----------

V2_FIXTURE = {"schema_version": 2,
              "categories": {"编程": {"Stutter": {"correct": "Starter", "hits": 0,
                                                  "source": "", "disabled": False}},
                             "通用": {"祸心": {"correct": "和稀泥", "hits": 0,
                                               "source": "", "disabled": False}}},
              "candidates": {"编程": {"awell": {"correct": "Aware", "hits": 0}},
                             "美食": {"磨炸": {"correct": "mock 咋", "hits": 0}}}}


def test_categories_filter_limits_official_replacement(tmp_path):
    compiled = apply_glossary.compile_glossary([_layer(V2_FIXTURE)],
                                               categories={"编程"})
    hits, new_text = apply_glossary.scan_text_v2("祸心 and Stutter", compiled)
    assert [(h["wrong"], h["category"]) for h in hits] == [("Stutter", "编程")]
    assert "Starter" in new_text and "祸心" in new_text   # 未启用类别不替换


def test_candidate_reported_but_never_replaced():
    compiled = apply_glossary.compile_glossary([_layer(V2_FIXTURE)])
    hits, new_text = apply_glossary.scan_text_v2("awell 和 Stutter", compiled)
    assert [(h["wrong"], h["candidate"]) for h in hits] == [("awell", True),
                                                            ("Stutter", False)]
    assert new_text == "awell 和 Starter"                 # 候选原样保留
    assert [h["category"] for h in hits] == ["编程", "编程"]


def test_topic_limits_candidate_disclosure_only():
    full = apply_glossary.compile_glossary([_layer(V2_FIXTURE)])
    assert set(full["candidates"]) == {"awell", "磨炸"}
    narrowed = apply_glossary.compile_glossary([_layer(V2_FIXTURE)], topic="美食")
    assert set(narrowed["candidates"]) == {"磨炸"}        # 候选披露只对同类别积极
    assert set(narrowed["official"]) == {"stutter", "祸心"}  # 正式替换不受影响


def test_report_groups_by_category_and_marks_candidates(tmp_path, monkeypatch, capsys):
    gp = _write_v2(tmp_path, V2_FIXTURE)
    doc = tmp_path / "d.txt"
    doc.write_text("awell 祸心 Stutter\n", encoding="utf-8")
    out = _run_main([str(doc), "--glossary", str(gp), "--dry-run"],
                    monkeypatch, capsys)
    assert "== 通用 ==" in out and "== 编程 ==" in out
    assert '== 候选（只报告，不替换）==' in out
    assert '[候选] "awell" -> Aware' in out
    assert "提示: 候选命中 1 处" in out and "--promote" in out
    summary = json.loads(out.strip().splitlines()[-1])
    assert summary["by_category"] == {"编程": 1, "通用": 1}
    assert summary["candidate_hits"] == 1


# ---------- S4：--apply hits 累加写回 ----------

def test_apply_bumps_hits_and_writes_glossary_back(tmp_path, monkeypatch, capsys):
    gp = tmp_path / "glossary.json"
    gp.write_text(json.dumps({"map": {"Stutter": "Starter", "祸心": "和稀泥"}},
                             ensure_ascii=False), encoding="utf-8")
    doc = tmp_path / "d.txt"
    doc.write_text("use STUTTER, 祸心\n", encoding="utf-8")
    _run_main([str(doc), "--glossary", str(gp), "--apply"], monkeypatch, capsys)
    data = json.loads(gp.read_text(encoding="utf-8"))
    assert data["schema_version"] == 2                     # 写回升级为 v2
    assert data["categories"]["编程"]["Stutter"]["hits"] == 1
    assert data["categories"]["通用"]["祸心"]["hits"] == 1
    assert "Starter" in doc.read_text(encoding="utf-8")
    assert not (tmp_path / "glossary.json.tmp").exists()


def test_dry_run_does_not_write_hits(tmp_path, monkeypatch, capsys):
    gp = _write_v2(tmp_path, {"schema_version": 2, "categories": {"编程": {
        "Stutter": {"correct": "Starter", "hits": 5, "source": "", "disabled": False}}},
        "candidates": {}})
    doc = tmp_path / "d.txt"
    doc.write_text("STUTTER\n", encoding="utf-8")
    _run_main([str(doc), "--glossary", str(gp), "--dry-run"], monkeypatch, capsys)
    data = json.loads(gp.read_text(encoding="utf-8"))
    assert data["categories"]["编程"]["Stutter"]["hits"] == 5  # dry-run 不写


def test_apply_hits_write_back_respects_user_layer(tmp_path, monkeypatch, capsys):
    """用户层覆盖的词条：命中累加记在用户层，仓库层原值不动。"""
    repo = _write_v2(tmp_path, {"schema_version": 2, "categories": {"通用": {
        "平分": {"correct": "评分", "hits": 1, "source": "", "disabled": False}}},
        "candidates": {}})
    user = tmp_path / "glossary.user.json"
    user.write_text(json.dumps({"schema_version": 2, "categories": {"通用": {
        "平分": {"correct": "评分", "hits": 9, "source": "", "disabled": False}}},
        "candidates": {}}, ensure_ascii=False), encoding="utf-8")
    doc = tmp_path / "out.txt"
    doc.write_text("平分\n", encoding="utf-8")
    monkeypatch.setattr(apply_glossary, "default_glossary_path", lambda: repo)
    monkeypatch.setattr(apply_glossary, "user_glossary_home",
                        lambda: tmp_path / "none.json")
    _run_main([str(doc), "--apply"], monkeypatch, capsys)
    assert json.loads(user.read_text(encoding="utf-8"))["categories"]["通用"]["平分"]["hits"] == 10
    assert json.loads(repo.read_text(encoding="utf-8"))["categories"]["通用"]["平分"]["hits"] == 1


# ---------- S4：--add / --promote ----------

def test_add_creates_category_and_promote(tmp_path, monkeypatch, capsys):
    gp = _write_v2(tmp_path, {"schema_version": 2, "categories": {"编程": {}},
                              "candidates": {}})
    _run_main(["--add", "磨炸=mock 咋（测试语境）", "--category", "美食",
               "--candidate", "--glossary", str(gp)], monkeypatch, capsys)
    data = json.loads(gp.read_text(encoding="utf-8"))
    assert "美食" in data["candidates"]                    # 类别不存在自动建
    assert data["candidates"]["美食"]["磨炸"]["correct"] == "mock 咋（测试语境）"
    _run_main(["--promote", "磨炸", "--glossary", str(gp)], monkeypatch, capsys)
    data = json.loads(gp.read_text(encoding="utf-8"))
    assert data["categories"]["美食"]["磨炸"]["correct"] == "mock 咋（测试语境）"
    assert data["categories"]["美食"]["磨炸"]["source"].startswith("manual:")
    assert "promoted:" in data["categories"]["美食"]["磨炸"]["source"]
    assert "磨炸" not in data["candidates"]["美食"]
    assert not (tmp_path / "glossary.json.tmp").exists()   # 原子写无残留


def test_add_official_cleans_same_key_candidate(tmp_path, monkeypatch, capsys):
    gp = _write_v2(tmp_path, {"schema_version": 2, "categories": {},
                              "candidates": {"通用": {"磨炸": {
                                  "correct": "mock 咋", "hits": 3}}}})
    _run_main(["--add", "磨炸=mock 咋", "--glossary", str(gp)], monkeypatch, capsys)
    data = json.loads(gp.read_text(encoding="utf-8"))
    assert data["categories"]["通用"]["磨炸"]["correct"] == "mock 咋"
    assert data["candidates"]["通用"] == {}                # 同键候选已清理
    assert data["categories"]["通用"]["磨炸"]["hits"] == 0  # 新增正式词条 hits 从 0 起


def test_add_requires_correct_format(monkeypatch, capsys):
    with pytest.raises(SystemExit) as ei:
        _run_main(["--add", "没有分隔符"], monkeypatch, capsys)
    assert ei.value.code == 1


def test_promote_missing_candidate_errors(tmp_path, monkeypatch, capsys):
    gp = _write_v2(tmp_path, {"schema_version": 2, "categories": {}, "candidates": {}})
    with pytest.raises(SystemExit) as ei:
        _run_main(["--promote", "不存在", "--glossary", str(gp)],
                  monkeypatch, capsys)
    assert ei.value.code == 1


def test_promote_searches_user_layer_first(tmp_path, monkeypatch, capsys):
    """不带 --user/--glossary 时按 用户层 > 仓库层 查找，写回候选所在的层。"""
    repo = _write_v2(tmp_path, {"schema_version": 2, "categories": {},
                                "candidates": {}})
    user = tmp_path / "glossary.user.json"
    user.write_text(json.dumps({"schema_version": 2, "categories": {},
                                "candidates": {"通用": {"磨炸": {
                                    "correct": "mock 咋", "hits": 4}}}},
                               ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(apply_glossary, "default_glossary_path", lambda: repo)
    monkeypatch.setattr(apply_glossary, "user_glossary_home", lambda: user)
    out = _run_main(["--promote", "磨炸"], monkeypatch, capsys)
    data = json.loads(user.read_text(encoding="utf-8"))
    assert data["categories"]["通用"]["磨炸"]["hits"] == 4  # hits/source 保留
    assert data["candidates"]["通用"] == {}
    assert json.loads(repo.read_text(encoding="utf-8"))["candidates"] == {}  # 仓库层未动
    assert json.loads(out.strip().splitlines()[-1])["file"] == str(user)


# ---------- S4：--list / top_terms ----------

def test_list_outputs_categories_with_hits(tmp_path, monkeypatch, capsys):
    gp = _write_v2(tmp_path, V2_FIXTURE)
    out = _run_main(["--list", "--glossary", str(gp)], monkeypatch, capsys)
    assert "== 编程 ==" in out and "== 通用 ==" in out
    assert "Stutter -> Starter    (hits=0)" in out
    assert "-- 候选（只报告，不替换）--" in out and "awell -> Aware" in out
    summary = json.loads(out.strip().splitlines()[-1])
    assert summary["categories"]["编程"] == {"official": 1, "candidates": 1}


def test_top_terms_orders_by_hits(tmp_path, monkeypatch):
    repo = _write_v2(tmp_path, {"schema_version": 2, "categories": {
        "编程": {"Stutter": {"correct": "Starter", "hits": 5, "source": "", "disabled": False},
                 "art component": {"correct": "@Component", "hits": 9, "source": "", "disabled": False},
                 "awell": {"correct": "Aware", "hits": 1, "source": "", "disabled": True}},
        "通用": {"祸心": {"correct": "和稀泥", "hits": 1, "source": "", "disabled": False}}},
        "candidates": {"编程": {"磨炸": {"correct": "mock 咋", "hits": 99}}}})
    monkeypatch.setattr(apply_glossary, "default_glossary_path", lambda: repo)
    monkeypatch.setattr(apply_glossary, "user_glossary_home",
                        lambda: tmp_path / "none.json")
    assert apply_glossary.top_terms(limit=2) == ["@Component", "Starter"]
    assert apply_glossary.top_terms() == ["@Component", "Starter", "和稀泥"]
    assert apply_glossary.top_terms(category="通用") == ["和稀泥"]
    assert apply_glossary.top_terms(category="美食") == []


def test_top_terms_dedupes_correct_words(tmp_path, monkeypatch):
    repo = _write_v2(tmp_path, {"schema_version": 2, "categories": {"编程": {
        "加瓦一": {"correct": "JavaEE", "hits": 2, "source": "", "disabled": False},
        "Y211": {"correct": "JavaEE", "hits": 7, "source": "", "disabled": False}}},
        "candidates": {}})
    monkeypatch.setattr(apply_glossary, "default_glossary_path", lambda: repo)
    monkeypatch.setattr(apply_glossary, "user_glossary_home",
                        lambda: tmp_path / "none.json")
    assert apply_glossary.top_terms() == ["JavaEE"]        # 去重，hits 取最大值 7
