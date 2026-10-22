# -*- coding: utf-8 -*-
"""长样本(~1.5分钟)对比: 第六章前7段, 三种粒度, 只变粒度。
A_碎片 = 复用已生成的 001~038(逗号碎片+0.2s)
B_句级 = 按句号(。！？)切, 每句一次合成
C_段级 = 每段一次合成(逗号句号交给模型, 段内自然停顿)
B/C 的文本只做重标点(软换行->逗号, 破折号->逗号), 不改字。
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

# 第六章前7段(自然重标点, 无破折号, 不改字) —— 对应已生成的碎片 001~038
paras = [
    "第六章：时间尺度，你一定会活到28岁",
    "你一定会活到28岁的。因为我活到了。",
    "这句话不是在安慰你“别担心”。它在做一件更有用的事：给你当下的痛苦标一个有效期。",
    "距离越远的东西，越陌生，越轻，越不重要。你现在觉得天塌了的事，考砸了、选错了、被人看不起了，放到28岁回头看，你甚至记不清具体是多少分。不是因为它不重要，是因为你后面还会遇到更大的事。更大的选择、更大的失败、更大的快乐，会把今天的痛稀释到几乎不可感知。",
    "但这个道理有一个陷阱：它容易被用来逃避当下。“反正以后也不重要，现在也别太当回事了”，不对。正确的用法是：给情绪一个有效期，但不给行动打折扣。你现在可以全力以赴，同时知道结果对你的影响是有半衰期的。半衰期大概是三个月。三个月之后如果你还在为同一件事痛，那不是事件本身的问题了，是你的注意力被卡住了。",
    "然后这句话藏了一个更大的东西：你一定还会面临死亡。",
    "这不是在吓你。这是在给你一个终极的缩放比例尺。把任何一件事放到“我会死”这个背景下，它的尺寸就自动缩小了。高考在死亡面前是一粒沙子。这不是说它不重要，是说你可以全力打这粒沙子，但不必把它当成你的整个沙滩。",
]

# ---- C 段级 ----
print("== C 段级 ==")
citems = []
for i, p in enumerate(paras):
    fp = os.path.join(out, f"C_{i:02d}.mp3")
    open(fp, "wb").write(M.synthesize(p, cfg))
    citems.append((fp, 0.0 if i == 0 else 0.35))
    print(f"  C{i}: {p[:18]}...")
M.merge_audio(citems, os.path.join(out, "C_段级_长.mp3"), "mp3", cfg)

# ---- B 句级(只按 。！？ 切, 不在冒号处切) ----
print("== B 句级 ==")
bitems = []
first = True
bi = 0
for p in paras:
    sents = [s.strip() for s in re.split(r"(?<=[。！？])", p) if s.strip()]
    for j, s in enumerate(sents):
        fp = os.path.join(out, f"B_{bi:02d}.mp3")
        open(fp, "wb").write(M.synthesize(s, cfg))
        gap = 0.0 if first else (0.4 if j == 0 else 0.3)
        bitems.append((fp, gap))
        first = False
        bi += 1
    print(f"  段含 {len(sents)} 句")
M.merge_audio(bitems, os.path.join(out, "B_句级_长.mp3"), "mp3", cfg)

# ---- A 碎片(复用 001~038) ----
print("== A 碎片 ==")
aitems = []
k = 0
for n in range(1, 39):
    fp = os.path.join(HERE, "output", f"{n:03d}.mp3")
    if os.path.exists(fp):
        aitems.append((fp, 0.2 if k > 0 else 0.0))
        k += 1
M.merge_audio(aitems, os.path.join(out, "A_碎片_长.mp3"), "mp3", cfg)

print("\n完成. 三版在 output/对比/ : A_碎片_长.mp3  B_句级_长.mp3  C_段级_长.mp3")
print(f"B 共 {len(bitems)} 句, C 共 {len(citems)} 段, A 复用 {len(aitems)} 碎片")
