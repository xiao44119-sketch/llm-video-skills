# -*- coding: utf-8 -*-
"""纯重合并: 用 output/ 里现有的逐段 mp3 重新拼出 合并.mp3, 不调用 API。
换掉某段(挑了重 roll 的版本覆盖 NNN.mp3)后, 跑这个即可重拼整篇。
用法: python 合并.py [原文.txt]   (默认 原文.txt, 用它的分段结构算停顿)
"""
import os
import sys
import json
import importlib.util

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("peiyin", os.path.join(HERE, "配音.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))

out_dir = M.latest_out_dir()  # 自动跟随最近一次配音的输出目录
argv = sys.argv[1:]
if "--minchars" in argv:  # 与配音.py --minchars 同口径(并段批次重拼时段结构必须一致)
    k = argv.index("--minchars")
    cfg["min_unit_chars"] = int(argv[k + 1])
    del argv[k:k + 2]
# 文案默认用该目录的 文案.txt; 也可显式传参
default_src = os.path.join(out_dir, "文案.txt")
src = argv[0] if argv else (default_src if os.path.exists(default_src) else "原文.txt")
src = src if os.path.isabs(src) else (src if os.path.exists(src) else os.path.join(HERE, src))
text = open(src, encoding="utf-8-sig").read()
units = M.build_units(text, cfg)  # 拿每段的前置停顿
fmt = cfg.get("format", "mp3")
items = []   # [(path, gap, 文本)]
missing = []
for i in range(len(units)):
    p = os.path.join(out_dir, f"{i + 1:03d}.{fmt}")
    if os.path.exists(p):
        items.append((p, units[i][1] if items else 0.0, units[i][0]))
    else:
        missing.append(i + 1)

if missing:
    print(f"⚠ 缺少这些段, 会跳过: {missing}")
merged = os.path.join(out_dir, f"合并.{fmt}")
if M.merge_audio([(p, g) for p, g, _ in items], merged, fmt, cfg):
    srt = os.path.join(out_dir, "合并.srt")
    M.write_srt(items, srt)
    print(f"✅ 重新合并完成: {merged}  (共 {len(items)} 段)\n   同步字幕: {srt}")
