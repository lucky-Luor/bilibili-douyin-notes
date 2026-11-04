from pathlib import Path  # 构造临时课程文件


def card(name="机会成本", definition="放弃的最佳选择。", appear="第 2 节「权衡」、第 1 节「选择」", original="Opportunity Cost", depends="", confusable=""):  # 生成独立可组合的概念卡
    return "\n".join([f"### {name}", f"+ 定义：{definition}", f"+ 出现：{appear}", f"+ 原文：{original}", f"+ 依赖：{depends}", f"+ 易混：{confusable}"])  # 单行构造避免多行字面量遗漏注释


def note_text(cards=None, correspond="机会成本", nav=True):  # 生成带真实小节编号与来源链接的最小笔记
    return "\n\n".join([  # 章节之间保持模板空行
        "> 核心主线：用机会成本说明选择。",  # 笔记首行主线
        "## 1. 选择\n\n+ 每次选择都有代价。",  # 首个编号节
        "## 2. 权衡\n\n+ 权衡最佳替代选择。",  # 第二编号节
        "## 概念卡片\n\n" + "\n\n".join(cards if cards is not None else [card()]),  # 显式空列表用于无概念测试
        f"## 自测题\n\n1. 如何衡量代价？（对应：{correspond}）\n\n**答案：** 观察放弃的最佳选择。",  # 原始题号与答案
        "## 视频时间索引\n\n+ [00:00](https://www.bilibili.com/video/BVtest?p=1&t=0) 1. 选择\n+ [03:12](https://www.bilibili.com/video/BVtest?p=1&t=192) 2. 权衡" if nav else "## 视频时间索引",  # 两个来源时间点
    ]) + "\n"  # 使用标准结束换行


def write_note(root, index=1, text=None):  # 所有文件只写pytest临时目录
    root = Path(root)  # 同时支持字符串和路径
    path = root / "notes" / f"{index:03d}-中文 课堂.md"  # 覆盖中文与空格相对路径
    path.parent.mkdir(parents=True, exist_ok=True)  # 创建隔离笔记目录
    path.write_text(note_text() if text is None else text, encoding="utf-8")  # 写入真实UTF8字节
    return {"index": index, "title": f"第{index}讲 选择", "status": "done", "note": path.relative_to(root).as_posix()}  # 最小课程完成项


def course(*items):  # 旧版清单允许缺review和mastery字段
    return {"version": 1, "title": "中文 课程", "scope": {"first": 1, "last": 1}, "items": list(items)}  # scope有意只含首集


def make_current(root, model):  # 仅测试夹具模拟后续生成阶段的落盘
    for name in ("概念表.md", "课程自测.md"):  # 两个产物缺一不可
        (root / name).write_text("已有复习内容\n", encoding="utf-8")  # 测试检查无需实现render
    model["course"]["review"] = {"source_digest": model["source_digest"]}  # 模拟保存生成时摘要
    return model  # 便于测试组合
