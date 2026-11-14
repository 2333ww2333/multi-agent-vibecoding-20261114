# multi-agent

单进程、多线程、文件持久化的多智能体协作运行时。
主 Agent（lead）会拆计划、调工具、带记忆、派子代理、组团队、记 token、在上下文超限时自我压缩。

> 本 README 只讲后端「怎么跑起来」和「怎么用」。
> 完整的前后端安装、启动、停止见 [`../README.md`](../README.md)。

## 0. 最快启动（前后端）

```powershell
# 终端 1：后端
cd D:\Desktop\multi-agent-vibecoding-20261114\multi-agent
uv venv --python 3.12 .venv   # 首次运行，已有 .venv 可跳过
uv pip install --python .\.venv\Scripts\python.exe -e ".[dev,web]"
$env:MA_WEB_DEV = "1"
.\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8787 --reload

# 终端 2：前端
cd D:\Desktop\multi-agent-vibecoding-20261114\muti-agent-ui
pnpm install
pnpm dev
```

> 不要直接用全局 `uvicorn`：系统 Python 与项目 `.venv` 是两套环境。  
> 统一用 `.\.venv\Scripts\python.exe -m uvicorn ...`。

打开 <http://localhost:5173>。停止：两个终端各按 `Ctrl+C`；后台进程按端口停：

```powershell
Get-NetTCPConnection -LocalPort 8787,5173 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

> 不要 `Stop-Process -Name python,node` 全杀，会误伤其他项目。

## 1. 环境要求

- Python 3.11+（本仓库在 3.12.11 上验证）
- Windows / macOS / Linux 均可；Windows 终端建议先切 UTF-8：
  ```powershell
  chcp 65001
  $env:PYTHONUTF8 = "1"
  ```

## 2. 安装

```powershell
cd multi-agent

# 首次运行：先建一个独立 venv（从 GitHub 克隆时通常没有 .venv）
uv venv --python 3.12 .venv

# 方式 A（推荐，显式使用项目 .venv）
uv pip install --python .\.venv\Scripts\python.exe -e ".[dev,web]"

# 方式 B（venv 里没有 pip 时先补一个）
.\.venv\Scripts\python.exe -m ensurepip --upgrade
.\.venv\Scripts\python.exe -m pip install -e ".[dev,web]"
```

验证：

```powershell
.\.venv\Scripts\python.exe -c "import agent; print(agent.__file__)"
```

> ⚠️ 如果项目目录被移动/改名过，editable 安装可能还指向旧目录，
> 重新执行一次安装命令即可（会重写 `.pth` 与入口脚本）。

## 3. 配置 Key

```powershell
Copy-Item .env.example .env
notepad .env        # 填 DEEPSEEK_API_KEY
```

也可以直接用系统环境变量（`.env` 可以不建）：

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
```

`.env` 已在 `.gitignore` 里，不会被提交。

## 4. 自检与测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 5. 使用

先激活项目 venv，或直接使用 `.venv\Scripts\magent.exe`：

```powershell
.\.venv\Scripts\Activate.ps1
```

```powershell
magent chat                 # 交互对话（默认子命令）
magent run "任务描述"        # 一次性任务
magent board --all          # 看板
magent team                 # 队友状态
magent inbox alice --peek   # 看某人收件箱（--peek 不清空）
magent task add "标题"       # 手工建任务
magent skills               # 技能索引
magent stats                # token 账单
python scripts/cost.py --days 7   # 成本估算
```

聊天内命令：`/help /plan /board /team /inbox /skills /reload-skills /stats /task ... /quit`。

## 6. 目录

```
agent/         核心代码（L1 基础层 → L5 入口层）
agent/agri/    农业病虫害 RAG：语料/BM25/工具/HTML 报告（见第 12 节）
server/        Web BFF（FastAPI + SSE + SQLite；见第 9 节）
config/        default.yaml（运行时）/ team.yaml（花名册）/ mcp_servers.json
data/agri/     农业语料 docs.zh.json（默认忽略提交）
templates/     system_rules.md（行为规矩）/ compact_v1.md（压缩提示词）
skills/        SKILL.md 技能库（按需加载正文）
memory/        运行时：MEMORY.md / USER.md / history.jsonl / token.jsonl / 日期.md
.team/         运行时：plan.json / task_board.json / config.json / inbox/ / audit.jsonl
.web/          运行时：web.db（会话/消息/运行）
tests/         pytest（全部离线，用脚本化假模型）
```

## 7. 常见问题

| 现象 | 处理 |
|------|------|
| `E_LLM_NO_KEY` | `.env` 没建或 key 为空；确认在 `multi-agent/` 下启动 |
| `E_LLM_AUTH` | key 无效 |
| `E_LLM_MODEL`（404） | 端点没有该模型，用 `DEEPSEEK_MODEL` 覆盖 |
| `E_LLM_CONNECTION` | 网络/代理/`DEEPSEEK_BASE_URL` |
| `LockTimeout` | 上个进程没退干净；确认无 python 进程后删 `.team/*.lock` |
| 队友一直 `offline` | 用 `spawn_teammate` 唤回；只读命令不会改队友状态 |

## 8. 设计红线（改代码前先看）

- 状态只在 `AgentContext` / `Planner` / `TaskBoard` 实例里，禁止模块级全局可变状态。
- 依赖单向：L5 → L4 → L3 → L2 → L1，下层不许 import 上层。
- 写状态文件一律 `atomic_write_text` / `JsonStore`，多写者加 `FileLock`。
- `registry.invoke` 永不抛异常；失败一律 `[工具错误] E_XXX: 人话`。
- API Key 不落盘（除 `.env`）、不进日志、不进 `Settings`。

## 9. Web BFF（前后端联调）

`server/` 是一个 FastAPI 适配层：会话/消息落 SQLite，运行时能力通过 REST + SSE 暴露给 `muti-agent-ui`。
它**不重写** `agent/`，只做事件转发、并发控制、审批与持久化。

```powershell
# 安装 Web 依赖（fastapi / uvicorn）
uv pip install --python .\.venv\Scripts\python.exe -e ".[web]"

# 开发
$env:MA_WEB_DEV="1"
.\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8787 --reload

# 一键（等价）
magent-web
```

前端：

```powershell
cd ..\muti-agent-ui
pnpm install
pnpm dev        # http://localhost:5173
```

### 停止

前台启动：在对应终端按 `Ctrl+C`。

后台/找不到窗口：按端口停（先确认 PID 再杀）：

```powershell
Get-NetTCPConnection -LocalPort 8787 -State Listen |
  Select-Object LocalAddress,LocalPort,OwningProcess

Get-NetTCPConnection -LocalPort 8787,5173 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

确认：

```powershell
Get-NetTCPConnection -LocalPort 8787,5173 -State Listen
```

生产模式只停 8787 即可。

关键环境变量：

| 变量 | 默认 | 说明 |
|------|------|------|
| `MA_WEB_HOST` / `MA_WEB_PORT` | `127.0.0.1` / `8787` | 监听地址 |
| `MA_WEB_TOKEN` | 空 | Bearer token；空时仅允许本机 |
| `MA_WEB_CORS` | `http://127.0.0.1:5173,http://localhost:5173` | 允许的前端源 |
| `MA_WEB_DB` | `.web/web.db` | SQLite 路径 |
| `MA_WORKSPACE_ROOT` | `.` | 工具沙箱根目录 |
| `MA_WEB_DIST` | `../muti-agent-ui/dist` | 前端构建产物（生产托管） |
| `MA_APPROVAL_TIMEOUT` | `180` | 审批超时秒数（默认 3 分钟，避免演示时来不及点） |
| `MA_WEB_DEV` | `0` | 暴露 `/api/docs` 与 `/api/openapi.json` |

约束：

- **同一工作区同时只允许一个 run**（工具会改文件），第二个请求返回 `409 E_WORKSPACE_BUSY`。
- 进程重启后，`runs` 表里残留的 `running/queued` 会自动改成 `failed E_RUN_CRASHED`。
- SSE 事件按 `seq` 单调递增，支持 `?after=` 续传；缓冲淘汰后返回 `410 E_STREAM_EXPIRED`。
- 危险命令走审批：`approval.required` → 前端弹窗 → `POST /api/approvals/{id}`；超时按拒绝处理。

## 10. 测试

后端（离线，不需要 API key）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

当前全量 **166 passed**（含 `tests/test_agri.py`、`tests/test_artifacts.py` 的
农业 RAG/报告/产物预览/安全用例与计划会话隔离用例）。

BFF API 冒烟（需要先启动 `server`，会真实调用一次模型）：

```powershell
.\.venv\Scripts\python.exe scripts\web_smoke.py
```

覆盖：health/meta、会话增删改、消息幂等、SSE 顺序与终态、看板流转、队友召入/关停、inbox、文件树/内容、账单。

前端：

```powershell
cd ..\muti-agent-ui
pnpm test        # 16 个 SSE 解析 / reducer / artifact 单测
pnpm typecheck
pnpm lint
pnpm build       # tsc --noEmit + vite build
```

浏览器联调脚本（需要后端 + 前端 dev server）：

```powershell
node scripts\e2e-live.mjs       # 发消息、SSE、刷新持久化、看板/队友/文件/账单、审批拒绝
node scripts\e2e-approval.mjs   # 审批弹窗 + 允许（pip --dry-run）
node scripts\e2e-cancel.mjs     # 流式过程中停止
node scripts\e2e-session.mjs    # 会话重命名 / 删除
node scripts\e2e-workbench.mjs  # 看板流转 / 队友召入 / 设置 / 主题 / 命令面板
node scripts\e2e-prod.mjs       # FastAPI 托管 dist，同源 /api
node scripts\e2e-mock.mjs       # Mock 模式独立运行（5174）
```

## 11. 当前边界

- 同一工作区同时只允许一个 run；多 workspace 需要在外层做进程/实例路由。
- BFF 只做单用户本地/内网形态；生产多租户需要接入正式账号体系，并把 SQLite 换成 Postgres。
- 附件上传、会话全文搜索、消息虚拟滚动、i18n 尚未实现。
- 前端移动端隐藏侧栏后没有抽屉入口，桌面优先。

## 12. 农业病虫害 RAG

在现有 Agent 上增加一个记录级 RAG 知识库：

- 语料：`data/agri/docs.zh.json`（6,042 条，字段 `doc/disease/time/answer`；
  已加入 `.gitignore`，来源见 `data/agri/README.md`）；
- 检索：字段加权 BM25（disease 3.0 / time 1.5 / answer 1.0）+ 过滤 + 去重；
- 工具：`search_agri_kb`、`export_agri_report`；
- 报告：自包含 HTML，写到桌面 `AgriReports`，需要审批；
- 预览：报告登记为 artifact，SSE `artifact.created`，前端回答下方/内联链接可打开右侧 iframe；
- 默认关闭，`.env` 设置 `AGRI_KB_ENABLED=1` 开启。

```powershell
# 已在本机 .env 开启；手动开启：
$env:AGRI_KB_ENABLED="1"

# 可选覆盖
$env:AGRI_CORPUS_PATH="D:\...\docs.zh.json"
$env:AGRI_REPORT_DIR="D:\tmp\AgriReports"

# 测试
.\.venv\Scripts\python.exe -m pytest tests\test_agri.py -q
```

示例：

```text
用户：香蕉冠腐病采后怎么防治？
Agent：调用 search_agri_kb → 回答带 [K1] 引用
用户：把刚才的内容生成可视化报告放到桌面
Agent：调用 export_agri_report → 生成 HTML 到 Desktop\AgriReports

