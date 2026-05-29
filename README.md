# Seedance 视频工具包

支持 **参考 + 文字**（图片/视频可混用）、**首尾帧上传** 生成视频，可拼接导出成片。

## 你需要准备

1. Python 3.10+
2. ffmpeg（命令行可用）
3. 火山/Seedance API Key

## 生成模式（GUI）

| GUI 选项 | 说明 |
|----------|------|
| **参考 + 文字** | 上传参考视频和参考图（可混用、可删除），填 Prompt |
| **首尾帧（上传）** | 上传首帧、尾帧，填 Prompt |

### 参考素材限制

- 图片：PNG / JPG / WEBP，最多 **9** 个，单文件 ≤ 30MB
- 视频：MP4 / MOV / WEBM，最多 **3** 个，单文件 ≤ 50MB
- 图片与视频可放在同一次任务的「参考」里一起提交

## 时长

默认 **4 秒**，范围 **4–15 秒**（在任务面板「时长（秒）」中修改；以面板设置为准，不再被配置文件里的 4 秒覆盖）。

## 启动 GUI

- Windows: 双击 `start_seedance_gui.bat`
- 开发改 GUI 后快速重启（无 `pause`，供 Cursor Agent 使用）: `restart_seedance_gui.bat`
- 浏览器: `http://127.0.0.1:8765`
- 生成任务的**运行日志**在任务面板「视频预览」下方，可滚动查看（无需黑窗口）

### 集数与任务

- 首页为 **集数列表**，点击 **+** 添加新集（弹窗内可**滚动数字表**或**直接输入**集数编号），点击 **✎** 或集内「修改集数」可后续调整
- 拖动集数卡片左侧 **⠿** 可调整显示顺序，顺序会保存到本地
- 集内点击 **+** 添加任务；任务面板可并行生成
- 集内工具栏 **一键拼接本集**：按 **分镜任务1 → 2 → 3…** 顺序，把各任务**最新预览成片**拼成一条（输出 `05_Video/exports/ep-{集数}-concat_TikTok_v001.mp4`，可多次拼接递增 v002…）
- 每个任务面板都有 **DeepSeek 提示词优化**：通过本地 OpenAI 兼容代理与 DeepSeek 网页端反代服务沟通，返回分析和可一键应用的优化 Prompt
- 集数卡片右上角 **×** 可删除整集（至少保留 1 集）；任务标题栏 **删除** 可移除任务（每集至少保留 1 个）
- 所有集数、任务（Prompt、参考、首尾帧路径等）自动保存到本地 `05_Video/workspace/episodes.json`，刷新页面不丢失

输出成片与集数、分镜任务对应，例如 `ep-1-task-1_TikTok_v001.mp4`；每次点击「正式生成」都会重新请求 API，并自动递增版本号（`v002`、`v003`…）。修改集数或任务顺序后会自动重命名已有文件。

### AI API 提示词优化

每个任务面板都有 **AI 提示词优化**，可在窗口内切换 **DeepSeek** / **Gemini** 及具体模型，分析当前视频 Prompt，并返回可一键应用的优化版本。

每个任务面板右侧还有 **图片生成（Gemini）**：

- **文生图**：只输入图片提示词生成参考图
- **图生图**：上传参考图，基于图片生成新图
- **文 + 图生图**：上传参考图并用文字控制修改方向
- 生成图会保存到 `05_Video/uploads/`，可一键加入当前任务参考图

默认模型：

- DeepSeek：`deepseek-chat`、`deepseek-reasoner`
- Gemini：`gemini-3-pro-preview`、`gemini-3-flash-preview`、`gemini-3.1-pro-preview`、`gemini-3.5-flash`
- Gemini 图片：`gemini-3-pro-image-preview`、`gemini-3.1-flash-image-preview`、`gemini-2.5-flash-image`

本地密钥文件：

- DeepSeek：`API_Key/deepseek_api_key.txt`
- Gemini：`API_Key/gemini_api_key.txt`

也可用环境变量覆盖：`DEEPSEEK_API_KEY`、`GEMINI_API_KEY`、`DEEPSEEK_BASE_URL`、`GEMINI_BASE_URL`、`DEEPSEEK_MODEL`、`GEMINI_MODEL`。`API_Key/` 已加入 `.gitignore`，请勿外传。

如后续仍想切换回网页端反代，可把 `PROMPT_OPTIMIZER_BASE_URL` 改为本地 OpenAI 兼容代理地址，例如 `http://127.0.0.1:8000/v1`。

## 目录说明

- `05_Video/uploads/`：上传的参考（图/视频）/ 首尾帧
- `05_Video/segments/`、`05_Video/exports/`：片段与成片
- `API_Key/`：本地密钥（勿外传）
