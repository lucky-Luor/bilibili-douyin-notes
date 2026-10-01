# bilibili-douyin-notes

ZCode skill：把 B站 / 抖音视频整理成 Markdown 课堂笔记。

## 功能

- **多平台**：bilibili.com / b23.tv 短链 / BV号，douyin.com / v.douyin.com 短链 / 分享口令文本
- **字幕优先**：B站官方/AI 字幕（可选填 SESSDATA），抖音直接下载无水印视频
- **ASR 兜底**：无字幕时 faster-whisper 本地语音识别（int8 CPU，走 hf-mirror 镜像）
- **重点截图**：按转写稿时间戳锚点 + PyAV 抽帧，无硬编码锚点时自动提取关键词
- **课堂笔记模板**：小节 + ★重点 + 真实代码 + 知识地图 + 自测题

整条管线 100% 免费：ASR/抽帧/抓取全部本地运行，无需登录（抖音走游客身份）。

## 依赖

```bash
pip install faster-whisper av gmssl   # 抖音仅需 gmssl
```

## 使用

安装为 ZCode skill（放入 `~/.zcode/skills/bilibili-douyin-notes/`），然后对 AI 说：
> 帮我把这个视频做成笔记：<链接>

详细流程见 [SKILL.md](SKILL.md)。

## 目录结构

```
├── SKILL.md              # skill 定义与工作流程
├── scripts/
│   ├── bili_fetch.py     # B站：元数据 + 字幕抓取
│   ├── douyin_fetch.py   # 抖音：元数据 + 无水印视频下载（游客身份）
│   ├── bili_transcribe.py# faster-whisper ASR 兜底（双平台兼容）
│   ├── bili_screenshot.py# 重点截图（锚点/自动关键词 + PyAV 抽帧）
│   └── abogus.py         # 抖音 a_bogus 签名（GPLv3，源自 TikTokDownloader）
└── config.json           # 本地配置（SESSDATA，不入库）
```

## 致谢

- [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) 与 [JefferyHcool/BiliNote](https://github.com/JefferyHcool/BiliNote)——抖音 Web API 签名方案参考
- [JoeanAmier/TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader)——abogus.py 原始作者
