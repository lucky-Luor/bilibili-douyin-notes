<div align="center">

# bilibili-douyin-notes

**An AI Agent Skill that turns Bilibili / Douyin / YouTube / Xiaohongshu (RedNote) videos into structured Markdown lecture notes**

Four platforms · Subtitle fetching · Local speech recognition · Key-frame screenshots · Note template — works without login, no paid API

[![CI](https://github.com/lucky-Luor/bilibili-douyin-notes/actions/workflows/ci.yml/badge.svg)](https://github.com/lucky-Luor/bilibili-douyin-notes/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Bilibili%20%7C%20Douyin%20%7C%20YouTube%20%7C%20Xiaohongshu-green.svg)]()
[![Python](https://img.shields.io/badge/Python-3.10%2B-yellow.svg)]()
[![Agent Skills](https://img.shields.io/badge/spec-Agent%20Skills%20(SKILL.md)-orange.svg)]()

[简体中文](README.md) | English

</div>

> 💬 Just tell your AI agent "make lecture notes from this video: <Bilibili / Douyin / YouTube / Xiaohongshu link>" and you get notes like this 👇
> (Full example: [Hung-yi Lee ML course · Local Minima & Saddle Points](examples/01-局部最小值与鞍点.md), in Chinese)

<p align="center"><img src="docs/images/note-preview.png" alt="Note preview" width="760"></p>

> 📜 **Personal study only**: public videos only, everything runs locally, no data collected; do not use it to redistribute or for commercial purposes. See [Usage Notice & Data Flow](#-usage-notice--data-flow).

---

## ✨ Features

- **Four platforms**: Bilibili (`bilibili.com` / `b23.tv` short links / BV IDs), Douyin (`douyin.com` / `v.douyin.com` / `iesdouyin.com` links / share text), YouTube (`youtube.com` / `youtu.be` / Shorts, official or auto captions first) and Xiaohongshu / RedNote (`xiaohongshu.com` / `xhslink.com` video-note share links)
- **Subtitles first**: fetches official / AI subtitles from Bilibili (login optional) — instant transcripts at zero cost
- **Local ASR fallback**: when no subtitles exist, transcribes locally with faster-whisper (CPU int8; uses the hf-mirror inside mainland China). **No paid API calls**
- **Key-frame screenshots**: anchored on transcript timestamps and extracted with PyAV; a local quality check flags black / blank / blurry frames (white-background slides are not misjudged), and the agent makes the final visual call
- **Douyin without login**: accesses public videos as a guest, no account cookie needed; the video is used only for local transcription and can be cleaned up afterwards
- **Xiaohongshu without login**: parses the data embedded in the share page and downloads video notes directly — no signature, no account (video notes only; image notes and comments are not supported)
- **Lecture-note template**: sections, bolded key terms, real code references, a mind map, concept cards, self-test questions, and a collapsible video time index with deep links back to the video

## 🧩 Workflow

```
Video link (Bilibili / Douyin / YouTube / Xiaohongshu)
        │
        ▼
 Step 1  Fetch metadata + subtitles (Bilibili / YouTube) / download video (Douyin / Xiaohongshu)
        │
        ├── subtitles found ──► use them directly
        └── no subtitles ─────► Step 2  local faster-whisper ASR
        │
        ▼
 Step 2.5 (optional)  key-frame screenshots (PyAV)
        │
        ▼
 Step 3  AI writes the notes from the template → validated by a quality gate
```

## ⚡ Quick Start

```bash
# One-line install (Claude Code; for other agents, use their skills directory)
git clone https://github.com/lucky-Luor/bilibili-douyin-notes.git ~/.claude/skills/bilibili-douyin-notes && pip install -r ~/.claude/skills/bilibili-douyin-notes/requirements.txt
```

Run the environment check once and fix anything it marks as failed:

```bash
python ~/.claude/skills/bilibili-douyin-notes/scripts/doctor.py
```

Then just ask your agent: **"Make lecture notes from this video: https://www.bilibili.com/video/BVxxxx"**.

## 📦 Installation

This skill follows the **Agent Skills spec** (`SKILL.md`) and works with any compatible AI agent. Supported locations:

- `~/.claude/skills/` — Claude Code
- `~/.zcode/skills/` — ZCode
- `~/.agents/skills/` — shared skills path for Codex and other agents

> 🧩 **DeepSeek Harness (DSH) users**: install the plugin edition [dsh-video-notes](https://github.com/lucky-Luor/dsh-video-notes) with one command and get updates with the plugin:
> `npx -y @deepseek-ai/dsh plugin --profile web add github:lucky-Luor/dsh-video-notes`

```bash
# 1. Clone into your agent's skills directory
git clone https://github.com/lucky-Luor/bilibili-douyin-notes.git ~/.claude/skills/bilibili-douyin-notes

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. (Optional) Log in to Bilibili by QR code to get AI subtitles
python scripts/bili_fetch.py --login

# 4. Environment check: ✅ / ⚠️ / ❌ per item with fix commands (--platform douyin for Douyin only, --json for agents)
python scripts/doctor.py
```

| Component | Purpose | Required? |
|---|---|---|
| `faster-whisper` | ASR fallback | For videos without subtitles |
| `av` (PyAV) | Audio decoding / frame extraction | Same as above |
| `gmssl` | Douyin a_bogus signature (SM3) | Douyin links only |
| `yt-dlp` / `youtube-transcript-api` | YouTube metadata, captions and audio (deno recommended) | YouTube links only |
| `numpy` / `pillow` | Screenshot quality check | Screenshot step only |

### Using Chinese models in Claude Code

Claude is not available in mainland China. Claude Code can use Chinese models instead, with no change to this skill: all three providers offer an Anthropic-compatible endpoint. Set the base URL and put your key in `ANTHROPIC_AUTH_TOKEN` (do not also set `ANTHROPIC_API_KEY`):

| Provider | `ANTHROPIC_BASE_URL` | Official guide (check model names there) |
|---|---|---|
| DeepSeek | `https://api.deepseek.com/anthropic` | [Integrate with Claude Code](https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/) |
| Kimi | `https://api.moonshot.cn/anthropic` | [Claude Code with Kimi](https://platform.kimi.com/docs/guide/claude-code-kimi) |
| Zhipu GLM | `https://open.bigmodel.cn/api/anthropic` | [Claude API compatibility](https://docs.bigmodel.cn/cn/guide/develop/claude/introduction) |

Model names change quickly, so set `ANTHROPIC_MODEL` and related variables according to the official guides. Note quality depends on the model; the `validate_note.py` quality gate keeps the format in check.

## ⚙️ Configuration

Copy `config.example.json` to `config.json` in the same directory. `SESSDATA` (Bilibili login cookie) is optional and is filled in automatically by `--login`. `config.json` is git-ignored — never share it.

ASR tuning: `asr_model` (tiny ~ large-v3, falls back one size automatically on failure), `asr_device` (auto / cpu / cuda, falls back to cpu), `asr_cpu_threads` (0 = auto).

## 🚀 Usage

Talk to your agent:

> Make lecture notes from this video: https://www.bilibili.com/video/BVxxxx
>
> Summarize this Douyin video: https://v.douyin.com/xxxx/
>
> Make notes from this YouTube video: https://www.youtube.com/watch?v=xxxx

Or run the scripts by hand (see [SKILL.md](SKILL.md) for details):

```bash
python scripts/bili_fetch.py --probe "<url>"                 # metadata only
python scripts/bili_fetch.py "<url>" "<outdir>"              # Bilibili: metadata + subtitles
python scripts/douyin_fetch.py "<url or share text>" "<outdir>"  # Douyin: metadata + video
python scripts/yt_fetch.py "<url>" "<outdir>"  # YouTube: metadata + captions (--download for audio)
python scripts/xhs_fetch.py "<share link or text>" "<outdir>"  # Xiaohongshu: metadata + video (link must carry xsec_token)
python scripts/bili_transcribe.py "<outdir>/metadata.json"  # local ASR when no subtitles
python scripts/note_nav.py "<note.md>" --template v4 --meta "<outdir>/metadata.json" --txt-dir "<outdir>"       # fill deep links
python scripts/validate_note.py "<note.md>" --template v4 --meta "<outdir>/metadata.json" --txt-dir "<outdir>"  # quality gate
```

## ❓ FAQ

<details>
<summary><b>What do I need?</b></summary>

Python 3.10+ and any AI agent that supports the Agent Skills (`SKILL.md`) spec, such as Claude Code, Codex or ZCode. No GPU and no paid API required. Not sure your setup is complete? Run `python scripts/doctor.py`.
</details>

<details>
<summary><b>Do I have to log in to Bilibili?</b></summary>

No. Official CC subtitles work without login, and local ASR kicks in when there are none. Logging in by QR code unlocks Bilibili AI subtitles, which lets most videos skip ASR entirely. Douyin and Xiaohongshu need no login at all; YouTube usually doesn't either — add cookies / a proxy only if it blocks you.
</details>

<details>
<summary><b>YouTube says "Sign in to confirm you're not a bot"?</b></summary>

YouTube blocks some server IPs. Pass `--cookies-from-browser chrome` (or `--cookies cookies.txt`) to `yt_fetch.py`, add `--proxy` if needed, keep `yt-dlp` up to date and install deno.
</details>

<details>
<summary><b>Xiaohongshu link fails / mentions xsec_token?</b></summary>

Use the full share link (App → Share → Copy link) that carries `xsec_token`; a bare note ID won't work. Tokens expire — just copy the link again. Only video notes are supported.
</details>

<details>
<summary><b>ASR is too slow?</b></summary>

The default `small` model runs at about 0.2× the video length on a 6-core laptop CPU (a 26-minute lecture takes about 4 minutes). If you are in a hurry, set `asr_model` to `base` in `config.json`: about 3× faster, but noticeably less accurate (only 0.72–0.79 text agreement with `small` in our test). Set `asr_device: "cuda"` if you have an NVIDIA GPU. You can also transcribe a single part first (`--page 1`) to estimate the time.
</details>

<details>
<summary><b>Does it work for English videos?</b></summary>

Yes — faster-whisper is multilingual. Note that the note template and examples are written in Chinese, so ask your agent explicitly if you want the notes in English.
</details>

## 📜 Usage Notice & Data Flow

- **Personal study only**: this tool turns public courses into your own study notes. Do not use it to repost, redistribute or for any commercial purpose; follow each platform's terms of service and respect creators' copyright.
- **Public content only**: paid / premium content and private favorites are not supported and are not planned; the Bilibili SESSDATA cookie is only an optional fallback for subtitles.
- **Where your data goes**:
  - Fetching, ASR and screenshots all run on your own machine; this project collects no usage data and uploads nothing (it has no server).
  - Downloaded video / audio is used only for local ASR and screenshots, and can be removed with `clean_media.py` afterwards (the agent asks you first).
  - Screenshots stay local unless you configure OSS yourself and run `upload_images.py --apply`, which uploads to **your own** bucket.
  - Notes are written by your own AI agent; subtitles and transcripts are sent to whatever model service that agent uses, under its own rules.
- **Platform notices**: if a platform raises a notice or objection, the maintainer will take the related module offline within 48 hours and explain it in this README.

## 🛡️ Platform Support Tiers

This project is maintained by one person, so Bilibili comes first.

| Tier | Platforms | Commitment |
|---|---|---|
| Tier 1 | Bilibili | Fixed **within 7 days** after a platform change breaks it; real-link smoke tests run twice a week and before every release |
| Tier 2 | YouTube, Douyin, Xiaohongshu | **Best effort**: breakages are confirmed and tracked in issues, with no fixed timeline; Douyin's signature and Xiaohongshu's page structure are controlled by the platforms and may break temporarily |

When something fails, the scripts print a three-part message (cause / impact / next step) and tell you whether the link itself is the problem (just use another link) or the platform changed (wait for a fix). Please open an issue with the link and that message.

## ⚠️ Known Limitations

- Videos without subtitles need local ASR: measured at about 0.2× the video length on a 6-core laptop CPU (CPU int8; older CPUs are slower), faster with an NVIDIA GPU
- ASR may garble technical terms — use `scripts/apply_glossary.py` to correct them
- Douyin depends on the platform's signature algorithm and may break temporarily when the platform updates (detail API returns 403)
- YouTube may block server / cloud IPs (cookies or a proxy needed); keep `yt-dlp` updated
- Xiaohongshu relies on the share page's embedded data and may break when the site changes; frequent requests trigger captchas (461/471); video notes only
- Only publicly accessible videos are supported

## 📄 License

[MIT](LICENSE). **Exception:** `scripts/abogus.py` (Douyin signature, GPLv3) is **not** included in this repository; `scripts/ensure_abogus.py` downloads it from a pinned upstream commit with SHA-256 verification on first use. See the [Chinese README](README.md#-许可证) for details.

## 🙏 Acknowledgements

[JoeanAmier/TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader) · [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) · [JefferyHcool/BiliNote](https://github.com/JefferyHcool/BiliNote) · [youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api) · [yt-dlp](https://github.com/yt-dlp/yt-dlp) · [ReaJason/xhs](https://github.com/ReaJason/xhs) · [XHS-Downloader](https://github.com/JoeanAmier/XHS-Downloader) (ideas only) · [faster-whisper](https://github.com/SYSTRAN/faster-whisper)

<div align="center">

**If this skill saves you note-taking time, a ⭐ Star would mean a lot!**

Got notes you're happy with? Share them in [Issues](https://github.com/lucky-Luor/bilibili-douyin-notes/issues) — bug reports and ideas are welcome too 🙌

</div>
