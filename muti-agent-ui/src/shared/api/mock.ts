/**
 * 前端 Mock 后端：内存态 + localStorage 持久化 + 伪 SSE 事件流。
 *
 * 目的：在后端 BFF 未就绪时，界面能完整跑通「发消息 → 流式 → 工具 → 审批 → 计划 → 看板」。
 * 接真实后端时把 VITE_USE_MOCK 设为 false，端点是同一套。
 */

import { ApiError } from './client';
import type {
  Approval,
  Artifact,
  Envelope,
  FileContent,
  FileNode,
  Member,
  Message,
  Meta,
  Plan,
  RosterMember,
  Run,
  SendMessageResult,
  Session,
  Skill,
  Task,
  TaskStatus,
  UsageReport,
} from './types';
import type { RunEvent } from '../events/types';
import { isTerminalEvent } from '../events/types';
import type { PlanItem, TraceItem } from './types';

const STORAGE_KEY = 'ma.mock.v2';
const DAY_MS = 86_400_000;

function rid(): string {
  return crypto.randomUUID().replace(/-/g, '').slice(0, 12);
}

function nowIso(): string {
  return new Date().toISOString();
}

function dayKey(ts: number): string {
  const d = new Date(ts);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// ---------------------------------------------------------------- 状态

interface MockUsageEvent {
  agent: string;
  in: number;
  out: number;
  day: string;
}

interface PersistedState {
  sessions: Session[];
  messages: Record<string, Message[]>;
  plan: Plan;
  board: Task[];
  team: Member[];
  inbox: Record<string, Envelope[]>;
  usage: MockUsageEvent[];
}

function emptyState(): PersistedState {
  const t = Date.now();
  const mkTask = (
    n: number,
    title: string,
    status: TaskStatus,
    owner: string | null,
    extra: Partial<Task> = {},
  ): Task => ({
    id: `T-${String(n).padStart(6, '0')}`,
    title,
    description: '',
    status,
    owner,
    priority: 3,
    depends_on: [],
    tags: [],
    created_at: new Date(t - n * 3_600_000).toISOString(),
    updated_at: new Date(t - n * 600_000).toISOString(),
    attempts: owner ? 1 : 0,
    result: status === 'done' ? '已完成（演示数据）' : '',
    artifacts: status === 'done' ? ['reports/INDEX.md'] : [],
    error: null,
    ...extra,
  });

  return {
    sessions: [],
    messages: {},
    plan: { goal: '', revision: 0, updated_at: nowIso(), items: [] },
    board: [
      mkTask(1, '整理 reports 索引', 'done', 'alice', {
        description: '把 reports/ 下的 md 汇总成 INDEX.md',
        priority: 2,
        tags: ['docs'],
      }),
      mkTask(2, '给 searcher 补超时说明', 'in_progress', 'bob', {
        description: 'web_fetch 超时要写进回禀',
        priority: 1,
      }),
      mkTask(3, '补一版 README 快速开始', 'pending', null, { priority: 4 }),
    ],
    team: [
      {
        name: 'alice',
        role: 'researcher',
        status: 'idle',
        tools: ['read_file', 'grep', 'web_fetch'],
        max_turns: 10,
        turns_used: 2,
        last_seen: nowIso(),
        last_error: null,
      },
      {
        name: 'bob',
        role: 'coder',
        status: 'offline',
        tools: ['read_file', 'write_file', 'run_command'],
        max_turns: 14,
        turns_used: 0,
        last_seen: null,
        last_error: null,
      },
    ],
    inbox: {
      lead: [
        {
          id: 'M-100001',
          type: 'task_result',
          sender: 'alice',
          recipient: 'lead',
          subject: '任务 T-000001 完成',
          body: '已生成 reports/INDEX.md，共 14 条。',
          ts: new Date(t - 2 * 3_600_000).toISOString(),
          correlation_id: 'T-000001',
          reply_to: null,
          expects_reply: false,
        },
      ],
      alice: [],
      bob: [],
    },
    usage: [
      { agent: 'lead', in: 42_000, out: 3_200, day: dayKey(t - 2 * DAY_MS) },
      { agent: 'searcher#1', in: 21_600, out: 6_100, day: dayKey(t - 2 * DAY_MS) },
      { agent: 'lead', in: 55_800, out: 4_400, day: dayKey(t - DAY_MS) },
      { agent: 'alice', in: 9_100, out: 1_200, day: dayKey(t - DAY_MS) },
      { agent: 'lead', in: 30_200, out: 2_800, day: dayKey(t) },
    ],
  };
}

let state: PersistedState = load();

function load(): PersistedState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return emptyState();
    const parsed = JSON.parse(raw) as PersistedState;
    if (!parsed.sessions || !parsed.messages) return emptyState();
    // 刷新后不可能还有运行中的 run（mock 的 run 在内存里）
    for (const session of parsed.sessions) {
      if (session.last_run?.status === 'running' || session.last_run?.status === 'queued') {
        session.last_run = { id: session.last_run.id, status: 'failed' };
      }
    }
    return parsed;
  } catch {
    return emptyState();
  }
}

function save(): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // 配额满了就算了，mock 不阻塞界面
  }
}

// ---------------------------------------------------------------- 运行态

interface MockRun {
  id: string;
  sessionId: string;
  status: Run['status'];
  messageId: string;
  input: string;
  text: string;
  trace: TraceItem[];
  events: RunEvent[];
  seq: number;
  listeners: Set<(event: RunEvent) => void>;
  cancelled: boolean;
  finished: boolean;
  startedAt: string;
  approval: { id: string; resolve: (value: boolean) => void } | null;
}

const runs = new Map<string, MockRun>();
const clientMessageRuns = new Map<string, string>();
const approvals = new Map<string, { runId: string; approval: Approval }>();

function emit(
  run: MockRun,
  type: RunEvent['type'],
  data: Record<string, unknown>,
): void {
  run.seq += 1;
  const event = {
    run_id: run.id,
    seq: run.seq,
    ts: nowIso(),
    type,
    data,
  } as unknown as RunEvent;
  run.events.push(event);
  for (const listener of run.listeners) listener(event);
}

function addTrace(run: MockRun, item: TraceItem): void {
  run.trace.push(item);
}

async function pause(run: MockRun, ms: number): Promise<boolean> {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (run.cancelled) return false;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  return !run.cancelled;
}

// ---------------------------------------------------------------- 消息与持久化

function getSessionOrThrow(sessionId: string): Session {
  const session = state.sessions.find((s) => s.id === sessionId);
  if (!session) throw new ApiError(404, { code: 'E_SESSION_NOT_FOUND', message: '会话不存在' });
  return session;
}

function appendMessage(
  sessionId: string,
  role: Message['role'],
  content: string,
  extra: Partial<Message> = {},
): Message {
  const list = state.messages[sessionId] ?? [];
  const message: Message = {
    id: extra.id ?? `m_${rid()}`,
    session_id: sessionId,
    run_id: extra.run_id ?? null,
    role,
    content,
    status: extra.status ?? 'done',
    seq: (list.at(-1)?.seq ?? 0) + 1,
    created_at: nowIso(),
    usage: extra.usage ?? null,
    trace: extra.trace ?? null,
  };
  list.push(message);
  state.messages[sessionId] = list;

  const session = state.sessions.find((s) => s.id === sessionId);
  if (session) {
    session.message_count = list.length;
    session.updated_at = message.created_at;
    if (extra.run_id) session.last_run = { id: extra.run_id, status: 'running' };
    if (role === 'user' && session.title === '新会话') {
      session.title = content.slice(0, 24) || '新会话';
    }
  }
  save();
  return message;
}

function persistAssistant(run: MockRun, status: Message['status']): void {
  if (run.status === 'running') return;
  const content = run.text.trim() || (status === 'cancelled' ? '[已取消]' : '[运行中断]');
  appendMessage(run.sessionId, 'assistant', content, {
    id: run.messageId,
    run_id: run.id,
    status,
    usage: { input_tokens: 800 + run.input.length, output_tokens: content.length },
    trace: run.trace,
  });
  const session = state.sessions.find((s) => s.id === run.sessionId);
  if (session) session.last_run = { id: run.id, status: run.status };
  save();
}

// ---------------------------------------------------------------- 脚本

function buildPlan(text: string): PlanItem[] {
  const goal = text.trim().slice(0, 40) || '完成当前任务';
  return [
    { id: 1, content: `盘点与「${goal.slice(0, 12)}」相关的文件`, status: 'completed', owner: null, note: '' },
    { id: 2, content: '执行核心动作并验证结果', status: 'in_progress', owner: null, note: '' },
    { id: 3, content: '汇总结论与产物路径', status: 'pending', owner: null, note: '' },
  ];
}

function updatePlan(run: MockRun, items: PlanItem[]): void {
  state.plan = {
    goal: run.input.trim().slice(0, 60) || '当前任务',
    revision: state.plan.revision + 1,
    updated_at: nowIso(),
    items,
  };
  emit(run, 'plan.updated', {
    revision: state.plan.revision,
    goal: state.plan.goal,
    items,
  });
  save();
}

function wantsSubagent(text: string): boolean {
  return /(调研|查一下|检索|搜索|research|search|网页)/i.test(text);
}

function wantsApproval(text: string): boolean {
  return /(pytest|git push|部署|发布|命令|run_command|执行)/i.test(text);
}

function answerText(options: { subagent: boolean; approval: 'none' | 'approved' | 'denied' }): string {
  const lines: string[] = [];
  lines.push('## 结论');
  if (options.approval === 'denied') {
    lines.push('命令被策略拒绝（`E_TOOL_DENIED`）。我没有绕路执行，改成只读方式核对了一遍现状。');
  } else if (options.approval === 'approved') {
    lines.push('命令已执行并验证通过（`exit_code: 0`）。结果与预期一致。');
  } else {
    lines.push('已按计划完成。工作区结构清晰，核心文件都能正常读取。');
  }

  lines.push('');
  lines.push('## 依据');
  lines.push('- `reports/` 下现有 3 个文件（`INDEX.md`、`a.md`、`_loop_demo.md`）。');
  lines.push('- `README.md` 第一行是 `# multi-agent`，描述与运行时一致。');
  if (options.subagent) {
    lines.push('- 子代理 `searcher` 回禀 420 字，结论与本地盘查一致。');
  }
  lines.push('');
  lines.push('## 示例代码');
  lines.push('```python');
  lines.push('def add(a: int, b: int) -> int {');
  lines.push('    return a + b');
  lines.push('}');
  lines.push('```');
  lines.push('');
  lines.push('> 这是前端 Mock 生成的回答；接入真实 BFF 后，内容会来自运行时的事件流。');
  return lines.join('\n');
}

async function runTool(
  run: MockRun,
  name: string,
  risk: 'read' | 'exec',
  inputPreview: string,
  outputPreview: string,
  ms: number,
  ok = true,
  errorCode: string | null = null,
): Promise<string> {
  const id = `call_${rid().slice(0, 8)}`;
  emit(run, 'tool.started', {
    tool_call_id: id,
    name,
    risk,
    input_preview: inputPreview,
    agent: 'lead',
  });
  await pause(run, ms);
  if (run.cancelled) return id;
  emit(run, 'tool.finished', {
    tool_call_id: id,
    name,
    ok,
    error_code: errorCode,
    duration_ms: ms,
    output_preview: outputPreview,
    truncated: false,
    agent: 'lead',
  });
  addTrace(run, {
    kind: 'tool',
    tool_call_id: id,
    name,
    risk,
    agent: 'lead',
    input_preview: inputPreview,
    ok,
    error_code: errorCode,
    duration_ms: ms,
    output_preview: outputPreview,
    truncated: false,
  });
  return id;
}

function waitApproval(run: MockRun, approvalId: string): Promise<{ approved: boolean; reason: 'user' | 'timeout' }> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      run.approval = null;
      resolve({ approved: false, reason: 'timeout' });
    }, 60_000);

    run.approval = {
      id: approvalId,
      resolve: (approved: boolean) => {
        clearTimeout(timer);
        run.approval = null;
        resolve({ approved, reason: 'user' });
      },
    };

    if (run.cancelled) {
      clearTimeout(timer);
      run.approval = null;
      resolve({ approved: false, reason: 'user' });
    }
  });
}

async function runCommand(run: MockRun): Promise<'approved' | 'denied'> {
  const id = `call_${rid().slice(0, 8)}`;
  const inputPreview = '{"command": "pytest -q demo_project", "cwd": "."}';
  emit(run, 'tool.started', {
    tool_call_id: id,
    name: 'run_command',
    risk: 'exec',
    input_preview: inputPreview,
    agent: 'lead',
  });

  const approvalId = `a_${rid()}`;
  const approval: Approval = {
    id: approvalId,
    run_id: run.id,
    tool_call_id: id,
    name: 'run_command',
    reason: '执行命令：pytest -q demo_project',
    expires_at: new Date(Date.now() + 60_000).toISOString(),
  };
  approvals.set(approvalId, { runId: run.id, approval });
  emit(run, 'approval.required', {
    approval_id: approvalId,
    tool_call_id: id,
    name: approval.name,
    reason: approval.reason,
    expires_at: approval.expires_at,
  });

  const { approved, reason } = await waitApproval(run, approvalId);
  approvals.delete(approvalId);
  emit(run, 'approval.resolved', {
    approval_id: approvalId,
    approved,
    reason,
  });
  if (run.cancelled) return 'denied';

  await pause(run, 500);
  const ok = approved;
  const output = ok
    ? 'exit_code: 0\nduration_ms: 482.1\n--- stdout ---\n4 passed in 0.48s\n--- stderr ---\n(empty)'
    : '[工具错误] E_TOOL_DENIED: 用户未确认：执行命令：pytest -q demo_project';
  emit(run, 'tool.finished', {
    tool_call_id: id,
    name: 'run_command',
    ok,
    error_code: ok ? null : 'E_TOOL_DENIED',
    duration_ms: 500,
    output_preview: output,
    truncated: false,
    agent: 'lead',
  });
  addTrace(run, {
    kind: 'tool',
    tool_call_id: id,
    name: 'run_command',
    risk: 'exec',
    agent: 'lead',
    input_preview: inputPreview,
    ok,
    error_code: ok ? null : 'E_TOOL_DENIED',
    duration_ms: 500,
    output_preview: output,
    truncated: false,
  });
  return ok ? 'approved' : 'denied';
}

async function streamText(
  run: MockRun,
  channel: 'text' | 'reasoning',
  text: string,
  chunkSize = 8,
  delay = 22,
): Promise<boolean> {
  for (let index = 0; index < text.length; index += chunkSize) {
    if (run.cancelled) return false;
    const delta = text.slice(index, index + chunkSize);
    emit(run, 'message.delta', {
      message_id: run.messageId,
      channel,
      delta,
      agent: 'lead',
    });
    if (channel === 'text') run.text += delta;
    const ok = await pause(run, delay);
    if (!ok) return false;
  }
  return true;
}

function completeRun(run: MockRun): void {
  if (run.finished) return;
  const inputTokens = 800 + run.input.length;
  const outputTokens = Math.max(1, run.text.length);
  emit(run, 'message.completed', {
    message_id: run.messageId,
    text: run.text,
    stop_reason: 'end_turn',
    usage: { input_tokens: inputTokens, output_tokens: outputTokens },
  });
  emit(run, 'run.completed', {
    reply: run.text,
    usage_total: { input_tokens: inputTokens, output_tokens: outputTokens },
    counters: { turns: 2, tool_calls: run.trace.filter((t) => t.kind === 'tool').length },
  });
  state.usage.push({ agent: 'lead', in: inputTokens, out: outputTokens, day: dayKey(Date.now()) });
  run.status = 'completed';
  run.finished = true;
  persistAssistant(run, 'done');
}

function cancelRun(run: MockRun): void {
  if (run.finished) return;
  run.cancelled = true;
  run.approval?.resolve(false);
  emit(run, 'run.cancelled', { by: 'user' });
  run.status = 'cancelled';
  run.finished = true;
  persistAssistant(run, 'cancelled');
}

function failRun(run: MockRun, message: string): void {
  if (run.finished) return;
  emit(run, 'run.failed', { code: 'E_RUN_CRASHED', message, retryable: true });
  run.status = 'failed';
  run.finished = true;
  persistAssistant(run, 'error');
}

async function executeRun(run: MockRun): Promise<void> {
  try {
    const text = run.input;
    emit(run, 'run.started', {
      message_id: run.messageId,
      turn: 0,
      max_turns: 40,
    });
    if (!(await pause(run, 200))) return;

    emit(run, 'turn.started', { turn: 1, agent: 'lead' });
    const planItems = buildPlan(text);
    updatePlan(run, planItems);
    if (!(await pause(run, 300))) return;

    const withSubagent = wantsSubagent(text);
    if (withSubagent) {
      emit(run, 'subagent.started', {
        key: 'searcher',
        title: '检索专员',
        task_preview: `查：${text.slice(0, 40)}`,
      });
      if (!(await pause(run, 900))) return;
      emit(run, 'subagent.finished', {
        key: 'searcher',
        ok: true,
        turns: 3,
        summary_chars: 420,
        duration_ms: 900,
      });
      addTrace(run, {
        kind: 'subagent',
        key: 'searcher',
        ok: true,
        turns: 3,
        summary_chars: 420,
        duration_ms: 900,
      });
      state.usage.push({ agent: 'searcher#1', in: 12_400, out: 420, day: dayKey(Date.now()) });
    }

    await runTool(
      run,
      'list_dir',
      'read',
      '{\n  "path": "reports",\n  "depth": 1\n}',
      'reports/\n  INDEX.md  (1.2 KB)\n  a.md  (0.4 KB)\n  _loop_demo.md  (35 B)',
      420,
    );
    if (run.cancelled) return;

    planItems[0] = { ...planItems[0], status: 'completed' };
    planItems[1] = { ...planItems[1], status: 'in_progress' };
    updatePlan(run, planItems);
    if (!(await pause(run, 200))) return;

    let approval: 'none' | 'approved' | 'denied' = 'none';
    if (wantsApproval(text)) {
      approval = await runCommand(run);
      if (run.cancelled) return;
    } else {
      await runTool(
        run,
        'read_file',
        'read',
        '{"path": "README.md", "limit": 5}',
        '# multi-agent\n\n单进程、多线程、文件持久化的多智能体协作运行时。\n…',
        420,
      );
      if (run.cancelled) return;
    }

    planItems[1] = { ...planItems[1], status: 'completed' };
    planItems[2] = { ...planItems[2], status: 'in_progress' };
    updatePlan(run, planItems);
    if (!(await pause(run, 220))) return;

    emit(run, 'turn.started', { turn: 2, agent: 'lead' });
    await streamText(
      run,
      'reasoning',
      withSubagent ? '子代理返回了摘要，本地目录也已经盘点完，接下来汇总。' : '目录与 README 已读，接下来直接给结论。',
      12,
      18,
    );
    if (run.cancelled) return;

    await streamText(run, 'text', answerText({ subagent: withSubagent, approval }), 8, 22);
    if (run.cancelled) return;

    planItems[2] = { ...planItems[2], status: 'completed' };
    updatePlan(run, planItems);
    await pause(run, 180);
    if (run.cancelled) return;

    completeRun(run);
  } catch (error) {
    failRun(run, error instanceof Error ? error.message : '未知错误');
  }
}

// ---------------------------------------------------------------- 对外 API

export function mockMeta(): Meta {
  return {
    api_version: 1,
    app_version: '0.2.0-mock',
    model: 'deepseek-flash',
    workspace: { id: 'default', root: 'D:\\Desktop\\multi-agent', name: 'multi-agent' },
    capabilities: { stream: true, reasoning: true, approvals: true, team: true, mcp: false },
    limits: { max_turns_per_task: 40, daily_token_budget: 200_000_000, max_message_chars: 20_000 },
    server_time: nowIso(),
  };
}

export function mockListSessions(): { sessions: Session[] } {
  const sessions = [...state.sessions].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  return { sessions };
}

export function mockCreateSession(title = '新会话'): Session {
  const ts = nowIso();
  const session: Session = {
    id: `s_${rid()}`,
    title,
    status: 'active',
    created_at: ts,
    updated_at: ts,
    message_count: 0,
    last_run: null,
    archived: false,
  };
  state.sessions.push(session);
  state.messages[session.id] = [];
  save();
  return session;
}

export function mockPatchSession(sessionId: string, patch: Partial<Pick<Session, 'title' | 'archived'>>): Session {
  const session = getSessionOrThrow(sessionId);
  if (patch.title !== undefined) session.title = patch.title.trim() || '新会话';
  if (patch.archived !== undefined) {
    session.archived = patch.archived;
    session.status = patch.archived ? 'archived' : 'active';
  }
  session.updated_at = nowIso();
  save();
  return session;
}

export function mockDeleteSession(sessionId: string): { ok: true } {
  state.sessions = state.sessions.filter((s) => s.id !== sessionId);
  delete state.messages[sessionId];
  save();
  return { ok: true };
}

export function mockListMessages(
  sessionId: string,
  beforeSeq?: number,
  limit = 50,
): { messages: Message[]; has_more: boolean; next_before_seq: number | null } {
  getSessionOrThrow(sessionId);
  const all = state.messages[sessionId] ?? [];
  const filtered = beforeSeq ? all.filter((m) => m.seq < beforeSeq) : all;
  const slice = filtered.slice(Math.max(0, filtered.length - limit));
  return {
    messages: slice,
    has_more: filtered.length > slice.length,
    next_before_seq: slice.length ? slice[0].seq : null,
  };
}

export function mockSendMessage(
  sessionId: string,
  text: string,
  clientMsgId: string,
): SendMessageResult {
  getSessionOrThrow(sessionId);
  const existingRunId = clientMessageRuns.get(`${sessionId}:${clientMsgId}`);
  if (existingRunId) {
    const existing = runs.get(existingRunId);
    if (existing) {
      return {
        run_id: existing.id,
        message_id: existing.messageId,
        stream_url: `/api/runs/${existing.id}/events`,
        session_seq: 0,
      };
    }
  }

  const userMessage = appendMessage(sessionId, 'user', text, {
    id: `m_${rid()}`,
    status: 'done',
  });

  const run: MockRun = {
    id: `r_${rid()}`,
    sessionId,
    status: 'running',
    messageId: `m_${rid()}`,
    input: text,
    text: '',
    trace: [],
    events: [],
    seq: 0,
    listeners: new Set(),
    cancelled: false,
    finished: false,
    startedAt: nowIso(),
    approval: null,
  };
  runs.set(run.id, run);
  clientMessageRuns.set(`${sessionId}:${clientMsgId}`, run.id);

  const session = state.sessions.find((s) => s.id === sessionId);
  if (session) {
    session.last_run = { id: run.id, status: 'running' };
    session.updated_at = userMessage.created_at;
    save();
  }

  void executeRun(run);

  return {
    run_id: run.id,
    message_id: run.messageId,
    stream_url: `/api/runs/${run.id}/events`,
    session_seq: userMessage.seq,
  };
}

export function mockGetRun(runId: string): Run {
  const run = runs.get(runId);
  if (!run) throw new ApiError(404, { code: 'E_RUN_NOT_FOUND', message: '运行不存在' });
  return {
    id: run.id,
    session_id: run.sessionId,
    status: run.status,
    input: run.input,
    reply: run.text || null,
    error_code: run.status === 'failed' ? 'E_RUN_CRASHED' : null,
    error_message: null,
    started_at: run.startedAt,
    finished_at: run.finished ? nowIso() : null,
    usage: { input_tokens: 800 + run.input.length, output_tokens: run.text.length },
    turn_count: 2,
  };
}

export function mockCancelRun(runId: string): { ok: boolean } {
  const run = runs.get(runId);
  if (!run) throw new ApiError(404, { code: 'E_RUN_NOT_FOUND', message: '运行不存在' });
  cancelRun(run);
  return { ok: true };
}

export function mockRetryRun(runId: string): SendMessageResult {
  const run = runs.get(runId);
  if (!run) throw new ApiError(404, { code: 'E_RUN_NOT_FOUND', message: '运行不存在' });
  return mockSendMessage(run.sessionId, run.input, crypto.randomUUID());
}

export function mockListApprovals(runId: string): { approvals: Approval[] } {
  const list = [...approvals.values()]
    .filter((item) => item.runId === runId)
    .map((item) => item.approval);
  return { approvals: list };
}

export function mockResolveApproval(approvalId: string, approved: boolean): { status: string } {
  const entry = approvals.get(approvalId);
  if (!entry) throw new ApiError(404, { code: 'E_APPROVAL_NOT_FOUND', message: '审批不存在或已过期' });
  const run = runs.get(entry.runId);
  approvals.delete(approvalId);
  if (run?.approval?.id === approvalId) run.approval.resolve(approved);
  return { status: approved ? 'approved' : 'denied' };
}

export async function mockStreamRun(
  runId: string,
  after: number,
  onEvent: (event: RunEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const run = runs.get(runId);
  if (!run) throw new ApiError(404, { code: 'E_RUN_NOT_FOUND', message: '运行不存在' });

  for (const event of run.events) {
    if (event.seq > after) onEvent(event);
  }
  if (run.finished || signal.aborted) return;

  await new Promise<void>((resolve) => {
    const listener = (event: RunEvent) => {
      onEvent(event);
      if (isTerminalEvent(event.type)) cleanup();
    };
    const onAbort = () => cleanup();
    const cleanup = () => {
      run.listeners.delete(listener);
      signal.removeEventListener('abort', onAbort);
      resolve();
    };
    run.listeners.add(listener);
    signal.addEventListener('abort', onAbort, { once: true });
    if (signal.aborted) cleanup();
  });
}

export function mockGetPlan(): Plan {
  return state.plan;
}

export function mockGetBoard(includeDone = false): { tasks: Task[] } {
  const tasks = includeDone ? state.board : state.board.filter((t) => t.status !== 'done');
  return { tasks: [...tasks].sort((a, b) => a.priority - b.priority) };
}

export function mockCreateTask(body: {
  title: string;
  description?: string;
  priority?: number;
}): Task {
  const task: Task = {
    id: `T-${rid().slice(0, 6)}`,
    title: body.title,
    description: body.description ?? '',
    status: 'pending',
    owner: null,
    priority: body.priority ?? 3,
    depends_on: [],
    tags: [],
    created_at: nowIso(),
    updated_at: nowIso(),
    attempts: 0,
    result: '',
    artifacts: [],
    error: null,
  };
  state.board.push(task);
  save();
  return task;
}

const TRANSITIONS: Record<TaskStatus, TaskStatus[]> = {
  pending: ['claimed', 'cancelled'],
  claimed: ['in_progress', 'pending', 'cancelled'],
  in_progress: ['review', 'blocked', 'failed', 'cancelled'],
  review: ['done', 'in_progress', 'failed', 'cancelled'],
  blocked: ['in_progress', 'failed', 'cancelled'],
  done: [],
  failed: ['pending'],
  cancelled: ['pending'],
};

export function mockUpdateTask(
  taskId: string,
  body: { status: TaskStatus; owner?: string; result?: string; note?: string; artifacts?: string[] },
): Task {
  const task = state.board.find((t) => t.id === taskId);
  if (!task) throw new ApiError(404, { code: 'E_TASK_NOT_FOUND', message: '任务不存在' });
  const allowed = TRANSITIONS[task.status];
  if (!allowed.includes(body.status)) {
    throw new ApiError(422, {
      code: 'E_TASK_ILLEGAL_TRANSITION',
      message: `任务不能从 ${task.status} 变为 ${body.status}`,
      details: { allowed, current: task.status },
    });
  }
  if (body.status === 'done' && !(body.result ?? task.result).trim()) {
    throw new ApiError(422, { code: 'E_TASK_NO_RESULT', message: '标记完成需要填写结论摘要' });
  }
  if (body.status === 'blocked' && !(body.note ?? '').trim()) {
    throw new ApiError(422, { code: 'E_TASK_INVALID', message: '标记阻塞需要填写原因' });
  }
  task.status = body.status;
  task.updated_at = nowIso();
  if (body.owner) task.owner = body.owner;
  if (body.result) task.result = body.result;
  if (body.note) task.error = body.note;
  if (body.artifacts) task.artifacts = body.artifacts;
  if (body.status === 'pending') task.owner = null;
  if (body.status === 'claimed') task.attempts += 1;
  save();
  return task;
}

export function mockGetTeam(): { members: Member[] } {
  return { members: state.team };
}

export function mockSpawn(name: string, role?: string): { name: string; message: string } {
  let member = state.team.find((m) => m.name === name);
  if (!member) {
    member = {
      name,
      role: role ?? 'teammate',
      status: 'working',
      tools: ['read_file', 'write_file'],
      max_turns: 12,
      turns_used: 0,
      last_seen: nowIso(),
      last_error: null,
    };
    state.team.push(member);
  } else {
    member.status = 'working';
    member.role = role ?? member.role;
    member.last_seen = nowIso();
  }
  save();
  return { name, message: `已召入队友 ${name}` };
}

export function mockShutdown(name: string): { name: string; message: string } {
  const member = state.team.find((m) => m.name === name);
  if (!member) throw new ApiError(404, { code: 'E_MEMBER_UNKNOWN', message: `没有队友 ${name}` });
  member.status = 'shutdown';
  save();
  return { name, message: `已关停 ${name}` };
}

export function mockGetInbox(name: string, peek = true): { messages: Envelope[] } {
  const messages = state.inbox[name] ?? [];
  if (!peek) {
    state.inbox[name] = [];
    save();
  }
  return { messages };
}

export function mockDrainInbox(name: string): { messages: Envelope[]; count: number } {
  const messages = state.inbox[name] ?? [];
  state.inbox[name] = [];
  save();
  return { messages, count: messages.length };
}

export function mockSendTeamMessage(body: {
  to: string;
  content: string;
  subject?: string;
  msg_type?: string;
}): Envelope {
  const envelope: Envelope = {
    id: `M-${rid().slice(0, 6)}`,
    type: body.msg_type ?? 'message',
    sender: 'lead',
    recipient: body.to,
    subject: body.subject ?? '',
    body: body.content,
    ts: nowIso(),
    correlation_id: null,
    reply_to: null,
    expects_reply: true,
  };
  state.inbox[body.to] = [...(state.inbox[body.to] ?? []), envelope];
  save();
  return envelope;
}

const SKILLS: Skill[] = [
  {
    name: 'web-research',
    description: '做外部资料调研时的标准流程，包含检索、交叉验证与出处标注',
    tags: ['research', 'web'],
    version: '1',
  },
];

export function mockGetSkills(): { skills: Skill[] } {
  return { skills: SKILLS };
}

const FILES: Record<string, string> = {
  'README.md':
    '# multi-agent\n\n单进程、多线程、文件持久化的多智能体协作运行时。\n\n## 快速开始\n\n```powershell\nuv run magent doctor\nuv run magent chat\n```\n',
  'reports/INDEX.md': '# reports 索引\n\n- [a.md](a.md) — 示例报告\n- [_loop_demo.md](_loop_demo.md) — 循环演示\n',
  'reports/a.md': '# 示例报告\n\n这是 Mock 文件内容。\n',
  'reports/_loop_demo.md': '# loop demo\n\nMock content.\n',
  'config/default.yaml': 'llm:\n  model: deepseek-flash\nlimits:\n  max_turns_per_task: 40\n',
  'agent/loop.py': '# 运行时主循环（Mock 展示）\nclass AgentLoop:\n    def run(self, text: str) -> str: ...\n',
};

function buildFileTree(): FileNode[] {
  const root: FileNode[] = [];
  const dirs = new Map<string, FileNode>();
  for (const path of Object.keys(FILES).sort()) {
    const parts = path.split('/');
    let cursor = root;
    let prefix = '';
    parts.forEach((part, index) => {
      prefix = prefix ? `${prefix}/${part}` : part;
      const isLeaf = index === parts.length - 1;
      if (isLeaf) {
        cursor.push({ key: prefix, title: part, isLeaf: true });
        return;
      }
      let node = dirs.get(prefix);
      if (!node) {
        node = { key: prefix, title: part, isLeaf: false, children: [] };
        dirs.set(prefix, node);
        cursor.push(node);
      }
      cursor = node.children ?? [];
    });
  }
  return root;
}

export function mockGetFiles(): { nodes: FileNode[] } {
  return { nodes: buildFileTree() };
}

export function mockGetFileContent(path: string): FileContent {
  const content = FILES[path];
  if (content === undefined) {
    throw new ApiError(404, { code: 'E_FILE_NOT_FOUND', message: `文件不存在：${path}` });
  }
  return { path, content, truncated: false, size: content.length };
}

export function mockGetUsage(days = 7): UsageReport {
  const today = dayKey(Date.now());
  const byAgent: UsageReport['by_agent'] = {};
  const byDay: Record<string, number> = {};
  let todayTokens = 0;
  let total = 0;

  for (const event of state.usage) {
    const bucket = byAgent[event.agent] ?? { calls: 0, input_tokens: 0, output_tokens: 0 };
    bucket.calls += 1;
    bucket.input_tokens += event.in;
    bucket.output_tokens += event.out;
    byAgent[event.agent] = bucket;
    byDay[event.day] = (byDay[event.day] ?? 0) + event.in + event.out;
    total += event.in + event.out;
    if (event.day === today) todayTokens += event.in + event.out;
  }

  const daysList = Object.keys(byDay).sort().slice(-days);
  const budget = 200_000_000;
  return {
    today_tokens: todayTokens,
    daily_budget: budget,
    budget_ratio: todayTokens / budget,
    days: daysList,
    by_day: byDay,
    by_agent: byAgent,
    total_tokens: total,
    total_cost_estimate: Number((total / 1_000_000 * 1.8).toFixed(3)),
  };
}

export function mockGetTeamRoster(): { members: RosterMember[] } {
  return {
    members: [
      {
        name: 'alice',
        role: 'researcher',
        tools: ['read_file', 'grep', 'web_fetch'],
        max_turns: 10,
        description: '检索专员',
      },
      {
        name: 'bob',
        role: 'coder',
        tools: ['read_file', 'write_file', 'run_command'],
        max_turns: 14,
        description: '实施工程师',
      },
    ],
  };
}

export function mockListArtifacts(_sessionId: string): { artifacts: Artifact[] } {
  void _sessionId;
  return { artifacts: [] };
}

export function resetMock(): void {
  localStorage.removeItem(STORAGE_KEY);
  state = emptyState();
  runs.clear();
  clientMessageRuns.clear();
  approvals.clear();
  save();
}
