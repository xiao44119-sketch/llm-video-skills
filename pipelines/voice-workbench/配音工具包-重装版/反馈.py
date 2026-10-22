# -*- coding: utf-8 -*-
"""用户反馈 -> 自迭代训练: 你的每次耳朵裁决都让质检系统更懂"像不像本人"。
用法:
    python 反馈.py 4 最像              # 认可 output/004.mp3 (锚点已冻结: 记台账, 不动音色基准)
    python 反馈.py 4 不像              # 否决 (冻结期同样只记台账; 台账=日后解冻/重训的依据)
    python 反馈.py 4 读错              # 读音问题: 只记台账(不动声纹库, 读错≠音色不像)
    python 反馈.py 4 听点对            # 该段预检/错读闸门标的听点确认没问题(误报统计+1, 误报多的规则自动静默)
    python 反馈.py 4 听点错 [词]       # 听点确认真错(可指定词): 风险库计真错, ASR实锤案例自动入库
    python 反馈.py <mp3路径> 最像/不像/读错   # 也可直接对任意文件(如对比集里的版本)
    python 反馈.py --status            # 看当前锚点/负样本/台账规模
所有裁决记入 models/反馈台账.jsonl。
"""
import os
import re
import sys
import importlib.util

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, fn):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fn))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


Z = load("zhijian", "质检.py")
M = load("peiyin", "配音.py")
import json
cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
qc = Z.VoiceQC(cfg)
if not qc.enabled:
    sys.exit("质检不可用: 缺模型或 qc.enable=false")


def status():
    n_led = 0
    if os.path.exists(Z.LEDGER_PATH):
        n_led = sum(1 for _ in open(Z.LEDGER_PATH, encoding="utf-8"))
    print(f"锚点: {len(qc.embs)} 段   负样本: {len(qc.negs)} 个   台账: {n_led} 条")
    if qc.ready:
        y, r = qc.thresholds()
        print(f"当前阈值: 黄线 {y:.4f}, 红线 {r:.4f}")


if "--status" in sys.argv:
    status()
    sys.exit(0)

args = [a for a in sys.argv[1:] if not a.startswith("--")]

# ---------- 听点裁决回流: 预检/错读闸门标的听点, 耳朵裁决后回写风险库(误报率自收敛) ----------
if len(args) >= 2 and args[1] in ("听点对", "听点错"):
    import datetime
    seg, good = int(args[0]), args[1] == "听点对"
    word_filter = args[2] if len(args) > 2 else None
    od = M.latest_out_dir()
    tp = os.path.join(od, "听点.json")
    if not os.path.exists(tp):
        sys.exit(f"该批没有听点文件: {tp}")
    points = json.load(open(tp, encoding="utf-8"))
    sel = [h for h in points if h["seg"] == seg and (word_filter is None or word_filter in h["word"])]
    if not sel:
        sys.exit(f"段{seg:03d} 没有匹配的听点" + (f"(词: {word_filter})" if word_filter else ""))
    lib = json.load(open(M.RISK_LIB, encoding="utf-8-sig"))
    changed = False
    for h in sel:
        if h["src"] == "预检":
            pool = lib.get("patterns" if h.get("kind") == "pattern" else "words", [])
            for e in pool:
                if e.get("re") == h["key"] or e.get("w") == h["key"]:
                    st = e.setdefault("stat", {"对": 0, "错": 0})
                    st["对" if good else "错"] = int(st.get("对" if good else "错", 0)) + 1
                    changed = True
        elif h["src"] == "ASR" and not good:
            # 错读闸门嫌疑被耳朵实锤 -> 案例自动入风险库(库的自生长)
            w = re.sub(r"^(漏读|多读)?「", "", h["word"]).split("」")[0]
            if w and not any(x.get("w") == w for x in lib.get("words", [])):
                lib.setdefault("words", []).append(
                    {"w": w, "hint": f"实案确认错读({datetime.date.today().isoformat()}): {h['word']}"
                                     f"——加标点断歧义或进读音表, 见发音规则.md"})
                changed = True
        Z.log_feedback({"feedback": args[1], "seg": seg, "word": h["word"],
                        "src": h["src"], "batch": os.path.basename(od)})
        print(("✅ 听点无误: " if good else "❌ 确认错读: ") + f"段{seg:03d}「{h['word']}」({h['src']})")
    if changed:
        with open(M.RISK_LIB, "w", encoding="utf-8") as f:
            json.dump(lib, f, ensure_ascii=False, indent=1)
    if good:
        print("已计入规则统计: 同一规则误报≥5次且零真错将自动静默, 预检越用越安静")
    else:
        print(f"已记台账/入库。修法见 发音规则.md, 修完: python 重roll.py {seg} --force")
    sys.exit(0)

if len(args) < 2 or args[-1] not in ("最像", "不像", "读错", "好", "差"):
    sys.exit(__doc__)
target, judge = args[0], args[-1]
judge = {"好": "最像", "差": "不像"}.get(judge, judge)

# 目标解析: 段号 -> 最近输出目录/NNN.mp3 + 预览文本; 路径 -> 直接用
text = None
if re.fullmatch(r"\d{1,3}", target):
    seg = int(target)
    od = M.latest_out_dir()
    path = os.path.join(od, f"{seg:03d}.mp3")
    prev = os.path.join(od, "分句预览.txt")
    if os.path.exists(prev):
        for l in open(prev, encoding="utf-8"):
            if l.startswith(f"{seg:03d} | "):
                text = l.split("] ", 1)[1].strip() if "] " in l else None
                break
else:
    path = target if os.path.isabs(target) else os.path.join(HERE, target)
if not os.path.exists(path):
    sys.exit(f"找不到文件: {path}")

s = qc.score(path, text)
entry = {"file": os.path.relpath(path, HERE), "text": text, "sim": round(s["sim"], 4),
         "neg_sim": round(s["neg_sim"], 4) if s.get("neg_sim") is not None else None,
         "verdict_before": qc.verdict(s), "feedback": judge}

if judge == "最像":
    if qc.frozen:
        print(f"✅ 已记录认可(像{s['sim']:.4f})。锚点已冻结(音色标准锁定), 只记台账不动声纹基准")
    else:
        qc.feedback_good(path, text)
        print(f"✅ 已认可(像{s['sim']:.4f}): 3倍权重滚入锚点, 音色基准向你的耳朵靠拢")
elif judge == "不像":
    if qc.frozen:
        print(f"🚫 已记录否决(像{s['sim']:.4f})。锚点已冻结, 只记台账不动声纹库")
    else:
        qc.feedback_bad(path)
        print(f"🚫 已否决(像{s['sim']:.4f}): 入负样本库, 以后这种腔调的候选自动降级")
else:
    print(f"📝 读音问题已记台账(声纹库不动, 像{s['sim']:.4f})")

qc.save()
Z.log_feedback(entry)
status()
