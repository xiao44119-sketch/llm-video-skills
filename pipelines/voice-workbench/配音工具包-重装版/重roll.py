# -*- coding: utf-8 -*-
"""单段重 roll + 声纹自动挑最像: 生成 N 版, 按「像本人分数」排序, 最高分自动覆盖 NNN.mp3。
用法:
    python 重roll.py 2            # 第2段, 默认4版, 自动选最像的覆盖 002.mp3
    python 重roll.py 2 6          # 生成6版
    python 重roll.py 2 4 0.6      # 4版, 临时把 temperature 调到 0.6
    python 重roll.py 2 --keep     # 只生成+打分, 不覆盖(想自己耳朵挑时用)
覆盖后重拼整篇: python 合并.py
"""
import os
import sys
import json
import shutil
import importlib.util

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, fn):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fn))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = load("peiyin", "配音.py")
cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
M.require_creds(cfg)

args = [a for a in sys.argv[1:] if not a.startswith("--")]
keep = "--keep" in sys.argv
force = "--force" in sys.argv  # 强制用新最高分覆盖(读音修复等场景: 旧版读错, 分数高也得换)
if not args:
    sys.exit(__doc__)
seg = int(args[0])
count = int(args[1]) if len(args) > 1 else 4
if len(args) > 2:
    cfg = dict(cfg)
    cfg["temperature"] = float(args[2])

OUT = M.latest_out_dir()  # 自动跟随最近一次配音的输出目录
prefix = f"{seg:03d} | "
text = None
for l in open(os.path.join(OUT, "分句预览.txt"), encoding="utf-8"):
    if l.startswith(prefix):
        text = l.split("] ", 1)[1].strip() if "] " in l else l[len(prefix):].strip()
        break
if not text:
    sys.exit(f"分句预览里找不到第 {seg} 段 ({OUT})")

qc = None
try:
    z = load("zhijian", "质检.py")
    q = z.VoiceQC(cfg)
    qc = q if (q.enabled and q.ready) else None
except Exception:
    pass
if qc is None:
    print("⚠ 声纹质检不可用(缺模型或锚点未建), 退回纯生成模式")

od = os.path.join(OUT, f"重roll_{seg:03d}")
os.makedirs(od, exist_ok=True)
print(f"第 {seg} 段: {text[:50]}")

RH_PEN = float(cfg.get("qc", {}).get("rhythm_penalty", 0.01))
CER_PEN = float(cfg.get("qc", {}).get("cer_penalty", 0.02))


def _pi(s):
    """韵律问题合集(节奏/重音/呼吸), 与配音.py prosody_issues 同口径。"""
    out = []
    for k in ("rhythm", "stress", "breath"):
        if s.get(k):
            out += s[k]["issues"]
    return out

# 现有版本也打分参赛: 新版本没超过它就不覆盖(防止越roll越差)
tts_text = M.apply_pronounce(text, cfg)  # 读音替换(如 AI->诶爱), 字幕仍用原文
cur_path = os.path.join(OUT, f"{seg:03d}.mp3")
cur_sim = cur_comp = None
if qc and os.path.exists(cur_path):
    cs = qc.score(cur_path, text, tts_text=tts_text)
    ce0 = (cs.get("cer") or {}).get("errs", [])
    pi0 = _pi(cs)
    cur_sim = cs["sim"]
    cur_comp = cur_sim - RH_PEN * len(pi0) - CER_PEN * len(ce0)
    print(f"现有版本: 像本人分数 {cur_sim:.4f}  {qc.verdict(cs)}"
          + ("  韵律⚠(" + ",".join(pi0) + ")" if pi0 else "")
          + ("  👂" + "；".join(ce0[:2]) if ce0 else ""))
print(f"temperature={cfg.get('temperature')}, 生成 {count} 版 ...")

rows = []  # (path, sim, verdict, cer_errs)
for i in range(1, count + 1):
    audio = M.synthesize(tts_text, cfg)
    if qc:
        s = qc.score(audio, text, tts_text=tts_text)
        v = qc.verdict(s)
        ce = (s.get("cer") or {}).get("errs", [])
        pi = _pi(s)
        mark = ("_韵律⚠" if pi else "") + ("_疑错读" if ce else "")
        p = os.path.join(od, f"v{i}_像{s['sim']:.4f}_{v}{mark}.mp3")  # 文件名自带分数, 文件夹内直接对比
        open(p, "wb").write(audio)
        rows.append((p, s["sim"], v, ce, pi))
        print(f"  v{i}  像本人分数 {s['sim']:.4f}  {v}"
              + ("  韵律⚠(" + ",".join(pi) + ")" if pi else "")
              + ("  👂" + "；".join(ce[:2]) if ce else ""))
    else:
        p = os.path.join(od, f"v{i}.mp3")
        open(p, "wb").write(audio)
        rows.append((p, None, "", [], []))
        print(f"  v{i} 完成")

def comp(r):
    return (r[1] or 0) - RH_PEN * len(r[4] or []) - CER_PEN * len(r[3])


if qc:
    # 综合分=音色-0.01×韵律-0.02×错读, 与配音.py批内挑版完全同口径
    # (翻车案2026-06-11: 旧排序只罚错读不罚韵律, 扫尾把节奏没把关的版本换了上去)
    rows.sort(key=lambda r: -comp(r))
    print("\n排名(高->低, 韵律/错读已罚分):")
    for rank, (p, s, v, ce, pi) in enumerate(rows, 1):
        print(f"  #{rank}  {os.path.basename(p)}  {s:.4f}  {v}"
              + ("  韵律⚠" if pi else "") + ("  👂错读嫌疑" if ce else ""))
    best_p, best_s, best_v, best_ce, best_pi = rows[0]
    if keep:
        print(f"\n[--keep] 未覆盖。版本在 {od}, 满意哪版自己复制成 {seg:03d}.mp3")
    elif not force and cur_comp is not None and comp(rows[0]) <= cur_comp:
        print(f"\n⏸ 新 {count} 版综合分最高 {comp(rows[0]):.4f} 没超过现有版(综合 {cur_comp:.4f}), 不覆盖(版本保留在 {od} 可自己听; 读音修复请加 --force)")
    else:
        dst = os.path.join(OUT, f"{seg:03d}.mp3")
        shutil.copyfile(best_p, dst)
        shutil.copyfile(best_p, os.path.join(od, f"已采用_像{best_s:.4f}.mp3"))  # 文件夹内标明最终选择
        print(f"\n✅ 已用最高分 {os.path.basename(best_p)} ({best_s:.4f} {best_v}) 覆盖 {seg:03d}.mp3"
              + (f" (原 {cur_sim:.4f})" if cur_sim is not None else ""))
        if best_v != "绿":
            print("   ⚠ 最高分仍未达绿: 建议自己听一下; 屡roll不绿通常是文本拗口, 改一两个字往往立竿见影")
        if best_ce:
            print("   👂 采用版仍有错读嫌疑: " + "；".join(best_ce[:3]) + " ——请耳朵裁决")
        print("   重拼整篇: python 合并.py    (不满意可加大版数或 --keep 自己挑)")
else:
    print(f"\n版本在: {od}, 挑好后复制覆盖 output/{seg:03d}.mp3, 再跑 python 合并.py")
