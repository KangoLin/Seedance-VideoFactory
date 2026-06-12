# AGENTS.md — Seedance 视频工具包

## 两个独立 Python 项目

| 项目 | 入口 | 端口 | 说明 |
|------|------|------|------|
| **种子视频 GUI** | `05_Video/scripts/seedance_gui.py` | 8765 | Python 内置 `http.server`（非 Flask/FastAPI），内嵌 `gui_page.html` |
| **md-edit（漫剧剪辑）** | `md_edit_project/mdedit/cli.py` | 8766 | 有 CLI 和独立 FastAPI webui（`run_webui.py`），通过 pip install -e 注册为 `md-edit` 命令 |

GUI 与管理面板通过 `05_Video/workspace/episodes.json` 交换数据。
md-edit 素材列表同时扫描 `output/concat/` 和 `output/exports/`（仅 `*concat*.mp4`）。

## 启动方式

```bat
start_seedance_gui.bat          # 完整启动（含环境体检）
restart_seedance_gui.bat         # 快速重启（无 pause，供 Agent 使用）
start_deepseek_proxy.bat         # DeepSeek 网页端反代（端口 8000）
python md_edit_project\run_webui.py   # md-edit 独立 Web UI（端口 8766）
```

启动后访问 `http://127.0.0.1:8765`。更新 GUI 后需 `Ctrl+F5` 强刷浏览器。

## API Key

| 文件 | 用途 |
|------|------|
| `API_Key/VE_Key.txt` | 火山/Seedance 视频生成 |
| `API_Key/deepseek_api_key.txt` | DeepSeek 提示词优化 |
| `API_Key/gemini_api_key.txt` | Gemini 提示词优化/图片生成/视频审核 |

环境变量可覆盖：`DEEPSEEK_API_KEY`、`GEMINI_API_KEY`、`DEEPSEEK_BASE_URL`、`GEMINI_BASE_URL`、`DEEPSEEK_MODEL`、`GEMINI_MODEL`。`PROMPT_OPTIMIZER_BASE_URL` 可指向本地 OpenAI 兼容代理（如 `http://127.0.0.1:8000/v1`）。

`API_Key/` 已加入 `.gitignore`，勿提交。

## 关键路径

| 路径 | 内容 |
|------|------|
| `05_Video/uploads/` | 上传的参考图/视频/首尾帧 |
| `output/exports/` | 生成的分镜视频（`ep-{集}-task-{任务}_TikTok_v001.mp4`） |
| `output/segments/` | 分镜片段目录 |
| `output/exports/` | 拼接成片（`ep-{集}-concat_TikTok_v001.mp4`） |
| `05_Video/workspace/episodes.json` | 所有集数/任务/Prompt/引用路径 |
| `.md_cache/` | md-edit 管线缓存（manifest/analyze/plan 各阶段） |

- 每次「正式生成」自动递增版本号（v001→v002...）
- 修改集数或任务顺序会自动重命名已有文件
- 拼接顺序 = 任务在 JSON 中的数组顺序（1→2→3...）

## 前置条件

- Python 3.10+
- ffmpeg + ffprobe（PATH 或 WinGet 安装于 `Gyan.FFmpeg*` 均可）
- 火山/Seedance API Key

启动时 `preflight_check.py` 会自动验证上述条件。

## 代理自动检测

`start_seedance_gui.bat` 和 `seedance_gui.py` 均会从 Windows 注册表 `HKCU\...\Internet Settings` 读取系统代理并设置 `HTTP_PROXY`/`HTTPS_PROXY`。Python 内置 `urllib` 不走系统代理，代码中已做了自动适配，但环境变量优先级更高。

## 版本号

整个工具包版本号记录在项目根目录 `VERSION` 文件中，格式为纯文本版本号（如 `0.2.0`）。
两个 Web UI 统一读取此文件并显示在页面标题旁：
  - **种子视频 GUI**（端口 8765）：标题右侧 `v{{SEEDANCE_VERSION}}`
  - **md-edit**（端口 8766）：标题右侧 `v{{MDEDIT_VERSION}}`
 
**每次对任一工具修改代码后，必须递增 `VERSION` 文件中的版本号**（patch 或 minor，按变更幅度）。

## 测试 / Lint / 类型检查

无测试框架，无 lint 配置，无类型检查。修改后需手动启动 GUI 验证。

## md-edit CLI

```bash
pip install -e md_edit_project
md-edit --input "output/concat/ep-1-concat_v001.mp4" --output output.mp4
md-edit --input "output/concat/ep-1-concat_v001.mp4" --preview-only
md-edit --input "output/concat/ep-1-concat_v001.mp4" --force            # 清缓存重跑
```

CLI 管线：Ingest → Analyze（LLM，并行 Gemini） → Supervisor（AI监制） → Plan（LLM） → Preview（低分辨率） → Render

## 架构要点

- 视频生成用 **Seedance API**（火山引擎），通过 `run_seedance_batch.py` 子进程调用
- 提示词优化和图片生成走 **Gemini API**（或可切换 DeepSeek）
- 视频审核（整集内容审核）走 Gemini Vision + JSON Schema 受控输出
- 所有 LLM 调用都不依赖本地分析库（Whisper/PySceneDetect/MediaPipe/librosa）
- md-edit 管线有自动缓存（TTL 3600s），`--force` 全局清缓存
- GUI 任务通过 subprocess 执行，10 秒心跳记录，日志上限 400 行
- 生成模式：参考+文字、首尾帧（上传）

## md-edit AI 监制系统

管线分为两阶段：

**阶段1（分析+监制）：** `POST /api/run`
```
Ingest → Analyze (Gemini Vision) → Supervisor (AI监制) → 返回 clips + suggestions
```
- Analyze: 识别高光片段、评分
- Supervisor: 对每个高光片段分析特效、字幕、调色、转场建议
- 结果存入 `manifest.json` 的 `supervisor_suggestions` 字段

**阶段2（精剪）：** `POST /api/render`
```
Plan (整合建议) → Render (特效+转场+调色) → 输出视频
```
- Plan: 将监制建议整合到 edit_plan
- Render: 应用特效（zoom/shake/slowmo/vignette/glow）、转场（xfade）、调色（color_grade）

**前端交互：**
- 分析完成后显示 AI 监制建议（特效标签、字幕预览、调色指示）
- 用户可 toggle 单个建议开关
- 点击"开始精剪"发送已确认的建议到后端

**监制模块文件：**
- `mdedit/supervisor.py` — 调用 Gemini Vision 做监制分析
- `mdedit/prompts/supervisor_vision.txt` — 监制提示词
- `mdedit/ffmpeg.py` — 特效函数（apply_zoom/apply_shake/apply_slow_motion/apply_color_grade/apply_crossfade 等）
