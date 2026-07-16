"""重建某个项目的 project.json + project.md。

用法:
  uv run python scripts/rebuild.py <项目目录或 project.json> [--en] [--reframe]

  --en       额外用 whisper task=translate 生成英文字幕轨（双语），写入 subtitle.en.srt
  --reframe  删除旧帧图片重新抽帧（迁移到 frame_{ms} 时间命名 / 调阈值后重抽）

适合：改了 markdown 逻辑后重新生成 md，给老项目补双语，或把旧命名帧迁移成时间命名。
"""
import json
import shutil
import sys
from pathlib import Path

import config
from v2md import markdown, subtitle, pipeline, frames
from v2md.models import Project, VideoAsset, SubtitleSegment, Frame


def main():
    args = sys.argv[1:]
    gen_en = "--en" in args
    reframe = "--reframe" in args
    target = next((a for a in args if not a.startswith("--")), None)
    if not target:
        print("用法: python scripts/rebuild.py <项目目录|project.json> [--en] [--reframe]")
        sys.exit(1)
    p = Path(target)
    workdir = p if p.is_dir() else p.parent
    jp = workdir / "project.json"
    if not jp.exists():
        print(f"找不到 {jp}")
        sys.exit(1)

    d = json.loads(jp.read_text(encoding="utf-8"))
    ad = d.get("asset") or {}
    asset = VideoAsset(
        video_path=workdir / (ad.get("video_path") or "video.mp4"),
        subtitle_path=workdir / ad["subtitle_path"] if ad.get("subtitle_path") else None,
        source_url=ad.get("source_url", ""),
        bvid=ad.get("bvid"),
        title=ad.get("title", ""),
        duration_s=ad.get("duration_s", 0.0),
        source_lang=d.get("source_lang"),
    )
    proj = Project(
        id=d.get("id", workdir.name),
        workdir=workdir,
        asset=asset,
        subtitles=[SubtitleSegment(**s) for s in d.get("subtitles", [])],
        subtitles_en=[SubtitleSegment(**s) for s in d.get("subtitles_en", [])],
        frames=[Frame(timestamp_s=f["timestamp_s"],
                      image_path=workdir / f["image_path"],
                      phash=f.get("phash", "")) for f in d.get("frames", [])],
        source_lang=d.get("source_lang"),
        secondary_lang=d.get("secondary_lang"),
    )
    if gen_en:
        proj.subtitles_en, proj.secondary_lang = subtitle.ensure_subtitle_secondary(asset, workdir)
    if reframe:
        fd = workdir / "frames"
        if fd.exists():
            shutil.rmtree(fd, ignore_errors=True)  # 删旧命名帧（pid_u*.jpg / manual_*.jpg）
        proj.frames = frames.extract_keyframes(asset, workdir=workdir)
    markdown.build(proj)
    pipeline.save_project_json(proj)
    print(f"[rebuild] {workdir.name}  frames={len(proj.frames)} "
          f"subs={len(proj.subtitles)} sec={len(proj.subtitles_en)} "
          f"src={proj.source_lang} sec_lang={proj.secondary_lang}")


if __name__ == "__main__":
    main()
