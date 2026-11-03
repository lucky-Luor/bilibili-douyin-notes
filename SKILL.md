---
name: bilibili-douyin-notes
description: 把B站/抖音/YouTube/小红书视频整理成Markdown课堂笔记。用户发来 bilibili.com、b23.tv、douyin.com、youtube.com、youtu.be、xiaohongshu.com、xhslink.com 等视频链接（或分享口令）并要求做笔记、总结、转文字时使用。产出符合模板规范、经质量门禁校验的课堂笔记 .md 文件。
---

# B站/抖音/YouTube/小红书视频 → Markdown 课堂笔记

## 触发条件

用户消息中包含 bilibili.com / b23.tv / BV号 / douyin.com / v.douyin.com / iesdouyin.com / youtube.com / youtu.be（含 /shorts/、/live/）/ xiaohongshu.com / xhslink.com 链接（或抖音、小红书分享口令文本），且要求总结、做笔记、整理复习资料、提取视频内容等。

## 平台分流（第一步先看链接属于哪个平台）

- **B站** → `bili_fetch.py`：走官方API，优先字幕，按需下载音频/视频流
- **抖音** → `douyin_fetch.py`：游客身份（无需登录/SESSDATA），下载视频到本地供语音识别（只用于本地转写，用完可清理），一般无字幕 → 必走第二步ASR（抖音视频短，耗时可忽略）。需要 `pip install gmssl`；`abogus.py` 为 GPLv3 代码（源自 Evil0ctal/Douyin_TikTok_Download_API 经 BiliNote 移植），分发需保留其许可头
- **YouTube** → `yt_fetch.py`：默认优先原语言人工字幕 → 中文人工字幕 → 原语言自动字幕 → 原有兜底（yt-dlp json3 字幕 → 无字幕交 ASR）；显式 `--lang` 覆盖默认语言优先级。有字幕时不下载任何媒体；需要 `pip install yt-dlp youtube-transcript-api`，并建议安装 [deno](https://deno.com)（yt-dlp 解 YouTube JS 挑战所需）
- **小红书** → `xhs_fetch.py`：免登录、无签名，手机 UA 请求分享页并解析内嵌 `__INITIAL_STATE__`，下载视频到本地（默认 h264 转码流，`--origin` 下载体积大得多的原片）；**只支持视频笔记**，图文笔记脚本直接提示退出；链接必须带 `xsec_token`（让用户从 App「分享 → 复制链接」整段发来）；一般无字幕 → 必走第二步 ASR
- 四个平台的 metadata.json 字段一致，第二/三步脚本自动兼容：B站分P按 `bvid/cid` 按需下载，抖音分P带 `media_path` 直接用本地视频文件

## 工具位置

- 脚本目录：本 skill 安装目录下的 `scripts/`（命令中的 `<skill安装目录>` 占位符请替换为本 skill 实际安装路径）
- 配置文件：本 skill 安装目录下的 `config.json`（存放 SESSDATA；截图上传语雀用的 OSS 配置写在 `oss` 字段）
- 参考资产：
  - `references/note-template.md` —— 课堂笔记模板 v4 / v4-obsidian（语雀 / Obsidian 版式、骨架、语法契约、时间戳规范）
  - `references/asr-glossary.json` —— ASR 术语纠错词表（真实错法→正确写法）
  - `references/anchors.json` —— 截图关键词锚点表（按视频 ID 隔离；仅在不用 `--shots` 清单时兜底）

## 工作流程

### 第零步：环境自检（每个会话第一次处理视频前跑一次）

```bash
python "<skill安装目录>/scripts/doctor.py" --platform <本次链接的平台> --json
```

- 平台取 `bilibili` / `douyin` / `youtube` / `xiaohongshu`；退出码 1 表示有 FAIL
- 有 `FAIL`：先把对应的 `fix` 原样告诉用户并停下，修好再开工；不要跳过自检硬跑
- 只有 `WARN`：可以开工，开工前用一句话说明影响（如“没有显卡，这个无字幕视频大约要 X 分钟”“YouTube 连不上，需要代理”）
- `skill 文件` 为 FAIL（被加密软件处理过）时，提示用户用 `git clone` 重新安装，不要尝试自己修文件

### 整门课模式（B站，先于第一步判断）

用户发来 B站链接或 BV号，环境自检通过后，先建立课程清单：

```bash
python "<skill安装目录>/scripts/course_plan.py" plan "<链接>" "<课程目录>"
```

1. 读取 stdout **最后一行 JSON**；`course:false` 表示单P无合集，继续下面的原单视频流程。`course:true` 时，把 `confirm_text` **原样发给用户**，并转述 `hint_optional`；用户没有回复，不能调用 `start` 或开始抓取、转写、写笔记。
2. 用户选好范围后调用 `start "<课程目录>" --all`、`--first 3` 或 `--range 5-12`；标题明显为英文原声课且没有“中字 / 中英 / 配音”提示时，启动前先确认是否英文转写（见第一步）。用户选择另一种组织范围时加 `--scope pages|season`。多分P有合集时默认只做当前视频；单P有合集默认展开，也可选择只做当前视频。超过 200 集或 100 小时的清单禁止 `--all`，只能选前若干集或指定范围。模板默认取 `config.note_template`，也可在 `start` 显式选择 `--template v4|v4-obsidian`；ASR 语言用 `--asr-lang zh|en|auto`。例如：
   ```bash
   # 语雀版式，中文转写
   python "<skill安装目录>/scripts/course_plan.py" start "<课程目录>" --first 3 --template v4 --asr-lang zh
   # Obsidian 版式，英文转写；需要自动检测时将 en 改为 auto
   python "<skill安装目录>/scripts/course_plan.py" start "<课程目录>" --first 3 --template v4-obsidian --asr-lang en
   ```
3. 串行循环 `next "<课程目录>"`。`status:next` 的 `steps` 给出 fetch / transcribe / note 步骤；`skip:true` 不执行。优先按 `argv` 参数数组执行，避免含空格路径或特殊字符经过 shell 二次解释；`cmd` 是展示用命令。
4. fetch 后重新检查**本集**的 `subtitle_detail` 和正式文本：`cc/ai` 且正式文本存在可跳过转写；其它无字幕状态走单集 ASR。`ai_mismatch` 的存疑文本不可用于笔记。读取转写逐集 JSON，进程退出码非 0 或 JSON `status:error` 都算失败；`nothing_to_do` 时也必须确认目标页及正式文本存在，不能直接视为成功。需要恢复元数据时按三段提示处理。
5. 每集按第三步的全部规则独立制作笔记，模板以 `next` 返回的 `note.template`（`v4` / `v4-obsidian`）为准，使用 `note_path`。`existing_note:true` 时在原文件上修正；除文件缺失外，不要整篇重写。只读这一集的正式转写稿，不要把前面各集的全文持续留在上下文。英文课正文首次出现的术语写作 **中文（Original）**，概念卡用中文卡名并加 `+ 原文：Original`；原本中文课程、或卡名本身是纯外文专名时免加原文行。截图仅用户明确要求时执行。按 `nav_cmd` 填深链，再按 `validate_cmd` 做最终门禁，确认 errors 为 0；两个命令都带本集 `--page N` 和选定模板。
6. 成功后 `mark "<课程目录>" <序号> done --note "<笔记路径>"`；`mark done` 还会执行进程内门禁，拒绝时按报告修正后重试，不能凭先前校验结果强记 done。一集任何步骤失败（含门禁修正后仍失败），用该步的“原因 / 下一步”调用 `mark "<课程目录>" <序号> failed --reason "<原因>" --next-step "<下一步>"`，**继续下一集，不停下来反复询问用户**。课程批处理不强制扫码登录，可将可选登录建议留给用户；失败集可稍后补跑。
7. 每次 `mark` 把摘要的 `progress` 原样转告用户。`next` 返回 `status:finished` 时，把 `receipt_text` **原样转述**，保留其中的完成数、失败集、课程索引和补跑信息，不自行编造回执。仅当 `cache.bytes > 0` 时询问是否清理缓存；用户同意后才执行 `python "<skill安装目录>/scripts/course_plan.py" clean "<课程目录>" --apply`，`cache.bytes = 0` 时不询问。
8. 中途被打断时再次 `next` 会先复查已 done 的集，再交出待处理集（含原来的 running 集）。笔记 SHA 与模板未变化时复用缓存、跳过检查；笔记改坏或缺失时退回 pending，保留 `planned_note` 供原位修复。已有草稿不会自动成为 done。需要手动复查时用 `verify "<课程目录>"`，它与 `next` 共享 SHA 校验缓存，并非强制全量检查；`index "<课程目录>"` 刷新课程索引，`status "<课程目录>"` 只读查询、不触发复查。`mark "<课程目录>" <序号> pending` 可把失败集放回待处理，再调用 `next`。`plan` 刷新同源同模式清单会保留结果；切换模式用 `start --scope`。不要把转写 `.done` 与课程笔记的 done 状态混为一谈。

课程目录中的 `course.json` 是唯一状态来源；`videos/<BV号>/` 复用单视频抓取产物，同 BV 只抓取一次，`notes/` 每集一篇。默认最多探测 60 集字幕、间隔 0.3 秒；`plan --probe-all` 全探，`--no-probe` 跳过，未探测或失败按需要 ASR 保守估算。预估按六核笔记本 CPU，后续剩余时间使用已完成集的有效平均耗时修正。

### 第一步：抓取元数据 + 字幕（B站 / YouTube）或下载视频（抖音 / 小红书）

B站标题明显指向英文原声课（如 Lecture / MIT / Stanford / CS61A），且没有“中字 / 中英 / 配音”等提示时，先问用户是否按英文转写；确认后再启动转写，课程模式在 `start` 选择 `--asr-lang en`（需要自动检测时用 `auto`）。不要只凭平台或中文字幕推断音频语言。

```bash
# B站（也支持 b23.tv 短链）
python "<skill安装目录>/scripts/bili_fetch.py" "<视频链接>" "<工作目录>/bili-notes-<BV号>"

# B站只探元数据：不抓字幕、不建目录、不写任何文件（先确认视频可访问/时长/分P数时用）
python "<skill安装目录>/scripts/bili_fetch.py" --probe "<视频链接>"

# 抖音（也支持 v.douyin.com 短链和整段分享口令文本）
python "<skill安装目录>/scripts/douyin_fetch.py" "<链接或口令>" "<工作目录>/douyin-notes-<视频ID>"

# YouTube（watch / youtu.be / shorts / live / embed 链接或 11 位 ID 均可）
python "<skill安装目录>/scripts/yt_fetch.py" "<视频链接>" "<工作目录>/yt-notes-<视频ID>"
# 可选：--lang zh-Hans,en（字幕语言优先级）--download（无字幕时下载音频供 ASR）
#       --cookies <cookies.txt> / --cookies-from-browser chrome（风控/年龄限制）--proxy http://127.0.0.1:7890

# 小红书（xhslink.com 短链 / explore / discovery/item 链接或整段分享文本，必须带 xsec_token）
python "<skill安装目录>/scripts/xhs_fetch.py" "<分享链接或分享文本>" "<工作目录>/xhs-notes-<笔记ID>"
```

- 两个脚本都自动生成 `<输出目录>/metadata.json`，stdout 最后一行是 JSON 摘要。
- **B站**：每个分P的字幕 `.txt`（带 `[mm:ss]` 时间戳），按摘要里每个分P的 **`subtitle_detail` 字段六值分流（cc / ai / ai_mismatch / none / api_empty / error，其中 cc 与 ai 处置相同）**：
  - `cc` / `ai` → 已拿到官方/AI字幕，直接进入第三步生成笔记（AI 字幕已由脚本自动做"内容↔标题 ASCII 词"交叉校验，错位时会判为 `ai_mismatch` 而不是 `ai`）；
  - `ai_mismatch` → AI 字幕整体错位（返回了 ai 状态但内容是别的音频，实测有配成无关电影对白的案例）：**不可信，直接进入第二步 ASR 兜底**，不要用该字幕生成笔记；存疑字幕已另存 `*.ai字幕存疑.txt` 备查，摘要带 `hint_mismatch` 时可向用户提一句；
  - `none` → 该分P确实没有字幕，直接进入第二步 ASR，不用再折腾；**但若摘要带 `hint_optional`（未配置登录态时可能出现），转述给用户**：「若该视频本应有 AI 字幕，可扫码登录（`--login`）后重跑，通常能跳过语音识别」——由用户判断要不要扫，不强制；
  - `api_empty` → 接口没返回可用字幕URL（`sessdata_loaded:false` 时常见，AI字幕需要登录）：**主动询问用户「要现在扫码登录B站吗？」，确认后代跑 `python "<skill安装目录>/scripts/bili_fetch.py" --login`（脚本生成二维码 PNG 图片并打印路径，把路径给用户打开扫码即登录，SESSDATA 自动写入 config.json）后重跑一次 fetch**；用户拒绝或重跑后仍是 `api_empty` 才走第二步 ASR 兜底；
  - `error` → 查看摘要里的 error 信息（可能是视频失效/风控），重试一次或直接告知用户。
- **抖音**：视频已下载到 `<输出目录>/media_p01.mp4`（仅供本地转写，本地已存在则跳过下载），无字幕，直接进入第二步 ASR。
- **YouTube**：默认按原语言人工字幕 → 中文人工字幕 → 原语言自动字幕 → 原有兜底选择；显式 `--lang` 覆盖默认语言优先级。结合 `audio_lang` / `subtitle_lang` 判断是否为原语言课程及术语表达，不因选到中文字幕就抹去英文原声课的原文术语。按摘要里分P的 `subtitle_detail` 分流：
  - `manual` / `auto` / `ytdlp_manual` / `ytdlp_auto` → 已写出 `01_<标题>.txt`（`[mm:ss]` 格式，自动字幕已去滚动重复）+ `.done` 标记，直接进入第三步；英文原声或英文字幕课程按第三步的中文正文、双语术语与概念卡规则撰写；
  - `none` → 无可用字幕：加 `--download` 重跑（下载音频到 `media_p01.m4a`），再进入第二步 ASR；
  - `blocked` → YouTube 拒绝了当前 IP：先建议用户配 `--proxy` 或 `--cookies-from-browser` 重跑；仍不行再 `--download` 走 ASR；
  - `error` → 看 stderr 日志，重试一次或告知用户。
  - 没有 `media_path` 的 YouTube 分P不能直接跑第二步（脚本不会去 B站接口下载），务必先 `--download`。
- **小红书**：视频已下载到 `<输出目录>/media_p01.mp4`（本地已存在则跳过下载），无字幕，直接进入第二步 ASR；时间索引与抖音一样无深链（保持纯文本）。

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
- **耗时**：CPU int8 下 small 模型实测约为视频时长的 0.2 倍（6 核笔记本 CPU，批量推理 + 静音过滤；更老的 CPU 会慢一些，有 NVIDIA GPU 时更快），转写前按“时长 × 0.2”告知用户预计时间，先转第 1 个分P后按实际耗时修正；长视频或对质量不满意时用 `--model medium` 重转（**先删对应的 txt**，否则脚本会跳过）。
- 首次运行会下载模型（small≈480MB，自动走 hf-mirror.com 镜像）。
- ASR 文稿没有标点分段质量保证，技术术语常被转错——生成笔记前用 `references/asr-glossary.json` 词表对照校正（见第三步第2条）。

### 第二步半（可选，只在用户要“带截图”时做）：抽取重点截图

用户没提截图就跳过本步。要截图时，**先完成第三步的正文，再回来按小节定截图**——
截什么由理解了内容的 Agent 决定，脚本只负责按清单抽帧：

1. 正文写完后，为需要画面佐证的小节（PPT 关键页、架构图、代码演示、对比表）写截图清单
   `<工作目录>/shots.json`，每小节 0~2 张、宁缺毋滥：
   ```json
   {"shots": [
     {"page": 1, "section": 2, "t": "05:12", "caption": "开闭原则两层含义", "name": "two-meanings"}
   ]}
   ```
   `page` 分P号、`section` 笔记 `## N.` 小节号、`t` 该分P**转写稿里真实存在的时刻**
   （先 grep 确认；不在转写稿 ±5s 内的条目会被脚本拒绝，进 `failed`，reason=`t_not_in_transcript`）、
   `caption` 图注、`name` 文件名（可选，缺省用 caption）。
2. 按清单抽帧：
   ```bash
   python "<skill安装目录>/scripts/bili_screenshot.py" "<工作目录>/metadata.json" "<工作目录>" "<工作目录>/images" --shots "<工作目录>/shots.json"
   ```
   输出 `images/p<页>_s<小节>_<name>.png` + `images/manifest.json`（记录 section）。
   结束时 stderr 打一句**回执**（同时写进 `manifest.json` 的 `summary.receipt`），如“截图 6 张，剔除坏帧 2 张（重复 1、取不到画面或时刻不对 1）”。**交付时把这句原样告诉用户**，不单独出质检报告。
   **一张截图都没出时脚本以非 0 退出码报错**（原因 / 影响 / 下一步：缺转写稿、找不到截图时刻、清单时刻不在转写稿里、下载被拦截等），按“下一步”处理后重跑；用户要了截图，不能静默交付一篇没有图的笔记，处理不了就明确告诉用户。
3. **逐张看图（必做，不能跳过）**：脚本只保证画面不黑、不糊、不重复，**不保证画面对得上图注**。
   2026-11 抽查 10 篇课程笔记，脚本直出的截图只有 62% 能佐证图注（见 `docs/verification/W4-截图抽查.md`），
   所以每张截图都要打开看，按下表处理：

   | 判定 | 标准 | 处理 |
   |---|---|---|
   | 保留 | 画面上能看清图注说的内容（PPT、类图、代码、板书） | 嵌入笔记 |
   | 换时刻 | 板书还没写完、被讲师挡住，或画面还停在别的内容上 | 在转写稿里往后找这部分讲完的时刻（通常晚 30–90 秒）改 `t`，只重跑这一集；**最多换一次** |
   | 删除 | 换一次仍不行，或这一段只有讲师在讲（黑板、投影都是空的） | 从 `shots.json` 删掉这一条，不嵌入笔记 |

   - 保留的截图以 `![<caption>](images/xxx.png)` 嵌入**清单里写的那个小节**、它佐证的要点旁边；**没看过的截图不能嵌入**
   - 回执按复核后的结果说，例如“截图 5 张（复核删掉 2 张，其中 1 张换时刻后保留）”
   - 一集里一张可用的都没有时，直接告诉用户“第 X 集以口头讲解为主，没有可截的画面”，不要硬凑

脚本行为：
- **多帧择优**：每个时刻向后取 3 张候选（间隔 4s），优先画质合格帧，再挑与相邻候选差异最小
  （画面已静止，避开 PPT 翻页过渡）的一张；
- **去重**：同一分P里与已选截图是同一画面（感知哈希接近且像素差小）的不落盘，记入进度 JSON 的 `duplicates`；
- **画质 QC**：亮度用像素占比法（近黑>97% 判 dark、近白>98.5% 判 bright——白底 PPT 不会误判），
  清晰度用拉普拉斯方差（<10 判 blurry）；每帧 `qc` 字段非 `ok` 时由 Agent 视觉复核决定是否采用；
- B站下载 480p DASH 视频抽帧后即删（填了 SESSDATA 自动取更高画质）；抖音 / 小红书复用本地视频不联网；
- **不给 `--shots` 时**退回关键词锚点：查 `references/anchors.json` 中**本视频 ID** 的条目
  （按视频隔离，别的课程的锚点不会串用），没有就用 `auto_anchors()` 从转写稿提取高频词——
  这种方式与小节无关、定位粗糙，仅作兜底；
- 截图文件名默认可中文；需要 ASCII 文件名时加 `--ascii-names`。

### 第三步：生成课堂笔记

> **使用场景定位（2026-10-21，v4）**：本 skill 解决的是“看完课不用自己做笔记”——
> 产出的是**课后复习资料**，正文按技术知识体系组织、阅读连贯：**正文零时间戳、
> 零考试向标签**（要点用 `+` 列表 + 关键术语加粗）；视频时间戳只存在于文末的
> “视频时间索引”，供回看原片时跳转。默认 v4 为**可直接导入语雀**的纯 Markdown：
> 无 frontmatter、无一级标题、答案与时间索引不折叠。`v4-obsidian` 变体增加 frontmatter，
> 目录改用精确标题的 Wiki 链接，其它 v4 正文规则不变。

模板默认取 `config.note_template`，用户可显式选择；课程以 `start --template` 保存、`next` 返回的 `note.template` 为准。英文课结合 `audio_lang` / `subtitle_lang` 与用户确认的语言判断，正文首次出现的术语写成 **中文（Original）**，概念卡用中文卡名并加 `+ 原文：Original`；原本中文课程或卡名本身是纯外文专名时免加原文行，详见模板。

1. 读取 metadata.json（分P结构、时长）+ 各分P转写/字幕文本。
2. **ASR 术语校正（工具化，不是凭感觉）**：先跑
   `python "<skill安装目录>/scripts/apply_glossary.py" "<转写稿.txt>"`（默认 dry-run，按类别分组只出报告不改文件），按报告核对命中项，确认后再加 `--apply` 落盘（自动留 `.bak` 备份，命中词条 hits 自动累加）。词表分两层：仓库层 `references/asr-glossary.json`（编程/美食/游戏/通用大类）+ 用户层（输出目录 `glossary.user.json` 或 `~/.zcode/bdn-glossary.user.json`，同键用户层优先）；候选词命中只报告（标 `[候选]`）永不替换。**维护闭环**：发现词表未覆盖的新错法 → `--add "错法=正确词" --category 编程`（或 `--candidate` 先入候选、`--promote "错法"` 转正）；`--list` 查看全部；`--categories`/`--topic` 控制启用范围。注意：转写阶段已自动把高频术语注入 initial_prompt（见第二步），词表越丰富转写越准。
3. **长视频分块（写死流程，禁止跳过）**：单分P转写稿超过约 2 万字时——用 `scripts/transcript_chunk.py` 分块（默认 15000 字/块、末尾重叠 2 段，输出 `chunks/` 目录）→ **map**：逐块提炼该块要点清单 → **reduce**：把各块要点按小节层级合并汇总成最终笔记。**reduce 去重**：重叠区会在相邻块产生重复内容，合并时按 `[mm:ss]` 时间戳唯一化——同一时刻的要点/锚点只保留一次，跨块重复的锚点去重后再排进小节（该去重仅作用于分块合并的中间产物；正文按 v4 规则仍然禁时刻）。禁止跳过分块直接全文阅读长转写稿。
4. 按 `references/note-template.md`（**v4 / v4-obsidian 模板**）生成一份 MD 文件，保存到用户工作目录，命名 `<序号>-<主题>.md`（如 `2.13-业务流程建模.md`；序号取课程选集号，或按用户已有笔记递增如 `01`；主题取视频标题关键词——文件名即语雀文档标题）。生成前必读该模板文件。以下为 v4 硬性要求；`v4-obsidian` 仅将开头换成模板规定的 frontmatter（闭合后紧接核心主线引语块），目录换成 `+ [[#精确正文标题]]`，其它规则相同：
   - **无 frontmatter、无 `#` 一级标题**：文件第一行就是引语块 `> 核心主线：<一句话知识主线>`（行尾两个空格换行），下一行 `> 课程来源：<视频标题>（B站 BVxxx，UP主：xxx，约 N 分钟）`，有代码仓库再加 `> 课程代码：`；笔记类型取 metadata.json 顶层 `suggested_note_type`（技术密度高的短片可改为 lecture，此时第 6、7 步加 `--note-type lecture`）；
   - **目录**：`+ [N. 小节名](#锚点)` 不带时刻，末尾固定列出 本讲知识地图 / 概念卡片 / 自测题（复习用）/ 视频时间索引；
   - **小节**：`## N. 小节名`（禁时刻后缀），**下一行紧接** `**核心思想：** <一句话>`（不空行），每小节 ≥3 行实质内容；要点用 `+ 要点句`、关键术语 `**加粗**`，**禁止三词标签前缀（核心必考/重点掌握/了解即可及任何变体）、禁止行内 `[mm:ss]`**；对比性内容优先用表格；小节之间可用 `---` 分隔；
   - **代码块**：真实语言 fence；来源标注可选——来自克隆仓库写 `# 来源：仓库 <子项目>/<文件路径>`（注释符随语言），按视频讲解组织的可写 `# 按视频讲解还原` 或不写；任何代码块内禁时刻；
   - **概念卡片**：`### 概念名`（禁标签后缀）+ `+ 定义：` / `+ 出现：` / `+ 依赖：` / `+ 易混：` 四行；`出现` 写作 `第 N 节「小节名」、第 M 节「小节名」`（≥2 个小节，节名与正文小节标题一致），禁时刻；
   - **自测题（不折叠）**：`N. <题干>？（对应：概念名）`（概念必须在概念卡片集合内：lecture 必须、short 可选），空一行后直接写 `**答案：** <至多一行>`，**不用 `<details>`**，禁时刻；lecture 4~7 道（short 2~3 道）；选题主打高频原理与面试易错点，禁 yes/no 题干；
   - **mermaid 知识地图 = 概念关系图，不是目录树**：` ```mermaid ` 代码块，推荐 `graph TD`；`R((主题))` 为根，**节点用概念卡片里的概念**（每张概念卡都要出现），小节名放在 `subgraph S1[分组名]` 标题上而不是当节点；**边带关系标签**表达概念之间怎么关联——`A -->|依赖| B`、`A -->|推出| B`、`A -->|组成| B`、`A -.->|易混| B`，lecture 带标签边 ≥ 概念卡数 − 1，关系尽量与概念卡「依赖 / 易混」一致；节点标签禁半角 ( ) [ ] { } 与引号、≤12 字（需要括号用全角）；导入语雀后自动渲染为图；
   - **视频时间索引**：文末最后一节，前接 `---`，**不折叠、不写 NAV 标记**；模型只写条目 `+ [mm:ss] <与正文一级小节同名>`（一级章节一条），**不写 URL**；时刻必须取自转写稿真实时间行（允许 ±5s 内小幅前移，禁止虚构）；lecture 必须有本节，short 可整体省略。
5. 笔记中引用的代码/命令必须以视频文稿内容为准；文稿未讲到的细节不要编造，可标注"（视频中未展开，建议补充）"。若视频简介里有代码仓库，克隆下来读取源码，把笔记中的代码示例换成仓库中的真实代码（首选做法）。
6. **填充时间索引深链（v4 笔记默认执行）**：
   `python "<skill安装目录>/scripts/note_nav.py" "<笔记.md>" --template v4 --meta "<metadata.json>" --txt-dir "<转写稿目录>"`
   （每个选集单独一份笔记时加 `--page <分P号>`）脚本幂等地把「## 视频时间索引」节内的 `+ [mm:ss] 标题` 条目原位填充为 `+ [mm:ss](URL) 标题`（B站生成 `?p=N&t=S` 深链，YouTube 生成 `watch?v=<id>&t=<秒>s` 深链，抖音 / 小红书无深链保持纯文本）；`note_type=short` 跳过本步；找不到时间索引节时 warning 跳过。v3 老笔记（frontmatter 写 v3）按 NAV 标记填链；v1 / v2 已不再支持。模型输出不含 URL，脚本输出不含自由文本，职责不重叠。
7. **质量门禁**：生成后运行
   `python "<skill安装目录>/scripts/validate_note.py" "<笔记.md>" --template v4 --meta "<metadata.json>" --txt-dir "<转写稿目录>"`
   单视频选择 Obsidian 时，第 6、7 步的模板都显式改为 `--template v4-obsidian`，例如：
   ```bash
   python "<skill安装目录>/scripts/note_nav.py" "<笔记.md>" --template v4-obsidian --meta "<metadata.json>" --txt-dir "<转写稿目录>"
   python "<skill安装目录>/scripts/validate_note.py" "<笔记.md>" --template v4-obsidian --meta "<metadata.json>" --txt-dir "<转写稿目录>"
   ```
   （每个选集单独一份笔记时加 `--page <分P号>`）errors 必须为 0 才交付；warnings 酌情处理（如精简篇幅）。没有 frontmatter 的笔记默认按 v4 检查（`--template v4` 可省略）；v4 下出现 frontmatter / 一级标题、正文时刻、标签残留、`<details>` 折叠或 NAV 标记、时间索引缺失（lecture）、自测数量越界、概念卡「出现」格式非法等均为 error。`v4-obsidian` 需按模板提供 frontmatter 与 Wiki 目录，其它 v4 门禁不变。frontmatter 写 `template_version: v3` 的老笔记按 v3 规则检查；写 v1 / v2 或缺版本号会直接报 `VERSION` 错误（v1 / v2 规则已移除）。报告 JSON 的 `errors` / `warnings` 为**对象数组**（每项含 `check` 检查编号与 `line` 行号）。
8. **截图上传 OSS（仅带截图的笔记）**：语雀导入 Markdown 不会上传本地图片，交付前先预演
   `python "<skill安装目录>/scripts/upload_images.py" "<笔记.md>"`（列出将上传的图片与目标 OSS 链接，不联网不改文件），
   确认后加 `--apply`：按内容哈希命名上传（远端已有则跳过，可重复执行），再把笔记里的 `images/...` 原位改写为 OSS 链接（留 `.bak` 备份）。
   OSS 配置读 `config.json` 的 `oss` 字段或环境变量 `OSS_ENDPOINT / OSS_BUCKET / OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET / OSS_PREFIX / OSS_PUBLIC_BASE_URL`
   （也可 `--env-file <xxx.env>`）；Bucket 需公共读或经 CDN 可公开访问。**门禁要在上传前跑**（上传后图片是网络链接，位置与图注校验会跳过）；
   缺配置时脚本只列出缺哪些键，不要向用户索要密钥明文，让用户自己填进 config.json 或环境变量。
9. **媒体清理（单视频交付后按缓存询问；整门课按课程步骤 7 处理）**：若输出目录存在缓存媒体（抖音的 `media_p01.mp4` 等本地视频，或残留的 `.part`/`.audio_*`/`.video_*` 临时文件），**询问用户「是否清理缓存的视频/音频文件？重新做笔记会重新下载」**：确认 → 先跑
   `python "<skill安装目录>/scripts/clean_media.py" "<输出目录>"`
   把将删除的清单给用户过目，再执行 `--apply` 实删；拒绝 → 跳过并说明缓存保留的意义（重跑截图/转写不再联网）。B站的临时音频/视频在流程中已自动删除，通常无残留。

## 笔记模板

**完整骨架与语法契约以 `references/note-template.md` 为准（生成前必读）**——全文含
lecture 完整骨架、时间戳规范、语法契约、short 差异表、截图嵌入规范、生成流程。
结构层次速览（节点内容以 `<…>` 占位）：

```markdown
> 核心主线：<一句话知识主线>       # 文件第一行；无 frontmatter、无 # 标题（文件名 <序号>-<主题>.md 即标题）
> 课程来源：<视频标题等>          # 有仓库再加 > 课程代码：<仓库地址>
## 目录              # + [N. 小节名](#锚点) 不带时刻；末尾固定列出 本讲知识地图 / 概念卡片 / 自测题（复习用）/ 视频时间索引
## N. 小节名 ×N      # 下一行紧接 **核心思想：** <一句话> + `+` 要点列表（术语加粗）+ 代码块 / 对比表格 +（带截图时）本节截图
## 本讲知识地图      # mermaid graph TD 概念关系图：节点=概念卡，subgraph 分组，边带关系标签
## 概念卡片          # ### <概念名>：+ 定义 / + 出现：第 N 节「小节名」 / + 依赖 / + 易混
## 自测题（复习用）  # 不折叠：题干后空一行写 **答案：**，lecture 4~7 道 / short 2~3 道
---
## 视频时间索引      # 不折叠、无 NAV 标记；条目 + [mm:ss] <章节名>，URL 由 note_nav.py 填充
```

## 常见问题

脚本失败时统一输出三段：`原因：` / `影响：` / `下一步：`（必要时附 `详情：`）。**把这三段原样转告用户，按“下一步”处理**，不要只说“脚本报错了”。单集失败的进度 JSON 另有 `reason` / `impact` / `next_step` / `rerun` 字段，`rerun` 是只补跑这一集的命令。

| 现象 | 处理 |
|---|---|
| 分P `subtitle_detail` 为 `api_empty` | 主动询问用户「要现在扫码登录B站吗？」，确认后代跑 `bili_fetch.py --login`（生成二维码 PNG 图片并打印路径）后重跑；用户拒绝或重跑后仍 `api_empty` 才走 ASR |
| 分P `subtitle_detail` 为 `ai_mismatch` | AI 字幕整体错位（内容是别的音频，看着通顺但与视频无关），fetch 已自动判出并按无字幕处理；直接走 ASR，不要试图沿用存疑字幕 |
| 提示“请求被B站风控拦截”（HTTP 412） | 云服务器 / 机房 IP 上常见，脚本不会重复请求：换到家用网络或在本机运行；等几分钟再试 |
| playurl 下载失败/音频为空 | 按提示的“下一步”处理（多为风控或需要登录）；单集失败照 `rerun` 补跑这一集 |
| faster-whisper 首次运行卡在下载 | 检查 HF_ENDPOINT 是否为 https://hf-mirror.com，或手动 `pip install -U huggingface_hub` |
| 转写文本术语错乱 | 用 `references/asr-glossary.json` 对照校正；词表未覆盖的新错法按同样格式补进词表 `map`，下次自动生效 |
| `subtitle_detail` 为 `error` | 看该分P的 `reason` / `next_step`：这一集会改走语音识别；想重取字幕按 `rerun` 重跑 |
| 转写质量差（长视频尤甚） | 先删对应 txt，再用 `--model medium` 重转；有 NVIDIA GPU 时脚本会自动用 CUDA float16，也可 `--device cuda` 强制 |
| 抖音提示“作品不可见” | 作品已删除、设为私密或仅粉丝可见（平台返回了 `filter_detail`），不是签名问题：告知用户换链接 |
| 抖音提示“签名算法已被平台升级” / HTTP 403 | 参考 Evil0ctal/Douyin_TikTok_Download_API 或 JefferyHcool/BiliNote 更新 `abogus.py` / `WEB_SIGN_SALT`，或提 issue |
| 抖音 / 小红书提示“下载的文件过小” | 多半被风控或下载地址过期：等几分钟重跑 fetch 脚本重新解析（本地已有完整 mp4 会自动跳过下载） |
| YouTube 报 "Sign in to confirm you're not a bot" / `blocked` | 云服务器 IP 常被 YouTube 拒绝：加 `--cookies-from-browser chrome`（或导出 cookies.txt 用 `--cookies`），必要时 `--proxy` 或在 config.json 配 `youtube_proxy` |
| YouTube 缺格式 / 下载失败 | 升级 `pip install -U yt-dlp` 并安装 deno（2025 年起 YouTube 需要 JS 运行时解挑战） |
| YouTube 年龄限制视频 | youtube-transcript-api 拿不到，需 `--cookies`/`--cookies-from-browser` 让 yt-dlp 回退取字幕 |
| 小红书提示缺少 xsec_token / 被重定向 / 无笔记数据 | 让用户在 App「分享 → 复制链接」把整段文本发来（token 有时效，过期重新复制）；笔记需公开可见 |
| 小红书提示风控验证（HTTP 461/471） | 请求过频触发验证码：稍后重试，或在 config.json 配 `xhs_cookie`（浏览器登录后复制 Cookie） |
| 小红书提示「这是图文笔记」 | 本工具只处理视频笔记，告知用户换视频笔记链接 |
| YouTube 正在直播 / 预告 | 脚本直接提示退出，等直播结束、回放生成后再试 |
| 环境缺依赖 | B站流程需 faster-whisper + av；抖音流程另需 `pip install gmssl`；YouTube 流程另需 `pip install yt-dlp youtube-transcript-api`；小红书流程无额外依赖 |
