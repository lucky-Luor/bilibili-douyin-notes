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

- 脚本目录：本 skill 安装目录下的 `scripts/`（命令中的 `<skill安装目录>` 占位符请替换为本 skill 实际安装路径）
- 配置文件：本 skill 安装目录下的 `config.json`（存放 SESSDATA）
- 参考资产：
  - `references/note-template.md` —— 课堂笔记模板 v3（骨架、要点表达、语法契约、时间戳规范）
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
- **B站**：每个分P的字幕 `.txt`（带 `[mm:ss]` 时间戳），按摘要里每个分P的 **`subtitle_detail` 字段六值分流（cc / ai / ai_mismatch / none / api_empty / error，其中 cc 与 ai 处置相同）**：
  - `cc` / `ai` → 已拿到官方/AI字幕，直接进入第三步生成笔记（AI 字幕已由脚本自动做"内容↔标题 ASCII 词"交叉校验，错位时会判为 `ai_mismatch` 而不是 `ai`）；
  - `ai_mismatch` → AI 字幕整体错位（返回了 ai 状态但内容是别的音频，实测有配成无关电影对白的案例）：**不可信，直接进入第二步 ASR 兜底**，不要用该字幕生成笔记；存疑字幕已另存 `*.ai字幕存疑.txt` 备查，摘要带 `hint_mismatch` 时可向用户提一句；
  - `none` → 该分P确实没有字幕，直接进入第二步 ASR，不用再折腾；**但若摘要带 `hint_optional`（未配置登录态时可能出现），转述给用户**：「若该视频本应有 AI 字幕，可扫码登录（`--login`）后重跑，通常能跳过语音识别」——由用户判断要不要扫，不强制；
  - `api_empty` → 接口没返回可用字幕URL（`sessdata_loaded:false` 时常见，AI字幕需要登录）：**主动询问用户「要现在扫码登录B站吗？」，确认后代跑 `python "<skill安装目录>/scripts/bili_fetch.py" --login`（脚本生成二维码 PNG 图片并打印路径，把路径给用户打开扫码即登录，SESSDATA 自动写入 config.json）后重跑一次 fetch**；用户拒绝或重跑后仍是 `api_empty` 才走第二步 ASR 兜底；
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
- **耗时**：CPU int8 下 small 模型约为视频时长的 1~3 倍（批量推理可缓解；有 NVIDIA GPU 时快于实时），长视频（>1小时）耗时明显，转写前告知用户预计时间；长视频或对质量不满意时用 `--model medium` 重转（**先删对应的 txt**，否则脚本会跳过）。
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

> **使用场景定位（2026-10-04，v3）**：本 skill 解决的是“看完课不用自己做笔记”——
> 产出的是**课后复习资料**，正文按技术知识体系组织、阅读连贯：**正文零时间戳、
> 零考试向标签**（三词标签体系废除，要点用普通列表 + 关键术语加粗）；视频时间戳
> 只存在于文末折叠的“视频时间索引”，供回看原片时跳转。

1. 读取 metadata.json（分P结构、时长）+ 各分P转写/字幕文本。
2. **ASR 术语校正（工具化，不是凭感觉）**：先跑
   `python "<skill安装目录>/scripts/apply_glossary.py" "<转写稿.txt>"`（默认 dry-run，按类别分组只出报告不改文件），按报告核对命中项，确认后再加 `--apply` 落盘（自动留 `.bak` 备份，命中词条 hits 自动累加）。词表分两层：仓库层 `references/asr-glossary.json`（编程/美食/游戏/通用大类）+ 用户层（输出目录 `glossary.user.json` 或 `~/.zcode/bdn-glossary.user.json`，同键用户层优先）；候选词命中只报告（标 `[候选]`）永不替换。**维护闭环**：发现词表未覆盖的新错法 → `--add "错法=正确词" --category 编程`（或 `--candidate` 先入候选、`--promote "错法"` 转正）；`--list` 查看全部；`--categories`/`--topic` 控制启用范围。注意：转写阶段已自动把高频术语注入 initial_prompt（见第二步），词表越丰富转写越准。
3. **长视频分块（写死流程，禁止跳过）**：单分P转写稿超过约 2 万字时——用 `scripts/transcript_chunk.py` 分块（默认 15000 字/块、末尾重叠 2 段，输出 `chunks/` 目录）→ **map**：逐块提炼该块要点清单 → **reduce**：把各块要点按小节层级合并汇总成最终笔记。**reduce 去重**：重叠区会在相邻块产生重复内容，合并时按 `[mm:ss]` 时间戳唯一化——同一时刻的要点/锚点只保留一次，跨块重复的锚点去重后再排进小节（该去重仅作用于分块合并的中间产物；正文按 v3 规则仍然禁时刻）。禁止跳过分块直接全文阅读长转写稿。
4. 按 `references/note-template.md`（**v3 模板**）生成一份 MD 文件，保存到用户工作目录，命名 `课堂笔记-<序号>-<主题>.md`（序号看用户已有的课堂笔记文件递增；主题取视频标题关键词）。生成前必读该模板文件。v3 硬性要求：
   - **frontmatter**（`---` 包围）写全（字段与 v2 一致）：`template_version: v3`、`note_type`（取 metadata.json 顶层 `suggested_note_type`，技术密度高的短片可覆盖为 lecture）、`platform`、`bvid`（抖音填 `video_id`）、`source`、`created`、`duration`（带引号的 `"mm:ss"`）；
   - **标题区**：`# 课堂笔记 <序号>：<主题>`，引语块先 `> 核心主线：`（一句话串起全片知识主线）再 `> 课程来源：`，有代码仓库再加 `> 课程代码：`；目录条目 `- [N. 小节名](#锚点)` 不带时刻，末尾固定列出 本讲知识地图 / 概念卡片 / 自测题（复习用）/ 视频时间索引；
   - **小节**：`## N. 小节名`（禁时刻后缀），以 `**核心思想：** <一句话>` 开头，每小节 ≥3 行实质内容；要点用普通列表 `- 要点句`、关键术语 `**加粗**`，**禁止三词标签前缀（核心必考/重点掌握/了解即可及任何变体）、禁止行内 `[mm:ss]`**；对比性内容优先用表格；
   - **代码块**：真实语言 fence；来源标注可选——来自克隆仓库写 `# 来源：仓库 <子项目>/<文件路径>`（注释符随语言），按视频讲解组织的可写 `# 按视频讲解还原` 或不写；任何代码块内禁时刻；
   - **概念卡片**：`### 概念名`（禁标签后缀）+ 定义/出现/依赖/易混 四行；`出现` 写 ≥2 个小节序号（如 `出现：2、5`，不足 2 个小节为 warning），禁时刻；
   - **自测题折叠答案**：每题末尾 `（对应：概念名）`（概念必须在概念卡片集合内：lecture 必须、short 可选），紧跟 `<details><summary>答案</summary>` 折叠块，答案以 `**答案：**` 开头、至多一行、禁时刻；lecture 4~7 道（short 2~3 道）；选题主打高频原理与面试易错点，禁 yes/no 题干；
   - **mermaid 知识地图**：` ```mermaid mindmap `（或 graph/flowchart），节点标签禁半角 ( ) [ ] { } 与引号、≤12 字（需要括号用全角）；标签卫生由门禁 warning 级校验；
   - **视频时间索引**：文末最后一节，`<details>` 折叠包裹 NAV 标记对；模型只写条目 `- [mm:ss] <与正文一级小节同名>`（一级章节一条，不做逐要点条目），**不写 URL**；时刻必须取自转写稿真实时间行（允许 ±5s 内小幅前移，禁止虚构）；lecture 必须有本节，short 可整体省略（省略时 NAV 标记也不留）。
5. 笔记中引用的代码/命令必须以视频文稿内容为准；文稿未讲到的细节不要编造，可标注"（视频中未展开，建议补充）"。若视频简介里有代码仓库，克隆下来读取源码，把笔记中的代码示例换成仓库中的真实代码（首选做法）。
6. **填充时间索引深链（v3 笔记默认执行）**：
   `python "<skill安装目录>/scripts/note_nav.py" "<笔记.md>" --meta "<metadata.json>" --txt-dir "<转写稿目录>"`
   脚本幂等替换 NAV 标记之间的内容，把 `- [mm:ss] 标题` 条目填充为 `- [mm:ss](URL) 标题`（B站生成 `?p=N&t=S` 深链，抖音无深链保持纯文本）；`note_type=short` 跳过本步；笔记里 NAV 标记缺失时 warning 跳过。v2 老笔记仅在用户明确要时间导航时才执行本步。模型输出不含 URL，脚本输出不含自由文本，职责不重叠。
7. **质量门禁**：生成后运行
   `python "<skill安装目录>/scripts/validate_note.py" "<笔记.md>" --meta "<metadata.json>" --txt-dir "<转写稿目录>"`
   errors 必须为 0 才交付；warnings 酌情处理（如精简篇幅）。门禁按 frontmatter 的 `template_version` 分派 v3 / v2 / v1 规则（缺失视为 v1 走旧 ★ 规则；v3 下正文出现时刻、标签残留、时间索引缺失（lecture）、自测数量越界、答案或概念卡含时刻等均为 error）。**破坏性变更（v2 起）**：报告 JSON 的 `errors` / `warnings` 从字符串数组升级为**对象数组**（每项含 `check` 检查编号与 `line` 行号），调用方若按字符串解析需同步适配。
8. **媒体清理（交付后必问用户）**：若输出目录存在缓存媒体（抖音的 `media_p01.mp4` 等本地视频，或残留的 `.part`/`.audio_*`/`.video_*` 临时文件），**询问用户「是否清理缓存的视频/音频文件？重新做笔记会重新下载」**：确认 → 先跑
   `python "<skill安装目录>/scripts/clean_media.py" "<输出目录>"`
   把将删除的清单给用户过目，再执行 `--apply` 实删；拒绝 → 跳过并说明缓存保留的意义（重跑截图/转写不再联网）。B站的临时音频/视频在流程中已自动删除，通常无残留。

## 笔记模板

**完整骨架与语法契约以 `references/note-template.md` 为准（生成前必读）**——全文含
lecture 完整骨架、时间戳规范、语法契约、short 差异表、截图嵌入规范、生成流程。
结构层次速览（节点内容以 `<…>` 占位）：

```markdown
---                  # frontmatter：template_version: v3 / note_type / platform / bvid / source / created / duration
# 课堂笔记 <序号>：<主题>
> 核心主线：<一句话知识主线> ／ > 课程来源：<视频标题等>（有仓库再加 > 课程代码：<仓库地址>）
## 目录              # - [N. 小节名](#锚点) 不带时刻；末尾固定列出 本讲知识地图 / 概念卡片 / 自测题（复习用）/ 视频时间索引
## N. 小节名 ×N      # **核心思想：** <一句话> + 要点列表（术语加粗）+ 代码块 / 对比表格 + 截图
## 本讲知识地图      # mermaid mindmap 代码块
## 概念卡片          # ### <概念名>：定义 / 出现 / 依赖 / 易混
## 自测题（复习用）  # <details> 折叠答案，lecture 4~7 道 / short 2~3 道
---
<!-- NAV:BEGIN 条目由模型写出，深链由 scripts/note_nav.py 填充 -->
## 视频时间索引      # <details> 折叠；条目 - [mm:ss] <章节名>，不写 URL
<!-- NAV:END -->
```

## 常见问题

| 现象 | 处理 |
|---|---|
| 分P `subtitle_detail` 为 `api_empty` | 主动询问用户「要现在扫码登录B站吗？」，确认后代跑 `bili_fetch.py --login`（生成二维码 PNG 图片并打印路径）后重跑；用户拒绝或重跑后仍 `api_empty` 才走 ASR |
| 分P `subtitle_detail` 为 `ai_mismatch` | AI 字幕整体错位（内容是别的音频，看着通顺但与视频无关），fetch 已自动判出并按无字幕处理；直接走 ASR，不要试图沿用存疑字幕 |
| playurl 下载失败/音频为空 | 可能是风控，重试一次；仍失败则改用 `yt-dlp` 下载音频后手动喂给 transcribe 的 transcribe 函数思路 |
| faster-whisper 首次运行卡在下载 | 检查 HF_ENDPOINT 是否为 https://hf-mirror.com，或手动 `pip install -U huggingface_hub` |
| 转写文本术语错乱 | 用 `references/asr-glossary.json` 对照校正；词表未覆盖的新错法按同样格式补进词表 `map`，下次自动生效 |
| `subtitle_detail` 为 `error` | 看摘要里的 error 信息：视频可能已失效/被风控，重试一次或告知用户换视频 |
| 转写质量差（长视频尤甚） | 先删对应 txt，再用 `--model medium` 重转；有 NVIDIA GPU 时脚本会自动用 CUDA float16，也可 `--device cuda` 强制 |
| 抖音 detail 接口 403 / "无 aweme_detail" | 签名算法可能被抖音升级，参考 Evil0ctal/Douyin_TikTok_Download_API 或 JefferyHcool/BiliNote 更新 `abogus.py` / `WEB_SIGN_SALT`；另确认视频可公开访问（非私密/仅粉丝可见） |
| 抖音视频下载中断/文件过小 | 下载地址有时效，重跑 douyin_fetch.py 重新解析即可（本地已有完整 mp4 会自动跳过下载） |
| 环境缺依赖 | B站流程需 faster-whisper + av；抖音流程另需 `pip install gmssl` |
