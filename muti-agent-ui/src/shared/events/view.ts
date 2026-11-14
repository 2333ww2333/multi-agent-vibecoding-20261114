/** 视图模型：组件只读这些，不读原始事件。 */

import type { Artifact, PlanItem, Risk, RunStatus, TraceItem } from '../api/types';

export type ToolStatus = 'running' | 'ok' | 'error';

export interface SubagentView {
  key: string;
  title: string;
  status: ToolStatus;
  turns?: number;
}

export interface ToolView {
  id: string;
  name: string;
  risk: Risk;
  agent: string;
  status: ToolStatus;
  inputPreview: string;
  outputPreview: string;
  errorCode: string | null;
  durationMs: number | null;
  truncated: boolean;
  subagent?: SubagentView;
}

export interface ApprovalView {
  id: string;
  name: string;
  reason: string;
  expiresAt: string;
}

export interface RunView {
  id: string;
  status: RunStatus;
  messageId: string | null;
  text: string;
  reasoning: string;
  streamed: boolean;
  turn: number;
  maxTurns: number;
  tools: Record<string, ToolView>;
  toolOrder: string[];
  artifacts: Artifact[];
  plan: { revision: number; goal: string; items: PlanItem[] } | null;
  approval: ApprovalView | null;
  error: { code: string; message: string; retryable: boolean } | null;
  budgetWarning: { used: number; budget: number; ratio: number } | null;
  compacted: { dropped: number; kept: number } | null;
  usage: { input_tokens: number; output_tokens: number } | null;
  lastSeq: number;
  startedAt: number;
}

export function emptyRun(id: string, maxTurns = 40): RunView {
  return {
    id,
    status: 'running',
    messageId: null,
    text: '',
    reasoning: '',
    streamed: false,
    turn: 0,
    maxTurns,
    tools: {},
    toolOrder: [],
    artifacts: [],
    plan: null,
    approval: null,
    error: null,
    budgetWarning: null,
    compacted: null,
    usage: null,
    lastSeq: 0,
    startedAt: Date.now(),
  };
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  status: 'done' | 'streaming' | 'error' | 'cancelled';
  createdAt: string;
  runId?: string | null;
  usage?: { input_tokens: number; output_tokens: number } | null;
  trace?: TraceItem[] | null;
}

export interface SessionView {
  messages: ChatMessage[];
  run: RunView | null;
  loaded: boolean;
}
