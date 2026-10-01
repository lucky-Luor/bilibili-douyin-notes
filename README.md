<div align="center">

# bilibili-douyin-notes

**把 B站 / 抖音视频一键整理成 Markdown 课堂笔记的 AI Agent Skill**

字幕抓取 · 本地语音识别 · 重点截图 · 笔记模板 —— 全流程免费，无需登录

[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/平台-B站%20%7C%20抖音-green.svg)]()
[![Python](https://img.shields.io/badge/Python-3.10%2B-yellow.svg)]()
[![Agent Skills](https://img.shields.io/badge/规范-Agent%20Skills%20(SKILL.md)-orange.svg)]()

</div>

---

## ✨ 功能特性

- **多平台支持**：B站（`bilibili.com` / `b23.tv` 短链 / BV号）与抖音（`douyin.com` / `v.douyin.com` 短链 / 分享口令文本）
- **字幕优先**：自动抓取B站官方/AI字幕（可选登录），零成本秒出文稿
- **ASR 兜底**：无字幕时用 faster-whisper 本地语音识别（CPU int8，国内走 hf-mirror 镜像），**不调任何付费 API**
- **重点截图**：按转写稿时间戳锚点 + PyAV 抽帧，自动校验剔除黑屏/无效帧；无硬编码锚点时自动从文稿提取关键词
- **抖音免登录**：游客身份（ttwid + a_bogus 签名）直接下载无水印视频，无需账号 Cookie
- **课堂笔记模板**：小节结构 + ★重点标注 + 真实代码引用 + 知识地图 + 自测题

## 🧩 工作流程

```
视频链接（B站/抖音）
        │
        ▼
┌─────────────────────┐
│ 第一步：抓取          │  B站：元数据 + 官方字幕（SESSDATA 可选）
│ （按平台分流）        │  抖音：元数据 + 无水印视频下载（免登录）
└─────────────────────┘
        │ 有字幕？──────否───┐
        ▼ 是                ▼
┌──────────────┐   ┌──────────────────────┐
│ 直接用字幕文本 │   │ 第二步：faster-whisper │
└──────────────┘   │ 本地 ASR 转写          │
        │          └──────────────────────┘
        ▼                  │
┌─────────────────────┐   │
│ 第二步半（可选）      │◄──┘
│ 重点截图（PyAV 抽帧） │
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│ 第三步：AI 按模板     │  课堂笔记 .md
│ 生成课堂笔记         │  （小节+★重点+代码+知识地图+自测题）
└─────────────────────┘
```

## 📦 安装

本 skill 遵循 **Agent Skills 规范**（`SKILL.md`），任何支持该规范的 AI Agent 均可安装：
Claude Code 放入 `~/.claude/skills/`，ZCode 放入 `~/.zcode/skills/`，其他兼容 Agent 放入其 skills 目录。

```bash
# 1. 克隆到你的 agent 的 skills 目录（以 ZCode 为例）
git clone https://github.com/lucky-Luor/bilibili-douyin-notes.git ~/.zcode/skills/bilibili-douyin-notes

# 2. 安装 Python 依赖
pip install faster-whisper av gmssl

# 3. （可选）配置 SESSDATA 以获取B站 AI 字幕
cp config.example.json config.json   # 按文件内注释填入 SESSDATA
```

> **路径说明**：`SKILL.md` 中示例命令使用了作者本机的绝对路径，安装后请把其中的
> `C:/Users/<你的用户名>/.zcode/skills/bilibili-douyin-notes/` 替换为你的实际安装路径
> （脚本内部均基于 `__file__` 相对定位，改路径不影响运行）。

<details>
<summary>最小依赖说明</summary>

| 组件 | 用途 | 是否必需 |
|---|---|---|
| `faster-whisper` | 语音识别兜底 | 做无字幕视频笔记时必需 |
| `av`（PyAV） | 音频解码 / 视频抽帧 | 同上（faster-whisper 依赖链会带上） |
| `gmssl` | 抖音 a_bogus 签名（SM3） | 仅抖音链接需要 |

</details>

## ⚙️ 配置

复制 `config.example.json` 为同目录 `config.json`：

```jsonc
{
  "SESSDATA": ""  // B站登录 cookie；留空也能用（官方CC字幕+ASR兜底均可）
}
```

获取方式：浏览器登录 bilibili.com → F12 → Application → Cookies → 复制 `SESSDATA`。
该文件已被 `.gitignore` 排除，不会被提交。抖音流程无需任何配置。

## 🚀 使用

安装后直接对 AI 说：

> 帮我把这个视频做成课堂笔记：https://www.bilibili.com/video/BVxxxx
>
> 总结一下这个抖音视频：https://v.douyin.com/xxxx/

也可以直接手动调用脚本（详见 [SKILL.md](SKILL.md) 的工作流程章节）：

```bash
# B站：抓取元数据与字幕
python scripts/bili_fetch.py "<视频链接>" "<输出目录>"

# 抖音：抓取元数据并下载无水印视频（免登录）
python scripts/douyin_fetch.py "<链接或分享口令>" "<输出目录>"

# 无字幕时：本地语音识别
python scripts/bili_transcribe.py "<输出目录>/metadata.json"

# （可选）抽取重点截图
python scripts/bili_screenshot.py "<输出目录>/metadata.json" "<输出目录>" "<输出目录>/images"
```

## 📁 目录结构

```
├── SKILL.md               # Agent Skills 规范定义：触发条件、三步工作流程、笔记模板
├── scripts/
│   ├── bili_fetch.py      # B站：元数据 + 字幕抓取
│   ├── douyin_fetch.py    # 抖音：元数据 + 无水印视频下载（游客身份）
│   ├── bili_transcribe.py # faster-whisper 本地 ASR 兜底（双平台兼容）
│   ├── bili_screenshot.py # 重点截图（锚点/自动关键词 + PyAV 抽帧）
│   └── abogus.py          # 抖音 a_bogus 签名算法（GPLv3，见致谢）
├── config.example.json    # 配置模板（SESSDATA）
└── LICENSE                # GPL-3.0
```

## ⚠️ 已知限制

- 长视频（>1小时）本地转写耗时约为视频时长的 1~3 倍（CPU int8）
- ASR 转写的技术术语可能出错（如 IoC、Starter），生成笔记时建议结合上下文或代码仓库校正
- 抖音依赖平台签名算法，平台升级时可能临时失效（表现为 detail 接口 403），需跟进上游项目更新签名实现
- 仅支持公开可访问的视频

## 📄 许可证

本项目采用 [GPL-3.0](LICENSE) 协议开源。

> 选择 GPL-3.0 的原因：`scripts/abogus.py` 源自 GPL-3.0 项目
> [TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader)（经
> [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)
> 与 [BiliNote](https://github.com/JefferyHcool/BiliNote) 移植），按 GPL 要求，
> 包含该代码的合并作品需以 GPL-3.0 整体分发，且保留其原始许可头。

## 🙏 致谢

- [JoeanAmier/TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader) —— `abogus.py` 原始作者
- [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) —— 抖音 Web API 签名与 x-secsdk-web-signature 方案参考
- [JefferyHcool/BiliNote](https://github.com/JefferyHcool/BiliNote) —— 多平台下载器架构参考
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) —— 本地语音识别引擎

<div align="center">

如果这个 skill 对你有帮助，欢迎点个 ⭐

</div>
