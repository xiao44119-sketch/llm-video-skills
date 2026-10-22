# -*- coding: utf-8 -*-
"""声纹质检模块: 给每段配音打「像本人分数」, 判级 绿/黄/红。
原理:
  - 锚点 = 你历次认可的段的声纹集合(models/锚点.npz), 取质心代表"本人音色"
  - 每段新音频提取声纹, 与质心算余弦相似度 => sim(像本人分数)
  - 阈值自适应: 从锚点自身分布算 μ/σ, 黄线≈μ-1.5σ, 红线≈μ-2.5σ(带绝对上下限)
  - 语速辅助判级: 字/秒 偏离锚点中位 >25% 黄, >40% 红
用法(独立运行):
  python 质检.py --init     # 用 output/NNN.mp3 现有认可段初始化锚点
  python 质检.py 文件.mp3   # 给单个文件打分
被 配音.py / 重roll.py 通过 importlib 调用。
依赖: sherpa-onnx, numpy, ffmpeg; 模型 models/campplus_zh-cn_16k.onnx
"""
import os
import re
import sys
import json
import threading
import subprocess
import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(HERE, "models", "campplus_zh-cn_16k.onnx")
ANCHOR_PATH = os.path.join(HERE, "models", "锚点.npz")
LEDGER_PATH = os.path.join(HERE, "models", "反馈台账.jsonl")
MAX_ANCHORS = 200  # 锚点只留最近 N 段, 跟随音色最新状态
MAX_NEGS = 60      # 负样本(用户否决的"不像")上限


def log_feedback(entry):
    """用户裁决落台账(jsonl), 是阈值校准/高危文本预判的数据底座。"""
    import datetime
    entry = {"date": datetime.date.today().isoformat(), **entry}
    os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
    with open(LEDGER_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

_PUNCT = re.compile(r"[，。！？；、：…,.!?;:\s\"'「」『』()（）]")


def _decode_pcm(src, sr=16000):
    """mp3(路径或bytes) -> 16k 单声道 float32 波形。"""
    if isinstance(src, (bytes, bytearray)):
        cmd = ["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"]
        r = subprocess.run(cmd, input=bytes(src), capture_output=True)
    else:
        cmd = ["ffmpeg", "-v", "error", "-i", src, "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"]
        r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("音频解码失败: " + (r.stderr or b"").decode("utf-8", "replace")[-200:])
    return np.frombuffer(r.stdout, dtype=np.float32)


def text_len(t):
    """有效字数(去标点空白), 算语速用。"""
    return len(_PUNCT.sub("", t or ""))


class VoiceQC:
    """声纹质检器。enabled=False 时所有调用都安全降级。"""

    def __init__(self, cfg=None):
        cfg = cfg or {}
        qc = cfg.get("qc", {})
        self.cfg = qc
        self.enabled = bool(qc.get("enable", True)) and os.path.exists(MODEL_PATH)
        self._extractor = None
        # 并行合成时多线程共用同一个 QC 实例: 两个模型各一把推理锁(懒加载也在锁内, 防双重初始化);
        # 声纹与 ASR 可互相重叠, ffmpeg 解码不加锁全并行
        self._emb_lock = threading.Lock()
        self._asr_lock = threading.Lock()
        self.embs = []     # list[np.ndarray] 已认可段声纹
        self.speeds = []   # list[float]      已认可段语速(字/秒)
        self.negs = []     # list[np.ndarray] 用户否决的"不像"声纹(负样本)
        self._dirty = False
        if self.enabled and os.path.exists(ANCHOR_PATH):
            try:
                d = np.load(ANCHOR_PATH)
                self.embs = [e for e in d["embs"]]
                self.speeds = list(d["speeds"])
                if "negs" in d.files:
                    self.negs = [e for e in d["negs"]]
            except Exception:
                pass

    # ---------------- 内部
    def _ex(self):
        if self._extractor is None:
            import sherpa_onnx
            c = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=MODEL_PATH, num_threads=4)
            self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(c)
        return self._extractor

    def _embed(self, src):
        samples = _decode_pcm(src)
        return self._embed_samples(samples), len(samples) / 16000.0

    def _embed_samples(self, samples):
        with self._emb_lock:
            st = self._ex().create_stream()
            st.accept_waveform(16000, samples)
            st.input_finished()
            v = np.array(self._ex().compute(st), dtype=np.float32)
        return v / (np.linalg.norm(v) + 1e-9)

    @staticmethod
    def _find_pauses(samples, thr=0.008, min_dur=0.12, sr=16000):
        """波形能量法找停顿区间, 返回 [(起,止)秒]。"""
        win = sr // 100
        n = len(samples) // win
        if n == 0:
            return []
        env = np.abs(samples[: n * win]).reshape(n, win).mean(axis=1)
        quiet = env < thr
        out, i = [], 0
        while i < n:
            if quiet[i]:
                j = i
                while j < n and quiet[j]:
                    j += 1
                if (j - i) * win / sr >= min_dur:
                    out.append((i * win / sr, j * win / sr))
                i = j
            else:
                i += 1
        return out

    _ASR_DIR = r"C:\Users\Public\asr-paraformer-ts"

    def _asr(self):
        """字级时间戳 ASR(重音分析+错读闸门用), 懒加载; 模型须在 ASCII 路径(中文路径打不开)。"""
        if not hasattr(self, "_asr_rec"):
            try:
                import sherpa_onnx
                self._asr_rec = sherpa_onnx.OfflineRecognizer.from_paraformer(
                    paraformer=os.path.join(self._ASR_DIR, "model.int8.onnx"),
                    tokens=os.path.join(self._ASR_DIR, "tokens.txt"),
                    num_threads=4, sample_rate=16000, feature_dim=80)
            except Exception:
                self._asr_rec = None
        return self._asr_rec

    def _transcribe(self, samples):
        """转写一次, 重音分析与错读闸门共用。返回 (tokens, timestamps) 或 None。"""
        with self._asr_lock:
            rec = self._asr()
            if rec is None:
                return None
            st = rec.create_stream()
            st.accept_waveform(16000, samples)
            rec.decode_stream(st)
            toks, tss = list(st.result.tokens), list(st.result.timestamps)
        if not toks or len(toks) != len(tss):
            return None
        return toks, tss

    @staticmethod
    def _digits_to_cn(s):
        """转写里偶发的阿拉伯数字 -> 中文读法(对齐 TTS 输入侧的口语化数字)。"""
        D = "零一二三四五六七八九"

        def conv(m):
            n = int(m.group(0))
            if n == 0:
                return "零"
            if n < 10:
                return D[n]
            if n < 100:
                t, o = divmod(n, 10)
                return ("十" if t == 1 else D[t] + "十") + (D[o] if o else "")
            return "".join(D[int(c)] for c in str(n))  # 更大数逐位兜底(罕见)
        return re.sub(r"\d+", conv, s)

    _CER_KEEP = re.compile(r"[一-鿿a-zA-Z0-9]")

    def _norm_for_cer(self, s):
        """CER 对比前归一: 只留汉字/字母/数字, de三兄弟(的/地/得)归一, 数字转中文, 小写。"""
        s = "".join(ch for ch in (s or "") if self._CER_KEEP.fullmatch(ch))
        s = s.translate(str.maketrans("地得", "的的"))  # de三兄弟归一: ASR按书面语法在 的/地/得 间互换(发音同de), 不算错读; 实测慢性误报(地→的5次, 含"地坐→的做")
        return self._digits_to_cn(s).lower()

    @staticmethod
    def _pinyin(s):
        """无声调拼音串(同音宽恕用); 缺 pypinyin 时返回 None。"""
        try:
            from pypinyin import lazy_pinyin
            return " ".join(lazy_pinyin(s))
        except Exception:
            return None

    def cer_check(self, tr, expected_text):
        """错读闸门: ASR 转写 vs TTS 输入文本(替换后的发音文本)。
        同音不同字(他/她、教验被LM写回校验)用拼音宽恕——只报真嫌疑:
        漏读/多读/读成别的音。注意: ASR 的语言模型会把部分多音字错读"纠正"回正字,
        所以这道闸对 漏字/多字/乱码/吞字 可靠, 对声调型错读只能部分捕获(仍以耳朵为准;
        已列管进 tone_sentinels 的字由声调哨兵按基频升降补这个盲区)。"""
        if tr is None or not self.cfg.get("cer_enable", True):
            return None
        exp = self._norm_for_cer(expected_text)
        heard = self._norm_for_cer("".join(tr[0]))
        if len(exp) < 4 or not heard:
            return None
        import difflib
        errs = []
        n_err = 0
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, exp, heard, autojunk=False).get_opcodes():
            if tag == "equal":
                continue
            a, b = exp[i1:i2], heard[j1:j2]
            if set(a + b) <= set("儿"):
                continue  # 儿化音: 声音的自然特征, 文本/转写两侧带不带"儿"都不算错读(实测5/10段误报)
                # 注: 笑声"哈"的个数差异不宽恕(用户质量优先): 那是真实声音差异, 留给闸门盯/耳朵裁决
            if tag == "replace":
                if set(a + b) <= set("的地得"):
                    continue  # 助词de三兄弟: ASR按书面语法归一(如"地"->"的"), 发音同为de, 不算错读(实测5/6听点为此误报)
                pa, pb = self._pinyin(a), self._pinyin(b)
                if pa is not None and pa == pb:
                    continue  # 同音异字 = 转写差异不是错读
                errs.append(f"「{a}」读成「{b}」")
            elif tag == "delete":
                errs.append(f"漏读「{a}」")
            else:
                errs.append(f"多读「{b}」")
            n_err += max(i2 - i1, j2 - j1)
        cer = n_err / len(exp)
        if cer > 0.5:
            return None  # 转写本身崩了(噪声/极端段), 不报垃圾嫌疑
        issues = []
        if errs:
            issues.append(f"错读嫌疑({len(errs)}): " + "；".join(errs[:3]))
        return {"cer": round(cer, 3), "errs": errs, "issues": issues}

    def stress(self, samples, tr=None):
        """重音签名分析: 用户的签名是「句首重砸(+0.62)/句尾轻收(-0.37)」(12397字研究)。
        句界由字间隙(>0.45s)判定, 不依赖文本对齐。返回 {head_z, tail_z, n_sent} 或 None。"""
        if len(samples) < 16000 * 3:
            return None
        if tr is None:
            tr = self._transcribe(samples)
        if tr is None:
            return None
        toks, tss = tr
        if len(toks) < 10:
            return None
        blk_dur = len(samples) / 16000
        cjk = []
        for k, (tok, t0) in enumerate(zip(toks, tss)):
            if not re.fullmatch(r"[一-鿿]", tok):
                continue
            t1 = tss[k + 1] if k + 1 < len(tss) else min(blk_dur, t0 + 0.5)
            gap_next = (tss[k + 1] - t0) if k + 1 < len(tss) else 9.9
            seg = samples[int(t0 * 16000):int(min(t1, blk_dur) * 16000)]
            rms = float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0
            cjk.append([tok, rms, gap_next])
        if len(cjk) < 10:
            return None
        rmss = np.array([c[1] for c in cjk])
        if rmss.std() < 1e-6:
            return None
        # 纯能量口径(时长被停顿污染弃用)。真人基准: 句首+1.01/句尾-0.83(砸出来滑走);
        # 类别: 转折+0.73/程度+0.45 必须砸, 虚词-0.45 必须滑。TTS 句首尾天然同向, 只做异常兜底。
        z = (rmss - rmss.mean()) / rmss.std()
        heads, tails = [0], []
        for k in range(len(cjk) - 1):
            if cjk[k][2] > 0.45:  # 句界(字间隙)
                tails.append(k)
                heads.append(k + 1)
        tails.append(len(cjk) - 1)
        head = float(np.mean(z[heads])); tail = float(np.mean(z[tails]))
        issues = []
        if head < 0.3:
            issues.append(f"句首平{head:+.1f}")
        if tail > -0.3:
            issues.append(f"句尾重{tail:+.1f}")
        pivot = [z[k] for k, c in enumerate(cjk) if c[0] in "但而却最更都全才只"]
        if len(pivot) >= 1 and float(np.mean(pivot)) < 0:
            issues.append("枢纽词没砸")
        func = [z[k] for k, c in enumerate(cjk) if c[0] in "的了着吗呢吧啊嘛得"]
        if len(func) >= 3 and float(np.mean(func)) > 0.3:
            issues.append("虚词过重")
        return {"head_z": round(head, 2), "tail_z": round(tail, 2), "n_sent": len(heads),
                "pivot_z": round(float(np.mean(pivot)), 2) if pivot else None,
                "issues": issues}

    def breath_check(self, samples, sr=16000):
        """呼吸模式检测(实案: 连续两声短呼"呵呵"不自然)。呼吸=能量介于静音与语音间的半静音区间;
        相邻呼吸间隔<0.6s 判连喘 issue, 进韵律体系触发roll换版。源头另有净化参考压基线。"""
        win = sr // 100
        n = len(samples) // win
        if n < 50:
            return None
        env = np.abs(samples[: n * win]).reshape(n, win).mean(axis=1)
        mask = (env > 0.006) & (env < 0.045)
        spans, i = [], 0
        while i < n:
            if mask[i]:
                j = i
                while j < n and mask[j]:
                    j += 1
                if (j - i) * win / sr >= 0.15:
                    spans.append((i * win / sr, j * win / sr))
                i = j
            else:
                i += 1
        gap_thr = float(self.cfg.get("breath_pair_gap", 0.45))  # "呵呵"实测紧挨, 0.6偏敏感会误报正常换气
        pairs = sum(1 for k in range(1, len(spans)) if spans[k][0] - spans[k - 1][1] < gap_thr)
        issues = [f"连续呼吸x{pairs}"] if pairs else []
        return {"n_breath": len(spans), "issues": issues}

    def rhythm(self, samples, text):
        """节奏画像比对: 语速/停顿中位/字每停 vs 真人区间(config qc.rhythm)。
        返回 {rate, pause_med, cpp, issues:[...]}; 段太短(样本不足)返回 None。"""
        rc = self.cfg.get("rhythm", {})
        if not rc.get("enable", True):
            return None
        n = text_len(text or "")
        dur = len(samples) / 16000
        if n < 20 or dur < 4:
            return None
        pauses = self._find_pauses(samples)
        # 段首尾静音不算句间停顿
        inner = [(a, b) for a, b in pauses if a > 0.1 and b < dur - 0.1]
        speech = dur - sum(b - a for a, b in pauses)
        if speech <= 0.5:
            return None
        rate = n / speech
        issues = []
        # 内容自适应区间(语速起伏画像, 2026-06): 真人语速起伏1.55x——
        # 设问/解释密的段语速天然快(钩子/推进), 短宣告密的段天然慢(金句逐字砸)
        lo, hi = float(rc.get("rate_lo", 4.4)), float(rc.get("rate_hi", 5.8))
        kind = "平叙"
        n_hook = len(re.findall(r"什么|吗|呢|为什么|怎么|因为|就是|比如|所以|其实", text))
        sents = [s for s in re.split(r"[。！？…]", text) if s.strip()]
        short_ratio = sum(1 for s in sents if text_len(s) <= 10) / max(1, len(sents))
        if short_ratio >= 0.5:
            lo, hi, kind = lo - 0.5, hi - 0.4, "宣告"   # 金句/短宣告段: 该慢
        elif n_hook * 100 / max(1, n) >= 3:
            lo, hi, kind = lo + 0.3, hi + 0.6, "设问"   # 设问/解释段: 该快
        if rate > hi:
            issues.append(f"语速快{rate:.1f}({kind}段宜≤{hi:.1f})")
        elif rate < lo:
            issues.append(f"语速慢{rate:.1f}({kind}段宜≥{lo:.1f})")
        med = cpp = None
        if len(inner) >= 2:
            med = float(np.median([b - a for a, b in inner]))
            cpp = n / (len(inner) + 1)
            if med > float(rc.get("pause_hi", 0.48)):
                issues.append(f"停顿拖{med:.2f}s")
            elif med < float(rc.get("pause_lo", 0.22)):
                issues.append(f"停顿促{med:.2f}s")
            if cpp > float(rc.get("cpp_hi", 40)):
                issues.append(f"一口气{cpp:.0f}字不停")
        return {"rate": round(rate, 2), "pause_med": round(med, 2) if med else None,
                "cpp": round(cpp, 1) if cpp else None, "kind": kind, "issues": issues}

    def _centroid(self):
        c = np.mean(self.embs, axis=0)
        return c / (np.linalg.norm(c) + 1e-9)

    @property
    def ready(self):
        return self.enabled and len(self.embs) >= 8

    def thresholds(self):
        """(黄线, 红线) 自适应 + 绝对限位。"""
        sims = []
        c_all = self._centroid()
        for e in self.embs:
            sims.append(float(np.dot(e, c_all)))
        mu, sd = float(np.mean(sims)), float(np.std(sims))
        yellow = self.cfg.get("sim_yellow") or min(max(mu - 1.5 * sd, 0.80), 0.92)
        red = self.cfg.get("sim_red") or min(max(mu - 2.5 * sd, 0.75), yellow - 0.02)
        return yellow, red

    # ---------------- 声调哨兵 (2026-06-12)
    _TONE_LIB = os.path.join(HERE, "models", "读音风险库.json")

    def _tone_sentinels(self):
        """风险库 tone_sentinels 表(升/降调哨兵字), 懒加载。"""
        if not hasattr(self, "_tone_sens"):
            try:
                with open(self._TONE_LIB, encoding="utf-8") as f:
                    self._tone_sens = json.load(f).get("tone_sentinels", [])
            except Exception:
                self._tone_sens = []
        return self._tone_sens

    def _f0_track(self, seg):
        """片段(16k单声道)的浊音帧基频轨迹 [(秒, Hz)], 纯numpy自相关法。"""
        sr, win, hop = 16000, 400, 160  # 25ms窗 / 10ms步
        lo, hi = sr // 340, sr // 70    # F0 70~340Hz 对应的周期采样数
        out = []
        for k in range(0, len(seg) - win, hop):
            fr = seg[k:k + win].astype(np.float64)
            fr -= fr.mean()
            if float(np.sqrt((fr ** 2).mean())) < 0.008:
                continue  # 静音/气声帧
            ac = np.correlate(fr, fr, "full")[win - 1:]
            if ac[0] <= 0:
                continue
            ac = ac / ac[0]
            p = int(np.argmax(ac[lo:hi])) + lo
            if ac[p] < 0.45:
                continue  # 周期性不足(清音)
            out.append((k / sr, sr / p))
        return out

    def tone_check(self, samples, tr, tts_text):
        """声调哨兵: 对 tone_sentinels 列管的字, 按字级时间戳截其音频窗, 用基频首尾升降
        判「该降却升/该升却降」型声调错读。背景(实案2026-06-12「不一定」读成bú且roll不愈):
        ASR 的语言模型会把声调错读纠正回正字, 错读闸门完全看不见; 而二声(升)vs四声(降)
        是基频上最易分的一对——哨兵让 roll 挑版第一次能自动避开声调错读。
        浊音帧<4 取证不足时宁可沉默, 不冤枉。"""
        if tr is None or not tts_text or not self.cfg.get("tone_enable", True):
            return None
        sens = self._tone_sentinels()
        if not sens:
            return None
        toks, tss = tr
        heard = "".join(toks)
        c2t = []  # 字符位置->token序号(转写可能混多字符英文token, 字符下标≠token下标)
        for ti, tk in enumerate(toks):
            c2t += [ti] * len(tk)
        errs = []
        for s in sens:
            mats = s.get("match", [])
            if not any(m in tts_text for m in mats):
                continue
            pos = -1
            for m in mats:  # LM 可能把发音文本的替换字写回原词, 两种写法都找
                pos = heard.find(m)
                if pos >= 0:
                    break
            if pos < 0:
                continue
            ci = pos + int(s.get("char", 0))
            if ci >= len(c2t):
                continue
            ti = c2t[ci]
            t0 = float(tss[ti])
            t1 = float(tss[ti + 1]) if ti + 1 < len(tss) else t0 + 0.30
            t1 = min(t1, t0 + 0.35)
            trk = self._f0_track(samples[int(t0 * 16000): int(t1 * 16000)])
            if len(trk) < 4:
                continue
            f0s = [f for _, f in trk]
            med = float(np.median(f0s))
            f0s = [f for f in f0s if 0.55 * med <= f <= 1.8 * med] or f0s  # 剔倍频/半频伪点
            head = float(np.median(f0s[:2]))
            tail = float(np.median(f0s[-2:]))
            delta = (tail - head) / max(head, 1.0)
            # 判据(2026-06-12 对照实验定标): 四声必须"降得足"——用户定罪的两版都是浅降(-5%/-7%),
            # 置信的四声降-20%~-28%; 浅降/平调/上升一律算嫌疑, 不只抓上升
            need = float(s.get("min_move", 0.10))
            want = s.get("expect", "降")
            if (want == "降" and delta > -need) or (want == "升" and delta < need):
                errs.append(f"「{heard[ci]}」{s.get('label', '声调疑错')}(基频{delta * 100:+.0f}%)")
        return errs or None

    # ---------------- 对外
    def score(self, src, text=None, tts_text=None):
        """打分: {sim, neg_sim, dur, speed, rhythm, stress, breath, cer, emb}。
        src 可以是路径或 mp3 bytes; tts_text=送给TTS的发音文本(错读闸门对它比, 默认用 text)。"""
        samples = _decode_pcm(src)
        dur = len(samples) / 16000.0
        emb = self._embed_samples(samples)
        c = self._centroid()
        sim = float(np.dot(emb, c))
        neg_sim = max((float(np.dot(emb, n)) for n in self.negs), default=None) if self.negs else None
        speed = None
        n = text_len(text) if text else 0
        if n and dur > 0.3:
            speed = n / dur
        rhythm = self.rhythm(samples, text) if text else None
        stress = cer = None
        if text:
            want_stress = self.cfg.get("stress_enable", True) and len(samples) >= 16000 * 3
            want_cer = self.cfg.get("cer_enable", True) and dur >= 1.0
            tr = self._transcribe(samples) if (want_stress or want_cer) else None
            if want_stress:
                stress = self.stress(samples, tr)
            if want_cer:
                cer = self.cer_check(tr, tts_text or text)
            tone = self.tone_check(samples, tr, tts_text or text)
            if tone:  # 哨兵命中并入错读嫌疑: 同口径罚分/打印/听点, 下游零改动
                if cer is None:
                    cer = {"cer": 0.0, "errs": [], "issues": []}
                cer["errs"] = list(cer["errs"]) + tone
                cer["issues"] = list(cer.get("issues", [])) + ["声调哨兵: " + "；".join(tone)]
        breath = self.breath_check(samples) if text else None
        return {"sim": sim, "neg_sim": neg_sim, "dur": dur, "speed": speed,
                "rhythm": rhythm, "stress": stress, "breath": breath, "cer": cer, "emb": emb}

    def verdict(self, s):
        """绿/黄/红。短段(<2.5s)声纹天然不可靠: 阈值放宽, 且最差只判黄(不触发自动重roll, 交人裁决)。"""
        yellow, red = self.thresholds()
        short = s.get("dur") is not None and s["dur"] < float(self.cfg.get("short_dur", 2.5))
        if short:
            relax = float(self.cfg.get("short_relax", 0.04))
            yellow -= relax
            red -= relax
        v = "绿"
        if s["sim"] < red:
            v = "红"
        elif s["sim"] < yellow:
            v = "黄"
        if short and v == "红":
            v = "黄"
        if s.get("speed") and self.speeds:
            base = float(np.median(self.speeds))
            dev = abs(s["speed"] - base) / base
            if dev > self.cfg.get("speed_red", 0.40):
                v = "红"
            elif dev > self.cfg.get("speed_warn", 0.25) and v == "绿":
                v = "黄"
        # 自迭代: 比起"像本人", 更像用户否决过的负样本 -> 降一级
        if s.get("neg_sim") is not None and s["neg_sim"] >= s["sim"]:
            v = {"绿": "黄", "黄": "红"}.get(v, v)
        return v

    # ---------------- 用户反馈(自迭代闭环)
    def feedback_good(self, src, text=None, weight=3):
        """用户裁决"最像": 该版声纹按 weight 倍权重滚入锚点, 把音色基准拉向用户耳朵。"""
        emb, dur = self._embed(src)
        for _ in range(max(1, int(weight))):
            self.embs.append(emb)
        n = text_len(text) if text else 0
        if n and dur > 0.3:
            self.speeds.append(n / dur)
        self.embs = self.embs[-MAX_ANCHORS:]
        self.speeds = self.speeds[-MAX_ANCHORS:]
        self._dirty = True
        return emb

    def feedback_bad(self, src):
        """用户裁决"不像": 入负样本库, 并从锚点剔除与之几乎相同的样本(防污染)。"""
        emb, _ = self._embed(src)
        self.embs = [e for e in self.embs if float(np.dot(e, emb)) < 0.995]
        self.negs.append(emb)
        self.negs = self.negs[-MAX_NEGS:]
        self._dirty = True
        return emb

    @property
    def frozen(self):
        """锚点冻结(用户定): 音色标准锁定为现阶段基准, 此后任何样本不得改动声纹锚。"""
        return bool(self.cfg.get("anchor_frozen", False))

    def absorb(self, s):
        """绿段滚入锚点(只吸收高质量样本, 避免污染); 锚点冻结后不再吸收。"""
        if self.frozen:
            return
        self.embs.append(s["emb"])
        if s.get("speed"):
            self.speeds.append(s["speed"])
        self.embs = self.embs[-MAX_ANCHORS:]
        self.speeds = self.speeds[-MAX_ANCHORS:]
        self._dirty = True

    def save(self):
        if not self._dirty or not self.embs:
            return
        os.makedirs(os.path.dirname(ANCHOR_PATH), exist_ok=True)
        kw = dict(embs=np.stack(self.embs), speeds=np.array(self.speeds, dtype=np.float32))
        if self.negs:
            kw["negs"] = np.stack(self.negs)
        np.savez(ANCHOR_PATH, **kw)
        self._dirty = False

    # ---------------- 初始化
    def init_from_output(self, out_dir, preview_path=None):
        """用 output/NNN.mp3(用户已认可的成品段)建立锚点。"""
        texts = {}
        if preview_path and os.path.exists(preview_path):
            for line in open(preview_path, encoding="utf-8"):
                m = re.match(r"(\d{3}) \| \[.*?\] (.*)", line.strip())
                if m:
                    texts[int(m.group(1))] = m.group(2)
        self.embs, self.speeds = [], []
        files = sorted(f for f in os.listdir(out_dir) if re.fullmatch(r"\d{3}\.mp3", f))
        for f in files:
            emb, dur = self._embed(os.path.join(out_dir, f))
            self.embs.append(emb)
            n = text_len(texts.get(int(f[:3]), ""))
            if n and dur > 0.3:
                self.speeds.append(n / dur)
        self._dirty = True
        self.save()
        return len(self.embs)


def main():
    cfg = {}
    cp = os.path.join(HERE, "config.json")
    if os.path.exists(cp):
        cfg = json.load(open(cp, encoding="utf-8"))
    qc = VoiceQC(cfg)
    if not qc.enabled:
        sys.exit("质检不可用: 缺模型 models/campplus_zh-cn_16k.onnx 或 qc.enable=false")

    if "--init" in sys.argv:
        out = os.path.join(HERE, "output")
        n = qc.init_from_output(out, os.path.join(out, "分句预览.txt"))
        yellow, red = qc.thresholds()
        print(f"锚点已建立: {n} 段 -> {ANCHOR_PATH}")
        print(f"自适应阈值: 黄线 {yellow:.4f}, 红线 {red:.4f}")
        if qc.speeds:
            print(f"语速基线: {np.median(qc.speeds):.2f} 字/秒")
        return

    targets = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not targets:
        sys.exit("用法: python 质检.py --init | python 质检.py 文件.mp3 [更多文件...]")
    if not qc.ready:
        sys.exit("锚点不足(需≥8段), 先跑: python 质检.py --init")
    for t in targets:
        p = t if os.path.isabs(t) else os.path.join(HERE, t)
        s = qc.score(p)
        print(f"{os.path.basename(p)}  像本人分数 {s['sim']:.4f}  时长 {s['dur']:.1f}s  判级 {qc.verdict(s)}")


if __name__ == "__main__":
    main()
