import type { TraceItem } from '../../shared/api/types';
import type { RunView, ToolStatus } from '../../shared/events/view';

export interface TraceEntry {
  id: string;
  name: string;
  status: ToolStatus;
  agent: string;
  inputPreview: string;
  outputPreview: string;
  errorCode: string | null;
  durationMs: number | null;
  truncated: boolean;
  subagent?: { key: string; title: string; status: ToolStatus; turns?: number };
}

export function entriesFromRun(run: RunView): TraceEntry[] {
  return run.toolOrder
    .map((id) => run.tools[id])
    .filter((tool): tool is NonNullable<typeof tool> => Boolean(tool))
    .map((tool) => ({
      id: tool.id,
      name: tool.name,
      status: tool.status,
      agent: tool.agent,
      inputPreview: tool.inputPreview,
      outputPreview: tool.outputPreview,
      errorCode: tool.errorCode,
      durationMs: tool.durationMs,
      truncated: tool.truncated,
      subagent: tool.subagent,
    }));
}

export function entriesFromTrace(trace: TraceItem[] | null | undefined): TraceEntry[] {
  if (!trace) return [];
  return trace.map((item, index) => {
    if (item.kind === 'tool') {
      return {
        id: item.tool_call_id || `trace-tool-${index}`,
        name: item.name,
        status: item.ok ? 'ok' : 'error',
        agent: item.agent ?? 'lead',
        inputPreview: item.input_preview,
        outputPreview: item.output_preview,
        errorCode: item.error_code,
        durationMs: item.duration_ms,
        truncated: item.truncated,
      };
    }
    if (item.kind === 'subagent') {
      return {
        id: `subagent:${item.key}-${index}`,
        name: 'dispatch_subagent',
        status: item.ok ? 'ok' : 'error',
        agent: 'lead',
        inputPreview: `子代理 ${item.key}`,
        outputPreview: `${item.turns} 轮 · ${item.summary_chars} 字摘要`,
        errorCode: null,
        durationMs: item.duration_ms,
        truncated: false,
        subagent: { key: item.key, title: item.key, status: item.ok ? 'ok' : 'error', turns: item.turns },
      };
    }
    return {
      id: `plan-${item.revision}-${index}`,
      name: 'update_plan',
      status: 'ok',
      agent: 'lead',
      inputPreview: `${item.items.length} 项`,
      outputPreview: item.items.map((planItem) => `[${planItem.status}] ${planItem.content}`).join('\n'),
      errorCode: null,
      durationMs: null,
      truncated: false,
    };
  });
}

export function totalDuration(entries: TraceEntry[]): number {
  return entries.reduce((sum, entry) => sum + (entry.durationMs ?? 0), 0);
}
