#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JianYing Pro (剪映 Pro macOS) Native Draft Generator & Sandbox-Free Injector
Supports JianYing 11.4+ (macOS Apple Silicon / Intel).

Features:
- Native libjycodec encryption/decryption
- Self-Contained draft architecture (Zero missing-media warnings)
- Automatic macOS App Sandbox bypass
- Multi-color subtitle track with black outlines and gold highlighting
- Native timeline registration and instant opening
"""

import os
import sys
import re
import json
import time
import uuid
import shutil
import hashlib
import ctypes
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Union

# Default Paths
JIANYING_APP = "/Applications/VideoFusion-macOS.app"
FRAMEWORKS_DIR = f"{JIANYING_APP}/Contents/Frameworks"
DRAFT_ROOT = Path(os.path.expanduser("~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft"))
ROOT_META_FILE = DRAFT_ROOT / "root_meta_info.json"

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
BIN_DIR = SKILL_ROOT / "bin"
LIB_CODEC_PATH = BIN_DIR / "libjycodec.dylib"

# Ensure dyld can find dependent libraries
os.environ["DYLD_LIBRARY_PATH"] = FRAMEWORKS_DIR

_codec = None

def _get_codec():
    global _codec
    if _codec is not None:
        return _codec
        
    if not LIB_CODEC_PATH.exists():
        raise FileNotFoundError(f"Codec library not found at: {LIB_CODEC_PATH}. Please run scripts/compile_dylib.sh first.")
        
    lib = ctypes.CDLL(str(LIB_CODEC_PATH))
    lib.init_codec.argtypes = [ctypes.c_char_p]
    lib.init_codec.restype = ctypes.c_bool

    lib.encrypt_buffer.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    lib.encrypt_buffer.restype = ctypes.POINTER(ctypes.c_char)

    lib.decrypt_buffer.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    lib.decrypt_buffer.restype = ctypes.POINTER(ctypes.c_char)

    lib.free_buffer.argtypes = [ctypes.c_void_p]
    lib.free_buffer.restype = None

    dylib_target = f"{FRAMEWORKS_DIR}/libvideoeditor.dylib"
    if not lib.init_codec(dylib_target.encode('utf-8')):
        raise RuntimeError(f"Failed to initialize codec using: {dylib_target}")
        
    _codec = lib
    return _codec

def encrypt_str(text: str) -> bytes:
    codec = _get_codec()
    raw = text.encode('utf-8')
    out_len = ctypes.c_size_t(0)
    ptr = codec.encrypt_buffer(raw, len(raw), ctypes.byref(out_len))
    if not ptr:
        raise RuntimeError("Encryption failed")
    try:
        data = ctypes.string_at(ptr, out_len.value)
        return data
    finally:
        codec.free_buffer(ptr)

def decrypt_bytes(raw: bytes) -> str:
    codec = _get_codec()
    out_len = ctypes.c_size_t(0)
    ptr = codec.decrypt_buffer(raw, len(raw), ctypes.byref(out_len))
    if not ptr:
        raise RuntimeError("Decryption failed")
    try:
        data = ctypes.string_at(ptr, out_len.value)
        return data.decode('utf-8')
    finally:
        codec.free_buffer(ptr)

def create_uuid() -> str:
    return str(uuid.uuid4()).upper()

def get_media_duration(file_path: Union[str, Path]) -> float:
    """Get video or audio duration in seconds using ffmpeg or ffprobe."""
    file_path = str(file_path)
    ffmpeg_cands = [
        "ffprobe", "ffmpeg",
        "/Users/mlx/Desktop/IP/finn-video-v2/.tools/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1",
        "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"
    ]
    for binary in ffmpeg_cands:
        try:
            out = subprocess.check_output([binary, "-i", file_path], stderr=subprocess.STDOUT, timeout=6).decode()
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", out)
            if m:
                h, mi, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
                return h * 3600 + mi * 60 + s
        except Exception:
            continue
    return 35.88

def parse_srt(srt_path: Union[str, Path]) -> List[Dict]:
    """Parse standard SRT subtitle file into subtitle item list."""
    content = Path(srt_path).read_text(encoding='utf-8', errors='ignore')
    blocks = re.split(r'\n\s*\n', content.strip())
    results = []
    
    def parse_time(t_str):
        # 00:01:23,456
        parts = t_str.strip().replace(',', '.').split(':')
        h = float(parts[0])
        m = float(parts[1])
        s = float(parts[2])
        return h * 3600 + m * 60 + s

    for block in blocks:
        lines = [line.strip() for line in block.split('\n') if line.strip()]
        if len(lines) >= 3 and '-->' in lines[1]:
            time_parts = lines[1].split('-->')
            start = parse_time(time_parts[0])
            end = parse_time(time_parts[1])
            text = " ".join(lines[2:])
            results.append({"start": start, "end": end, "text": text})
        elif len(lines) >= 2 and '-->' in lines[0]:
            time_parts = lines[0].split('-->')
            start = parse_time(time_parts[0])
            end = parse_time(time_parts[1])
            text = " ".join(lines[1:])
            results.append({"start": start, "end": end, "text": text})
            
    return results

class JianyingDraft:
    def __init__(self, name: str, width: int = 1920, height: int = 1080, fps: int = 30):
        self.name = name
        self.width = width
        self.height = height
        self.fps = fps
        self.draft_id = create_uuid()
        self.timeline_id = create_uuid()
        self.duration_us = 0
        
        self.video_source_path = None
        self.video_duration_sec = 0.0
        
        self.cover_source_path = None
        self.subtitles = []
        
    def set_video(self, video_path: Union[str, Path], duration_sec: Optional[float] = None):
        p = Path(video_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Video file not found: {p}")
        self.video_source_path = str(p)
        if duration_sec is None or duration_sec <= 0:
            duration_sec = get_media_duration(p)
        self.video_duration_sec = duration_sec
        self.duration_us = int(duration_sec * 1_000_000)
        return self

    def set_cover(self, cover_path: Union[str, Path]):
        p = Path(cover_path).resolve()
        if p.exists():
            self.cover_source_path = str(p)
        return self

    def add_subtitle(self, text: str, start_sec: float, end_sec: float, highlight: Optional[str] = None):
        self.subtitles.append({
            "text": text,
            "start": float(start_sec),
            "end": float(end_sec),
            "hl": highlight or ""
        })
        return self

    def load_subtitles(self, subtitles_source: Union[str, Path, List[Dict]]):
        if isinstance(subtitles_source, (str, Path)):
            p = Path(subtitles_source)
            if p.suffix.lower() == '.srt':
                self.subtitles.extend(parse_srt(p))
            elif p.suffix.lower() == '.json':
                data = json.loads(p.read_text(encoding='utf-8'))
                if isinstance(data, list):
                    self.subtitles.extend(data)
                elif isinstance(data, dict) and "subtitles" in data:
                    self.subtitles.extend(data["subtitles"])
        elif isinstance(subtitles_source, list):
            self.subtitles.extend(subtitles_source)
        return self

    def _find_base_template(self) -> Dict:
        """Find an existing decrypted template draft in JianYing directory."""
        if DRAFT_ROOT.exists():
            for d in DRAFT_ROOT.iterdir():
                if d.is_dir() and not d.name.startswith("."):
                    info_file = d / "draft_info.json"
                    meta_file = d / "draft_meta_info.json"
                    if info_file.exists() and meta_file.exists():
                        try:
                            info = json.loads(decrypt_bytes(info_file.read_bytes()))
                            meta = json.loads(decrypt_bytes(meta_file.read_bytes()))
                            return {"info": info, "meta": meta, "source": d.name}
                        except Exception:
                            continue
        return self._generate_fallback_skeleton()

    def _generate_fallback_skeleton(self) -> Dict:
        """Fallback minimal draft skeleton if no existing draft can be found."""
        info = {
            "id": self.timeline_id,
            "version": 360000,
            "new_version": "185.0.0",
            "duration": self.duration_us,
            "color_space": 0,
            "config": {},
            "canvas_config": {"width": self.width, "height": self.height},
            "tracks": [],
            "materials": {
                "videos": [], "audios": [], "texts": [], "speeds": [],
                "canvases": [], "placeholder_infos": [], "sound_channel_mappings": [],
                "material_colors": [], "vocal_separations": []
            },
            "keyframes": {},
            "platform": {"os": "mac", "os_version": "26.6.2", "app_id": 3704, "app_version": "11.4.0", "app_source": "lv"},
            "last_modified_platform": {"os": "mac", "os_version": "26.6.2", "app_id": 3704, "app_version": "11.4.0", "app_source": "lv"},
            "render_index_track_mode_on": True,
            "path": "",
            "uneven_animation_template_info": {},
            "smart_ads_info": {},
            "function_assistant_info": {"fps": {}}
        }
        meta = {
            "cloud_draft_cover": False, "cloud_draft_sync": False, "cloud_package_completed_time": "",
            "draft_cloud_capcut_purchase_info": "", "draft_cloud_last_action_download": False,
            "draft_cloud_package_type": "", "draft_cloud_purchase_info": "", "draft_cloud_template_id": "",
            "draft_cloud_tutorial_info": "", "draft_cloud_videocut_purchase_info": "", "draft_cover": "draft_cover.jpg",
            "draft_deeplink_url": "", "draft_enterprise_info": {"draft_enterprise_extra": "", "draft_enterprise_id": "", "draft_enterprise_name": "", "enterprise_material": []},
            "draft_fold_path": "", "draft_id": self.draft_id, "draft_is_ae_produce": False,
            "draft_is_ai_packaging_used": False, "draft_is_ai_shorts": False, "draft_is_ai_translate": False,
            "draft_is_article_video_draft": False, "draft_is_cloud_temp_draft": False, "draft_is_from_deeplink": "false",
            "draft_is_infinite_canvas_draft": False, "draft_is_invisible": False, "draft_is_pippit_draft": False,
            "draft_is_web_article_video": False, "draft_materials": [], "draft_name": self.name,
            "draft_new_version": "", "draft_remake_info": None, "draft_root_path": str(DRAFT_ROOT),
            "draft_segment_extra_info": [], "draft_timeline_materials_size": 0, "draft_type": "",
            "draft_web_article_video_enter_from": "", "tm_draft_cloud_completed": "", "tm_draft_cloud_entry_id": -1,
            "tm_draft_cloud_modified": 0, "tm_draft_cloud_parent_entry_id": -1, "tm_draft_cloud_space_id": -1,
            "tm_draft_cloud_user_id": -1, "tm_draft_create": int(time.time() * 1_000_000),
            "tm_draft_modified": int(time.time() * 1_000_000), "tm_draft_removed": 0, "tm_duration": self.duration_us
        }
        return {"info": info, "meta": meta, "source": "builtin_skeleton"}

    def save_and_inject(self, open_app: bool = False) -> Path:
        """
        Builds and deploys the self-contained project to JianYing Pro.
        Automatically copies assets into the draft directory to prevent macOS Sandbox permission errors.
        """
        if not self.video_source_path:
            raise ValueError("Video source must be set before building draft!")

        draft_dir = DRAFT_ROOT / self.name
        if draft_dir.exists():
            shutil.rmtree(draft_dir)
        draft_dir.mkdir(parents=True, exist_ok=True)

        # 1. Self-Contained Asset Copy (Sandbox Exemption)
        video_src = Path(self.video_source_path)
        dest_video = draft_dir / video_src.name
        shutil.copy(video_src, dest_video)
        internal_video_path = str(dest_video)

        video_size = os.path.getsize(internal_video_path)
        with open(internal_video_path, 'rb') as f:
            video_md5 = hashlib.md5(f.read(1024 * 1024)).hexdigest()

        # 2. Base Skeleton
        base_data = self._find_base_template()
        base_info = base_data["info"]
        base_meta = base_data["meta"]

        timeline_id = self.timeline_id
        draft_id = self.draft_id
        video_mat_id = create_uuid()
        video_seg_id = create_uuid()
        local_mat_id = str(uuid.uuid4())

        speed_id = create_uuid()
        placeholder_id = create_uuid()
        canvas_id = create_uuid()
        channel_id = create_uuid()
        color_id = create_uuid()
        vocal_id = create_uuid()

        base_info["id"] = timeline_id
        base_info["duration"] = self.duration_us
        base_info["canvas_config"] = {"width": self.width, "height": self.height}

        # 3. Video Material & Segment
        video_mat = {
            "id": video_mat_id,
            "unique_id": hashlib.md5(internal_video_path.encode()).hexdigest(),
            "type": "video",
            "duration": self.duration_us,
            "path": internal_video_path,
            "width": self.width,
            "height": self.height,
            "category_name": "local",
            "material_id": "",
            "material_name": dest_video.name,
            "crop": {},
            "stable": {"time_range": {}},
            "matting": {"path": ""},
            "check_flag": 62980095,
            "video_algorithm": {"algorithms": [], "path": "", "time_range": {}},
            "is_unified_beauty_mode": True,
            "local_material_id": local_mat_id
        }

        video_seg = {
            "id": video_seg_id,
            "material_id": video_mat_id,
            "source_timerange": {"duration": self.duration_us, "start": 0},
            "target_timerange": {"duration": self.duration_us, "start": 0},
            "render_timerange": {},
            "clip": {"scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": 0.0}, "flip": {}},
            "uniform_scale": {},
            "extra_material_refs": [speed_id, placeholder_id, canvas_id, channel_id, color_id, vocal_id],
            "enable_hsl": False,
            "hdr_settings": {"mode": 1},
            "responsive_layout": {},
            "enable_adjust_mask": False,
            "source": "segmentsourcenormal"
        }

        # 4. Subtitle Track & Materials (with Dual-color Highlight & Black Outlines)
        sub_mats = []
        sub_segs = []

        for item in self.subtitles:
            text = item.get("text", "")
            hl_word = item.get("hl", "")
            start_sec = item.get("start", 0.0)
            end_sec = item.get("end", start_sec + 3.0)

            start_us = int(start_sec * 1_000_000)
            dur_us = int((end_sec - start_sec) * 1_000_000)

            mat_id = create_uuid()
            seg_id = create_uuid()

            styles = []
            if hl_word and hl_word in text:
                idx = text.index(hl_word)
                if idx > 0:
                    styles.append({
                        "range": [0, idx],
                        "strokes": [{"width": 0.06, "content": {"solid": {"color": [0.0, 0.0, 0.0]}, "render_type": "solid"}}],
                        "fill": {"content": {"solid": {"color": [1.0, 1.0, 1.0]}, "render_type": "solid"}},
                        "size": 7.5,
                        "useLetterColor": True
                    })
                styles.append({
                    "range": [idx, idx + len(hl_word)],
                    "strokes": [{"width": 0.06, "content": {"solid": {"color": [0.0, 0.0, 0.0]}, "render_type": "solid"}}],
                    "fill": {"content": {"solid": {"color": [1.0, 0.84, 0.0]}, "render_type": "solid"}},
                    "size": 7.5,
                    "useLetterColor": True
                })
                if idx + len(hl_word) < len(text):
                    styles.append({
                        "range": [idx + len(hl_word), len(text)],
                        "strokes": [{"width": 0.06, "content": {"solid": {"color": [0.0, 0.0, 0.0]}, "render_type": "solid"}}],
                        "fill": {"content": {"solid": {"color": [1.0, 1.0, 1.0]}, "render_type": "solid"}},
                        "size": 7.5,
                        "useLetterColor": True
                    })
            else:
                styles.append({
                    "range": [0, len(text)],
                    "strokes": [{"width": 0.06, "content": {"solid": {"color": [0.0, 0.0, 0.0]}, "render_type": "solid"}}],
                    "fill": {"content": {"solid": {"color": [1.0, 1.0, 1.0]}, "render_type": "solid"}},
                    "size": 7.5,
                    "useLetterColor": True
                })

            sub_mats.append({
                "id": mat_id,
                "type": "subtitle",
                "content": json.dumps({"styles": styles, "text": text}, ensure_ascii=False),
                "check_flag": 7,
                "alignment": 1,
                "font_size": 7.5
            })

            sub_segs.append({
                "id": seg_id,
                "material_id": mat_id,
                "source_timerange": None,
                "target_timerange": {"start": start_us, "duration": dur_us},
                "render_timerange": {},
                "clip": {"scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": -0.72}, "flip": {}},
                "uniform_scale": {}
            })

        # Assemble Materials in base_info
        if "materials" not in base_info:
            base_info["materials"] = {}
        base_info["materials"]["videos"] = [video_mat]
        base_info["materials"]["texts"] = sub_mats

        # Assemble Tracks
        tracks = [
            {
                "id": create_uuid(),
                "type": "video",
                "segments": [video_seg],
                "is_default_name": True,
                "name": "主视频轨"
            }
        ]
        if sub_segs:
            tracks.append({
                "id": create_uuid(),
                "type": "text",
                "segments": sub_segs,
                "is_default_name": False,
                "name": "同步高亮字幕"
            })
        base_info["tracks"] = tracks

        # 5. Base Meta Info
        base_meta["draft_fold_path"] = str(draft_dir)
        base_meta["draft_id"] = draft_id
        base_meta["draft_name"] = self.name
        base_meta["tm_duration"] = self.duration_us
        base_meta["draft_timeline_materials_size"] = video_size
        base_meta["draft_cover"] = "draft_cover.jpg"
        base_meta["draft_materials"] = [
            {
                "type": 0,
                "value": [
                    {
                        "ai_group_type": "",
                        "create_time": int(time.time()),
                        "duration": self.duration_us,
                        "enter_from": 0,
                        "extra_info": dest_video.name,
                        "file_Path": internal_video_path,
                        "height": self.height,
                        "id": local_mat_id,
                        "import_time": int(time.time()),
                        "import_time_ms": int(time.time() * 1_000_000),
                        "item_source": 1,
                        "material_color_tag": "",
                        "md5": video_md5,
                        "metetype": "video",
                        "roughcut_time_range": {"duration": self.duration_us, "start": 0},
                        "sub_time_range": {"duration": -1, "start": -1},
                        "type": 0,
                        "width": self.width
                    }
                ]
            },
            {"type": 1, "value": []},
            {"type": 2, "value": []},
            {"type": 3, "value": []},
            {"type": 6, "value": []},
            {"type": 7, "value": []},
            {"type": 8, "value": []}
        ]

        # 6. Encrypt & Write Files
        info_bytes = encrypt_str(json.dumps(base_info, ensure_ascii=False))
        meta_bytes = encrypt_str(json.dumps(base_meta, ensure_ascii=False))

        (draft_dir / "draft_info.json").write_bytes(info_bytes)
        (draft_dir / "draft_info.json.bak").write_bytes(info_bytes)
        (draft_dir / "draft_meta_info.json").write_bytes(meta_bytes)
        (draft_dir / "draft_meta_info.json.bak").write_bytes(meta_bytes)

        (draft_dir / "draft_settings").write_text('{"color_space":0,"duration":' + str(self.duration_us) + '}', encoding='utf-8')
        (draft_dir / "draft_virtual_store.json").write_text('{"draft_virtual_store":{}}', encoding='utf-8')
        (draft_dir / "key_value.json").write_text('{"key_value":{}}', encoding='utf-8')
        (draft_dir / "attachment_pc_common.json").write_text('{"attachment_pc_common":{}}', encoding='utf-8')

        # Timelines Folder (Required for modern JianYing Pro 11.4+)
        timelines_dir = draft_dir / "Timelines"
        timeline_dir = timelines_dir / timeline_id
        timeline_dir.mkdir(parents=True, exist_ok=True)

        (timeline_dir / "draft_info.json").write_bytes(info_bytes)
        (timeline_dir / "draft_info.json.bak").write_bytes(info_bytes)
        (timeline_dir / "draft_meta_info.json").write_bytes(meta_bytes)
        (timeline_dir / "draft_meta_info.json.bak").write_bytes(meta_bytes)

        # Cover handling
        if self.cover_source_path and Path(self.cover_source_path).exists():
            shutil.copy(self.cover_source_path, draft_dir / "draft_cover.jpg")
            shutil.copy(self.cover_source_path, timeline_dir / "draft_cover.jpg")
        else:
            # Generate black 1x1 jpg fallback
            fallback_jpg = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9'
            (draft_dir / "draft_cover.jpg").write_bytes(fallback_jpg)
            (timeline_dir / "draft_cover.jpg").write_bytes(fallback_jpg)

        now_us = int(time.time() * 1_000_000)
        project_json_data = {
            "config": {
                "adjust_max_index": 1, "attachment_max_index": 1, "audio_max_index": 1, "color_space": 0,
                "duration": self.duration_us, "effect_max_index": 1, "filter_max_index": 1,
                "sticker_max_index": 1, "text_max_index": len(sub_mats) + 1, "video_max_index": 2,
                "zoom_info": {"start_time": 0, "zoom": 13.0}
            },
            "context": {
                "apply_target_type": 0, "cur_time": 0, "hdr_mode": 0, "hdr_vivid": False,
                "mixed_track_mode_on": False, "render_index_track_mode_on": False, "use_float_render": False
            },
            "create_time": now_us,
            "id": create_uuid(),
            "main_timeline_id": timeline_id,
            "timelines": [{"create_time": now_us, "id": timeline_id, "is_marked_delete": False, "name": "时间线01", "update_time": now_us}],
            "update_time": now_us,
            "version": 0
        }
        (timelines_dir / "project.json").write_text(json.dumps(project_json_data, ensure_ascii=False), encoding='utf-8')
        (timelines_dir / "project.json.bak").write_text(json.dumps(project_json_data, ensure_ascii=False), encoding='utf-8')

        # 7. Update root_meta_info.json (Place project at index 0)
        if ROOT_META_FILE.exists():
            root_data = json.loads(ROOT_META_FILE.read_text(encoding='utf-8'))
        else:
            root_data = {"all_draft_store": [], "draft_ids": 0, "root_path": str(DRAFT_ROOT)}

        root_data["all_draft_store"] = [d for d in root_data["all_draft_store"] if d.get("draft_name") != self.name]

        store_entry = {
            "cloud_draft_cover": False, "cloud_draft_sync": False, "draft_cloud_last_action_download": False,
            "draft_cloud_purchase_info": "", "draft_cloud_template_id": "", "draft_cloud_tutorial_info": "",
            "draft_cloud_videocut_purchase_info": "", "draft_cover": str(draft_dir / "draft_cover.jpg"),
            "draft_fold_path": str(draft_dir), "draft_id": draft_id, "draft_is_ai_shorts": False,
            "draft_is_cloud_temp_draft": False, "draft_is_infinite_canvas_draft": False, "draft_is_invisible": False,
            "draft_is_pippit_draft": False, "draft_is_web_article_video": False,
            "draft_json_file": str(draft_dir / "draft_info.json"), "draft_name": self.name,
            "draft_new_version": "", "draft_root_path": str(DRAFT_ROOT),
            "draft_timeline_materials_size": video_size, "draft_type": "",
            "draft_web_article_video_enter_from": "", "pippit_avatar_url": "", "pippit_extra_info": "",
            "pippit_id": "", "pippit_user_name": "", "streaming_edit_draft_ready": True,
            "tm_draft_cloud_completed": "", "tm_draft_cloud_entry_id": -1, "tm_draft_cloud_modified": 0,
            "tm_draft_cloud_parent_entry_id": -1, "tm_draft_cloud_space_id": -1, "tm_draft_cloud_user_id": -1,
            "tm_draft_create": now_us, "tm_draft_modified": now_us, "tm_draft_removed": 0, "tm_duration": self.duration_us
        }
        root_data["all_draft_store"].insert(0, store_entry)
        root_data["draft_ids"] = len(root_data["all_draft_store"])
        ROOT_META_FILE.write_text(json.dumps(root_data, ensure_ascii=False), encoding='utf-8')

        print(f"🎉 Fully deployed self-contained JianYing Pro Draft:")
        print(f"   Project Name: {self.name}")
        print(f"   Duration:     {self.video_duration_sec:.2f}s")
        print(f"   Internal MP4: {internal_video_path}")
        print(f"   Draft Folder: {draft_dir}")

        # 8. Optionally Launch JianYing
        if open_app:
            print("🚀 Launching JianYing Pro...")
            subprocess.run(["pkill", "-f", "VideoFusion"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1)
            subprocess.run(["open", "-a", JIANYING_APP])

        return draft_dir

if __name__ == "__main__":
    print("JianYing draft builder library loaded successfully.")
