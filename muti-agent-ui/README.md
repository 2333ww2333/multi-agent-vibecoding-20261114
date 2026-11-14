# multi-agent UI

多智能体运行时的 Web 前端。React 18 + Ant Design 6 + Ant Design X 2.10，视觉走极简风：单色 + 一个 accent、细线、760px 阅读列、助手消息不套气泡。

默认连接 `multi-agent/server` 的 FastAPI BFF（`http://127.0.0.1:8787`）；也可以切回内置 Mock 独立运行。
完整的前后端安装、启动和停止见 [`../README.md`](../README.md)。

## 快速开始

**终端 1：后端**

```powershell
cd D:\Desktop\multi-agent-vibecoding-20261114\multi-agent
uv venv --python 3.12 .venv    # 首次运行，已有 .venv 可跳过
uv pip install --python .\.venv\Scripts\python.exe -e ".[web]"
$env:MA_WEB_DEV="1"
.\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8787 --reload
```

> 不要直接运行全局 `uvicorn`；显式用项目 `.venv` 的 Python 启动。

**终端 2：前端**

```powershell
cd D:\Desktop\multi-agent-vibecoding-20261114\muti-agent-ui
pnpm install
pnpm dev
# http://localhost:5173
```

`/api/*` 默认直连 8787（后端 CORS 已允许 5173）；SSE 走 `fetch`，不依赖代理。`vite.config.ts` 里也配了 `/api → 8787` 代理，切到同源 base 时可用。

### 验证

```powershell
Invoke-WebRequest http://127.0.0.1:8787/api/health -UseBasicParsing
Invoke-WebRequest http://localhost:5173 -UseBasicParsing
```

浏览器发一句“只回复：联调成功”，能流式返回、刷新后消息还在即可。

### 停止

前台启动的终端按 `Ctrl+C`；`pnpm dev` 一次没退再按一次。

后台/找不到窗口时按端口停，不要全杀 node/python：

```powershell
# 前端
Get-NetTCPConnection -LocalPort 5173 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }

# 后端
Get-NetTCPConnection -LocalPort 8787 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

确认：

```powershell
Get-NetTCPConnection -LocalPort 8787,5173 -State Listen
# 没有输出 = 已停
```


## Mock / Live / 生产

`.env.development`（默认联调）：

```dotenv
VITE_USE_MOCK=false
VITE_API_BASE_URL=http://127.0.0.1:8787
```

- **Live**：数据来自 BFF；事件契约见 `shared/events/types.ts`。
- **Mock**：`VITE_USE_MOCK=true`，会话、SSE、工具轨迹、审批、看板等由 `src/shared/api/mock.ts` 在浏览器内模拟，数据存 `localStorage`（`ma.mock.v2`）。
- **生产**：`.env.production` 的 `VITE_API_BASE_URL` 留空，`pnpm build` 后由 FastAPI 托管 `dist`：
  ```powershell
  cd D:\Desktop\multi-agent-vibecoding-20261114\multi-agent
  uvicorn server.app:app --host 127.0.0.1 --port 8787
  # 打开 http://127.0.0.1:8787
  ```
- 访问令牌在「设置」页填写，存 `sessionStorage`（`ma.token`），以 `Authorization: Bearer` 发送；后端未配置 `MA_WEB_TOKEN` 时只允许本机访问。

## 已实现

| 区域 | 内容 |
|------|------|
| 对话 | 新建/切换/重命名/删除会话；流式文本与思考过程；停止；重试；复制 |
| 过程 | 工具卡片（名称、agent、参数、耗时、结果、错误码）；子代理；计划步骤 |
| 审批 | 高危命令弹窗，180s 倒计时，超时自动拒绝；确认后立即关闭，不依赖 SSE 回包；支持「本次运行内不再询问」 |
| 计划 | 按会话隔离；进度、当前项、完成/阻塞样式 |
| 看板 | 只显示待处理/进行中任务；done/cancelled 自动隐藏；状态流转（done/blocked/claimed 必填）、新建任务 |
| 队友 | 状态点、花名册下拉/自定义名字、职司说明、关停、最近错误 |
| 收件箱 | 按成员查看、全部已读（drain） |
| 文件 | 目录树 + 只读内容查看 |
| 产物 | 回答下方卡片/内联链接，右侧抽屉预览 HTML/Markdown |
| 账单 | 今日/近 7 天、预算条、按 agent 分组 |
| 全局 | `⌘/Ctrl+K` 命令面板、深浅色、右栏抽屉、`Esc` 关闭 |

## 测试

```powershell
pnpm test        # 16 个 SSE 解析 / reducer / artifact 单测
pnpm typecheck   # tsc --noEmit
pnpm lint
pnpm build       # 类型检查 + 生产构建
```

浏览器联调（先启动后端与 `pnpm dev`）：

```powershell
node scripts\e2e-live.mjs       # 发消息、SSE、刷新持久化、右栏、审批拒绝
node scripts\e2e-approval.mjs   # 审批弹窗 + 允许（pip --dry-run）
node scripts\e2e-cancel.mjs     # 流式过程中停止
node scripts\e2e-session.mjs    # 重命名 / 删除
node scripts\e2e-workbench.mjs  # 看板流转 / 队友 / 设置 / 主题 / 命令面板
node scripts\e2e-prod.mjs       # FastAPI 托管 dist，同源 /api
node scripts\e2e-mock.mjs       # Mock 模式（5174）
```

后端 API 冒烟见 `../multi-agent/scripts/web_smoke.py`。

## 常见问题

| 现象 | 处理 |
|------|------|
| `ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY`（移动项目后常见） | 删除 `node_modules`，重新 `pnpm install`；或 `$env:CI="true"; pnpm install` |
| 页面请求一直失败 | 确认后端 `8787` 已启动，`.env.development` 的 `VITE_API_BASE_URL` 指向 `http://127.0.0.1:8787` |
| 端口 `10048` | 按 README 的端口命令停掉旧进程，不要全杀 node |
| 改了前端但页面没变 | 开发模式看 HMR；生产模式重新 `pnpm build` |

## 目录

```
src/
├─ app/                 # 路由、providers、theme、AppShell/Sidebar/RightRail/CommandPalette
├─ features/
│  ├─ chat/             # ChatPage、MessageList、LiveBubble、TraceChain、Composer、ApprovalModal
│  ├─ sessions/         # 会话 query/mutation
│  ├─ workbench/        # 计划/看板/队友/收件箱/文件/账单
│  └─ settings/         # 设置页
└─ shared/
   ├─ api/              # types / client / endpoints / mock
   ├─ events/           # SSE 类型、解析器、纯函数 reducer、视图模型
   ├─ stores/           # zustand：chat（流状态）+ ui（主题/面板）
   ├─ lib/              # 格式化、错误码文案、剪贴板
   ├─ ui/               # StatusDot / Kbd / ErrorState
   └─ styles/           # tokens.css（浅/深）+ global.css
```

## 设计约定

- 助手消息 `Bubble variant="borderless"`，只有用户消息有浅底；阅读列 `--reading-w: 760px`。
- 颜色、间距、圆角、动效全部走 `src/shared/styles/tokens.css`，组件里不写 hex。
- 数字（token/耗时）用等宽字体；状态必须「圆点 + 文字」，不能只靠颜色。
- 错误展示 = 错误码 + 人话 + 下一步（`shared/lib/errorHints.ts`）。

## 当前边界

- 移动端隐藏侧栏后没有抽屉入口，桌面优先。
- 附件上传、会话全文搜索、消息虚拟滚动、i18n 未实现。
- 版本组合：React 18.3 + antd 6.7 + `@ant-design/x` 2.10；后续可统一升到 React 19。
- 若 `pnpm install` 提示 ignored build scripts，仓库里的 `pnpm-workspace.yaml` 已通过 `allowBuilds` 放行 `esbuild`/`msw`。
