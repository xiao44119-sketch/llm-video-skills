# -*- coding: utf-8 -*-
"""成品加BGM: 合并.mp3 + models\\BGM标准.mp3 -> 合并_带BGM.mp3 (人声不动, BGM 垫底)。
配方(2026-06-11 用户耳定): 浓度 0.18(轻=0.12), BGM 淡入2s/尾部淡出4s, amix 不归一(人声响度不变)。
用法:
    python 加BGM.py                  # 处理最近一次配音的输出目录
    python 加BGM.py 训练\\配音-黑粉    # 指定目录
    python 加BGM.py --vol 0.12       # 轻浓度
    python 加BGM.py --bgm 新曲.mp3    # 换BGM曲目(会复制进 models\\BGM标准.mp3 永久保管, 防源文件丢失)
"""
import os
import sys
import json
import shutil
import subprocess
import importlib.util

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
BGM = os.path.join(HERE, "models", "BGM标准.mp3")


def load(name, fn):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fn))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def dur_of(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", p], capture_output=True)
    return float(json.loads(r.stdout.decode("utf-8", "replace"))["format"]["duration"])


args = sys.argv[1:]


def take_opt(name):
    if name in args:
        k = args.index(name)
        if k + 1 < len(args):
            v = args[k + 1]
            del args[k:k + 2]
            return v
    return None


vol = float(take_opt("--vol") or 0.18)
bgm_arg = take_opt("--bgm")
if bgm_arg:  # 新曲目: 复制进项目永久保管(2026-06-11 教训: 外部源文件被移走, BGM断供)
    os.makedirs(os.path.dirname(BGM), exist_ok=True)
    shutil.copyfile(bgm_arg, BGM)
    print(f"✓ BGM 曲目已收进 {BGM}")

if not os.path.exists(BGM):
    sys.exit("缺 BGM 曲目: models\\BGM标准.mp3 不存在。\n"
             "把曲子拖给我或执行: python 加BGM.py --bgm <曲目路径>  (会自动收进 models 永久保管)")

M = load("peiyin", "配音.py")
out_dir = args[0] if args else M.latest_out_dir()
if not os.path.isabs(out_dir):
    out_dir = os.path.join(HERE, out_dir)
src = os.path.join(out_dir, "合并.mp3")
if not os.path.exists(src):
    sys.exit(f"找不到 {src} (先跑配音/合并)")

d = dur_of(src)
fo = max(0.0, d - 4.0)
dst = os.path.join(out_dir, "合并_带BGM.mp3")
r = subprocess.run(
    ["ffmpeg", "-y", "-v", "error", "-i", src, "-stream_loop", "-1", "-i", BGM,
     "-filter_complex",
     f"[1:a]volume={vol},afade=t=in:d=2,atrim=0:{d:.3f},afade=t=out:st={fo:.3f}:d=4[bg];"
     f"[0:a][bg]amix=inputs=2:duration=first:normalize=0",
     "-b:a", "192k", dst],
    capture_output=True)
if r.returncode != 0:
    sys.exit("ffmpeg 失败:\n" + (r.stderr or b"").decode("utf-8", "replace")[-400:])
print(f"✅ {dst}  (BGM浓度 {vol}, 时长 {d:.1f}s)")
