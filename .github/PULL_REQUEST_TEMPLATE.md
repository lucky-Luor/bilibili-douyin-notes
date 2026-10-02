<!-- PR 标题建议遵循 Conventional Commits：type(scope): 中文描述 -->

## 改动说明

<!-- 简述动机、改动内容与影响范围。 -->

## 改动类型

- [ ] 功能（feat）
- [ ] 修复（fix）
- [ ] 测试（test）
- [ ] 文档（docs）
- [ ] 重构（refactor）
- [ ] 杂项（chore / CI / 依赖）

## 自测清单

- [ ] `python -m py_compile scripts/*.py` 全部通过
- [ ] `python -m pytest tests/ -v` 全绿（新增/改动行为已补离线测试）
- [ ] 涉及笔记模板/校验规则时：`python scripts/validate_note.py <测试笔记.md>` errors 为 0（门禁通过）
- [ ] 测试保持离线可跑（不联网、不依赖 faster-whisper/av/gmssl 已安装）
- [ ] 行为变化已同步更新 README.md / SKILL.md / references/note-template.md
- [ ] 未引入 `scripts/abogus.py`（GPLv3 组件不入库，见 SECURITY.md）

## 补充信息

<!-- 词表/锚点贡献请附 dry-run 报告片段或锚点匹配佐证（见 CONTRIBUTING.md）。 -->
