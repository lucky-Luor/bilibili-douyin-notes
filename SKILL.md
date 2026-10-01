---
name: bilibili-notes
description: 把B站视频整理成Markdown课堂笔记。当用户发来 bilibili.com 或 b23.tv 的视频链接并要求"做笔记/总结/课堂笔记/转成文字/复习资料"时使用。流程：抓取视频分P元数据 → 优先取官方/AI字幕（需SESSDATA）→ 无字幕则用 faster-whisper 本地语音识别兜底 → 按课堂笔记模板生成 MD 文件。也适用于"把这个B站视频内容提取出来"类请求。
---

# B站视频 → Markdown 课堂笔记

## 触发条件

用户消息中包含 bilibili.com / b23.tv / BV号 链接，且要求总结、做笔记、整理复习资料、提取视频内容等。

## 工具位置

- 脚本目录：`~/.zcode/skills/bilibili-notes/scripts/`
  （Windows 实际路径 `C:\Users\罗任\.zcode\skills\bilibili-notes\scripts\`）
- 配置文件：`~/.zcode/skills/bilibili-notes/config.json`（存放 SESSDATA）

## 工作流程

### 第一步：抓取元数据 + 官方字幕

```bash
python "C:/Users/罗任/.zcode/skills/bilibili-notes/scripts/bili_fetch.py" "<视频链接>" "<工作目录>/bili-notes-<BV号>"
```

- 脚本自动生成 `<BV>/metadata.json` 和每个分P的字幕 `.txt`（带 `[mm:ss]` 时间戳）。
- stdout 最后一行是 JSON 摘要，检查每个分P的 `subtitle` 字段：
  - 全部为 `true` → 直接进入第三步；
  - 有 `false`（`sessdata_loaded:false` 时常见，AI字幕需要登录）→ 先提示用户可在 `config.json` 里填 SESSDATA 获得 AI 字幕，然后进入第二步 ASR 兜底。
- 链接若是 b23.tv 短链脚本也能解析。

### 第二步（兜底）：语音识别转写

仅对缺字幕的分P转写（脚本自动跳过已有文本的分P）：

```bash
# 只转写第N个分P（推荐先转1个试听质量/估算耗时）
python "C:/Users/罗任/.zcode/skills/bilibili-notes/scripts/bili_transcribe.py" "<工作目录>/bili-notes-<BV号>/metadata.json" --page 1

# 转写所有缺字幕的分P
python "C:/Users/罗任/.zcode/skills/bilibili-notes/scripts/bili_transcribe.py" "<工作目录>/bili-notes-<BV号>/metadata.json"
```

注意事项：
- **耗时**：CPU int8 下 small 模型约 1~3 倍速实时，长视频（>1小时）耗时明显，转写前告知用户预计时间；质量不满意可 `--model medium` 重跑（先删对应 txt）。
- 首次运行会下载模型（small≈480MB，自动走 hf-mirror.com 镜像）。
- ASR 文稿没有标点分段质量保证，生成笔记时要靠上下文重新组织和纠错（技术术语常被转错，如 IoC→"控制反转"，需按课程主题人工校正）。

### 第二步半（可选）：抽取重点截图

用户需要"带截图的笔记"时，用脚本按转写稿时间戳自动抽取重点画面：

```bash
# 全部分P（脚本内置每节的锚点关键词表）
python "C:/Users/罗任/.zcode/skills/bilibili-notes/scripts/bili_screenshot.py" "<工作目录>/metadata.json" "<工作目录>" "<工作目录>/images"

# 只抽指定分P（逗号分隔页码）
python ".../bili_screenshot.py" <meta> <txt目录> <images目录> 2,6,11
```

- 原理：在转写稿里搜每节"锚点关键词"（如 @Component、循环依赖、log4j），取首次出现时间-3s（ASR滞后），下载该分P 480p DASH 视频，PyAV 抽帧存 PNG；
- 未登录最高 480p（852×480），够看清 PPT/代码；填了 SESSDATA 会自动取更高画质；
- **截图必须校验**：抽到的帧可能是黑屏/转场/老师头像。用 MiMo-V2.5 批量识别（curl 调 opencode 代理端点），BAD 的帧把时间戳 +30s 左右重抽（可用 mcp clipboard-vision 或在脚本 ANCHORS 表里调整）；锚点未命中时先 grep 转写稿找 ASR 实际用词（如"权限"被转成"全线"、@Component→"art component"、Starter→"Stutter"）再补正则；
- 生成笔记时以 `![说明](images/pXX_yyy.png)` 相对路径嵌入对应小节，文件名用 ASCII。

### 第三步：生成课堂笔记

1. 读取 metadata.json（分P结构、时长）+ 各分P转写/字幕文本。
2. 按下方模板生成一份 MD 文件，保存到用户工作目录，命名 `课堂笔记-<序号>-<主题>.md`
   （序号看用户已有的课堂笔记文件递增；主题取视频标题关键词）。
3. 笔记中引用的代码/命令必须以视频文稿内容为准；文稿未讲到的细节不要编造，
   可标注"（视频中未展开，建议补充）"。若视频简介里有代码仓库，克隆下来读取源码，
   把笔记中的代码示例换成仓库中的真实代码（首选做法，参考本次 JavaEE 课程的笔记效果）。
4. 长视频（>1小时）内容量大，可将转写文本分批阅读后再汇总。

## 笔记模板

```markdown
# 课堂笔记 <序号>：<主题>

> 课程来源：<视频标题>（B站 <BV号>，UP主：<UP主名>，共N个小节约M分钟）
> 课程代码：<简介中的仓库地址，如有>

## 目录

- <按分P或知识点列出>

---

## <分P/小节名>（时长）

**核心思想：**<一句话>

<要点、代码示例、表格对比……>

---

## 本讲知识地图

<用一个 ASCII 图/缩进列表把本讲知识点串起来>

## 自测题（复习用）

1. <5~9道覆盖核心概念的问答题，附提示>
```

模板要点（以 JavaEE 第1讲笔记为范本，位于 `C:\Users\罗任\Desktop\JavaEE平台技术\课堂笔记-01-Spring与SpringBoot.md`）：

- 按视频小节顺序组织，每节标注时长；
- 重点内容用 ★ 标注数量区分优先级；
- 代码块用课程真实代码，注明来自哪个子项目；
- 概念对比用表格；
- 结尾必有"知识地图"+"自测题"。

## 常见问题

| 现象 | 处理 |
|---|---|
| 所有分P都无字幕且 `sessdata_loaded:false` | 提示用户填写 config.json 的 SESSDATA（浏览器F12 → Application → Cookies → bilibili.com → SESSDATA），或直接走ASR |
| playurl 下载失败/音频为空 | 可能是风控，重试一次；仍失败则改用 `yt-dlp` 下载音频后手动喂给 transcribe 的 transcribe 函数思路 |
| faster-whisper 首次运行卡在下载 | 检查 HF_ENDPOINT 是否为 https://hf-mirror.com，或手动 `pip install -U huggingface_hub` |
| 转写文本术语错乱 | 生成笔记时结合课程主题校正，代码类词汇对照仓库源码 |
