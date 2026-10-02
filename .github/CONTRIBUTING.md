# 贡献指南（CONTRIBUTING）

感谢参与贡献！本仓库是一个 AI Agent Skill（B站/抖音视频 → Markdown 课堂笔记），
主体代码 MIT 协议；唯一的例外是 `scripts/abogus.py`（GPLv3 组件，本仓库不包含、
也不分发，详见 [SECURITY.md](.github/SECURITY.md) 与 README「许可证」一节）——
**请勿把该文件或其衍生改动提交进 PR**。

## 环境准备

```bash
git clone https://github.com/lucky-Luor/bilibili-douyin-notes.git
cd bilibili-douyin-notes
pip install -r requirements-dev.txt        # pytest（测试离线可跑）
# 运行时依赖按需安装：pip install -r requirements.txt
```

## Commit 风格

采用 **Conventional Commits**（类型用英文、描述用中文）：

```
<type>(<scope>): <中文描述>

[可选正文：动机与影响]
```

常用 type：

| type    | 用途                                   |
|---------|----------------------------------------|
| feat    | 新功能                                 |
| fix     | 修复缺陷                               |
| test    | 补充/调整 tests/                       |
| docs    | 只改文档（README / SKILL.md / 模板）    |
| refactor| 不改行为的重构                         |
| chore   | 构建、CI、依赖等杂项                   |

示例：`feat(screenshot): 画质初筛改用像素占比法，白底PPT不再误判`

## 提交前自测清单（PR 模板也会要求）

1. **语法**：`python -m py_compile scripts/*.py` 全过；
2. **测试**：`python -m pytest tests/ -v` 全绿（新增/改动行为必须补对应离线测试，
   测试不得联网、不得依赖 faster-whisper/av/gmssl 已安装）；
3. **门禁**：改动涉及笔记模板/校验规则时，用 `scripts/validate_note.py` 校验一篇
   合成笔记，errors 为 0；
4. **文档**：行为变化同步更新 README.md / SKILL.md / references/note-template.md。

## 词表（asr-glossary.json）贡献方式

ASR 术语纠错词表按「真实错法 → 正确写法」维护（`references/asr-glossary.json`
的 `map` 对象）。贡献新条目时：

1. 错法必须是**实际转写稿中出现过的写法**（附上哪个视频/哪一秒更好）；
2. 正确写法如需语境说明，写在括号里：`"全线": "权限（Reactive权限例子语境）"`;
   替换时只取括号前的主词；
3. 避免把常用词映射为生僻词（替换是子串级的，会误伤）；
4. 一次 PR 一批相关条目，并在 PR 描述里给出 dry-run 报告片段佐证。

## 截图锚点（anchors.json）贡献方式

`references/anchors.json` 按 `{页码: [[label, 中文说明, [正则...]], ...]}` 组织，
用于按转写稿关键词定位抽帧时刻。新增锚点：

1. 正则匹配的是**转写稿文本**，请先 grep 实际文稿确认 ASR 实际用词
   （如 @Component 常被转成 "art component"，权限 → "全线"）；
2. label 尽量简短；中文 label 在 `--ascii-names` 下会自动转 `zh-<md5前6位>`；
3. 每页条目不超过 5 个，优先 PPT 切换/代码演示时刻。
