# Multi-Agent Vibecoding

一个可本地运行的多智能体应用：

- **后端**：Python 单进程、多线程 Agent 运行时 + FastAPI BFF + SSE
- **前端**：React 18 + Ant Design 6 + Ant Design X
- **能力**：RAG 混合检索（BM25 + 向量 int8）、工具调用、计划、看板、队友、收件箱、文件、token 账单、审批、HTML/Excel/PDF 报告

前端是 [`muti-agent-ui`](muti-agent-ui/)，后端是 [`multi-agent`](multi-agent/)。

---

## 1. 项目结构

```text
multi-agent-vibecoding-20261114/
├─ README.md                     ← 当前文件
├─ multi-agent/                  ← Python 后端
│  ├─ agent/                     Agent 核心
│  ├─ server/                    FastAPI BFF
│  ├─ data/agri/                 本地数据目录（大文件不进 git）
│  ├─ config/default.yaml        默认配置
│  ├─ tests/                     后端测试
│  └─ README.md                  后端详细说明
├─ muti-agent-ui/                ← React 前端
│  ├─ src/
│  ├─ scripts/                   E2E 脚本
│  ├─ shots/                     README 截图
│  └─ README.md                  前端详细说明
├─ models/bge-small-zh-v1.5/     ← Embedding 模型（不进 git，见第 9 节下载）
└─ kb/                           ← 知识库索引产物（不进 git，见第 9 节构建）
```

---

## 2. 环境要求

| 依赖 | 要求 |
|------|------|
| Python | 3.11+，推荐 3.12 |
| Node.js | 20+ |
| pnpm | 9+ |
| Git | 可选，用于拉取/推送 |
| 系统 | Windows 10/11、macOS、Linux |

Windows PowerShell 建议先设置 UTF-8：

```powershell
chcp 65001
$env:PYTHONUTF8 = "1"
```

> macOS / Linux 把 `.\.venv\Scripts\python.exe` 换成 `.venv/bin/python`，
> 路径分隔符换成 `/` 即可；其余命令相同。

---

## 3. 第一次安装

### 3.1 后端

```powershell
cd D:\Desktop\multi-agent-vibecoding-20261114\multi-agent

# 如果 .venv 不存在（从 GitHub 克隆时通常没有）
uv venv --python 3.12 .venv

# 显式装进项目自己的 venv
uv pip install --python .\.venv\Scripts\python.exe -e ".[dev,web]"
```

验证：

```powershell
.\.venv\Scripts\python.exe -c "import agent; print(agent.__file__)"
```

输出的路径应该在当前 `multi-agent` 目录下。

> 如果你是从旧目录复制过来的，`.venv` 里的 editable 安装可能还指向旧路径。
> 看到 `D:\Desktop\multi-agent\agent\...` 就说明没指向新目录，重新执行上面的安装命令即可。

### 3.2 前端

```powershell
cd D:\Desktop\multi-agent-vibecoding-20261114\muti-agent-ui
pnpm install
```

如果 `pnpm install` 提示 ignored build scripts，仓库里的 `pnpm-workspace.yaml` 已经放行 `esbuild` / `msw`，重新执行一次即可。

---

## 4. 配置 Key

```powershell
cd D:\Desktop\multi-agent-vibecoding-20261114\multi-agent
Copy-Item .env.example .env
notepad .env
```

至少填写：

```dotenv
DEEPSEEK_API_KEY=sk-...
```

`.env` 已被 `.gitignore` 忽略，不要提交到 GitHub。

也可以不建 `.env`，直接用系统环境变量：

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
```

---

## 5. 启动（开发模式）

开两个 PowerShell 终端。

### 终端 1：后端

```powershell
cd D:\Desktop\multi-agent-vibecoding-20261114\multi-agent
$env:MA_WEB_DEV = "1"
.\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8787 --reload
```

看到：

```text
Uvicorn running on http://127.0.0.1:8787
```

### 终端 2：前端

```powershell
cd D:\Desktop\multi-agent-vibecoding-20261114\muti-agent-ui
pnpm dev
```

看到：

```text
Local: http://localhost:5173/
```

打开：

**<http://localhost:5173>**

---

## 6. 验证是否正常

```powershell
# 后端健康检查
Invoke-WebRequest http://127.0.0.1:8787/api/health -UseBasicParsing

# 前端
Invoke-WebRequest http://localhost:5173 -UseBasicParsing
```

浏览器里发一句：

```text
只回复：联调成功
```

满足以下条件即可：

- 能流式返回
- 刷新页面后消息还在
- 右侧栏能打开计划/看板/队友

---

## 7. 停止

### 前台启动

两个终端分别按：

```text
Ctrl + C
```

`pnpm dev` 一次没退就再按一次。

### 后台启动 / 找不到窗口

按端口停，不要全杀 python/node：

```powershell
# 后端
Get-NetTCPConnection -LocalPort 8787 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }

# 前端
Get-NetTCPConnection -LocalPort 5173 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

确认：

```powershell
Get-NetTCPConnection -LocalPort 8787,5173 -State Listen
```

没有输出 = 已经停止。

---

## 8. 生产模式（只开一个端口）

```powershell
# 1. 构建前端
cd D:\Desktop\multi-agent-vibecoding-20261114\muti-agent-ui
pnpm build

# 2. 启动后端，托管前端 dist
cd D:\Desktop\multi-agent-vibecoding-20261114\multi-agent
.\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8787
```

打开：

**<http://127.0.0.1:8787>**

生产模式下修改前端后，需要重新执行 `pnpm build` 并刷新页面。

---

## 9. 农业 RAG（可选）：92.8 万条语料 + BM25/向量混合检索

`.env` 中开启：

```dotenv
AGRI_KB_ENABLED=1
AGRI_CORPUS_PATH=../kb
AGRI_KB_DIR=../kb
AGRI_EMBEDDING_MODEL_DIR=../models/bge-small-zh-v1.5
```

### 9.1 检索方案

| 项 | 说明 |
|------|------|
| 语料 | Chinese-QA-AFAF 中文农林牧渔问答数据集，927,864 条（id / prompt / response） |
| 检索通道 | BM25（自研 numpy 倒排，480,534 词项）+ 向量（faiss **SQ8 int8 量化**，512 维） |
| 融合 | RRF（Reciprocal Rank Fusion，k=60）；向量通道不可用时自动降级纯 BM25 |
| Embedding | `bge-small-zh-v1.5`（BAAI），纯 CPU 推理，惰性加载 |
| 内存占用 | int8 索引驻内存约 475MB；答案文本走 mmap 按行读，不进内存 |
| 性能 | 启动加载约 2~3 秒，单次检索约 0.5 秒（首次含模型加载约 7 秒） |

### 9.2 Fork 后从零跑通 RAG 的完整流程

仓库只含代码，语料与模型不进 git（体积大），按下面 5 步操作。

**步骤 1：安装 RAG 依赖**

```powershell
cd D:\path\to\multi-agent-vibecoding-20261114\multi-agent
# 按第 3 节装好基础环境后，加装 kb 可选依赖（faiss / sentence-transformers / pyarrow / openpyxl / reportlab）
uv pip install --python .\.venv\Scripts\python.exe -e ".[kb]"
```

**步骤 2：下载 Embedding 模型**（约 100MB，HuggingFace，国内可用 hf-mirror）

> 注意：请在**仓库根目录**（`multi-agent` 的上一级）执行，模型最终位于
> `models/bge-small-zh-v1.5/`，与步骤 5 的 `.env` 路径 `../models/bge-small-zh-v1.5` 对应。

```powershell
cd D:\path\to\multi-agent-vibecoding-20261114   # 仓库根目录
pip install -U "huggingface_hub[cli]"
$env:HF_ENDPOINT = "https://hf-mirror.com"   # 国内镜像，可选
huggingface-cli download BAAI/bge-small-zh-v1.5 --local-dir models/bge-small-zh-v1.5
```

> 下载完成后 `models/bge-small-zh-v1.5/` 下应能看到 `config.json`、
> `model.safetensors`、`tokenizer.json` 等文件。

**步骤 3：准备语料 parquet**（约 172MB，三选一）

- **方式 A · 本项目 Releases（推荐）**：到仓库的 Releases 页下载
  `Chinese-QA-AFAF-train-v2.parquet`，放到任意本地路径；
- **方式 B · HuggingFace 自取**（CC BY-SA 4.0）：本项目语料来自
  [`Mxode/Chinese-QA-Agriculture_Forestry_Animal_Husbandry_Fishery`](https://huggingface.co/datasets/Mxode/Chinese-QA-Agriculture_Forestry_Animal_Husbandry_Fishery)（900K+ 农林牧渔问答），
  train-v2 清洗版是 [`Mxode/Chinese-Instruct`](https://huggingface.co/datasets/Mxode/Chinese-Instruct)
  的 Chinese-QA-AFAF 子集（jsonl，prompt/response 两列）。
  转成 parquet（补一列 id）的参考脚本：

  ```powershell
  pip install datasets pandas pyarrow
  python -c "
  import pandas as pd, uuid
  df = pd.read_json('hf://datasets/Mxode/Chinese-Instruct/Chinese-QA-AFAF/Chinese-QA-AFAF.jsonl', lines=True)
  df = df[df['response'].str.len() >= 5].reset_index(drop=True)
  df.insert(0, 'id', [uuid.uuid4().hex[:12] for _ in range(len(df))])
  df = df[['id', 'prompt', 'response']]
  df.to_parquet('Chinese-QA-AFAF-train-v2.parquet', index=False)
  print(len(df))
  "
  ```

- **方式 C · 你自己的数据**：任意领域的语料都行，只要最终是一个含
  `id`、`prompt`、`response` 三列的 parquet 文件（id 唯一、response ≥ 5 字），
  例如用 `pandas.DataFrame({'id': ..., 'prompt': ..., 'response': ...}).to_parquet(...)` 导出。

**步骤 4：构建索引**（一次性，纯 CPU 编码 92.8 万条约 1 小时）

```powershell
.\.venv\Scripts\python.exe scripts\build_kb.py `
    --parquet "D:\path\to\Chinese-QA-AFAF-train-v2.parquet" `
    --model "D:\path\to\models\bge-small-zh-v1.5" `
    --out "..\kb"
```

产物写入 `kb/`（文本块 + BM25 倒排 + fp16 向量 + faiss int8 索引 + `kb_meta.json`）。
调试建议先加 `--limit 5000` 跑小样验证链路，再删掉重建全量。
产物文件说明见 [`kb/README.md`](kb/README.md)。

**步骤 5：配置并启动**

```dotenv
# multi-agent/.env 追加
AGRI_KB_ENABLED=1
AGRI_CORPUS_PATH=../kb
AGRI_KB_DIR=../kb
AGRI_EMBEDDING_MODEL_DIR=../models/bge-small-zh-v1.5
```

启动后端后日志出现即成功：

```text
agri knowledge base loaded mode=qa records=927864 index=bm25+vector-rrf(int8) vector=True
```

对话里直接问：

```text
香蕉采后怎么防腐保鲜？用 3 条要点回答，每条带 [K#] 和来源 doc。
```

无证据时系统会明确拒答（"知识库没有足够依据"），不编造。

> 注意：`kb_meta.json` 记录了语料 SHA-256；换语料后**必须重新构建索引**，
> 直接复用旧产物会导致检索结果与语料不一致。

### 9.5 报告导出（HTML / Excel / PDF / Markdown）

```text
把刚才的内容生成一份 Excel 报告 / PDF 报告放到桌面。
```

模型会调用 `export_agri_report`（`format` 可选 `html / xlsx / pdf`）：

- 报告写入 `%USERPROFILE%\Desktop\AgriReports\`（可用 `AGRI_REPORT_DIR` 覆盖）；
- 写入前弹审批确认，默认等待 180 秒；
- 回答下方出现"查看报告"入口，右侧抽屉页内预览：
  HTML / **Markdown** 直接渲染，PDF 内嵌预览，Excel 提供下载；
- Excel 为 openpyxl 生成、PDF 为 reportlab 生成（中文字体内置回退），来源逐条可追溯。

---

## 10. Web 页面怎么用

右侧栏：

| 面板 | 用途 |
|------|------|
| 计划 | 当前会话的多步骤计划，按会话隔离 |
| 看板 | 团队任务；已完成/已取消默认隐藏 |
| 队友 | 花名册下拉或自定义名字，召入/关停 |
| 收件 | 查看 lead / 队友的消息回执 |
| 文件 | 查看工作区文件 |
| 账单 | 今日/近 7 天 token 与预算 |

常用操作：

1. 等待批准时，在审批弹窗点“允许”；默认 180 秒超时。
2. 看板上点“推进”修改任务状态。
3. 给队友派活时，在对话里让 Lead 发 `task_assigned`，不要只点看板。
4. 报告生成后，回答下方会出现“查看报告”，点击右侧预览。
5. **设置页可切换工作区**（Agent 文件读写的沙箱根目录）：输入绝对路径点“切换”，立即生效并持久保存，重启后端沿用；有任务运行时不可切换。知识库/模型路径已锚定项目根，切换不受影响。

---

## 11. 测试

后端（不需要 API Key）：

```powershell
cd D:\Desktop\multi-agent-vibecoding-20261114\multi-agent
.\.venv\Scripts\python.exe -m pytest -q
```

前端：

```powershell
cd D:\Desktop\multi-agent-vibecoding-20261114\muti-agent-ui
pnpm test
pnpm typecheck
pnpm lint
pnpm build
```

当前状态：

- 后端：**193 passed**
- 前端：**16 passed**
- 前端 typecheck / lint / build 通过

浏览器联调脚本见 [`muti-agent-ui/README.md`](muti-agent-ui/README.md)。

---

## 12. 常见问题

| 现象 | 处理 |
|------|------|
| `WinError 10048` 端口占用 | 按第 7 节停掉旧进程 |
| `ModuleNotFoundError: No module named 'agent'` | 用 `.\.venv\Scripts\python.exe -m uvicorn ...`，不要用全局 `uvicorn` |
| `import agent` 指向旧目录 | 重新执行 `uv pip install --python .\.venv\Scripts\python.exe -e ".[dev,web]"` |
| `E_LLM_NO_KEY` | `.env` 没有 `DEEPSEEK_API_KEY`，或启动目录不对 |
| 农业工具不出现 | `AGRI_KB_ENABLED=1` 且语料文件存在 |
| 审批超时 | 默认 180 秒；演示时及时点“允许”，或在 `.env` 调大 `MA_APPROVAL_TIMEOUT` |
| 看板看不到任务 | 已完成/已取消默认隐藏；新建或恢复未终态任务 |
| 计划为空 | 计划按会话隔离；新会话需要重新生成 |
| `pnpm` ignored build scripts | 重新 `pnpm install`，仓库已放行 esbuild/msw |
| 移动项目后 `pnpm` 报 `ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY` | 删除 `muti-agent-ui/node_modules`，再执行 `pnpm install`；或 `$env:CI="true"; pnpm install` |
| 移动项目后 import 到旧目录 | 删除 `multi-agent/.venv` 重建，或重新执行 `uv pip install --python .\.venv\Scripts\python.exe -e ".[dev,web]"` |

---

## 13. 安全与边界

- 不要提交 `.env`、`.venv`、`.team/`、`.web/`、`memory/`、`reports/`。
- 大体积数据资产（`kb/` 索引产物、`models/` 模型、语料 parquet）已在根 `.gitignore` 排除，不进 git。
- 桌面写入、危险命令走审批。
- 文件工具限制在工作区内；桌面报告由专用工具生成。
- 农业建议只复述知识库原文，不构成用药推荐。
- 当前 RAG 是 BM25 + 向量（faiss int8）RRF 混合检索，向量通道不可用时自动降级纯 BM25。
- 报告支持自包含 HTML、Excel（openpyxl）、PDF（reportlab）三种格式，由后端确定性生成。

---

## 14. 详细文档

- 后端：[`multi-agent/README.md`](multi-agent/README.md)
- 前端：[`muti-agent-ui/README.md`](muti-agent-ui/README.md)
