# -*- coding: utf-8 -*-
"""
自动化配音 —— FishAudio TTS
用法:
    python 配音.py            # 完整流程: 分句 -> 逐句合成 -> 合并成一条
    python 配音.py --preview  # 只分句并打印/写出预览, 不调用 API(不耗额度)
    python 配音.py 原文.txt    # 指定输入文件(默认 文案.txt)

切分理念(贴合用户手切风格):
  - 尊重原文换行(作者已经分好的行尽量保留, 不强行打散)
  - 破折号 —— 一律去掉并在该处断开(破折号会让 TTS 出问题)
  - (注解) 变成 : 注解
  - 只有"太长"的行才进一步切: 句号/问号/分号/逗号优先, 顿号最后才切
  - 短行 / 顿号列表 / 排比 整体保留, 不机械切碎
配置在 config.json: api_key, voice_id, split_mode, max_chars ...
"""
import sys
import os
import re
import json
import time
import threading
import contextlib
import subprocess
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
API_URL = "https://api.fish.audio/v1/tts"
# 并行合成时由 main() 装上 BoundedSemaphore: 全局限制同时在途的 TTS 请求数(撞限流就排队)
_API_GATE = None

# 各种破折号 / 长横线, 一律删除并在此断句
DASH_RUN = re.compile(r"[—–―－ー]{1,}|-{2,}")
# 进一步切分时的标点优先级(从强到弱), 顿号(、)放最后
DELIM_LEVELS = ["。！？…!?", "；;", "，,", "、"]
# 判断片段里是否有真正的文字(中日韩 + 字母数字)
HAS_TEXT = re.compile(r"[\w㐀-鿿豈-﫿]")
# 章节标题(第X章 / 结尾): 标题前用更长的停顿; "结尾"须独立或带冒号, 防误伤正文句
HEADER_RE = re.compile(r"^(第.{1,6}章|结尾(?=[：:，,]|$))")


# ---------------------------------------------------------------- 质检(声纹"像本人"判级)
def load_qc(cfg):
    """加载 质检.py 的声纹质检器; 缺模型/缺依赖时返回 None, 不影响主流程。"""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("zhijian", os.path.join(HERE, "质检.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        qc = m.VoiceQC(cfg)
        return qc if qc.enabled else None
    except Exception as e:
        print(f"⚠ 质检模块不可用({e}), 本次不做质检")
        return None


# ---------------------------------------------------------------- 配置
def read_config():
    path = os.path.join(HERE, "config.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def require_creds(cfg):
    if not cfg.get("api_key") or "在这里填" in str(cfg.get("api_key")):
        sys.exit("请在 config.json 里填上 FishAudio 的 api_key")
    if not cfg.get("voice_id") or "在这里填" in str(cfg.get("voice_id")):
        sys.exit("请在 config.json 里填上你的声音模型 voice_id")


# ---------------------------------------------------------------- 预处理 / 分句
def _split_at(s, delim_chars):
    """在 delim_chars 标点后断开, 保留标点; 连续标点算一组; 数字里的 . , 不断。"""
    out, buf = [], []
    chars = list(s)
    n = len(chars)
    for i, ch in enumerate(chars):
        buf.append(ch)
        if ch in delim_chars:
            prev = chars[i - 1] if i > 0 else ""
            nxt = chars[i + 1] if i + 1 < n else ""
            if ch in ".," and prev.isdigit() and nxt.isdigit():
                continue  # 小数 / 千分位不断
            if nxt in delim_chars:
                continue  # 等连续标点结束再断
            out.append("".join(buf))
            buf = []
    if buf:
        out.append("".join(buf))
    return [x for x in out if x.strip()]


def _split_long(s, max_chars):
    """只有超过 max_chars 的片段才递归切; 按 DELIM_LEVELS 从强到弱找切点。"""
    s = s.strip()
    if not s:
        return []
    if max_chars > 0 and len(s) <= max_chars:
        return [s]
    for delim in DELIM_LEVELS:
        parts = _split_at(s, delim)
        if len(parts) > 1:
            res = []
            for p in parts:
                res.extend(_split_long(p, max_chars))
            return res
    return [s]  # 找不到切点, 整段保留(交给 TTS 的 chunk_length 处理)


def _convert_parens(line):
    """(注解) -> :注解  ; 开括号变冒号引出解释, 闭括号后面是标点就删、接正文就变逗号给停顿。"""
    res = []
    chars = list(line)
    n = len(chars)
    for i, ch in enumerate(chars):
        if ch in "（(":
            res.append("：")
        elif ch in "）)":
            nxt = chars[i + 1] if i + 1 < n else ""
            if not nxt or nxt in "。！？，、；：…!?,;:":
                continue  # 后面已有标点, 直接去掉
            res.append("，")   # 后面是正文, 给个逗号停顿(也成了切点)
        else:
            res.append(ch)
    return "".join(res)


def split_sentences(text, cfg=None):
    cfg = cfg or {}
    mode = cfg.get("split_mode", "smart")          # smart / verbatim / auto
    max_chars = int(cfg.get("max_chars", 30))
    convert_parens = cfg.get("convert_parens", True)
    if mode == "auto":
        max_chars = 0  # 0 = 每个标点都切(老的机械切分)

    segs = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if convert_parens:
            line = _convert_parens(line)
        if mode == "verbatim":
            # 行是用户的节奏单元: 行内破折号变逗号(不切碎), 只清行首残留逗号;
            # 行尾无终止标点必须补句号 —— 无尾标点的短文本会让 TTS 拖尾/出怪音(实测翻车)
            ln = DASH_RUN.sub("，", line).strip()
            ln = ln.lstrip("，")
            if ln and ln[-1] not in "。！？…；：，、":
                ln += "。"
            if ln:
                segs.append(ln)
            continue
        for chunk in DASH_RUN.split(line):  # 破折号处硬断, 破折号被丢弃
            chunk = chunk.strip()
            if not chunk:
                continue
            segs.extend(_split_long(chunk, max_chars))

    clean = []
    for s in segs:
        s = DASH_RUN.sub("", s).strip()  # 兜底: 残留破折号清掉
        if s and HAS_TEXT.search(s):
            clean.append(s)
    return clean


# 行尾若已是这些停顿/终止标点, 接下一行时不再补逗号
_SKIP_COMMA = "，。！？；、：…"


def naturalize(lines, convert_parens=True, line_period=False):
    """把一段里的多行(软换行)接成自然口语文本: 行尾无停顿标点就补逗号; 破折号->逗号。只动标点不改字。
    line_period=True(节奏模式): 行尾逗号/无标点一律句号化。实测(2026-06, 节奏试验B):
    TTS 对句号的自然停顿(~0.31s/每11字一停)与作者真实停顿画像一致, 整段长请求又远比
    短行独立合成稳定(短行请求会拖尾出怪音)。"""
    parts = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if convert_parens:
            ln = _convert_parens(ln)
        ln = DASH_RUN.sub("，", ln)  # 破折号一律变逗号(无破折号, 给自然停顿)
        ln = ln.lstrip("，、")        # 行首破折号转出的逗号清掉
        if line_period:
            ln = ln.rstrip("，、")
            if ln and ln[-1] not in "。！？…；：":
                ln += "。"
        parts.append(ln)
    text = ""
    for i, ln in enumerate(parts):
        text += ln
        if i != len(parts) - 1 and text and text[-1] not in _SKIP_COMMA:
            text += "，"
    text = re.sub(r"，{2,}", "，", text)          # 连续逗号合一
    text = re.sub(r"，([。！？；：])", r"\1", text)  # 逗号紧跟终止标点 -> 去逗号
    text = re.sub(r"([。！？])，", r"\1", text)
    return text.strip("，")


# ---------------------------------------------------------------- 数字归一化
_CN_DIGIT = "零一二三四五六七八九"


def _cn_under_10000(n):
    units = ["", "十", "百", "千"]
    s, pending_zero = "", False
    digs = str(n)
    L = len(digs)
    for i, ch in enumerate(digs):
        d = int(ch)
        if d == 0:
            pending_zero = True
        else:
            if pending_zero:
                s += "零"
                pending_zero = False
            s += _CN_DIGIT[d] + units[L - 1 - i]
    return s


def cardinal(n):
    """整数 -> 中文基数词: 70->七十, 95->九十五, 470->四百七十, 28->二十八。"""
    if n == 0:
        return "零"
    yi, wan, base = n // 10**8, (n % 10**8) // 10**4, n % 10**4
    res = ""
    if yi:
        res += _cn_under_10000(yi) + "亿"
    if wan:
        if yi and wan < 1000:
            res += "零"
        res += _cn_under_10000(wan) + "万"
    elif yi and base:
        res += "零"
    if base:
        if (yi or wan) and base < 1000:
            res += "零"
        res += _cn_under_10000(base)
    if res.startswith("一十"):   # 十/十几, 不读"一十"
        res = res[1:]
    return res


def normalize_numbers(text):
    """阿拉伯数字 -> 中文读法。千分位剥逗号, 四位数+年逐位读, 小数(含小数百分比)整数部基数词+小数部逐位, N% 读"百分之N", 其余按基数词。"""
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)  # 千分位: 1,000 -> 1000
    text = re.sub(r"(?<!\d)(\d{4})(?=\s*年)",
                  lambda m: "".join(_CN_DIGIT[int(c)] for c in m.group(1)), text)
    # 小数必须先于百分比/整数规则: 否则 99.99% 被切成 "99."+"百分之99" 读出乱码
    # (实案2026-06-11《命运的排除法》, --preview 拦截于送TTS前)
    text = re.sub(r"(\d+)\.(\d+)(?:\s*([%％]))?",
                  lambda m: ("百分之" if m.group(3) else "") + cardinal(int(m.group(1)))
                  + "点" + "".join(_CN_DIGIT[int(c)] for c in m.group(2)), text)
    text = re.sub(r"(\d+)\s*[%％]", lambda m: "百分之" + cardinal(int(m.group(1))), text)
    text = re.sub(r"\d+", lambda m: cardinal(int(m.group(0))), text)
    # 口语略读收窄(两实案对撞: "六百八十试试"拗口宜略成"六百八"; 但"一百五十个小时"被旧版
    # 这条规则吃掉"十"读成"一百五"——2026-06-11《黑粉》耳测点名要全读, 根因不是TTS缩读)。
    # 只在确定安全的语境略"十": ①句尾/停顿前 ②撞shì音前(十试/十是, 本规则的出生案例)
    text = re.sub(r"([一二三四五六七八九]百[一二三四五六七八九])十(?=[试是事世市识视]|[，。！？；、：…\s]|$)", r"\1", text)
    # 口语"两"(实案: "2分钟"应读"两分钟"不是"二分钟"): 单独的"二"接量词/数位时改"两"
    text = re.sub(r"(?<![一二三四五六七八九十])二(?=分钟|个|条|天|年|块|次|遍|句|行|页|步|场|百|千|万)", "两", text)
    return text


_SPEECH_PUNCT = re.compile(r"[，。！？；、：…,.!?;:\s\"'「」『』（）()=\-]")


def _speech_len(t):
    """有效发音字数(去标点/空白), 比 len() 更贴近声纹对段长的感知。"""
    return len(_SPEECH_PUNCT.sub("", t))


def _join(a, b):
    """两段文本拼接: 前段尾部无停顿标点时补逗号。"""
    sep = "" if a[-1] in "。！？…；：，" else "，"
    return a + sep + b


def _merge_short_units(units, min_chars):
    """有效字数不足 min_chars 的段链式并入下一段(末段并入上一段); 章节标题永远独立。
    实验依据(2026-06): 声纹分数天花板与段长强相关, <25有效字的段怎么roll都到不了0.95,
    并段是治本; 标题两侧都不可被并入。"""
    out = []
    pending = None  # (text, gap): 等待并入下一段的短段(可链式累积)

    units = [(t.lstrip("!！").strip() if t[:1] in "!！" else t, gb) for t, gb in units]  # 并段模式下剥强调标记

    def flush_to_prev():
        """下一段不可并(标题/没了)时, 短段回头并入上一段; 上一段是标题则只能独立保留。"""
        nonlocal pending
        if pending is None:
            return
        pt, pgb = pending
        if out and not HEADER_RE.match(out[-1][0]):
            lt, lgb = out[-1]
            out[-1] = (_join(lt, pt), lgb)
        else:
            out.append((pt, pgb))
        pending = None

    for t, gb in units:
        if HEADER_RE.match(t):
            # 标题独立合成天花板仅~0.86, 故并入下一段(音色随长段0.95+);
            # 章节停顿由 悬停.py 对标题尾拉伸到 chapter_gap 补回
            flush_to_prev()
            pending = (t, gb)
            continue
        if pending is not None:
            pt, pgb = pending
            t, gb = _join(pt, t), pgb  # 短段/标题接到本段开头, 停顿沿用其原位置
            pending = None
        # 含标题的段并到40字再停(标题音色要靠足够长的上下文才能上0.95)
        thresh = 40 if HEADER_RE.match(t) else min_chars
        if _speech_len(t) < thresh:
            pending = (t, gb)
        else:
            out.append((t, gb))
    flush_to_prev()
    return out


def _mark_emphasis(units, min_chars, gap_before, gap_after):
    """强调段模式(显式标记制): 段首 ! 标记的段才悬停, 其余段保持作者分段、正常间隔。
    悬停规律研究(2026-06, ASR精确版): 用户习惯是**说完金句再悬停**(悬停前78%是<=15字短句),
    念稿场景停顿上限~0.5s——故金句后停顿(0.65) > 金句前(0.5)。
    防疲劳(用户定): 不是所有金句都悬停, 每批至多1~2处, 由切批时人工标 !。"""
    out = []
    prev_emph = False
    for i, (t, gb) in enumerate(units):
        forced = t[:1] in "!！"
        if forced:
            t = t.lstrip("!！").strip()
        if forced and i > 0:
            gb = max(gb, gap_before)
        elif prev_emph:
            gb = max(gb, gap_after)  # 金句说完, 让它悬一下再起新话头
        out.append((t, gb))
        prev_emph = forced
    return out


def apply_pronounce(text, cfg):
    """配音前的读音替换(如 AI -> 诶爱), 只影响送给 TTS 的文本, 字幕/预览仍用原文。
    映射配置在 config.json 的 "pronounce"。"""
    for k, v in (cfg.get("pronounce") or {}).items():
        text = text.replace(k, v)
    return text


def build_units(text, cfg=None):
    """返回 [(待合成文本, 前置停顿秒), ...]; 按 synth_unit 决定合成粒度。
    paragraph=整段 / sentence=整句 / clause=细切(逗号级) / verbatim=照行。"""
    cfg = cfg or {}
    mode = cfg.get("synth_unit", "paragraph")
    convert_parens = cfg.get("convert_parens", True)
    do_num = cfg.get("normalize_numbers", True)
    sent_gap = float(cfg.get("gap", 0.2))
    para_gap = float(cfg.get("paragraph_gap", 0.35))
    chap_gap = float(cfg.get("chapter_gap", 1.0))

    units = []
    first = True
    for rp in re.split(r"\n\s*\n", text):  # 按空行分段
        lines = [l for l in rp.split("\n") if l.strip()]
        if not lines:
            continue
        is_header = bool(HEADER_RE.match(lines[0].strip()))

        if mode == "clause":
            chunks = split_sentences(rp, cfg)
        elif mode == "verbatim":
            chunks = split_sentences(rp, {**cfg, "split_mode": "verbatim"})
        else:
            natural = naturalize(lines, convert_parens, bool(cfg.get("rhythm_line_end", True)))
            if not natural:
                continue
            if mode == "sentence":
                chunks = [s.strip() for s in re.split(r"(?<=[。！？])", natural) if s.strip()]
            else:  # paragraph
                chunks = [natural]

        for j, t in enumerate(chunks):
            t = t.strip()
            if not t:
                continue
            if do_num:
                t = normalize_numbers(t)
            if first:
                gb = 0.0
                first = False
            elif j == 0:
                gb = chap_gap if is_header else para_gap
            else:
                gb = sent_gap
            units.append((t.strip(), gb))

    min_chars = int(cfg.get("min_unit_chars", 14))
    if mode in ("paragraph", "sentence") and min_chars > 0:
        if cfg.get("emphasis_units", False):  # 强调段模式: 金句独立+悬停
            units = _mark_emphasis(units, min_chars,
                                   float(cfg.get("emphasis_gap", 0.5)),
                                   float(cfg.get("emphasis_gap_after", 0.65)))
        else:                                  # 默认: 短段并入相邻段(音色最稳)
            units = _merge_short_units(units, min_chars)
    return units


# ---------------------------------------------------------------- 调用 TTS
_REF_CACHE = {}


def _load_reference(cfg):
    """真人参考(实验证实: 随请求附带25s原声, 像真人+0.015/像锚点+0.014且更稳)。
    需要 models/参考音频.mp3 + 参考文本.txt + ormsgpack; 缺任一则返回 None 走老路径。"""
    ref = cfg.get("reference", {})
    if not ref.get("enable", True):
        return None
    if "data" not in _REF_CACHE:
        try:
            import ormsgpack  # noqa: F401
            ap = os.path.join(HERE, ref.get("audio", "models/参考音频.mp3"))
            tp = os.path.join(HERE, ref.get("text", "models/参考文本.txt"))
            _REF_CACHE["data"] = {
                "audio": open(ap, "rb").read(),
                "text": open(tp, encoding="utf-8-sig").read().strip(),
            }
        except Exception as e:
            _REF_CACHE["data"] = None
            print(f"⚠ 真人参考加载失败({e}) -> 本进程全程走无参考老路径, 音质会整体掉档!"
                  f" 检查 models/参考音频.mp3 + 参考文本.txt + ormsgpack")
    return _REF_CACHE["data"]


def synthesize(text, cfg):
    body = {
        "text": text,
        "reference_id": cfg["voice_id"],

        # 稳定性参数
        "temperature": cfg.get("temperature", 0.35),
        "top_p": cfg.get("top_p", 0.55),
        "normalize": cfg.get("normalize", True),
        "condition_on_previous_chunks": cfg.get("condition_on_previous_chunks", True),
        "repetition_penalty": cfg.get("repetition_penalty", 1.2),

        # 声音参数
        "prosody": cfg.get("prosody", {"speed": 1, "volume": 0, "normalize_loudness": True}),

        # 输出参数
        "chunk_length": cfg.get("chunk_length", 300),
        "min_chunk_length": cfg.get("min_chunk_length", 50),
        "format": cfg.get("format", "mp3"),
        "sample_rate": cfg.get("sample_rate", 44100),
        "mp3_bitrate": cfg.get("mp3_bitrate", 192),
        "latency": cfg.get("latency", "normal"),
    }

    ref = _load_reference(cfg)
    if ref:
        import ormsgpack
        body["references"] = [ref]  # voice_id + 真人参考叠加(实验B配置)
        data = ormsgpack.packb(body)
        ctype = "application/msgpack"
    else:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        ctype = "application/json"
    req = urllib.request.Request(API_URL, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {cfg['api_key']}")
    req.add_header("Content-Type", ctype)
    if cfg.get("model"):
        req.add_header("model", cfg["model"])  # s2-pro / speech-1.6 ...

    last_err = None
    for attempt in range(1, 5):
        try:
            with (_API_GATE or contextlib.nullcontext()):  # 并行时限制同时在途请求数
                with urllib.request.urlopen(req, timeout=180) as resp:
                    return resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:300]
            last_err = f"HTTP {e.code}: {detail}"
            if e.code in (429, 500, 502, 503, 504):
                # 429=限流: 退避加倍, 让别的在途请求先消化
                time.sleep((4 if e.code == 429 else 2) * attempt)
                continue
            break
        except Exception as e:
            last_err = str(e)
            time.sleep(2 * attempt)
    raise RuntimeError(last_err)


# ---------------------------------------------------------------- 合并
def _probe_audio(path):
    """探测音频的声道数和采样率, 让插入的静音跟配音完全一致(避免合并报错)。"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=channels,sample_rate", "-of", "json", path],
        capture_output=True,  # 读字节, 自己安全解码(Windows 下 ffmpeg 输出非 gbk 会崩)
    )
    try:
        st = json.loads(r.stdout.decode("utf-8", "replace"))["streams"][0]
        return int(st.get("channels", 1)), int(st.get("sample_rate", 44100))
    except Exception:
        return 1, 44100


def merge_audio(items, out_path, fmt, cfg):
    """items: [(seg_path, gap_before_秒), ...] —— 每段前按需插入静音。"""
    out_dir = os.path.dirname(out_path)
    br = cfg.get("mp3_bitrate", 192)
    ch, sr = _probe_audio(items[0][0]) if items else (1, 44100)
    cl = "mono" if ch == 1 else "stereo"

    sil_cache = {}  # 时长 -> 静音文件路径(同一时长只生成一次)

    def silence_for(d):
        d = round(float(d), 3)
        if d <= 0:
            return None
        if d not in sil_cache:
            sp = os.path.join(out_dir, f"_sil_{int(d * 1000)}.{fmt}")
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-t", str(d),
                 "-i", f"anullsrc=r={sr}:cl={cl}", "-b:a", f"{br}k", sp],
                capture_output=True,
            )
            sil_cache[d] = sp
        return sil_cache[d]

    def ff(p):  # 绝对路径 + 正斜杠, ffmpeg concat 在 Windows 也认
        return os.path.abspath(p).replace("\\", "/")

    list_path = os.path.join(out_dir, "_concat.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for i, (p, gb) in enumerate(items):
            if i > 0:
                sp = silence_for(gb)
                if sp:
                    f.write(f"file '{ff(sp)}'\n")
            f.write(f"file '{ff(p)}'\n")

    has_sil = bool(sil_cache)
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", ff(list_path)]
    # 插了静音就重编码并统一声道/采样率, 否则直接 copy
    cmd += (["-c", "copy"] if not has_sil
            else ["-ar", str(sr), "-ac", str(ch), "-b:a", f"{br}k"])
    cmd += [ff(out_path)]
    r = subprocess.run(cmd, capture_output=True)  # 读字节, 自己安全解码

    for sp in list(sil_cache.values()) + [list_path]:
        if os.path.exists(sp):
            os.remove(sp)
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace")[-500:]
        print("⚠ 合并失败(逐句文件已保存), ffmpeg 报错:\n" + err)
        return False
    return True


# ---------------------------------------------------------------- 字幕(SRT)
def _dur_of(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True,
    )
    try:
        return float(json.loads(r.stdout.decode("utf-8", "replace"))["format"]["duration"])
    except Exception:
        return 0.0


def _srt_ts(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"


_CUE_END_PUNCT = "，。！？；、：…,.!?;:"


def write_srt(items, out_path):
    """items: [(seg_path, gap_before, 文本)] -> SRT。
    段起止用真实音频时长精确打点; 段内按字数比例细分到句(近似)。
    字幕行尾一律不留标点, 句中逗号保留。"""
    idx, t, lines = 1, 0.0, []
    for i, (p, gb, text) in enumerate(items):
        if i > 0:
            t += float(gb)
        dur = _dur_of(p)
        sents = [s for s in re.split(r"(?<=[。！？…])", text) if s.strip()]
        if not sents:
            sents = [text]
        weights = [max(1, len(re.sub(r"[，。！？；、：…,.!?;:\s]", "", s))) for s in sents]
        total = sum(weights)
        st = t
        for s, w in zip(sents, weights):
            d = dur * w / total
            cue = s.strip().rstrip(_CUE_END_PUNCT)
            lines += [str(idx), f"{_srt_ts(st)} --> {_srt_ts(st + d)}", cue, ""]
            idx += 1
            st += d
        t += dur
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------- 读音风险预检
RISK_LIB = os.path.join(HERE, "models", "读音风险库.json")


def _rule_stat(entry):
    st = entry.get("stat") or {}
    return int(st.get("对", 0)), int(st.get("错", 0))


def check_pronounce_risks(units, cfg):
    """对照 models/读音风险库.json 扫描文案, 返回命中清单(听点)。
    已入 config.pronounce 的词视为已修跳过; 多次被耳朵确认"没问题"的规则自动静默
    (听点反馈回流: python 反馈.py <段号> 听点对/听点错)。库是活的, 误报率随反馈收敛。"""
    if not os.path.exists(RISK_LIB):
        return [], 0
    try:
        lib = json.load(open(RISK_LIB, encoding="utf-8-sig"))
    except Exception:
        return [], 0
    fixed = set((cfg.get("pronounce") or {}).keys())
    mute_after = int(lib.get("_mute_after", 5))
    hits, muted = [], 0

    def alive(entry):
        ok, bad = _rule_stat(entry)
        return not (bad == 0 and ok >= mute_after)

    for i, (t, _) in enumerate(units, 1):
        for p in lib.get("patterns", []):
            m = re.search(p["re"], t)
            if not m:
                continue
            if not alive(p):
                muted += 1
                continue
            hits.append({"seg": i, "word": m.group(0), "hint": p["hint"],
                         "src": "预检", "kind": "pattern", "key": p["re"], "_stat": _rule_stat(p)})
        for w in lib.get("words", []):
            if w["w"] not in t:
                continue
            if any(w["w"] in k for k in fixed):
                continue  # 已在读音表里修过
            if not alive(w):
                muted += 1
                continue
            hits.append({"seg": i, "word": w["w"], "hint": w["hint"],
                         "src": "预检", "kind": "word", "key": w["w"], "_stat": _rule_stat(w)})
    return hits, muted


def print_risks(hits, muted=0):
    if not hits and not muted:
        return
    if hits:
        print(f"\n⚠ 读音风险预检: 命中 {len(hits)} 处, 合成后请重点听这些位置")
        for h in hits:
            ok, bad = h.get("_stat", (0, 0))
            tail = f"  (历史: ✅{ok}/❌{bad})" if (ok or bad) else ""
            print(f"  段{h['seg']:03d}  「{h['word']}」 {h['hint']}{tail}")
        print("  听完反馈: python 反馈.py <段号> 听点对/听点错  (规则误报率随反馈自动收敛)")
    if muted:
        print(f"  (另有 {muted} 处命中已静默: 历史多次确认无误)")


def print_layout_risks(units, cfg):
    """版式预检: "只有长段保证质量"(用户2026-06-11点名的教训)是双维度——
    段长(≥40有效字)且段内别切碎(句号间均≥8字)。实案《命运的排除法》: 段长够,
    但短句逐行句号化(行均4~7字)=变相把长段拆回短段, 整批耳测掉档、5/7段掉出0.95;
    恢复"原文标点=停顿地图"后回0.95+。此检查=机器拦截, 不靠排版人的记性。"""
    if not cfg.get("rhythm_line_end", True):
        return
    warns = []
    for i, (t, gb) in enumerate(units, 1):
        if HEADER_RE.match(t):
            continue
        n = _speech_len(t)
        pieces = [p for p in re.split(r"[。！？]", t) if _speech_len(p) > 0]
        avg = n / max(1, len(pieces))
        if n < 40:
            warns.append(f"  段{i:03d}  仅{n}有效字(<40, 音色天花板受限) —— 建议并入相邻段或补足内容")
        elif avg < 8:
            warns.append(f"  段{i:03d}  句号间平均{avg:.0f}字(<8, 段内切碎=变相短段) —— 建议合行: 行尾只落原文句号, 句内用逗号")
    if warns:
        print(f"\n⚠ 版式预检: {len(warns)} 段有长度/切碎风险(天花板定律: 只有长段保质量)")
        for w in warns:
            print(w)


# ---------------------------------------------------------------- 输出目录定位
LATEST_PTR = os.path.join(HERE, "models", "最近输出.txt")


def latest_out_dir():
    """最近一次合成的输出目录(反馈/重roll/合并默认跟着它走), 没有则回退 output。"""
    if os.path.exists(LATEST_PTR):
        d = open(LATEST_PTR, encoding="utf-8-sig").read().strip()
        if d and os.path.isdir(d):
            return d
    return os.path.join(HERE, "output")


def remember_out_dir(d):
    os.makedirs(os.path.dirname(LATEST_PTR), exist_ok=True)
    with open(LATEST_PTR, "w", encoding="utf-8") as f:
        f.write(os.path.abspath(d))


# ---------------------------------------------------------------- 主流程
def main():
    raw = sys.argv[1:]

    def take_opt(name):
        if name in raw:
            k = raw.index(name)
            if k + 1 < len(raw):
                v = raw[k + 1]
                del raw[k:k + 2]
                return v
        return None

    out_arg = take_opt("--out")          # 输出目录
    gap_arg = take_opt("--gap")          # 行/句间停顿(秒), 覆盖 config
    pgap_arg = take_opt("--pgap")        # 段间停顿(秒), 覆盖 config
    mc_arg = take_opt("--minchars")      # 并段阈值(有效字), 覆盖 config.min_unit_chars
    #   用法: 格言体/短段密集批次 --minchars 55 -> 全文并成90~140字大段, 金句埋长段保0.95
    #   (2026-06-11 用户定: 0.93绝对不行, 不为金句分行——强调一律交给悬停拉伸)
    preview_only = "--preview" in raw
    positional = [a for a in raw if not a.startswith("--")]
    input_file = positional[0] if positional else "文案.txt"
    input_path = input_file if os.path.isabs(input_file) else os.path.join(HERE, input_file)

    if not os.path.exists(input_path):
        sys.exit(f"找不到文案文件: {input_path}")
    with open(input_path, "r", encoding="utf-8-sig") as f:  # -sig: 兼容带BOM的文件
        text = f.read()

    cfg = read_config()
    if "--verbatim" in raw:   # 照输入文件的行原样配音(不再重切), 用于手改后的切分稿
        cfg["synth_unit"] = "verbatim"
    if gap_arg is not None:
        cfg["gap"] = float(gap_arg)
    if pgap_arg is not None:
        cfg["paragraph_gap"] = float(pgap_arg)
    if mc_arg is not None:
        cfg["min_unit_chars"] = int(mc_arg)
    units = build_units(text, cfg)  # [(待合成文本, 前置停顿秒), ...]
    if not units:
        sys.exit("文案为空 / 没有可配音的内容")

    if out_arg:
        out_dir = out_arg if os.path.isabs(out_arg) else os.path.join(HERE, out_arg)
    else:
        out_dir = os.path.join(HERE, "output")
    os.makedirs(out_dir, exist_ok=True)
    preview_path = os.path.join(out_dir, "分句预览.txt")
    with open(preview_path, "w", encoding="utf-8") as f:
        for i, (t, gb) in enumerate(units, 1):
            f.write(f"{i:03d} | [{gb:>4.2f}s] {t}\n")
    print(f"共 {len(units)} 段 (粒度={cfg.get('synth_unit', 'paragraph')}), 预览写入 {preview_path}")
    for i, (t, gb) in enumerate(units, 1):
        print(f"  {i:03d} | [{gb:>4.2f}s] {t[:40]}")

    print_layout_risks(units, cfg)
    risk_hits, risk_muted = check_pronounce_risks(units, cfg)
    print_risks(risk_hits, risk_muted)

    if preview_only:
        print("\n[预览模式] 没有调用 API。确认无误后, 去掉 --preview 再跑。")
        return

    require_creds(cfg)
    qc = load_qc(cfg)
    qc_on = qc is not None and qc.ready
    if qc is not None and not qc_on:
        print("⚠ 质检锚点不足(<8段): 本次只生成不判级。跑完认可后执行 python 质检.py --init 建锚点。")
    auto_reroll = int(cfg.get("qc", {}).get("auto_reroll", 3))
    # 自动重roll的字符预算(熔断), 防止极端情况烧额度
    budget = int(sum(len(t) for t, _ in units) * float(cfg.get("qc", {}).get("reroll_budget", 0.5)))

    print(f"\n开始合成 (voice_id={cfg['voice_id']}, model={cfg.get('model', '默认')}, "
          f"粒度={cfg.get('synth_unit', 'paragraph')}, 质检={'开' if qc_on else '关'}) ...")
    fmt = cfg.get("format", "mp3")
    done = []    # [(seg_path, gap_before, 文本), ...]
    report = []  # [(idx, sim, speed, verdict, rolls, rhythm, prosody_issues, cer_errs), ...]
    chars_sent = 0  # 实际送 TTS 的字符数(含roll) = API 成本代理, 进批次指标台账
    qc_cfg = cfg.get("qc", {})
    target = float(qc_cfg.get("target_sim", 0))  # 0=只有红段才roll
    tiers = qc_cfg.get("target_tiers")           # [[有效字数下限, 目标分], ...] 字数降序
    short_dur = float(qc_cfg.get("short_dur", 2.5))

    def target_for(t):
        """分层目标分: 实测天花板与段长强相关(短句~0.91/中段~0.93/长段0.95+), 按现实定目标。"""
        if tiers:
            n = _speech_len(t)
            for min_n, tg in tiers:
                if n >= int(min_n):
                    return float(tg)
        return target

    rhythm_reroll = int(qc_cfg.get("rhythm_reroll", 3))
    rhythm_penalty = float(qc_cfg.get("rhythm_penalty", 0.01))
    cer_penalty = float(qc_cfg.get("cer_penalty", 0.02))

    def prosody_issues(s):
        """节奏+重音+呼吸问题合集(罚分与roll触发共用)。"""
        out = []
        for k in ("rhythm", "stress", "breath"):
            if s.get(k):
                out += s[k]["issues"]
        return out

    def cer_errs(s):
        """ASR 错读闸门的嫌疑清单(漏读/多读/读成别的音; 同音异字与儿化已宽恕)。"""
        return (s.get("cer") or {}).get("errs", [])

    def comp_score(s):
        """综合分: 音色分 - 韵律罚分 - 错读罚分。roll 挑版用它;
        错读罚分(0.02/处)比韵律(0.01/处)重: 有错读嫌疑的版本要明显更像才赢。"""
        return (s["sim"] - rhythm_penalty * len(prosody_issues(s))
                - cer_penalty * len(cer_errs(s)))

    def need_roll(bs, t, rolls):
        """红段必roll; 标题只在红时roll; 短段不强roll; 音色不达标roll;
        音色达标但节奏⚠/错读嫌疑时, 额外给最多 rhythm_reroll 次机会换干净版本。"""
        if qc.verdict(bs) == "红":
            return True
        if HEADER_RE.match(t) and _speech_len(t) < 25:
            return False  # 纯标题段(未并段时)天花板~0.86, 不为目标分白烧
        if bs.get("dur") is not None and bs["dur"] < short_dur:
            return False
        tg = target_for(t)
        if bool(tg) and bs["sim"] < tg:
            return True
        return bool(prosody_issues(bs) or cer_errs(bs)) and rolls < rhythm_reroll

    def roll_quota(bs, t, rolls):
        """本段还允许roll几次: 红/音色不达标走 auto_reroll 大配额;
        仅韵律⚠/错读嫌疑走 rhythm_reroll 小配额(与 need_roll 同口径, 防止并发波次超烧)。"""
        tg = target_for(t)
        if qc.verdict(bs) == "红" or (bool(tg) and bs["sim"] < tg):
            return auto_reroll - rolls
        return min(auto_reroll, rhythm_reroll) - rolls

    # ---- 并行施工: 工人队列(谁空谁领下一段) + 段内波次并发roll + 全局限流闸。
    # 段与段零依赖(每段独立请求, 锚点冻结后打分与顺序无关), 并行不改变产物语义;
    # 锚点未冻结时绿段滚入会随完成顺序漂移 -> 强制单工人保证可复现。
    par = cfg.get("parallel", {}) or {}
    workers = max(1, int(par.get("workers", 1)))
    roll_width = max(1, int(par.get("roll_width", 1)))
    if "--serial" in raw:
        workers = roll_width = 1
    if qc_on and workers > 1 and not qc.frozen:
        workers = 1
        print("⚠ 锚点未冻结(绿段滚入随完成顺序漂移), 本次退回单工人串行")
    global _API_GATE
    api_cap = max(1, int(par.get("api_concurrency", 4)))
    if workers * roll_width > 1:
        _API_GATE = threading.BoundedSemaphore(api_cap)
        print(f"⚡ 并行: {workers} 工人 × 段内 {roll_width} 路roll, 在途请求上限 {api_cap} (撞限流自动退避)")

    state = {"budget": budget, "chars": 0, "starved": []}
    st_lock = threading.Lock()
    print_lock = threading.Lock()
    cand_pool = ThreadPoolExecutor(max_workers=workers * roll_width) if roll_width > 1 else None

    def do_segment(i, t, gb):
        """一个段的完整流水: 合成 -> 质检 -> (波次并发)roll -> 落盘。线程安全。
        返回 (i, 段路径|None, gb, t, 报告行|None)。"""
        seg_path = os.path.join(out_dir, f"{i:03d}.{fmt}")
        tts_text = apply_pronounce(t, cfg)
        try:
            audio = synthesize(tts_text, cfg)
        except Exception as e:
            with print_lock:
                print(f"  ✗ 第 {i:03d} 段失败: {e}")
                print("    跳过这段, 继续。可稍后单独重跑。")
            return (i, None, gb, t, None)
        with st_lock:
            state["chars"] += len(tts_text)
        tag = ""
        row = None
        if qc_on:
            try:
                bs = qc.score(audio, t, tts_text=tts_text)
                rolls = 0
                arch = None

                def rh_mark(s):
                    mk = ""
                    if not (s.get("rhythm") is None and s.get("stress") is None):
                        mk = "_韵律✓" if not prosody_issues(s) else "_韵律⚠"
                    if cer_errs(s):
                        mk += "_疑错读"
                    return mk

                def make_cand():
                    cand = synthesize(tts_text, cfg)
                    with st_lock:
                        state["chars"] += len(tts_text)
                    return cand, qc.score(cand, t, tts_text=tts_text)

                # 不达标当场重roll: 一波 roll_width 个候选并发生成, 按综合分(音色-节奏-错读罚分)挑版;
                # 波内可能比串行多烧≤(width-1)个候选, 换 roll 纵深变横宽; 所有版本留档可审计
                while need_roll(bs, t, rolls) and rolls < auto_reroll:
                    with st_lock:
                        width = min(roll_width, roll_quota(bs, t, rolls), state["budget"] // max(1, len(t)))
                        if width > 0:
                            state["budget"] -= width * len(t)
                    if width <= 0:
                        if roll_quota(bs, t, rolls) > 0:  # 配额还在却没预算 = 预算饿死, 必须出声
                            with st_lock:                 # (实案2026-06-11: 0.901的段一次没roll就静默放行)
                                state["starved"].append(i)
                        break
                    if arch is None:
                        arch = os.path.join(out_dir, f"roll留档_{i:03d}")
                        if os.path.isdir(arch):  # 清掉上次运行的留档, 避免混档
                            for old in os.listdir(arch):
                                os.remove(os.path.join(arch, old))
                        os.makedirs(arch, exist_ok=True)
                        with open(os.path.join(arch, f"初版_像{bs['sim']:.3f}{rh_mark(bs)}.{fmt}"), "wb") as f:
                            f.write(audio)
                    base = rolls
                    rolls += width  # 失败的候选也计数, 与预算扣减口径一致
                    cands = []
                    if cand_pool is not None and width > 1:
                        for fu in [cand_pool.submit(make_cand) for _ in range(width)]:
                            try:
                                cands.append(fu.result())
                            except Exception:
                                pass
                    else:
                        for _ in range(width):
                            try:
                                cands.append(make_cand())
                            except Exception:
                                break
                    if not cands:
                        break  # 整波全失败(网络/限流耗尽), 不死磕
                    for k, (cand, cs) in enumerate(cands, 1):
                        with open(os.path.join(arch, f"roll{base + k}_像{cs['sim']:.3f}{rh_mark(cs)}.{fmt}"), "wb") as f:
                            f.write(cand)
                        if comp_score(cs) > comp_score(bs):
                            audio, bs = cand, cs
                if arch is not None:  # 最终采用的版本也放进留档, 文件夹内即可完整对比
                    with open(os.path.join(arch, f"已采用_像{bs['sim']:.3f}{rh_mark(bs)}.{fmt}"), "wb") as f:
                        f.write(audio)
                verdict = qc.verdict(bs)
                if verdict == "绿":
                    qc.absorb(bs)  # 绿段滚入锚点(锚点冻结时为空操作)
                pi = prosody_issues(bs)
                ce = cer_errs(bs)
                rh_tag = ""
                if bs.get("rhythm") is not None or bs.get("stress") is not None:
                    rh_tag = " 韵律✓" if not pi else " 韵律⚠(" + ",".join(pi) + ")"
                if ce:
                    rh_tag += " 👂错读嫌疑:" + "；".join(ce[:2])
                if i in state["starved"]:
                    rh_tag += " ⚠预算尽"
                row = (i, bs["sim"], bs.get("speed"), verdict, rolls, bs.get("rhythm"), pi, ce)
                tag = f"  像{bs['sim']:.3f}{verdict}" + (f" roll{rolls}" if rolls else "") + rh_tag
            except Exception as e:
                tag = f"  ⚠质检异常({e}), 本段按未质检落盘"
        with open(seg_path, "wb") as f:
            f.write(audio)
        with print_lock:
            print(f"  ✓ {i:03d}/{len(units)}  [{gb:.2f}s]{tag} {t[:24]}")
        return (i, seg_path, gb, t, row)

    # 长段先开工(LPT调度): 难啃的段不留到队尾拖工期; 串行时保持 001 起的自然顺序
    order = list(range(len(units)))
    if workers > 1:
        order.sort(key=lambda k: -_speech_len(units[k][0]))
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as seg_pool:
        for fu in as_completed([seg_pool.submit(do_segment, k + 1, units[k][0], units[k][1]) for k in order]):
            r = fu.result()
            results[r[0]] = r
    if cand_pool is not None:
        cand_pool.shutdown(wait=False)

    for i in sorted(results):
        _, seg_path, gb, t, row = results[i]
        if seg_path is None:
            continue
        done.append((seg_path, gb if done else 0.0, t))  # 第一段保留的停顿永远是0
        if row is not None:
            report.append(row)
    chars_sent = state["chars"]

    if not done:
        sys.exit("一句都没合成成功, 请检查 api_key / voice_id / 网络。")
    remember_out_dir(out_dir)  # 反馈.py / 重roll.py / 合并.py 自动跟到这个目录

    if qc_on:
        qc.save()
        rp = os.path.join(out_dir, "质检报告.txt")
        with open(rp, "w", encoding="utf-8") as f:
            for i, sim, spd, v, r, rh, pi, ce in report:
                s_spd = f" 语速{spd:.2f}" if spd else ""
                s_rh = ""
                if rh is not None or pi:
                    s_rh = "  韵律✓" if not pi else "  韵律⚠(" + ",".join(pi) + ")"
                if ce:
                    s_rh += "  👂错读嫌疑: " + "；".join(ce)
                if rh is not None:
                    s_rh += f" [发声{rh['rate']}字/s" + (f", 停顿中位{rh['pause_med']}s" if rh['pause_med'] else "") + (f", 每停{rh['cpp']}字" if rh['cpp'] else "") + "]"
                f.write(f"{i:03d}  像本人{sim:.4f}{s_spd}  {v}" + (f"  自动roll{r}次" if r else "") + s_rh + "\n")
        bad = [(i, sim, v, r) for i, sim, spd, v, r, rh, pi, ce in report if v != "绿"]
        if bad:
            print("\n🟡🔴 建议人工复听的段(绿段机器已替你听过, 可跳过):")
            for i, sim, v, r in bad:
                print(f"  {v} {i:03d}  像本人 {sim:.3f}" + (f"  (已自动roll{r}次仍未达绿)" if r else ""))
            print(f"  逐个重roll: python 重roll.py <段号>   质检明细: {rp}")
        else:
            print(f"\n✅ 质检全绿, 无需人工复听。明细: {rp}")
        asr_sus = [(i, ce) for i, sim, spd, v, r, rh, pi, ce in report if ce]
        if asr_sus:
            print("👂 错读闸门嫌疑(roll后仍存, 请耳朵裁决):")
            for i, ce in asr_sus:
                print(f"  段{i:03d}  " + "；".join(ce))
        if state["starved"]:
            sv = sorted(set(state["starved"]))
            print("⚠ roll预算耗尽, 这些段没拿到足额roll(当前分数是凑合不是天花板):")
            print("  段 " + ", ".join(f"{k:03d}" for k in sv)
                  + f"   补法: python 重roll.py <段号> 6 (单段不占批次预算), 或调大 qc.reroll_budget(当前{cfg['qc'].get('reroll_budget', 3.0)}x)")

        # ---- 听点持久化: 预检命中 + ASR嫌疑 -> 听点.json (反馈.py 听点对/听点错 回流风险库)
        tingdian = [{k: v for k, v in h.items() if k != "_stat"} for h in risk_hits]
        for i, ce in asr_sus:
            for e in ce:
                tingdian.append({"seg": i, "word": e, "hint": "ASR错读闸门嫌疑",
                                 "src": "ASR", "kind": "asr", "key": e})
        if tingdian:
            with open(os.path.join(out_dir, "听点.json"), "w", encoding="utf-8") as f:
                json.dump(tingdian, f, ensure_ascii=False, indent=1)
            print(f"👂 共 {len(tingdian)} 个听点已存 听点.json, 听完反馈: python 反馈.py <段号> 听点对/听点错")

        # ---- 批次指标(复利仪表盘): 成本/一次过率/复听负担, 追加到 models/批次指标.jsonl
        durs = {}
        for p, _, _ in done:
            try:
                durs[int(os.path.basename(p)[:3])] = _dur_of(p)
            except Exception:
                pass
        total_min = sum(durs.values()) / 60
        ear_segs = {i for i, sim, spd, v, r, rh, pi, ce in report if v != "绿" or ce}
        ear_segs |= {h["seg"] for h in risk_hits}
        listen_min = sum(durs.get(i, 0) for i in ear_segs) / 60
        sims = [sim for i, sim, spd, v, r, rh, pi, ce in report]
        rolls_total = sum(r for i, sim, spd, v, r, rh, pi, ce in report)
        first_pass = sum(1 for i, sim, spd, v, r, rh, pi, ce in report if r == 0) / max(1, len(report))
        metrics = {"date": time.strftime("%Y-%m-%d"), "batch": os.path.basename(out_dir),
                   "segs": len(units), "ok_segs": len(done), "audio_min": round(total_min, 2),
                   "chars_sent": chars_sent, "rolls": rolls_total, "first_pass": round(first_pass, 3),
                   "listen_min": round(listen_min, 2),
                   "listen_ratio": round(listen_min / total_min, 3) if total_min else None,
                   "avg_sim": round(sum(sims) / len(sims), 4) if sims else None,
                   "min_sim": round(min(sims), 4) if sims else None}
        with open(os.path.join(HERE, "models", "批次指标.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(metrics, ensure_ascii=False) + "\n")
        with open(rp, "a", encoding="utf-8") as f:
            f.write(f"\n批次指标: 成品{total_min:.1f}分钟 | 送TTS {chars_sent}字(roll{rolls_total}次, "
                    f"一次过率{first_pass:.0%}) | 复听负担{listen_min:.1f}分钟({metrics['listen_ratio'] or 0:.0%}) "
                    f"| 像本人均值{metrics['avg_sim']}\n")
        print(f"\n📈 本批指标: 成品 {total_min:.1f} 分钟 | 送TTS {chars_sent} 字(含roll{rolls_total}次, "
              f"一次过率 {first_pass:.0%}) | 复听负担 {listen_min:.1f} 分钟"
              f" | 台账: models\\批次指标.jsonl")

    if cfg.get("merge", True):
        merged = os.path.join(out_dir, f"合并.{fmt}")
        if merge_audio([(p, g) for p, g, _ in done], merged, fmt, cfg):
            srt = os.path.join(out_dir, "合并.srt")
            write_srt(done, srt)
            print(f"\n✅ 完成! 整段配音: {merged}\n   同步字幕: {srt}")
    print(f"逐句音频在: {out_dir}")
    if qc_on:
        print("听完可反馈训练系统(越用越准): python 反馈.py <段号> 最像/不像/读错")


if __name__ == "__main__":
    main()
