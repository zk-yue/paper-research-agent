# Paper Research Agent 前端

React / TypeScript 界面，提供任务管理、论文列表、详细综述工作台、Related Work 短节与调用日志。

```bash
npm ci
npm run dev
```

开发服务器将 `/api` 和 `/ws` 代理到 `localhost:8000`。后端配置与启动方式见[项目说明](../README.md)。Node.js 24 为推荐环境；Node.js 22.12+ 也可运行。

```bash
npm run lint
npm test
npm run build
```

`src/api/request.ts` 统一处理 JSON、HTTP 状态和后端校验错误；`src/api/client.ts` 声明业务接口；`src/api/types.ts` 声明传输类型。测试使用 Node 内置测试运行器和模拟 fetch，无需启动后端。

源码运行时，构建产物位于 `frontend/dist/`，由本项目 FastAPI 服务提供。Python wheel 不包含构建后的 Web UI；使用 Web 界面请从源码安装并完成前端构建。页面组件按功能拆分，任务详情的综述工作台、模板与指令预设编辑器位于 `src/components/task-detail/`。

## 综述工作台

进入任务详情的“文献综述”，默认生成中文、多章节的详细综述（`survey`）。没有预先选择论文时，默认纳入主列表全部论文；已在论文列表勾选时沿用该选择，也可在工作台中调整。填写研究问题或比较要求后即可生成，无需先手动下载 PDF 或提取 Reference Cards。

详细综述在后端优先读取 arXiv HTML 全文，无法获取时复用已有 PDF；缺少全文时使用原始摘要并标明依据。仅有标题和元数据的论文会要求补充资料。页面展示全文/摘要数量、网页/PDF 来源及正文引用覆盖，避免把摘要材料显示为已阅读全文。

生成依次经历原文准备、主题提纲、分章写作、论证与引用审校、跨章节综合、终稿证据审校和完成导出。当前阶段进度与已完成章节持续保存，正文支持目录跳转。任务详情标签同步到 URL，例如 `/task/{id}?tab=review`，刷新或直接打开可继续停留在综述工作台；浏览器前进和后退也会恢复对应标签。刷新或离开页面不会停止后台任务；“停止并保留进度”请求取消，“继续生成”仅对后端标记 `resumable` 的版本显示。未完成版本的已保存文稿会注明尚未通过全部质量检查。

每版综述在启动时保存独立的 `input.json` 快照，固定论文列表、筛选资料、写作配置和引用对应关系，快照不含历史综述正文。恢复继续使用原始输入，即使检索任务的论文列表后来发生变化；损坏或缺失快照会显示失败原因。改变范围、指令或模型时应新建版本。已有正文可导出 Markdown，历史版本可切换阅读。

高级选项提供语言、指令预设、期刊风格模板和 `related_work` 短节模式。Related Work 用于论文中的相关工作段落，会自动准备卡片、规划引用并写作，不保证覆盖全部论文，也不支持详细综述的章节恢复。翻译为中文是用户按需触发的独立操作。

## 综述接口与状态

`ReviewWorkspace.tsx` 负责创建、历史版本、进度、恢复/停止、阅读与导出；`src/lib/reviews.ts` 处理记录选择和 Markdown 目录；`MarkdownContent.tsx` 提供共享正文渲染。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| `POST` | `/api/tasks/{id}/write-review` | 接收 `paper_indices`、`instructions`、`template_id`、`mode`、`language`，立即返回 `{ "review_id": "..." }` |
| `GET` | `/api/tasks/{id}` | 从 `results.custom_reviews` 读取持久化状态和正文 |
| `POST` | `/api/tasks/{id}/reviews/{review_id}/resume` | 复用快照与有效章节，返回同一 `review_id`；不能恢复时返回 409 |
| `POST` | `/api/tasks/{id}/reviews/{review_id}/cancel` | 请求停止并保留进度，返回综述记录 |
| `GET` | `/api/tasks/{id}/reviews/{review_id}/export` | 下载 Markdown；尚无保存正文时返回 409 |

`paper_indices` 为从 0 开始的主列表索引，空数组表示全部；默认 `mode="survey"`、`language="zh"`。一个检索任务同时只能运行一版综述。

进度来自 WebSocket `task_update` 和活动任务轮询，不依赖 `review_stream` token 流。`CustomReview` 中的 `stages` 由后端提供，界面不固定阶段数；`progress.completed / total` 仅表示当前阶段进度。`sections` 提供逐章状态，`evidence_stats`、`coverage`、`quality` 分别描述材料、正文引用和质量检查。`failed`、`cancelled`、`interrupted` 会保留已保存内容，由 `resumable` 决定是否显示继续按钮。

## 配置与发布边界

模型密钥和服务端配置保留在后端；不要写入 `VITE_*` 变量、浏览器存储或前端源码。前端示例、截图与测试 fixture 不应包含真实任务、认证信息或未公开的研究内容。`dist/` 和 `node_modules/` 不随源码发布；锁文件用于可复现安装。完整检查和发布归档扫描见 [贡献指南](../CONTRIBUTING.md)。
