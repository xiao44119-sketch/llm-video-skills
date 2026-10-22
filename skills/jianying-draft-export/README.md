# JianYing Pro Native Draft Export Skill (剪映工程自包含自动导出技能)

用于自动化将代码渲染视频、AI成片、分镜脚本直接一键编译注入为**剪映 Pro 11.4+ 本地草稿工程**，并直接唤起剪映开剪。

## 🌟 核心特性
- **彻底解决“媒体丢失 / 暂无访问权限”**：基于自包含工程架构（Self-Contained），自动将素材注入剪映沙盒合法存储区，彻底杜绝手动找文件。
- **底层硬件 C++ 加密兼容**：内置编译好的 `libjycodec.dylib`，完美兼容剪映 11.4+ 加密协议。
- **高冲击力双色字幕**：支持精准关键词金黄高亮与实心黑边，字幕与音画毫秒级对齐。
- **一键极速调用**：提供标准 CLI 命令行与 Python API。

## 🚀 极速上手
```bash
python3 scripts/export_draft.py \
  --video "/path/to/my_video.mp4" \
  --name "我的高质感短视频" \
  --cover "/path/to/cover.jpg" \
  --subtitles "/path/to/subtitles.json" \
  --open
```

详细使用说明见 `SKILL.md`。
