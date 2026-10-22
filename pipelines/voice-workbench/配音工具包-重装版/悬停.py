# -*- coding: utf-8 -*-
"""停顿拉伸术: 金句并在长段里合成(音色=长段0.95+), 音频层把金句尾的句号停顿拉长成悬停。
原理: 行尾句号化后 TTS 在每个句末自然停 ~0.2-0.35s; 在静音正中切开插入差额静音 = 零音色损伤。
用法: python 悬停.py            # 处理最近输出目录(读文案.txt 的 ! 标记)
      python 悬停.py --gap 0.65 # 自定义悬停时长
处理完跑 python 合并.py 重拼。
"""
import os
import re
import sys
import json
import subprocess
import importlib.util
import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, fn):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fn))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = load("peiyin", "配音.py")
cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))

SR = 44100
END_PUNCT = "。！？…"


def decode(path):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                       capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


def encode(samples, path, br):
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0",
                        "-b:a", f"{br}k", path], input=samples.astype(np.float32).tobytes(), capture_output=True)
    return r.returncode == 0


def find_silences(w, thr=0.008, min_dur=0.12):
    """返回 [(start_sample, end_sample), ...] 的段内静音区间。"""
    win = SR // 100  # 10ms
    n = len(w) // win
    env = np.abs(w[: n * win]).reshape(n, win).mean(axis=1)
    quiet = env < thr
    out, i = [], 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if (j - i) * win / SR >= min_dur:
                out.append((i * win, j * win))
            i = j
        else:
            i += 1
    return out


def stretch_pause(seg_path, seg_text, sent_end_pos, target_gap, br):
    """把段文本第 sent_end_pos 个字符(句末标点)对应的音频静音拉长到 target_gap 秒。"""
    k = sum(1 for ch in seg_text[: sent_end_pos + 1] if ch in END_PUNCT)  # 第k个句末停顿
    total_punct = sum(1 for ch in seg_text if ch in END_PUNCT)
    w = decode(seg_path)
    sil = find_silences(w)
    # 段尾静音(若有)不算句间停顿
    sil_in = [s for s in sil if s[1] < len(w) - SR // 10]
    if not sil_in:
        return None, "段内未检测到停顿"
    if len(sil_in) == total_punct - (1 if seg_text.rstrip()[-1] in END_PUNCT else 0):
        idx = k - 1  # 静音数与段内句界数吻合, 直接取第k个
    else:
        # 容错: 按字符比例找最近静音
        ratio = sent_end_pos / max(1, len(seg_text))
        approx = ratio * len(w)
        idx = int(np.argmin([abs((a + b) / 2 - approx) for a, b in sil_in]))
    if idx < 0 or idx >= len(sil_in):
        return None, f"停顿序号越界(k={k}, 检测到{len(sil_in)}个)"
    a, b = sil_in[idx]
    cur = (b - a) / SR
    need = target_gap - cur
    if need <= 0.02:
        return 0.0, None
    mid = (a + b) // 2
    w2 = np.concatenate([w[:mid], np.zeros(int(need * SR), dtype=np.float32), w[mid:]])
    if not encode(w2, seg_path, br):
        return None, "编码失败"
    return need, None


def main():
    gap = 0.65
    if "--gap" in sys.argv:
        gap = float(sys.argv[sys.argv.index("--gap") + 1])
    out_dir = M.latest_out_dir()
    script = os.path.join(out_dir, "文案.txt")
    prev = os.path.join(out_dir, "分句预览.txt")
    if not (os.path.exists(script) and os.path.exists(prev)):
        sys.exit(f"缺 文案.txt 或 分句预览.txt: {out_dir}")

    # 1) 文案里的 ! 金句(悬停gap) + 章标题(并段合成后标题尾拉回章节停顿)
    chap_gap = float(cfg.get("chapter_gap", 1.0))
    quotes = []  # (文本, 目标停顿)

    def norm(q):
        q = M.naturalize([l for l in q.split("\n") if l.strip()], cfg.get("convert_parens", True),
                         bool(cfg.get("rhythm_line_end", True)))
        if cfg.get("normalize_numbers", True):
            q = M.normalize_numbers(q)  # 与主流程同步, 否则"模型1"对不上"模型一"
        return q

    for para in re.split(r"\n\s*\n", open(script, encoding="utf-8-sig").read()):
        p = para.strip()
        if p[:1] in "!！":
            quotes.append((norm(p.lstrip("!！").strip()), gap))
        elif M.HEADER_RE.match(p.split("\n")[0].strip()):
            quotes.append((norm(p.split("\n")[0].strip()), chap_gap))
    if not quotes:
        sys.exit("文案里没有 ! 金句或章标题")

    # 2) 预览段文本
    segs = {}
    for line in open(prev, encoding="utf-8-sig"):
        m = re.match(r"(\d{3}) \| \[.*?\] (.*)", line.strip())
        if m:
            segs[int(m.group(1))] = m.group(2)

    br = cfg.get("mp3_bitrate", 192)
    n_ok = 0
    for q, tgt in quotes:
        hit = None
        for i, t in segs.items():
            pos = t.find(q)
            if pos >= 0:
                hit = (i, t, pos)
                break
        if not hit:
            print(f"⚠ 金句/标题没匹配到任何段(可能被改写): {q[:25]}…")
            continue
        i, t, pos = hit
        end_pos = pos + len(q) - 1  # 句尾标点
        if t[end_pos] not in END_PUNCT:
            print(f"⚠ 尾部不是句末标点, 跳过: …{q[-12:]}")
            continue
        if end_pos >= len(t.rstrip()) - 1:
            print(f"  段{i:03d} 目标在段尾, 悬停由段间停顿承担, 跳过音频手术: …{q[-15:]}")
            continue
        seg_path = os.path.join(out_dir, f"{i:03d}.{cfg.get('format', 'mp3')}")
        added, err = stretch_pause(seg_path, t, end_pos, tgt, br)
        if err:
            print(f"⚠ 段{i:03d} 处理失败: {err}")
        else:
            n_ok += 1
            print(f"✓ 段{i:03d} 尾停顿已拉伸 +{added:.2f}s -> {tgt}s: …{q[-18:]}")
    if n_ok:
        print(f"\n完成 {n_ok} 处悬停。重拼: python 合并.py")


if __name__ == "__main__":
    main()
