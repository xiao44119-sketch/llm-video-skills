#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Utility script to verify and inspect JianYing Pro draft files.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from draft_builder import decrypt_bytes, DRAFT_ROOT

def verify(draft_name_or_path: str):
    p = Path(draft_name_or_path)
    if not p.is_dir():
        p = DRAFT_ROOT / draft_name_or_path
    if not p.exists():
        print(f"❌ Draft folder not found: {p}")
        return False

    print(f"🔍 Inspecting draft: {p.name}")
    info_file = p / "draft_info.json"
    meta_file = p / "draft_meta_info.json"

    if not info_file.exists():
        print("❌ draft_info.json missing!")
        return False

    try:
        info_json = json.loads(decrypt_bytes(info_file.read_bytes()))
        print("✅ draft_info.json successfully decrypted!")
        print(f"   Duration: {info_json.get('duration', 0) / 1e6:.2f}s")
        videos = info_json.get("materials", {}).get("videos", [])
        print(f"   Video Tracks count: {len(videos)}")
        for v in videos:
            print(f"     Path: {v.get('path')}")
        texts = info_json.get("materials", {}).get("texts", [])
        print(f"   Subtitle segments: {len(texts)}")
    except Exception as e:
        print(f"❌ Failed to decrypt draft_info.json: {e}")
        return False

    if meta_file.exists():
        try:
            meta_json = json.loads(decrypt_bytes(meta_file.read_bytes()))
            print("✅ draft_meta_info.json successfully decrypted!")
            print(f"   Name: {meta_json.get('draft_name')}")
        except Exception as e:
            print(f"❌ Failed to decrypt draft_meta_info.json: {e}")

    return True

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "临沂森兴科技_GEO三角咬合被动获客"
    verify(target)
