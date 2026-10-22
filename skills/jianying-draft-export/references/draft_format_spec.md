# JianYing Pro 11.4+ Draft Format Specification

## 1. Directory Structure

Each draft project lives under:
`~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/<ProjectName>/`

Key files:
- `draft_info.json`: Encrypted JSON containing tracks, timeline clips, materials, keyframes, canvas settings.
- `draft_meta_info.json`: Encrypted JSON containing draft registration, materials list, MD5 checksums, duration.
- `Timelines/<timeline_id>/`:
  - `draft_info.json`: Sub-timeline copy of draft info (required for 11.4+).
  - `draft_meta_info.json`: Sub-timeline copy of draft meta info.
  - `project.json`: Timeline configuration metadata.
- `draft_cover.jpg`: Cover image shown in the project picker grid.
- `root_meta_info.json` (at `com.lveditor.draft/root_meta_info.json`): Registry of all drafts.

## 2. Encryption Mechanism

Starting from version 11.4+, JianYing Pro encrypts `draft_info.json` and `draft_meta_info.json`.
- Implemented in `/Applications/VideoFusion-macOS.app/Contents/Frameworks/libvideoeditor.dylib`.
- Functions:
  - `_ZN4lvve12EncryptUtils6enableEb(void* self, bool enable)`
  - `_ZN4lvve12EncryptUtils7decryptERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEES9_Rb`
  - `_ZN4lvve12EncryptUtils7encryptERKNSt3__112basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE`
- Bound by `bin/libjycodec.dylib` and accessed via `scripts/draft_builder.py`.

## 3. Subtitle Multi-Style Format

Subtitles support per-range styling in `materials.texts[i].content`:
```json
{
  "styles": [
    {
      "range": [0, 5],
      "strokes": [{"width": 0.06, "content": {"solid": {"color": [0.0, 0.0, 0.0]}, "render_type": "solid"}}],
      "fill": {"content": {"solid": {"color": [1.0, 1.0, 1.0]}, "render_type": "solid"}},
      "size": 7.5,
      "useLetterColor": true
    },
    {
      "range": [5, 10],
      "strokes": [{"width": 0.06, "content": {"solid": {"color": [0.0, 0.0, 0.0]}, "render_type": "solid"}}],
      "fill": {"content": {"solid": {"color": [1.0, 0.84, 0.0]}, "render_type": "solid"}},
      "size": 7.5,
      "useLetterColor": true
    }
  ],
  "text": "示例字幕文本高亮"
}
```
