/** SSE 事件联合类型：与 03 章事件表一一对应。 */

import type { Channel, PlanItem, Risk } from '../api/types';

export interface EventBase<T extends string, D> {
  run_id: string;
  seq: number;
  ts: string;
  type: T;
  data: D;
}

export type RunEvent =
  | EventBase<'run.started', { message_id: string; turn: number; max_turns: number }>
  | EventBase<'turn.started', { turn: number; agent: string }>
  | EventBase<'message.delta', { message_id: string; channel: Channel; delta: string; agent?: string }>
  | EventBase<'message.snapshot', { message_id: string; channel: Channel; text: string }>
  | EventBase<'message.completed', {
      message_id: string;
      text: string;
      stop_reason: string;
      usage?: { input_tokens: number; output_tokens: number } | null;
    }>
  | EventBase<'tool.started', {
      tool_call_id: string;
      name: string;
      risk: Risk;
      input_preview: string;
      agent?: string;
    }>
  | EventBase<'tool.finished', {
      tool_call_id: string;
      name: string;
      ok: boolean;
      error_code: string | null;
      duration_ms: number | null;
      output_preview: string;
      truncated: boolean;
      agent?: string;
    }>
  | EventBase<'approval.required', {
      approval_id: string;
      tool_call_id: string;
      name: string;
      reason: string;
      expires_at: string;
    }>
  | EventBase<'approval.resolved', {
      approval_id: string;
      approved: boolean;
      reason: 'user' | 'timeout' | 'aborted';
    }>
  | EventBase<'plan.updated', { revision: number; goal: string; items: PlanItem[] }>
  | EventBase<'board.updated', { revision: number; counts: Record<string, number> }>
  | EventBase<'subagent.started', { key: string; title: string; task_preview: string }>
  | EventBase<'subagent.finished', {
      key: string;
      ok: boolean;
      turns: number;
      summary_chars: number;
      duration_ms: number;
    }>
  | EventBase<'context.compacted', {
      dropped: number;
      kept: number;
      tokens_before: number;
      tokens_after: number;
    }>
  | EventBase<'budget.warning', { used: number; budget: number; ratio: number }>
  | EventBase<'artifact.created', {
      artifact_id: string;
      run_id: string;
      session_id: string;
      message_id: string | null;
      kind: 'html' | 'markdown' | 'text';
      title: string;
      filename: string;
      bytes: number;
      created_at: string;
      preview_url: string;
    }>
  | EventBase<'run.completed', {
      reply: string;
      usage_total: { input_tokens: number; output_tokens: number };
      counters: { turns: number; tool_calls: number };
    }>
  | EventBase<'run.failed', { code: string; message: string; retryable: boolean }>
  | EventBase<'run.cancelled', { by?: 'user' | 'system' }>;

export type RunEventType = RunEvent['type'];

export const TERMINAL_EVENTS: ReadonlySet<string> = new Set([
  'run.completed',
  'run.failed',
  'run.cancelled',
]);

export function isTerminalEvent(type: string): boolean {
  return TERMINAL_EVENTS.has(type);
}

export interface RawEvent {
  id: number;
  type: string;
  data: unknown;
}
