---
name: bilibili-douyin-notes
description: 把B站/抖音视频整理成Markdown课堂笔记。当用户发来 bilibili.com、b23.tv、douyin.com、v.douyin.com、iesdouyin.com 的视频链接（或分享口令文本）并要求"做笔记/总结/课堂笔记/转成文字/复习资料"时使用。流程：按平台抓取元数据（B站优先取官方/AI字幕需SESSDATA；抖音直接下载无水印视频无需登录）→ 无字幕则用 faster-whisper 本地语音识别兜底 → 按课堂笔记模板生成 MD 文件。也适用于"把这个视频内容提取出来"类请求。
---

# B站/抖音视频 → Markdown 课堂笔记

## 触发条件

用户消息中包含 bilibili.com / b23.tv / BV号 / douyin.com / v.douyin.com / iesdouyin.com 链接（或抖音分享口令文本），且要求总结、做笔记、整理复习资料、提取视频内容等。

## 平台分流（第一步先看链接属于哪个平台）

- **B站** → `bili_fetch.py`：走官方API，优先字幕，按需下载音频/视频流
- **抖音** → `douyin_fetch.py`：游客身份（无需登录/SESSDATA），直接下载无水印视频到本地，一般无字幕 → 必走第二步ASR（抖音视频短，耗时可忽略）。需要 `pip install gmssl`；`abogus.py` 为 GPLv3 代码（源自 Evil0ctal/Douyin_TikTok_Download_API 经 BiliNote 移植），分发需保留其许可头
- 两个平台的 metadata.json 字段一致，第二/三步脚本自动兼容：B站分P按 `bvid/cid` 按需下载，抖音分P带 `media_path` 直接用本地视频文件

## 工具位置

- 脚本目录：`~/.zcode/skills/bilibili-douyin-notes/scripts/`
  （Windows 实际路径 `C:\Users\罗任\.zcode\skills\bilibili-douyin-notes\scripts\`）
- 配置文件：`~/.zcode/skills/bilibili-douyin-notes/config.json`（存放 SESSDATA）

## 工作流程

### 第一步：抓取元数据 + 官方字幕（B站）或下载视频（抖音）

```bash
# B站（也支持 b23.tv 短链）
python "C:/Users/罗任/.zcode/skills/bilibili-douyin-notes/scripts/bili_fetch.py" "<视频链接>" "<工作目录>/bili-notes-<BV号>"

# 抖音（也支持 v.douyin.com 短链和整段分享口令文本）
python "C:/Users/罗任/.zcode/skills/bilibili-douyin-notes/scripts/douyin_fetch.py" "<链接或口令>" "<工作目录>/douyin-notes-<视频ID>"
```

- 两个脚本都自动生成 `<输出目录>/metadata.json`，stdout 最后一行是 JSON 摘要。
- **B站**：每个分P的字幕 `.txt`（带 `[mm:ss]` 时间戳），检查摘要里每个分P的 `subtitle` 字段：
  - 全部为 `true` → 直接进入第三步；
  - 有 `false`（`sessdata_loaded:false` 时常见，AI字幕需要登录）→ 先提示用户可在 `config.json` 里填 SESSDATA 获得 AI 字幕，然后进入第二步 ASR 兜底。
- **抖音**：视频已下载到 `<输出目录>/media_p01.mp4`（无水印，本地已存在则跳过下载），`subtitle` 恒为 `false`，直接进入第二步 ASR。

### 第二步（兜底）：语音识别转写

仅对缺字幕的分P转写（脚本自动跳过已有文本的分P）：

```bash
# 只转写第N个分P（推荐先转1个试听质量/估算耗时）
python "C:/Users/罗任/.zcode/skills/bilibili-douyin-notes/scripts/bili_transcribe.py" "<工作目录>/bili-notes-<BV号>/metadata.json" --page 1

# 转写所有缺字幕的分P
python "C:/Users/罗任/.zcode/skills/bilibili-douyin-notes/scripts/bili_transcribe.py" "<工作目录>/bili-notes-<BV号>/metadata.json"
```

注意事项：
- **耗时**：CPU int8 下 small 模型约 1~3 倍速实时，长视频（>1小时）耗时明显，转写前告知用户预计时间；质量不满意可 `--model medium` 重跑（先删对应 txt）。
- 首次运行会下载模型（small≈480MB，自动走 hf-mirror.com 镜像）。
- ASR 文稿没有标点分段质量保证，生成笔记时要靠上下文重新组织和纠错（技术术语常被转错，如 IoC→"控制反转"，需按课程主题人工校正）。

### 第二步半（可选）：抽取重点截图

用户需要"带截图的笔记"时，用脚本按转写稿时间戳自动抽取重点画面：

```bash
# 全部分P（B站查内置锚点表，抖音/无锚点页自动提取关键词）
python "C:/Users/罗任/.zcode/skills/bilibili-douyin-notes/scripts/bili_screenshot.py" "<工作目录>/metadata.json" "<工作目录>" "<工作目录>/images"

# 只抽指定分P（逗号分隔页码）
python ".../bili_screenshot.py" <meta> <txt目录> <images目录> 2,6,11
```

- B站：在转写稿里搜每分P"锚点关键词"（如 @Component、循环依赖、log4j），取首次出现时间-3s（ASR滞后），下载 480p DASH 视频抽帧后即删；
- 抖音：直接复用第一步下载的本地视频抽帧，不再联网；锚点用 `auto_anchors()` 从转写稿自动提取英文/代码样关键词（@注解、驼峰词、专有名词）；
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

> 课程来源：<视频标题>（B站 <BV号> 或 抖音 <视频ID>，UP主/作者：<名字>，共N个小节约M分钟）
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
| 抖音 detail 接口 403 / "无 aweme_detail" | 签名算法可能被抖音升级，参考 Evil0ctal/Douyin_TikTok_Download_API 或 JefferyHcool/BiliNote 更新 `abogus.py` / `WEB_SIGN_SALT`；另确认视频可公开访问（非私密/仅粉丝可见） |
| 抖音视频下载中断/文件过小 | 下载地址有时效，重跑 douyin_fetch.py 重新解析即可（本地已有完整 mp4 会自动跳过下载） |
| 环境缺依赖 | B站流程需 faster-whisper + av；抖音流程另需 `pip install gmssl` |
