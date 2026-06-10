# 漫剧 AI 智能剪辑工具 — 架构设计

## 设计目标

将 5~15 个预生成的 AI 短视频片段，自动剪辑为一集完整的漫剧视频。

| 能力 | 参考来源 |
|------|----------|
| Gemini JSON Schema 受控生成 + Story Mode 编排 | opensource-clipping |
| LLM 叙事理解 + 场景重要性评分 + 节奏同步 | CutClaw |
| 质量评分 + BGM 混音 | CutAI |
| 审核角色预设 prompt 体系 | 已有 seedance 审核系统 |

## 核心设计原则

- **纯 LLM 驱动** — 不做本地分析（场景检测/ASR/运动量等），所有内容理解交给 Gemini
- **两轮 LLM 调用** — 第一轮逐片段分析（并行），第二轮整集叙事规划
- **缓存** — LLM 结果缓存，避免重复调用
- **本地化** — 除 LLM API 外无外部依赖，渲染走 FFmpeg

---

## 整体架构

```
┌───────────────────────────────────────────────────────────┐
│                     CLI / GUI                              │
│  md-edit --input clips/*.mp4 --config episode.json -o out │
└─────────────────────────┬─────────────────────────────────┘
                          │
┌─────────────────────────▼─────────────────────────────────┐
│                     编排器                                  │
│  Ingest → Analyze(LLM) → Plan(LLM) → Preview → Render    │
│                          │                                │
│  每阶段结果缓存到 .md_cache/，重复运行跳过已完成阶段      │
└───────────────────────────────────────────────────────────┘
```

---

## 五大模块

### Stage 1: Ingest（素材接入）

```python
输入: *.mp4 文件列表 + episode.json
输出: manifest.json

{
  "clips": [
    {"clip_id": 0, "path": "clip_0.mp4", "duration": 5.2,
     "width": 1080, "height": 1920, "fps": 30, "codec": "h264"}
  ],
  "episode_no": 1,
  "title": "第1集 初遇"
}
```

- ffprobe 探测每个视频 → 时长/分辨率/编码/帧率
- 检查一致性，标记需要转码的片段
- 缓存到 `.md_cache/manifest.json`

---

### Stage 2: Analyze（LLM 逐片段理解）

**纯 LLM，不做任何本地分析。** 每个片段独立发送给 Gemini，并行处理。

#### 发送内容

```
系统指令: [从 漫剧监制设定词.md 加载]

用户消息:
- 片段信息: clip_id, 时长, 宽高比
- 2 张关键帧: 第25%和第75%位置的帧，JPEG base64 (-q:v 5 缩小体积)
- 提示词: 生成该片段时使用的 prompt（可选，如果从 manifest 或 seedance 导出时携带）

输出 JSON Schema (responseMimeType: "application/json"):
{
  "clip_id": 0,
  "content_summary": "主角在雨中奔跑，表情焦虑",
  "importance": 8,
  "pace": "fast",
  "has_face": true,
  "character": "主角",
  "suggested_trim": {"head": 0.2, "tail": 0.3},
  "suggested_fade": {"in": 0.2, "out": 0.3},
  "suggested_speed": 1.0,
  "text_overlays": [{"text": "雨中奔跑", "position": "bottom", "fontsize": 48}],
  "suggested_bgm_mood": "tense",
  "risk_items": [],
  "quality_score": 7
}
```

#### 关键设计

- **并行请求** — N 个视频同时发 Gemini，非串行。`asyncio` + `aiohttp` 或 `ThreadPoolExecutor`
- **帧提取** — ffmpeg 取 2 帧，`-q:v 5` 控制体积，用完即删
- **缓存** — 写入 `.md_cache/analyze/clip_{id}.json`，再次运行跳过
- **重试** — 失败自动重试 2 次，回退到 `gemini-2.5-flash`

---

### Stage 3: Plan（LLM 整集规划）

将 Stage 2 的所有分析结果合并，一次 LLM 调用生成整集方案。

#### 发送内容

```
系统指令: [从 漫剧监制设定词.md 加载]

用户消息:
- 各片段分析摘要: [{clip_id, summary, importance, quality_score, suggested_bgm_mood}]
- episode 配置: 目标时长, 风格偏好, 平台(TikTok/YouTube)

输出 JSON Schema:
{
  "narrative_arc": {
    "acts": [
      {"name": "开场", "clip_ids": [0, 1], "summary": "引入冲突"}
    ],
    "recommended_order": [0, 1, 2, 3, 4, 5],
    "dropped_clips": []
  },
  "edit_plan": {
    "clips": [
      {
        "clip_id": 0,
        "trim_start": 0.2,
        "trim_end": 0.3,
        "fade_in": 0.2,
        "fade_out": 0.3,
        "speed": 1.0,
        "crop": "9:16",
        "text_overlays": [{"text": "雨中奔跑", "fontsize": 48, "xpos": 50, "ypos": 85}],
        "transition_in": "fade",
        "transition_out": "fade"
      }
    ]
  },
  "bgm_plan": {
    "tracks": [
      {"source": "auto_generate", "mood": "tense", "volume": 0.3,
       "fade_in": 0.5, "fade_out": 1.0}
    ]
  },
  "audit": {
    "score_retention": 7,
    "risk_level": "low",
    "summary": "节奏紧凑，高潮部分表现力强",
    "optimization_plans": ["第二幕可缩短0.5s"],
    "risk_items": []
  },
  "output_config": {
    "resolution": "1080x1920",
    "fps": 30,
    "duration_target": 45
  }
}
```

#### 关键设计

- **一次调用**编完全集，不拆分多轮对话
- 含 `edit_plan`（给渲染器用）和 `audit`（给用户看的审核反馈）两个视图
- 输出包含 `bgm_plan.mood`，后续可由 BGM 库匹配曲目，或留空由渲染器生成

---

### Stage 4: Preview（快速预览）

```
输入: edit_plan.json + manifest.json
输出: 低分辨率预览视频 preview.mp4

流程:
  1. 按 edit_plan 裁剪每个片段 (ffmpeg -ss -t)
  2. 不做转场/字幕/BGM/裁切
  3. 直接用 concat demuxer 拼接
  4. 缩放至 480p 加速预览
```

用户观看预览 → 满意则 Render / 不满意则手动调整 edit_plan 重新 Preview。

---

### Stage 5: Render（最终渲染）

按 edit_plan + bgm_plan 执行完整渲染。

```
每个 clip:
  1. Trim (ffmpeg -ss -t)
  2. Speed ramp (setpts)
  3. Crop to 9:16
  4. Fade in/out
  5. Transition
  6. Text overlay (drawtext, 字体用 SimHei)
  7. Resize to 1080x1920

全局:
  8. Concat 所有处理后片段
  9. BGM 混音 (amix, 音量按 bgm_plan.volume)
  10. 输出最终 mp4
```

---

## 数据流与缓存

```
.md_cache/
├── manifest.json              # Stage 1
├── analyze/
│   ├── clip_0.json            # Stage 2 缓存
│   ├── clip_1.json
│   └── ...
├── plan.json                  # Stage 3 (含 edit_plan + audit)
└── preview.mp4                # Stage 4
```

- 缓存文件包含源文件 mtime，源文件变化时自动失效
- `--force` 强制全部重跑
- `--replan` 跳过 Analyze，只重跑 Plan

---

## CLI 设计

```bash
# 完整流程
md-edit --input clips/*.mp4 --config episode.json --supervisor 监制设定词.md --output out.mp4

# 仅分析（查看逐片段理解结果）
md-edit analyze --input clips/*.mp4 --supervisor 监制设定词.md

# 仅规划（基于已有分析结果调整叙事）
md-edit plan --input clips/*.mp4 [--replan]

# 预览（低分辨率快速验证）
md-edit preview --plan .md_cache/plan.json

# 渲染（指定 edit_plan 输出）
md-edit render --plan .md_cache/plan.json --output final.mp4

# 强制刷新 LLM 缓存
md-edit --input clips/*.mp4 --force
```

---

## 技术栈

| 层 | 选型 | 理由 |
|----|------|------|
| 语言 | Python 3.10+ | |
| 视频处理 | FFmpeg subprocess | 稳定，你已有经验 |
| AI | Gemini API (`responseMimeType: application/json`) | JSON Schema 强制输出，无需额外解析 |
| CLI | click / typer | |
| 并行 | `concurrent.futures.ThreadPoolExecutor` | Python 内置 |

不依赖 Whisper/PySceneDetect/MediaPipe/librosa 等任何本地分析库。

---

## 与 seedance 工具链的关系

```
种子生成 (seedance)             剪辑工具 (md-edit)
┌──────────────┐               ┌──────────────────┐
│ 分镜任务1.mp4 │──────→       │ Ingest            │
│ 分镜任务2.mp4 │──────→       │  ├ Analyze (LLM)  │
│ 分镜任务3.mp4 │──────→       │  ├ Plan (LLM)     │
│ ...           │              │  ├ Preview        │
│ 分镜任务N.mp4 │──────→       │  └ Render         │
└──────────────┘               └────────┬─────────┘
                                        │
                                 ┌──────▼─────────┐
                                 │ 成品漫剧一集.mp4 │
                                 └────────────────┘
```

两者**独立部署**，通过 mp4 文件传递。md-edit 不关心视频如何生成，只负责智能剪辑。

