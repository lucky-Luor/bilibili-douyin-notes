#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-douyin-notes 收尾（可选）：清理输出目录中的媒体缓存。

用法:
    python clean_media.py <输出目录>            # 默认 dry-run：只列将删除的文件
    python clean_media.py <输出目录> --apply    # 实际删除

说明:
    - 抖音视频（metadata pages[].media_path）会永久保留在输出目录作为缓存，
      供截图/重转写复用；本脚本由 Agent 在笔记交付后询问用户、用户确认后调用 --apply
    - B站的临时音频/视频（.audio_*/.video_*）正常流程已自动删除，这里兜底清扫残留
    - 只删输出目录内、metadata 可辨识的媒体与已知临时文件，不做递归泛删
"""
import argparse
import json
import sys
from pathlib import Path

TEMP_PATTERNS = (".audio_*.m4s", ".video_*.m4s", "*.part")


def _reconfigure_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def collect(outdir: Path) -> list[Path]:
    """收集可清理的媒体文件（存在且是文件），去重排序。"""
    files: set[Path] = set()
    try:
        meta = json.loads((outdir / "metadata.json").read_text(encoding="utf-8"))
        for p in meta.get("pages", []):
            mp = p.get("media_path")
            if mp:
                f = outdir / mp
                if f.is_file():
                    files.add(f)
    except FileNotFoundError:
        pass  # 无 metadata：仍清扫已知临时文件
    except Exception as e:
        print(f"警告: metadata.json 解析失败（{e}），仅清扫已知临时文件", file=sys.stderr)
    for pat in TEMP_PATTERNS:
        files.update(f for f in outdir.glob(pat) if f.is_file())
    return sorted(files)


def main():
    _reconfigure_stdout()
    ap = argparse.ArgumentParser(description="清理输出目录中的媒体缓存（默认 dry-run）")
    ap.add_argument("outdir", help="笔记输出目录（含 metadata.json）")
    ap.add_argument("--apply", action="store_true",
                    help="实际删除（默认只列清单，不做任何改动）")
    args = ap.parse_args()
    outdir = Path(args.outdir)
    if not outdir.is_dir():
        raise SystemExit(f"目录不存在: {outdir}")

    files = collect(outdir)
    if not files:
        print_json_summary({"status": "ok", "mode": "apply" if args.apply else "dry-run",
                            "found": 0, "deleted": 0, "freed_mb": 0})
        return

    total = sum(f.stat().st_size for f in files)
    for f in files:
        print(f"  {f.name}  ({f.stat().st_size / 1048576:.1f} MB)", file=sys.stderr)

    deleted, freed = 0, 0
    if args.apply:
        for f in files:
            try:
                size = f.stat().st_size
                f.unlink()
                deleted += 1
                freed += size
            except OSError as e:
                print(f"警告: 删除失败 {f.name}: {e}", file=sys.stderr)

    print_json_summary({"status": "ok",
                        "mode": "apply" if args.apply else "dry-run",
                        "found": len(files),
                        "deleted": deleted,
                        "freed_mb": round(freed / 1048576, 1),
                        "pending_mb": round((total - freed) / 1048576, 1),
                        "files": [f.name for f in files]})


def print_json_summary(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=True))


if __name__ == "__main__":
    main()
