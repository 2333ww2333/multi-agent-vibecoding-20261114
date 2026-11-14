import type { RunEvent } from './types';
import type { RunView, ToolView } from './view';

function settleRunning(tools: RunView['tools']): RunView['tools'] {
  const next: RunView['tools'] = {};
  for (const [id, tool] of Object.entries(tools)) {
    next[id] =
      tool.status === 'running'
        ? {
            ...tool,
            status: 'error',
            errorCode: tool.errorCode ?? 'E_RUN_ENDED',
            subagent: tool.subagent ? { ...tool.subagent, status: 'error' } : undefined,
          }
        : tool;
  }
  return next;
}

function mergeTool(tools: Record<string, ToolView>, tool: ToolView): Record<string, ToolView> {
  return { ...tools, [tool.id]: tool };
}

/**
 * 纯函数：把一条事件归约进 RunView。
 * 重复 seq 直接返回原对象（引用不变 → zustand/React 不重渲染）。
 */
export function reduceRun(run: RunView, event: RunEvent): RunView {
  if (event.seq <= run.lastSeq) return run;
  const base: RunView = { ...run, lastSeq: event.seq };

  switch (event.type) {
    case 'run.started':
      return {
        ...base,
        messageId: event.data.message_id,
        turn: event.data.turn,
        maxTurns: event.data.max_turns || base.maxTurns,
        status: 'running',
      };

    case 'turn.started':
      return { ...base, turn: event.data.turn, status: 'running' };

    case 'message.delta': {
      if (event.data.agent && event.data.agent !== 'lead') return base;
      const key = event.data.channel === 'reasoning' ? 'reasoning' : 'text';
      return { ...base, streamed: true, [key]: base[key] + event.data.delta };
    }

    case 'message.snapshot': {
      const key = event.data.channel === 'reasoning' ? 'reasoning' : 'text';
      return { ...base, streamed: true, [key]: event.data.text };
    }

    case 'message.completed': {
      if (base.streamed) {
        return base.text ? base : { ...base, text: event.data.text };
      }
      const separator = base.text ? '\n\n' : '';
      return { ...base, text: base.text + separator + event.data.text };
    }

    case 'tool.started': {
      const id = event.data.tool_call_id;
      const tool: ToolView = {
        id,
        name: event.data.name,
        risk: event.data.risk,
        agent: event.data.agent ?? 'lead',
        status: 'running',
        inputPreview: event.data.input_preview,
        outputPreview: '',
        errorCode: null,
        durationMs: null,
        truncated: false,
      };
      return {
        ...base,
        tools: mergeTool(base.tools, tool),
        toolOrder: base.toolOrder.includes(id) ? base.toolOrder : [...base.toolOrder, id],
      };
    }

    case 'tool.finished': {
      const id = event.data.tool_call_id;
      const previous =
        base.tools[id] ??
        ({
          id,
          name: event.data.name,
          risk: 'read',
          agent: 'lead',
          status: 'ok',
          inputPreview: '',
          outputPreview: '',
          errorCode: null,
          durationMs: null,
          truncated: false,
        } satisfies ToolView);

      return {
        ...base,
        tools: mergeTool(base.tools, {
          ...previous,
          status: event.data.ok ? 'ok' : 'error',
          outputPreview: event.data.output_preview,
          errorCode: event.data.error_code,
          durationMs: event.data.duration_ms,
          truncated: event.data.truncated,
        }),
      };
    }

    case 'subagent.started': {
      const id = `subagent:${event.data.key}`;
      const tool: ToolView = {
        id,
        name: 'dispatch_subagent',
        risk: 'control',
        agent: 'lead',
        status: 'running',
        inputPreview: event.data.task_preview,
        outputPreview: '',
        errorCode: null,
        durationMs: null,
        truncated: false,
        subagent: { key: event.data.key, title: event.data.title, status: 'running' },
      };
      return {
        ...base,
        tools: mergeTool(base.tools, tool),
        toolOrder: base.toolOrder.includes(id) ? base.toolOrder : [...base.toolOrder, id],
      };
    }

    case 'subagent.finished': {
      const id = `subagent:${event.data.key}`;
      const previous = base.tools[id];
      if (!previous) return base;
      return {
        ...base,
        tools: mergeTool(base.tools, {
          ...previous,
          status: event.data.ok ? 'ok' : 'error',
          durationMs: event.data.duration_ms,
          subagent: {
            key: event.data.key,
            title: previous.subagent?.title ?? event.data.key,
            status: event.data.ok ? 'ok' : 'error',
            turns: event.data.turns,
          },
        }),
      };
    }

    case 'approval.required':
      return {
        ...base,
        approval: {
          id: event.data.approval_id,
          name: event.data.name,
          reason: event.data.reason,
          expiresAt: event.data.expires_at,
        },
      };

    case 'approval.resolved':
      return { ...base, approval: null };

    case 'plan.updated':
      return {
        ...base,
        plan: {
          revision: event.data.revision,
          goal: event.data.goal,
          items: event.data.items,
        },
      };

    case 'context.compacted':
      return {
        ...base,
        compacted: { dropped: event.data.dropped, kept: event.data.kept },
      };

    case 'budget.warning':
      return {
        ...base,
        budgetWarning: {
          used: event.data.used,
          budget: event.data.budget,
          ratio: event.data.ratio,
        },
      };

    case 'artifact.created': {
      const artifact = {
        id: event.data.artifact_id,
        artifact_id: event.data.artifact_id,
        run_id: event.data.run_id,
        session_id: event.data.session_id,
        message_id: event.data.message_id,
        kind: event.data.kind,
        title: event.data.title,
        filename: event.data.filename,
        bytes: event.data.bytes,
        created_at: event.data.created_at,
        preview_url: event.data.preview_url,
      };
      return {
        ...base,
        artifacts: [
          ...base.artifacts.filter((item) => item.id !== artifact.id),
          artifact,
        ],
      };
    }

    case 'run.completed':
      return {
        ...base,
        status: 'completed',
        text: base.streamed ? base.text || event.data.reply : event.data.reply,
        usage: event.data.usage_total,
        tools: settleRunning(base.tools),
      };

    case 'run.failed':
      return {
        ...base,
        status: 'failed',
        error: {
          code: event.data.code,
          message: event.data.message,
          retryable: event.data.retryable,
        },
        tools: settleRunning(base.tools),
      };

    case 'run.cancelled':
      return {
        ...base,
        status: 'cancelled',
        approval: null,
        tools: settleRunning(base.tools),
      };

    case 'board.updated':
      // 看板由 React Query 失效重取，不进对话状态
      return base;

    default:
      return base;
  }
}
