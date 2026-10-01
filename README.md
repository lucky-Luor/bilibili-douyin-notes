<div align="center">

# bilibili-douyin-notes

**把 B站 / 抖音视频一键整理成 Markdown 课堂笔记的 AI Agent Skill**

字幕抓取 · 本地语音识别 · 重点截图 · 笔记模板 —— 全流程免费，无需登录

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
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

本 skill 遵循 **Agent Skills 规范**（`SKILL.md`），任何支持该规范的 AI Agent 均可安装。
支持的安装路径：

- `~/.claude/skills/` —— Claude Code
- `~/.zcode/skills/` —— ZCode
- `~/.agents/skills/` —— DSH、Codex 等多种 Agent 共用的通用 skills 路径

其他兼容 Agent 放入其各自的 skills 目录即可。

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
│   ├── ensure_abogus.py   # abogus.py 缺失时从上游按需下载（含校验与重试）
│   └── abogus.py          # 抖音 a_bogus 签名算法（GPLv3，首次使用时自动下载，不入库）
├── references/
│   ├── anchors.json       # 截图时间戳锚点库
│   ├── asr-glossary.json  # ASR 技术术语校正表
│   └── note-template.md   # 课堂笔记模板
├── config.example.json    # 配置模板（SESSDATA）
└── LICENSE                # MIT
```

## ⚠️ 已知限制

- 长视频（>1小时）本地转写耗时约为视频时长的 1~3 倍（CPU int8）
- ASR 转写的技术术语可能出错（如 IoC、Starter），生成笔记时建议结合上下文或代码仓库校正
- 抖音依赖平台签名算法，平台升级时可能临时失效（表现为 detail 接口 403），需跟进上游项目更新签名实现
- 抖音签名的 `abogus.py` 依赖上游仓库（BiliNote）托管：上游失效或迁移时 `ensure_abogus.py` 下载的文件可能过时，需跟进上游更新
- 仅支持公开可访问的视频

## 📄 许可证

本仓库主体采用 [MIT](LICENSE) 协议开源。

**例外：`scripts/abogus.py` 不在本仓库中分发。** 该文件是抖音 a_bogus 签名算法的
GPLv3 代码，源自 [TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader)
（经 [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)
与 [BiliNote](https://github.com/JefferyHcool/BiliNote) 移植）。由于 GPL 的传染性
（copyleft）：一旦仓库中包含这份代码，整个仓库就必须整体按 GPL-3.0 分发。
为了让本仓库保持宽松的 MIT 协议，我们把 `abogus.py` 移出仓库，由
`scripts/ensure_abogus.py` 在**首次使用抖音功能时自动下载**到本地；下载后的该文件
遵循其自身的 **GPLv3** 条款（上游文件头的 GPLv3/Apache-2.0 声明自相矛盾，本项目
按更严格的 GPLv3 处理），与 MIT 的仓库主体构成"聚合作品"，互不传染。

> 手动安装（网络受限时）：从 BiliNote 仓库的
> `backend/app/downloaders/douyin_helper/abogus.py`
> 下载文件，放到本 skill 的 `scripts/abogus.py` 即可。

## 🙏 致谢

- [JoeanAmier/TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader) —— `abogus.py` 原始作者
- [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) —— 抖音 Web API 签名与 x-secsdk-web-signature 方案参考
- [JefferyHcool/BiliNote](https://github.com/JefferyHcool/BiliNote) —— 多平台下载器架构参考
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) —— 本地语音识别引擎

<div align="center">

如果这个 skill 对你有帮助，欢迎点个 ⭐

</div>
