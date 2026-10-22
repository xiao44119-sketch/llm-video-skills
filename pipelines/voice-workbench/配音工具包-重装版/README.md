# 配音工具包 · 重装版 · 快速上手

> **重装版**：在精简版基础上**已自带公开声纹模型 CAM++**（`models/campplus_zh-cn_16k.onnx`，27MB），少下一个。
> 仍需自备：Fish 密钥、ASR 模型、你自己的音色参考。

基于 **Fish Audio TTS** 的自动配音流水线：段级长请求合成 → 整段拼接 → 自动出 SRT 字幕 → 本地声纹质检（"像本人分数"）+ 低分自动重 roll → 金句悬停 → 加 BGM → 语料导出。

> 合成本身**零第三方依赖**（纯 Python 标准库 + ffmpeg），插上你的 Fish 密钥就能跑。
> 质检/自动重 roll 才需要装 numpy/sherpa-onnx + ASR 模型（CAM++ 已附）。

---

## 0. 前置

- **Python 3.8+**
- **ffmpeg**（装好、在 PATH 里：`ffmpeg -version` 能出版本）
- 一个 **Fish Audio** 账号 + 克隆好的音色（拿到 `voice_id`）

## 1. 配置密钥（必做）

```bash
copy config.example.json config.json      # Windows
# cp config.example.json config.json       # macOS/Linux
```

打开 `config.json`，填两项：
- `api_key`：你的 Fish Audio 密钥
- `voice_id`：你的音色 ID

> ⚠️ **`config.json` 含密钥——别外传、别提交到公开仓库**（`.gitignore` 已帮你挡掉）。本包**不含**任何人的密钥。

## 2. 只想先合成？（最省事）

把 `config.json` 里 `qc.enable` 设为 `false`，跳过模型，直接：

```bash
python 配音.py --preview      # 免费预览分句+版式/读音预检, 不调 API
python 配音.py                # 正式合成 -> output/合并.mp3 + 合并.srt
```

## 3. 开启质检 + 自动重 roll（推荐）

质检靠"像本人分数"= 合成音 vs **你音色锚点**的相似度，低于 0.95 自动重 roll 换版。需要：

**a) 声纹模型 CAM++ — ✅ 本版已自带**
`models/campplus_zh-cn_16k.onnx` 已随包附上，**无需下载**，直接看 b)。

**b) ASR 模型（字级时间戳，错读闸门 + 声调哨兵用）**
下载 sherpa-onnx 的 **paraformer-zh（带时间戳）** 模型，把 `model.int8.onnx` + `tokens.txt` 放到：
```
C:\Users\Public\asr-paraformer-ts\
```
> ⚠️ **必须 ASCII 纯英文路径**——中文路径会让 sherpa-onnx 报 `invalid unordered_map key`。
> 路径写死在 `质检.py` 第 133 行（`_ASR_DIR`），换地方就改这行。

```bash
pip install -r requirements.txt           # numpy + sherpa-onnx + pypinyin
```

> 不想配 ASR？质检仍能跑（只是少了错读闸门/声调哨兵），声纹"像本人分数"靠已附的 CAM++ 即可工作。

## 4. 建你自己的音色锚点（质检基准）

质检要拿"你的声音"当基准，所以先建锚：

1. 录 **20~30s 干净参考音频** + 逐字文本 → `models/参考音频.mp3` + `models/参考文本.txt`
2. 先合成一小批，人耳挑出几段**最像你**的，放进 `output/`
3. 建锚点：
   ```bash
   python 质检.py --init
   ```
   生成 `models/锚点.npz`。之后质检就以此为基准。

> 没建锚点时质检自动降级（不报错）；你也可以先 `qc.enable=false` 纯合成。

## 5. 日常用法

```bash
# 1) 写文案 -> 文案.txt  (怎么写能一次过 0.95, 看 文案写作规范.md)
# 2) 免费预检（分句 + 版式 + 读音风险）
python 配音.py --preview
# 3) 正式合成
python 配音.py                      # 产物在 output/: 逐句mp3 + 合并.mp3 + 合并.srt + 质检报告.txt
# 4) 某段不满意, 单独重roll换最像版
python 重roll.py 3 6                 # 第3段生成6版自动挑最像
# 5) 金句悬停 + 重拼
python 悬停.py && python 合并.py
# 6) 成品加BGM
python 加BGM.py --bgm 曲子.mp3
```

格言体/短段密集的稿子加 `--minchars 55`，把短段并成大段保音色（详见 `使用说明.md`）。

## 文档导航

| 看这个 | 当你想 |
|---|---|
| **使用说明.md** | 查全部命令 + 参数 + 机制 |
| **文案写作规范.md** | 学怎么写稿能一次过、不浪费 roll |
| **发音规则.md** | 读音决策树 + 已修案例库（多音字/同音误报） |
| **创造全流程.md** | 看机制由来 + **翻车博物馆**（别重蹈覆辙）+ 换音色复刻清单 |
| **CLAUDE.md** | AI 协作者先读：**七条红线** |
| 方案-人机协同配音流水线.md | 设计蓝本（含待做的审听台） |

## 红线（摘要，详见 CLAUDE.md）

1. **永不调速**（`prosody.speed` 焊死 1.0，调速出电音）
2. **永不后期对人声做去呼吸/衰减**（违和）
3. **音色 0.95 无例外**（够不着就 `--minchars` 并段加长，不降目标、不为金句分行）
4. **不替作者断句**（原文标点 = 停顿地图，切碎整批掉档）
5. **读音修复以你的读法为准**，不是字典；修完回填"预检+决策树+案例库"三件套
6. **`config.json` 含密钥，永不外传/提交**

## 本包**含 / 不含**

- ✅ **含**：全部脚本 + 文档 + 公开声纹模型 **CAM++**（`models/campplus_zh-cn_16k.onnx`）+ 读音风险库 + 脱敏 config 模板
- ❌ **不含**（各人自备）：**API 密钥 / voice_id**（模板已留空）、原作者的**音色锚点 / 参考音频 / 跑批历史**、**ASR 模型**（太大，按第 3b 步自行下载）
