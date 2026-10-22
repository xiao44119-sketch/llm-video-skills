# macOS App Sandbox & Self-Contained Draft Architecture

## 1. The Core Issue: Why JianYing Prompts "Missing Media / Permission Denied"

On macOS, JianYing Pro (`VideoFusion-macOS.app`) is distributed and signed with **macOS App Sandbox** enabled:
```xml
<key>com.apple.security.app-sandbox</key>
<true/>
<key>com.apple.security.assets.movies.read-write</key>
<true/>
<key>com.apple.security.files.user-selected.read-write</key>
<true/>
```

### What this means:
- The app has unrestricted read-write access **only** to the user's `~/Movies` directory (where its draft database `~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/` lives).
- The app has **NO DIRECT ACCESS** to files located in `~/Desktop/`, `~/Downloads/`, `~/.gemini/`, or arbitrary disk paths unless the human user manually selects them through macOS's native `NSOpenPanel` (which creates a Security-Scoped Bookmark).
- When an automated script generates a video in `~/Desktop/` and writes that absolute path directly into `draft_info.json`, JianYing's process tries to open the path and gets an operating system error: `EACCES (Permission Denied)`.
- JianYing detects the file cannot be opened and prompts:
  `"检测到以下媒体丢失，请重新链接后再剪辑…… 暂无访问权限"`

---

## 2. The Solution: Self-Contained Draft (自包含草稿)

By copying media assets directly inside the draft's own folder under `~/Movies/...`:
```text
~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/<ProjectName>/
├── 04-senxing-geo-FINN-FULL.mp4      <-- Video stored locally inside draft
├── draft_cover.jpg                  <-- Cover image
├── draft_info.json                  <-- References internal path
├── draft_meta_info.json             <-- Contains file MD5 & metadata
└── Timelines/
    └── <timeline_uuid>/
        ├── draft_info.json
        └── draft_meta_info.json
```

### Advantages:
1. **Zero Permissions Warning**: Because the file resides within `~/Movies`, macOS kernel grants full read/write permission immediately.
2. **JianYing Internal Normalization**: JianYing automatically normalizes the path to its native relative placeholder:
   `##_draftpath_placeholder_<UUID>_##/<filename>`
3. **Portability**: The entire draft folder can be copied, archived, or shared between machines without broken absolute paths.
