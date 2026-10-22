# -*- coding: utf-8 -*-
"""语料导出: 把各成品文件夹里的(段文本, 段音频, 像本人分数)三元组汇总成数据集清单。
这是平台无关的对冲资产——任何新TTS引擎/重训voice, 拿这份清单就能立刻取用全部已认可语料。
用法:
    python 语料.py              # 扫描全部成品夹 -> models/语料清单.jsonl + 汇总表
    python 语料.py --min 0.95   # 只统计重训级语料的口径线(清单仍全量, 汇总分级)
扫描范围: 训练\* 与 output_* 下含 分句预览.txt 的文件夹。
"""
import os
import re
import sys
import json
import subprocess

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "models", "语料清单.jsonl")

PREV_RE = re.compile(r"^(\d{3}) \| \[\s*([\d.]+)s\] (.*)$")
REP_RE = re.compile(r"^(\d{3})\s+像本人([\d.]+).*?(绿|黄|红)")


def _dur_of(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True)
    try:
        return float(json.loads(r.stdout.decode("utf-8", "replace"))["format"]["duration"])
    except Exception:
        return 0.0


def scan_dir(d):
    """一个成品夹 -> [entry]; 没有 分句预览.txt 则返回 []。"""
    prev = os.path.join(d, "分句预览.txt")
    if not os.path.exists(prev):
        return []
    texts = {}
    for line in open(prev, encoding="utf-8-sig"):
        m = PREV_RE.match(line.strip())
        if m:
            texts[int(m.group(1))] = m.group(3)
    sims = {}
    for rep_name in ("质检报告.txt", "质检校准报告.txt"):
        rp = os.path.join(d, rep_name)
        if os.path.exists(rp):
            for line in open(rp, encoding="utf-8-sig"):
                m = REP_RE.match(line.strip())
                if m:
                    sims[int(m.group(1))] = (float(m.group(2)), m.group(3))
            break
    entries = []
    batch = os.path.relpath(d, HERE)
    for f in sorted(os.listdir(d)):
        m = re.fullmatch(r"(\d{3})\.mp3", f)
        if not m:
            continue
        i = int(m.group(1))
        sim, verdict = sims.get(i, (None, None))
        entries.append({
            "batch": batch, "seg": i, "text": texts.get(i),
            "audio": os.path.join(batch, f), "dur": round(_dur_of(os.path.join(d, f)), 2),
            "sim": sim, "verdict": verdict,
        })
    return entries


def main():
    min_sim = 0.95
    if "--min" in sys.argv:
        k = sys.argv.index("--min")
        if k + 1 < len(sys.argv):
            min_sim = float(sys.argv[k + 1])

    roots = []
    train = os.path.join(HERE, "训练")
    if os.path.isdir(train):
        roots += [os.path.join(train, x) for x in sorted(os.listdir(train))
                  if os.path.isdir(os.path.join(train, x))]
    roots += [os.path.join(HERE, x) for x in sorted(os.listdir(HERE))
              if x.startswith("output") and os.path.isdir(os.path.join(HERE, x))]

    all_entries = []
    rows = []
    for d in roots:
        es = scan_dir(d)
        if not es:
            continue
        all_entries += es
        sims = [e["sim"] for e in es if e["sim"] is not None]
        dur = sum(e["dur"] for e in es)
        n_hi = sum(1 for e in es if (e["sim"] or 0) >= min_sim)
        rows.append((os.path.relpath(d, HERE), len(es), dur / 60,
                     (sum(sims) / len(sims)) if sims else None, n_hi))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for e in all_entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    print(f"{'成品夹':<28}{'段数':>4}{'分钟':>7}{'均像':>8}{'像≥%.2f' % min_sim:>9}")
    for name, n, mins, avg, n_hi in rows:
        print(f"{name:<30}{n:>4}{mins:>7.1f}{(('%.4f' % avg) if avg else '   -'):>9}{n_hi:>7}")
    tot_n = len(all_entries)
    tot_min = sum(e["dur"] for e in all_entries) / 60
    tot_hi = sum(1 for e in all_entries if (e["sim"] or 0) >= min_sim)
    hi_min = sum(e["dur"] for e in all_entries if (e["sim"] or 0) >= min_sim) / 60
    print("-" * 60)
    print(f"合计 {tot_n} 段 / {tot_min:.1f} 分钟;  重训级(像≥{min_sim}) {tot_hi} 段 / {hi_min:.1f} 分钟")
    print(f"清单: {OUT}")


if __name__ == "__main__":
    main()
