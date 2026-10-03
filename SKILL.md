---
name: bilibili-douyin-notes
description: 把B站/抖音视频整理成Markdown课堂笔记。用户发来 bilibili.com、b23.tv、douyin.com 等视频链接（或分享口令）并要求做笔记、总结、转文字时使用。产出符合模板规范、经质量门禁校验的课堂笔记 .md 文件。
---

# B站/抖音视频 → Markdown 课堂笔记

## 触发条件

用户消息中包含 bilibili.com / b23.tv / BV号 / douyin.com / v.douyin.com / iesdouyin.com 链接（或抖音分享口令文本），且要求总结、做笔记、整理复习资料、提取视频内容等。

## 平台分流（第一步先看链接属于哪个平台）

- **B站** → `bili_fetch.py`：走官方API，优先字幕，按需下载音频/视频流
- **抖音** → `douyin_fetch.py`：游客身份（无需登录/SESSDATA），直接下载无水印视频到本地，一般无字幕 → 必走第二步ASR（抖音视频短，耗时可忽略）。需要 `pip install gmssl`；`abogus.py` 为 GPLv3 代码（源自 Evil0ctal/Douyin_TikTok_Download_API 经 BiliNote 移植），分发需保留其许可头
- 两个平台的 metadata.json 字段一致，第二/三步脚本自动兼容：B站分P按 `bvid/cid` 按需下载，抖音分P带 `media_path` 直接用本地视频文件

## 工具位置

- 脚本目录：本 skill 安装目录下的 `scripts/`（下文命令中的绝对路径请替换为你的实际安装路径）
- 配置文件：本 skill 安装目录下的 `config.json`（存放 SESSDATA）
- 参考资产：
  - `references/note-template.md` —— 课堂笔记模板 v2（骨架、三词标签、语法契约、证据锚点规范）
  - `references/asr-glossary.json` —— ASR 术语纠错词表（真实错法→正确写法）
  - `references/anchors.json` —— 截图锚点关键词表（按分P，可按需补充）

## 工作流程

### 第一步：抓取元数据 + 官方字幕（B站）或下载视频（抖音）

```bash
# B站（也支持 b23.tv 短链）
python "<skill安装目录>/scripts/bili_fetch.py" "<视频链接>" "<工作目录>/bili-notes-<BV号>"

# B站只探元数据：不抓字幕、不建目录、不写任何文件（先确认视频可访问/时长/分P数时用）
python "<skill安装目录>/scripts/bili_fetch.py" --probe "<视频链接>"

# 抖音（也支持 v.douyin.com 短链和整段分享口令文本）
python "<skill安装目录>/scripts/douyin_fetch.py" "<链接或口令>" "<工作目录>/douyin-notes-<视频ID>"
```

- 两个脚本都自动生成 `<输出目录>/metadata.json`，stdout 最后一行是 JSON 摘要。
- **B站**：每个分P的字幕 `.txt`（带 `[mm:ss]` 时间戳），按摘要里每个分P的 **`subtitle_detail` 字段四态分流**：
  - `cc` / `ai` → 已拿到官方/AI字幕，直接进入第三步生成笔记；
  - `none` → 该分P确实没有字幕，直接进入第二步 ASR，不用再折腾；**但若摘要带 `hint_optional`（未配置登录态时可能出现），转述给用户**：「若该视频本应有 AI 字幕，可扫码登录（`--login`）后重跑，通常能跳过语音识别」——由用户判断要不要扫，不强制；
  - `api_empty` → 接口没返回可用字幕URL（`sessdata_loaded:false` 时常见，AI字幕需要登录）：**主动询问用户「要现在扫码登录B站吗？」，确认后代跑 `python "<skill安装目录>/scripts/bili_fetch.py" --login`（终端出二维码，B站App扫码即登录，SESSDATA 自动写入 config.json）后重跑一次 fetch**；用户拒绝或重跑后仍是 `api_empty` 才走第二步 ASR 兜底；
  - `error` → 查看摘要里的 error 信息（可能是视频失效/风控），重试一次或直接告知用户。
- **抖音**：视频已下载到 `<输出目录>/media_p01.mp4`（无水印，本地已存在则跳过下载），无字幕，直接进入第二步 ASR。

### 第二步（兜底）：语音识别转写

仅对缺字幕的分P转写（脚本自动跳过已有文本的分P）：

```bash
# 只转写第N个分P（推荐先转1个试听质量/估算耗时）
python "<skill安装目录>/scripts/bili_transcribe.py" "<工作目录>/bili-notes-<BV号>/metadata.json" --page 1

# 转写所有缺字幕的分P
python "<skill安装目录>/scripts/bili_transcribe.py" "<工作目录>/bili-notes-<BV号>/metadata.json"
```

注意事项：
- **设备与降级链**：自动检测 CUDA（有 NVIDIA GPU 用 float16，否则 CPU int8 + 批量推理）；cuda 加载失败自动回退 cpu，批量推理不可用自动回退逐段，高阶模型（medium/large）加载失败自动降一档直到 small——**每次落级都打印原因（stderr 警告 + JSON `fallback_reasons`），不静默**。检测异常时可用 `--device cpu` / `--device cuda` 强制指定；`--model` 未指定时读 config.json 的 `asr_model`（再默认 small），`asr_cpu_threads` 可调 CPU 线程数。
- **术语偏好注入**：脚本自动读词表（仓库层 + 用户层），把高频术语的正确词注入 ASR 的 initial_prompt——转写时就写对，比事后纠错更准。
- **耗时**：CPU int8 下 small 模型约 1~3 倍速实时（批量推理开启后更快），长视频（>1小时）耗时明显，转写前告知用户预计时间；长视频或对质量不满意时用 `--model medium` 重转（**先删对应的 txt**，否则脚本会跳过）。
- 首次运行会下载模型（small≈480MB，自动走 hf-mirror.com 镜像）。
- ASR 文稿没有标点分段质量保证，技术术语常被转错——生成笔记前用 `references/asr-glossary.json` 词表对照校正（见第三步第2条）。

### 第二步半（可选）：抽取重点截图

用户需要"带截图的笔记"时，用脚本按转写稿时间戳自动抽取重点画面：

```bash
# 全部分P（B站查 references/anchors.json 锚点表，抖音/无锚点页自动提取关键词）
python "<skill安装目录>/scripts/bili_screenshot.py" "<工作目录>/metadata.json" "<工作目录>" "<工作目录>/images"

# 只抽指定分P（逗号分隔页码）
python "<skill安装目录>/scripts/bili_screenshot.py" <meta> <txt目录> <images目录> 2,6,11
```

- B站：在转写稿里搜每分P"锚点关键词"（如 @Component、循环依赖、log4j），取首次出现时间-3s（ASR滞后），下载 480p DASH 视频抽帧后即删；锚点表外置在 `references/anchors.json`，该文件没有的页码（或抖音平台）自动用 `auto_anchors()` 从转写稿提取中英文关键词；
- 抖音：直接复用第一步下载的本地视频抽帧，不再联网；
- 未登录最高 480p（852×480），够看清 PPT/代码；填了 SESSDATA 会自动取更高画质；
- **画质 QC**：脚本内置本地画质初筛，亮度用**像素占比法**（近黑像素占比>97% 判 dark，近白像素占比>98.5% 判 bright——白底 PPT 少量文字不会误判为空屏），清晰度用灰度拉普拉斯方差（<10 判 blurry）；非 ok 帧自动 +30s 重抽一次并标记（进度 JSON 里每帧 `qc` 字段：`ok`=合格，`dark`/`bright`/`blurry`=重抽后仍未通过的原因；取不到帧的锚点进 `failed` 列表，status 变 `partial`）。Agent 生成笔记时对非 `ok` 帧复核（视觉识别）决定是否采用；
- 锚点未命中时先 grep 转写稿找 ASR 实际用词（对照 `references/asr-glossary.json`，如"权限"被转成"全线"、@Component→"art component"、Starter→"Stutter"）再补正则；
- 生成笔记时以 `![说明](images/pXX_yyy.png)` 相对路径嵌入对应小节；锚点文件名**默认可中文**，需要 ASCII 文件名时给脚本加 `--ascii-names`（中文 label 自动转为 `zh-<md5前6位>`）。

### 第三步：生成课堂笔记

1. 读取 metadata.json（分P结构、时长）+ 各分P转写/字幕文本。
2. **ASR 术语校正（工具化，不是凭感觉）**：先跑
   `python "<skill安装目录>/scripts/apply_glossary.py" "<转写稿.txt>"`（默认 dry-run，按类别分组只出报告不改文件），按报告核对命中项，确认后再加 `--apply` 落盘（自动留 `.bak` 备份，命中词条 hits 自动累加）。词表分两层：仓库层 `references/asr-glossary.json`（编程/美食/游戏/通用大类）+ 用户层（输出目录 `glossary.user.json` 或 `~/.zcode/bdn-glossary.user.json`，同键用户层优先）；候选词命中只报告（标 `[候选]`）永不替换。**维护闭环**：发现词表未覆盖的新错法 → `--add "错法=正确词" --category 编程`（或 `--candidate` 先入候选、`--promote "错法"` 转正）；`--list` 查看全部；`--categories`/`--topic` 控制启用范围。注意：转写阶段已自动把高频术语注入 initial_prompt（见第二步），词表越丰富转写越准。
3. **长视频分块（写死流程，禁止跳过）**：单分P转写稿超过约 2 万字时——用 `scripts/transcript_chunk.py` 分块（默认 15000 字/块、末尾重叠 2 段，输出 `chunks/` 目录）→ **map**：逐块提炼该块要点清单 → **reduce**：把各块要点按小节层级合并汇总成最终笔记。**reduce 去重**：重叠区会在相邻块产生重复内容，合并时按 `[mm:ss]` 时间戳唯一化——同一时刻的要点/锚点只保留一次，跨块重复的锚点去重后再排进小节。禁止跳过分块直接全文阅读长转写稿。
4. 按 `references/note-template.md`（**v2 模板**）生成一份 MD 文件，保存到用户工作目录，命名 `课堂笔记-<序号>-<主题>.md`（序号看用户已有的课堂笔记文件递增；主题取视频标题关键词）。生成前必读该模板文件。v2 硬性要求：
   - **frontmatter**（`---` 包围）写全：`template_version: v2`、`note_type`（取 metadata.json 顶层 `suggested_note_type`，技术密度高的短片可覆盖为 lecture）、`platform`、`bvid`（抖音填 `video_id`）、`source`、`created`、`duration`（带引号的 `"mm:ss"`）；
   - **三词标签标注行**：小节要点用 `- **核心必考|重点掌握|了解即可** 要点句 [mm:ss]` 格式，标签词严格用白名单三词（v2 不用 ★ 符号，禁止"必考/核心考点"等变体）；每讲至少 1 条核心必考，其数量占标注行总数 ≤1/3（lecture）；
   - **概念卡片**：`### 概念名（标签词）` + 定义/出现（≥2 小节）/依赖/易混 四行；
   - **自测题折叠答案**：每题末尾 `（对应：概念名）`，紧跟 `<details><summary>答案</summary>` 折叠块，答案以 `**答案：**` 开头、至多一行、带证据锚点；lecture 5~9 道（short 2~3 道）；
   - **mermaid 知识地图**：` ```mermaid mindmap `，节点标签禁半角括号与引号、≤12 字（括号用全角）；
   - **参考时间戳不要手写**：文末留 `<!-- NAV:BEGIN 由 scripts/note_nav.py 生成，请勿手工编辑 -->` 与 `<!-- NAV:END -->` 占位（内容交给下一步脚本填充）；`note_type=short` 时整个区块可省略；
   - 所有时间戳必须取自转写稿真实存在的时间行（允许 ±5s 内小幅前移，禁止虚构）。
5. 笔记中引用的代码/命令必须以视频文稿内容为准；文稿未讲到的细节不要编造，可标注"（视频中未展开，建议补充）"。若视频简介里有代码仓库，克隆下来读取源码，把笔记中的代码示例换成仓库中的真实代码（首选做法）。
6. **填充参考时间戳（lecture 型必做；short 跳过）**：
   `python "<skill安装目录>/scripts/note_nav.py" "<笔记.md>" --meta "<metadata.json>" --txt-dir "<转写稿目录>"`
   脚本幂等替换 NAV 标记之间的内容（B站生成 `?p=N&t=S` 深链，抖音纯文本）；标记缺失时追加到文件末尾。模型输出不含 URL，脚本输出不含自由文本，职责不重叠。
7. **质量门禁**：生成后运行
   `python "<skill安装目录>/scripts/validate_note.py" "<笔记.md>" --meta "<metadata.json>" --txt-dir "<转写稿目录>"`
   errors 必须为 0 才交付；warnings 酌情处理（如补证据锚点、精简篇幅）。门禁按 frontmatter 的 `template_version` 分派规则（缺失视为 v1 走旧 ★ 规则）。**破坏性变更（v2 起）**：报告 JSON 的 `errors` / `warnings` 从字符串数组升级为**对象数组**（每项含 `check` 检查编号与 `line` 行号），调用方若按字符串解析需同步适配。
8. **媒体清理（交付后必问用户）**：若输出目录存在缓存媒体（抖音的 `media_p01.mp4` 等本地视频，或残留的 `.part`/`.audio_*`/`.video_*` 临时文件），**询问用户「是否清理缓存的视频/音频文件？重新做笔记会重新下载」**：确认 → 先跑
   `python "<skill安装目录>/scripts/clean_media.py" "<输出目录>"`
   把将删除的清单给用户过目，再执行 `--apply` 实删；拒绝 → 跳过并说明缓存保留的意义（重跑截图/转写不再联网）。B站的临时音频/视频在流程中已自动删除，通常无残留。

## 笔记模板

模板全文（完整骨架 + 三词标签定义 + 语法契约 + 证据锚点规范 + short 差异表 + 要点清单）见 **`references/note-template.md`**（**v2**），生成笔记前必读。骨架速览：

```markdown
---
template_version: v2
note_type: lecture
platform: bilibili            # 抖音为 douyin，bvid 字段填 video_id
bvid: BV1xxxxxxxxx
source: https://www.bilibili.com/video/BV1xxxxxxxxx
created: 2026-10-03
duration: "42:10"
---

# 课堂笔记 <序号>：<主题>

> 课程来源：<视频标题>（B站 <BV号> 或 抖音 <视频ID>，UP主/作者：<名字>，共N个小节约M分钟）
> 课程代码：<简介中的仓库地址，如有>

## 目录

- <按分P或知识点列出>

---

## <分P/小节名>（时长）

**核心思想：**<一句话>

- **核心必考** 要点句 [mm:ss]
- **重点掌握** 要点句 [mm:ss]
- **了解即可** 要点句

<真实代码示例（注明来源：分P/时刻 或 仓库文件）……>
<概念对比表格……>

---

## 本讲知识地图

```mermaid
mindmap
  root((<主题>))
    小节1
      概念A
```

## 概念卡片

### <概念名>（核心必考）

- 定义：<一句话>
- 出现：<小节序号>（[mm:ss]）、<小节序号>（[mm:ss]）
- 依赖：<概念名>
- 易混：<概念名>——<一句话区别>

## 自测题（复习用）

1. <题干>？（对应：<概念名>）
<details><summary>答案</summary>

**答案：** <答案正文，至多一行> [mm:ss]

</details>

<!-- NAV:BEGIN 由 scripts/note_nav.py 生成，请勿手工编辑 -->
## 参考时间戳

- [mm:ss](<深链由脚本生成>) <关键节点>
<!-- NAV:END -->
```

模板要点（三词语义、语法契约、证据锚点规范、short 型差异的完整定义以 `references/note-template.md` 为准）：

- 按视频小节顺序组织，每节标注时长，以 `**核心思想：**` 一句话开头；
- 要点用 `- **核心必考|重点掌握|了解即可** 要点句 [mm:ss]` 三词标签标注
  （核心必考=核心概念/必考点，重点掌握=重要机制/易混淆点，了解即可=补充了解）；
- 概念卡片、自测题（折叠答案）、mermaid 知识地图按第五节语法契约写；
- 参考时间戳留 NAV 标记占位，由 `note_nav.py` 填充（short 型省略）；
- 代码块用课程真实代码，注明来源（子项目 / 时刻 / 仓库文件）。

## 常见问题

| 现象 | 处理 |
|---|---|
| 分P `subtitle_detail` 为 `api_empty` | 先提示用户填写 config.json 的 SESSDATA（浏览器F12 → Application → Cookies → bilibili.com → SESSDATA）后重跑一次 fetch；仍 `api_empty` 才直接走ASR |
| playurl 下载失败/音频为空 | 可能是风控，重试一次；仍失败则改用 `yt-dlp` 下载音频后手动喂给 transcribe 的 transcribe 函数思路 |
| faster-whisper 首次运行卡在下载 | 检查 HF_ENDPOINT 是否为 https://hf-mirror.com，或手动 `pip install -U huggingface_hub` |
| 转写文本术语错乱 | 用 `references/asr-glossary.json` 对照校正；词表未覆盖的新错法按同样格式补进词表 `map`，下次自动生效 |
| `subtitle_detail` 为 `error` | 看摘要里的 error 信息：视频可能已失效/被风控，重试一次或告知用户换视频 |
| 转写质量差（长视频尤甚） | 先删对应 txt，再用 `--model medium` 重转；有 NVIDIA GPU 时脚本会自动用 CUDA float16，也可 `--device cuda` 强制 |
| 抖音 detail 接口 403 / "无 aweme_detail" | 签名算法可能被抖音升级，参考 Evil0ctal/Douyin_TikTok_Download_API 或 JefferyHcool/BiliNote 更新 `abogus.py` / `WEB_SIGN_SALT`；另确认视频可公开访问（非私密/仅粉丝可见） |
| 抖音视频下载中断/文件过小 | 下载地址有时效，重跑 douyin_fetch.py 重新解析即可（本地已有完整 mp4 会自动跳过下载） |
| 环境缺依赖 | B站流程需 faster-whisper + av；抖音流程另需 `pip install gmssl` |
