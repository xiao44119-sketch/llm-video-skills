#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Universal CLI for exporting any video to a native, self-contained JianYing Pro draft.
Zero-prompt, Sandbox-safe, instant-open!

Example usage:
  python3 scripts/export_draft.py \
    --video /path/to/my_video.mp4 \
    --name "我的高质感短视频" \
    --subtitles /path/to/subtitles.json \
    --cover /path/to/cover.jpg \
    --open
"""

import sys
import argparse
from pathlib import Path

# Add script directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from draft_builder import JianyingDraft

def main():
    parser = argparse.ArgumentParser(
        description="Compile and inject video/audio/subtitles into JianYing Pro macOS native draft."
    )
    parser.add_argument(
        "-v", "--video", required=True,
        help="Path to the input video file (.mp4, .mov, etc.)"
    )
    parser.add_argument(
        "-n", "--name", default=None,
        help="Draft project name in JianYing. Defaults to video filename without extension."
    )
    parser.add_argument(
        "-c", "--cover", default=None,
        help="Optional path to cover image (.jpg or .png)."
    )
    parser.add_argument(
        "-s", "--subtitles", default=None,
        help="Optional subtitle file (.srt or .json)."
    )
    parser.add_argument(
        "-d", "--duration", type=float, default=None,
        help="Optional duration in seconds (probed automatically if omitted)."
    )
    parser.add_argument(
        "-w", "--width", type=int, default=1920,
        help="Canvas width (default: 1920)"
    )
    parser.add_argument(
        "-H", "--height", type=int, default=1080,
        help="Canvas height (default: 1080)"
    )
    parser.add_argument(
        "-o", "--open", action="store_true",
        help="Launch JianYing Pro and open the project immediately."
    )

    args = parser.parse_args()

    video_path = Path(args.video).resolve()
    if not video_path.exists():
        print(f"❌ Error: Video file not found: {video_path}", file=sys.stderr)
        sys.exit(1)

    project_name = args.name or video_path.stem

    print(f"🎬 Preparing JianYing Draft: '{project_name}'...")
    draft = JianyingDraft(name=project_name, width=args.width, height=args.height)
    draft.set_video(video_path, duration_sec=args.duration)

    if args.cover:
        cover_path = Path(args.cover).resolve()
        if cover_path.exists():
            draft.set_cover(cover_path)
            print(f"🖼️ Attached cover: {cover_path.name}")

    if args.subtitles:
        sub_path = Path(args.subtitles).resolve()
        if sub_path.exists():
            draft.load_subtitles(sub_path)
            print(f"📝 Loaded {len(draft.subtitles)} subtitles from {sub_path.name}")

    draft_folder = draft.save_and_inject(open_app=args.open)
    print(f"✨ All done! Draft located at:\n   {draft_folder}")

if __name__ == "__main__":
    main()
