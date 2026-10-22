# -*- coding: utf-8 -*-
"""质检校准实验: 验证"机器声纹打分"能否复现人耳的重roll选择。
原理:
  1. 把 output/ 里用户认可的最终段作为"本人音色"锚点(质心)
  2. 给 重roll_NNN/ 里的各候选版本打"像本人分数"(与质心的余弦相似度)
  3. 看用户当时耳朵选中的版本, 是不是机器打分最高的
  4. 顺带: 全篇每段与质心的相似度 + 语速体检, 找出离群段
用法: python 质检校准.py
依赖: pip install sherpa-onnx numpy ; ffmpeg/ffprobe 在 PATH; models/campplus_zh-cn_16k.onnx
"""
import os
import re
import sys
import json
import subprocess
import numpy as np
import sherpa_onnx

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
MODEL = os.path.join(HERE, "models", "campplus_zh-cn_16k.onnx")


# ---------------------------------------------------------------- 音频
def decode_pcm(path, sr=16000):
    """mp3 -> 16k 单声道 float32 波形(不落盘)。"""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"解码失败: {path}: {(r.stderr or b'').decode('utf-8','replace')[-200:]}")
    return np.frombuffer(r.stdout, dtype=np.float32)


def duration_sec(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True,
    )
    try:
        return float(json.loads(r.stdout.decode("utf-8", "replace"))["format"]["duration"])
    except Exception:
        return 0.0


# ---------------------------------------------------------------- 声纹
_extractor = None


def extractor():
    global _extractor
    if _extractor is None:
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=MODEL, num_threads=4)
        _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
    return _extractor


def embed(path):
    ex = extractor()
    samples = decode_pcm(path)
    st = ex.create_stream()
    st.accept_waveform(16000, samples)
    st.input_finished()
    v = np.array(ex.compute(st), dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def cos(a, b):
    return float(np.dot(a, b))  # 都已归一化


# ---------------------------------------------------------------- 主流程
def main():
    if not os.path.exists(MODEL):
        sys.exit(f"找不到声纹模型: {MODEL}")

    # 1) 最终认可段
    finals = {}
    for f in sorted(os.listdir(OUT)):
        m = re.fullmatch(r"(\d{3})\.mp3", f)
        if m:
            finals[int(m.group(1))] = os.path.join(OUT, f)
    if len(finals) < 5:
        sys.exit("output/ 里最终段太少, 无法建立音色锚点")
    print(f"锚点素材: {len(finals)} 个最终认可段")

    print("提取声纹中(首次较慢) ...")
    emb_final = {k: embed(p) for k, p in finals.items()}
    dur_final = {k: duration_sec(p) for k, p in finals.items()}

    # 2) 分句预览 -> 每段文本(算语速用)
    texts = {}
    prev = os.path.join(OUT, "分句预览.txt")
    if os.path.exists(prev):
        for line in open(prev, encoding="utf-8"):
            m = re.match(r"(\d{3}) \| \[.*?\] (.*)", line.strip())
            if m:
                t = re.sub(r"[，。！？；、：…,.!?;:\s]", "", m.group(2))
                texts[int(m.group(1))] = len(t)

    lines = ["=" * 72, "实验一: 重roll 各版本的「像本人分数」 vs 你当时的耳朵选择", "=" * 72]

    # 3) 重roll 目录校准
    reroll_dirs = sorted(d for d in os.listdir(OUT) if d.startswith("重roll_"))
    agree = total = 0
    for d in reroll_dirs:
        seg = int(d.split("_")[1])
        # 排除被测段, 用其余认可段建质心(leave-one-out)
        centroid = np.mean([v for k, v in emb_final.items() if k != seg], axis=0)
        centroid /= np.linalg.norm(centroid) + 1e-9

        final_bytes = open(finals[seg], "rb").read() if seg in finals else None
        rows = []
        for f in sorted(os.listdir(os.path.join(OUT, d))):
            if not f.endswith(".mp3"):
                continue
            p = os.path.join(OUT, d, f)
            chosen = final_bytes is not None and open(p, "rb").read() == final_bytes
            rows.append((f, cos(embed(p), centroid), chosen))
        if not rows:
            continue
        rows.sort(key=lambda r: -r[1])
        which = next((r[0] for r in rows if r[2]), None)
        lines.append(f"\n第 {seg:03d} 段 ({d}):")
        for rank, (f, s, chosen) in enumerate(rows, 1):
            mark = "  ← 你当时选的就是这版" if chosen else ""
            lines.append(f"  #{rank}  {f}  像本人分数 {s:.4f}{mark}")
        if which is None:
            lines.append("  (最终版不在候选里: 你选完可能又单独重生成过, 该段不计入一致率)")
        else:
            total += 1
            top = rows[0][2]
            agree += top
            lines.append(f"  >> 机器第1名 {'==' if top else '!='} 人耳选择")

    if total:
        lines.append(f"\n人机一致率: {agree}/{total}")

    # 4) 全篇体检: 每段与质心的相似度 + 语速
    lines += ["", "=" * 72, "实验二: 全篇 45 段体检(与整体音色质心的偏离 + 语速)", "=" * 72]
    all_centroid = np.mean(list(emb_final.values()), axis=0)
    all_centroid /= np.linalg.norm(all_centroid) + 1e-9
    sims, speeds = {}, {}
    for k in finals:
        c = np.mean([v for kk, v in emb_final.items() if kk != k], axis=0)
        c /= np.linalg.norm(c) + 1e-9
        sims[k] = cos(emb_final[k], c)
        if k in texts and dur_final.get(k, 0) > 0.3:
            speeds[k] = texts[k] / dur_final[k]

    sim_vals = np.array(list(sims.values()))
    mu, sd = sim_vals.mean(), sim_vals.std()
    lines.append(f"像本人分数: 均值 {mu:.4f} ± {sd:.4f}  (你认可的段就分布在这个区间)")
    spd_vals = np.array(list(speeds.values())) if speeds else None
    if spd_vals is not None:
        lines.append(f"语速: 中位 {np.median(spd_vals):.2f} 字/秒")

    lines.append("\n最偏离音色质心的 8 段(分数越低越可疑):")
    for k in sorted(sims, key=lambda x: sims[x])[:8]:
        spd = f", 语速 {speeds[k]:.2f} 字/秒" if k in speeds else ""
        flag = " ⚠" if sims[k] < mu - 2 * sd else ""
        lines.append(f"  {k:03d}  分数 {sims[k]:.4f}{spd}{flag}")
    if spd_vals is not None:
        med = np.median(spd_vals)
        lines.append("\n语速偏离中位 ±20% 的段:")
        odd = [k for k, v in speeds.items() if abs(v - med) / med > 0.20]
        if odd:
            for k in sorted(odd):
                lines.append(f"  {k:03d}  {speeds[k]:.2f} 字/秒 ({(speeds[k]-med)/med:+.0%})")
        else:
            lines.append("  无")

    report = "\n".join(lines)
    print("\n" + report)
    rp = os.path.join(OUT, "质检校准报告.txt")
    with open(rp, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print(f"\n报告已写入: {rp}")


if __name__ == "__main__":
    main()
