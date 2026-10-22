# -*- coding: utf-8 -*-
"""一次性对比试听: 同一段文字, 三种合成粒度, 只变粒度, 其余参数全相同。
A_碎片 = 现状(逗号切碎, 每片单独合成, 0.2s拼接)
B_句级 = 按句号切, 每个完整句子一次合成, 句间0.25s
C_段级 = 整段一次合成(逗号句号都交给模型, 内部自然停顿)
"""
import os
import re
import sys
import json
import importlib.util

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("peiyin", os.path.join(HERE, "配音.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
M.require_creds(cfg)

out = os.path.join(HERE, "output", "对比")
os.makedirs(out, exist_ok=True)

# 自然标点后的整段(把原文软换行补成逗号, 让模型有连读语气)
para = ("距离越远的东西，越陌生，越轻，越不重要。"
        "你现在觉得天塌了的事，考砸了、选错了、被人看不起了，放到28岁回头看，"
        "你甚至记不清具体是多少分。"
        "不是因为它不重要，是因为你后面还会遇到更大的事。"
        "更大的选择、更大的失败、更大的快乐，会把今天的痛稀释到几乎不可感知。")

# ---- C 段级: 整段一次 ----
audio = M.synthesize(para, cfg)
open(os.path.join(out, "C_段级.mp3"), "wb").write(audio)
print("C 段级 完成")

# ---- B 句级: 按句号切, 每句一次 ----
sents = [s.strip() for s in re.split(r"(?<=[。！？])", para) if s.strip()]
bitems = []
for i, s in enumerate(sents, 1):
    p = os.path.join(out, f"B_{i:02d}.mp3")
    open(p, "wb").write(M.synthesize(s, cfg))
    bitems.append((p, 0.25 if i > 1 else 0.0))
    print(f"B 句级 {i}: {s}")
M.merge_audio(bitems, os.path.join(out, "B_句级.mp3"), "mp3", cfg)

# ---- A 碎片: 复用第六章已生成的 008~016(就是这一段) ----
aitems = []
for k, n in enumerate(range(8, 17)):
    p = os.path.join(HERE, "output", f"{n:03d}.mp3")
    if os.path.exists(p):
        aitems.append((p, 0.2 if k > 0 else 0.0))
M.merge_audio(aitems, os.path.join(out, "A_碎片.mp3"), "mp3", cfg)
print("A 碎片 完成")

print("\n三版在 output/对比/ : A_碎片.mp3  B_句级.mp3  C_段级.mp3")
