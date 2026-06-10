# md-edit — 漫剧 AI 智能剪辑工具

基于 Gemini Vision 的 AI 视频精剪管线：分析剧情高光 → AI 监制建议特效/字幕/调色/转场 → 自动渲染成片。

## 安装

```bash
pip install -e md_edit_project
```

依赖：Python ≥ 3.10、ffmpeg + ffprobe（需在 PATH）、Gemini API Key（`API_Key/gemini_api_key.txt` 或环境变量 `GEMINI_API_KEY`）。

## CLI 用法

```bash
# 完整剪辑
md-edit -i "output/concat/ep-1-concat_v001.mp4" -o output.mp4

# 仅低分辨率预览（跳过渲染，快速验证）
md-edit -i "input.mp4" --preview-only

# 清缓存重跑
md-edit -i "input.mp4" --force

# 带 BGM + 自定义监制提示词
md-edit -i "input.mp4" -o output.mp4 --bgm bgm.mp3 --supervisor prompt.txt
```

### CLI 参数

| 参数 | 说明 |
|------|------|
| `-i, --input` | 输入视频（必填，支持 glob） |
| `-o, --output` | 输出路径（默认 `episode.mp4`） |
| `--preview-only` | 仅渲染 360p 预览 |
| `--force` | 忽略缓存重新执行 |
| `--bgm` | 背景音乐文件 |
| `--supervisor` | 监制提示词文件路径 |
| `--provider` | 方案阶段 LLM：`gemini`（默认）或 `deepseek` |
| `--cache-ttl` | 缓存有效期秒数（默认 3600） |
| `--work-dir` | 工作目录（默认 `.md_cache`） |

### CLI 管线

```
输入 → Ingest(扫描元数据) → Analyze(Gemini Vision 高光分析) → Plan(LLM 生成方案) → Render(输出视频)
```

## Web UI 用法

```bash
python md_edit_project\run_webui.py
# 浏览器打开 http://127.0.0.1:8766
```

### 两阶段操作

**阶段 1 — 分析 & 监制**（`POST /api/run`）
1. 选择拼接视频 → 配置参数 → 点击「运行 AutoClip」
2. 系统提取关键帧 → Gemini Vision 分析 → 识别高光片段（评分 0-100）
3. AI 监制对每个片段给出特效/字幕/调色/转场建议
4. 前端展示建议列表，可逐条 toggle 开关

**阶段 2 — 渲染**（`POST /api/render`）
1. 确认所需建议 → 点击「开始精剪」
2. Plan 阶段整合建议生成编辑方案
3. Render 阶段应用特效 + 调色 + 字幕 + 转场 + BGM 混音
4. 输出最终视频到 `output/renders/`

另有「智能剪辑」模式（`POST /api/smart_render`），仅应用 zoom/crop/speed，不依赖监制。

### 可应用的特效

| 类别 | 效果 |
|------|------|
| 视觉特效 | Zoom 推拉、Shake 抖动、Slow Motion、Vignette 暗角、Glow 发光、Freeze Frame 定格 |
| 转场 | fade / dissolve / wipe_left / wipe_right / zoom_in（circlecrop） |
| 调色 | 亮度 / 对比度 / 饱和度 / 色温（暖色/冷色/中性） |
| 字幕 | ASS 叠加，支持 fade_in / typewriter / bounce / slide_up 动画 |
| BGM | 背景音乐混音（默认音量 0.15） |

## 缓存机制

- 基于 SHA256 + 源文件 mtime 的 JSON 缓存，自动过期（默认 TTL 3600s）
- `--force` 全局清缓存
- 缓存目录：`.md_cache/manifest/`、`.md_cache/analyze/`、`.md_cache/plan/`

## 数据流

```
episodes.json ──→ 分析关键帧 ──→ Gemini Vision ──→ clips + supervisor_suggestions
                                                      ↓
                                             用户确认建议 → Plan → Render → output/renders/*.mp4
```

## 架构要点

- 视觉分析（Analyze + Supervisor）始终走 Gemini Vision，方案阶段可选 Gemini 或 DeepSeek
- 所有 LLM 调用不依赖本地分析库（无需 Whisper/PySceneDetect/MediaPipe）
- 字幕使用 ASS 格式叠加，可选 faster-whisper 转写
- 版本号：0.1.0
