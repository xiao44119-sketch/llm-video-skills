#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quickstart example demonstrating how to export a video with subtitles to JianYing Pro.
"""

import sys
from pathlib import Path

# Add scripts directory
SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
from draft_builder import JianyingDraft

def main():
    video_file = "/Users/mlx/Desktop/IP/code-video-works/output/04-senxing-geo-FINN-FULL.mp4"
    cover_file = "/Users/mlx/Desktop/IP/code-video-works/output/senxing-s01-ask-ai-phone-4.2s.png"
    subtitles_file = SKILL_ROOT / "examples" / "example_subtitles.json"

    # 1. Initialize draft
    draft = JianyingDraft(name="JianYing_Demo_QuickStart", width=1920, height=1080)

    # 2. Attach video, cover, and subtitles
    draft.set_video(video_file, duration_sec=35.88)
    draft.set_cover(cover_file)
    draft.load_subtitles(subtitles_file)

    # 3. Save, auto-copy to self-contained draft, and optionally launch app
    draft_folder = draft.save_and_inject(open_app=False)
    print(f"🎉 Created demo draft at: {draft_folder}")

if __name__ == "__main__":
    main()
