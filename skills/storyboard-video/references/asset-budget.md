# 素材预算

故事板点名要图的那一刻就按规则定尺寸。事后再改就是全部重导。

| 项 | 上限 |
| --- | --- |
| 源图边长 | **屏幕显示尺寸 × 2** |
| 单场景图片总量 | **3 MB**（预览导航预算 10 秒） |
| 格式 | **WebP q85**，PNG 只用于无损线稿 |

AI 生图默认吐 1024² 以上，而画面上往往只显示 136~560px —— 差 2~7 倍，一个场景堆十几张就超时。

## 定尺寸

先查实际显示尺寸，注意镜头里最大的那个 `scale()`：

```bash
grep -oE '(width|height):[ ]*[0-9]+px' compositions/frames/SCENE.html | sort -u
```

显示 340px → 导出 680px。

## 转换

ffmpeg 最可靠。**Windows 上不要用 `convert`** —— 那是系统自带的磁盘转换工具，不是 ImageMagick。

```bash
ffmpeg -i in.png -vf scale=680:-1 -c:v libwebp -quality 85 -compression_level 6 out.webp
# 带透明通道
ffmpeg -i in.png -vf scale=680:-1 -c:v libwebp -quality 85 -pix_fmt yuva420p out.webp
```

转到新目录，别覆盖原图。批量换完渲一帧和替换前的快照对比 —— 深色背景上少一张图是看不出来的。

## 加载

首次绘制前把所有图 decode 完。渲染器 seek 到某帧就截图，晚到的图会在成片里留一个空帧，而且预览时看不出来。

```js
await Promise.all(Object.entries(manifest).map(async ([k, src]) => {
  const img = new Image();
  img.src = src;
  await img.decode();
  images[k] = img;
}));
```

**绝对不要在 `img.onload` 里触发重绘** —— 那是轮询式写法，会撞确定性检查。

## 不装工具读 PNG 尺寸

宽高是偏移 16 和 20 处的大端 uint32：

```bash
od -An -tu1 -j16 -N8 f.png | awk '{printf "%dx%d\n", $1*16777216+$2*65536+$3*256+$4, $5*16777216+$6*65536+$7*256+$8}'
```
