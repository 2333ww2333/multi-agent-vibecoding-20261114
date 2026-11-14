import { describe, expect, it } from 'vitest';
import { reduceRun } from './reducer';
import type { RunEvent } from './types';
import { emptyRun, type RunView } from './view';

function ev<T extends RunEvent['type']>(
  seq: number,
  type: T,
  data: Record<string, unknown>,
): RunEvent {
  return { run_id: 'r_1', seq, ts: '', type, data } as unknown as RunEvent;
}

const base = (): RunView => emptyRun('r_1');

describe('reduceRun', () => {
  it('accumulates deltas and ignores duplicate seq', () => {
    let run = base();
    run = reduceRun(run, ev(1, 'message.delta', { message_id: 'm', channel: 'text', delta: '你' }));
    run = reduceRun(run, ev(2, 'message.delta', { message_id: 'm', channel: 'text', delta: '好' }));
    run = reduceRun(run, ev(2, 'message.delta', { message_id: 'm', channel: 'text', delta: '好' }));
    expect(run.text).toBe('你好');
    expect(run.streamed).toBe(true);
    expect(run.lastSeq).toBe(2);
  });

  it('keeps reasoning separate from text', () => {
    let run = base();
    run = reduceRun(run, ev(1, 'message.delta', { message_id: 'm', channel: 'reasoning', delta: '想' }));
    run = reduceRun(run, ev(2, 'message.delta', { message_id: 'm', channel: 'text', delta: '答' }));
    expect(run.reasoning).toBe('想');
    expect(run.text).toBe('答');
  });

  it('ignores deltas from non-lead agents', () => {
    let run = base();
    run = reduceRun(
      run,
      ev(1, 'message.delta', { message_id: 'm', channel: 'text', delta: '子代理', agent: 'searcher#1' }),
    );
    expect(run.text).toBe('');
  });

  it('tracks tool lifecycle', () => {
    let run = base();
    run = reduceRun(
      run,
      ev(1, 'tool.started', {
        tool_call_id: 'c1',
        name: 'read_file',
        risk: 'read',
        input_preview: '{}',
        agent: 'lead',
      }),
    );
    expect(run.tools.c1.status).toBe('running');
    expect(run.toolOrder).toEqual(['c1']);

    run = reduceRun(
      run,
      ev(2, 'tool.finished', {
        tool_call_id: 'c1',
        name: 'read_file',
        ok: true,
        error_code: null,
        duration_ms: 12,
        output_preview: 'ok',
        truncated: false,
        agent: 'lead',
      }),
    );
    expect(run.tools.c1.status).toBe('ok');
    expect(run.tools.c1.durationMs).toBe(12);
  });

  it('settles running tools when the run ends', () => {
    let run = base();
    run = reduceRun(
      run,
      ev(1, 'tool.started', {
        tool_call_id: 'c1',
        name: 'run_command',
        risk: 'exec',
        input_preview: 'pytest',
      }),
    );
    run = reduceRun(run, ev(2, 'run.cancelled', { by: 'user' }));
    expect(run.status).toBe('cancelled');
    expect(run.tools.c1.status).toBe('error');
    expect(run.tools.c1.errorCode).toBe('E_RUN_ENDED');
  });

  it('appends message.completed text when streaming is unavailable', () => {
    let run = base();
    run = reduceRun(
      run,
      ev(1, 'message.completed', {
        message_id: 'm',
        text: '第一轮',
        stop_reason: 'tool_use',
        usage: null,
      }),
    );
    run = reduceRun(
      run,
      ev(2, 'message.completed', {
        message_id: 'm',
        text: '第二轮',
        stop_reason: 'end_turn',
        usage: null,
      }),
    );
    expect(run.text).toBe('第一轮\n\n第二轮');
    expect(run.streamed).toBe(false);
  });

  it('does not overwrite streamed narration on completion', () => {
    let run = base();
    run = reduceRun(
      run,
      ev(1, 'message.delta', { message_id: 'm', channel: 'text', delta: '我先看一下。' }),
    );
    run = reduceRun(
      run,
      ev(2, 'message.completed', {
        message_id: 'm',
        text: '我先看一下。',
        stop_reason: 'tool_use',
        usage: null,
      }),
    );
    run = reduceRun(
      run,
      ev(3, 'message.delta', { message_id: 'm', channel: 'text', delta: '结论是 A。' }),
    );
    run = reduceRun(
      run,
      ev(4, 'run.completed', {
        reply: '结论是 A。',
        usage_total: { input_tokens: 10, output_tokens: 5 },
        counters: { turns: 2, tool_calls: 1 },
      }),
    );
    expect(run.text).toBe('我先看一下。结论是 A。');
    expect(run.status).toBe('completed');
    expect(run.usage).toEqual({ input_tokens: 10, output_tokens: 5 });
  });

  it('handles approval and plan events', () => {
    let run = base();
    run = reduceRun(
      run,
      ev(1, 'approval.required', {
        approval_id: 'a1',
        tool_call_id: 'c1',
        name: 'run_command',
        reason: 'git push',
        expires_at: new Date(Date.now() + 60_000).toISOString(),
      }),
    );
    expect(run.approval?.id).toBe('a1');

    run = reduceRun(run, ev(2, 'approval.resolved', { approval_id: 'a1', approved: false, reason: 'timeout' }));
    expect(run.approval).toBeNull();

    run = reduceRun(
      run,
      ev(3, 'plan.updated', {
        revision: 2,
        goal: '做点事',
        items: [{ id: 1, content: '第一步', status: 'in_progress', owner: null, note: '' }],
      }),
    );
    expect(run.plan?.revision).toBe(2);
    expect(run.plan?.items).toHaveLength(1);
  });

  it('maps subagent events to a tool card', () => {
    let run = base();
    run = reduceRun(
      run,
      ev(1, 'subagent.started', { key: 'searcher', title: '检索专员', task_preview: '查 X' }),
    );
    expect(run.tools['subagent:searcher'].subagent?.status).toBe('running');

    run = reduceRun(
      run,
      ev(2, 'subagent.finished', {
        key: 'searcher',
        ok: true,
        turns: 4,
        summary_chars: 300,
        duration_ms: 1200,
      }),
    );
    expect(run.tools['subagent:searcher'].status).toBe('ok');
    expect(run.tools['subagent:searcher'].subagent?.turns).toBe(4);
  });

  it('keeps generated artifacts on the run', () => {
    let run = base();
    run = reduceRun(
      run,
      ev(1, 'artifact.created', {
        artifact_id: 'art_1',
        run_id: 'r_1',
        session_id: 's_1',
        message_id: 'm_1',
        kind: 'html',
        title: '报告',
        filename: 'report.html',
        bytes: 100,
        created_at: '',
        preview_url: '/api/artifacts/art_1',
      }),
    );
    expect(run.artifacts).toHaveLength(1);
    expect(run.artifacts[0].preview_url).toBe('/api/artifacts/art_1');
  });
});
