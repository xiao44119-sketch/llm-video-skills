---
name: jianying-draft-export
description: >-
  Generate, export, or convert finished videos (or code-rendered videos) into native, self-contained JianYing Pro (剪映 / CapCut) project drafts, and automatically launch JianYing to open the project immediately with zero missing-media prompts. Use when the user wants to export a video, script, or timeline to JianYing, create a JianYing project from a video, or automate draft generation.
---

# JianYing Pro (剪映) Native Draft Export Skill

This skill provides an automated, hardware-native bridge to generate and deploy production-grade **JianYing Pro macOS (11.4+)** project drafts directly from video files, audio tracks, and subtitle scripts.

It completely solves the **"媒体丢失 / 暂无访问权限"** sandbox prompt by packaging projects under the **Self-Contained Draft Architecture (自包含草稿架构)**.

---

## Quick Reference

### 1. One-Line CLI Export & Instant Open

```bash
python3 scripts/export_draft.py \
  --video "/path/to/my_video.mp4" \
  --name "临沂森兴科技_GEO三角咬合被动获客" \
  --cover "/path/to/cover.jpg" \
  --subtitles "/path/to/subtitles.json" \
  --open
```

| Parameter | Short | Required | Description |
| :--- | :--- | :--- | :--- |
| `--video` | `-v` | **Yes** | Absolute path to the source video file |
| `--name` | `-n` | No | Project name in JianYing (defaults to video filename) |
| `--cover` | `-c` | No | Cover thumbnail image (`.jpg` / `.png`) |
| `--subtitles` | `-s` | No | Subtitle file (`.json` or `.srt`) |
| `--duration` | `-d` | No | Video duration in seconds (auto-probed if omitted) |
| `--open` | `-o` | No | Immediately restart/launch JianYing Pro and open draft |

---

## 2. Python API Usage

```python
from pathlib import Path
import sys

# Add skill scripts directory
sys.path.insert(0, "/path/to/jianying-draft-export/scripts")
from draft_builder import JianyingDraft

# 1. Initialize draft
draft = JianyingDraft(name="我的高维IP短视频", width=1920, height=1080)

# 2. Set video source (probes duration automatically)
draft.set_video("/path/to/rendered_video.mp4")

# 3. Attach cover image
draft.set_cover("/path/to/cover.jpg")

# 4. Add subtitles programmatically with dual-color highlighting
draft.add_subtitle(
    text="大模型底层有严格的防作弊机制。单一维度的自吹自擂，在算法眼里统统叫‘孤证’。",
    start_sec=8.13,
    end_sec=16.46,
    highlight="孤证"  # Highlight word will be rendered in pure gold [1.0, 0.84, 0.0] with black strokes
)

# Or load from an external JSON / SRT file:
# draft.load_subtitles("/path/to/subtitles.json")

# 5. Compile, auto-copy to self-contained draft, and launch app
draft.save_and_inject(open_app=True)
```

---

## 3. Subtitle JSON Format

The subtitle parser accepts either standard `.srt` files or a JSON list:

```json
[
  {
    "start": 0.0,
    "end": 7.68,
    "text": "2026年，实体店最残酷的现状，不是隔壁在降价，而是顾客问 AI 附近哪家好时，你的店根本不存在。",
    "hl": "根本不存在"
  },
  {
    "start": 8.13,
    "end": 16.46,
    "text": "大模型底层有严格的防作弊机制。单一维度的自吹自擂，在算法眼里统统叫‘孤证’。孤证不立，直接剔除。",
    "hl": "孤证不立"
  }
]
```

- `hl`: Optional substring that will be given high-contrast gold highlight styling with thick black borders.

---

## 4. Architecture: Why macOS Sandbox Needs "Self-Contained Drafts"

macOS App Sandbox restricts `VideoFusion-macOS.app` from accessing files in `~/Desktop/`, `~/.gemini/`, or arbitrary directories.

```text
❌ External Path Mode (Broken):
~/Desktop/IP/video.mp4  ----->  JianYing Pro [Sandbox EACCES]  ----->  "媒体丢失/暂无访问权限" 弹窗

✅ Self-Contained Draft Mode (This Skill):
~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/<Project>/video.mp4
  └─> JianYing Pro [Full Native Read/Write]  ──>  Zero Prompt, Instant Playback!
```

See [macos_sandbox_bypass.md](./references/macos_sandbox_bypass.md) for deeper technical analysis.

---

## 5. Verification & Troubleshooting

To inspect whether a draft was created and decrypted properly:

```bash
python3 scripts/verify_draft.py "项目名称"
```

If `libjycodec.dylib` needs recompilation (e.g. on a new architecture):

```bash
bash scripts/compile_dylib.sh
```
