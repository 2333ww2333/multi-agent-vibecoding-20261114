import { useEffect, useMemo, useState } from 'react';
import ThoughtChain from '@ant-design/x/es/thought-chain';
import { DownOutlined, RightOutlined } from '@ant-design/icons';
import type { TraceEntry } from '../trace';
import { totalDuration } from '../trace';
import { formatDuration } from '../../../shared/lib/format';
import { hintFor } from '../../../shared/lib/errorHints';

function TraceDetail({ entry }: { entry: TraceEntry }) {
  return (
    <div className="trace-detail">
      <dl className="trace-detail__meta">
        <div>
          <dt>agent</dt>
          <dd>{entry.agent}</dd>
        </div>
        <div>
          <dt>耗时</dt>
          <dd>{formatDuration(entry.durationMs)}</dd>
        </div>
        {entry.subagent && (
          <div>
            <dt>子代理</dt>
            <dd>
              {entry.subagent.key} · {entry.subagent.turns ?? 0} 轮
            </dd>
          </div>
        )}
      </dl>

      <h4 className="trace-detail__label">参数</h4>
      <pre className="code-block trace-detail__pre">{entry.inputPreview || '(空)'}</pre>

      <h4 className="trace-detail__label">结果</h4>
      <pre className="code-block trace-detail__pre">{entry.outputPreview || '(空)'}</pre>

      {entry.truncated && <p className="muted">输出已截断，完整内容见运行时日志。</p>}
      {entry.errorCode && (
        <p className="trace-detail__error">
          <code>{entry.errorCode}</code> {hintFor(entry.errorCode)}
        </p>
      )}
    </div>
  );
}

export function TraceChain({
  entries,
  running,
  className,
}: {
  entries: TraceEntry[];
  running?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(Boolean(running));
  const runningIds = useMemo(
    () => entries.filter((entry) => entry.status === 'running').map((entry) => entry.id),
    [entries],
  );
  const [expanded, setExpanded] = useState<string[]>(runningIds);

  useEffect(() => {
    if (running) {
      setOpen(true);
      setExpanded(runningIds);
    }
  }, [running, runningIds]);

  if (entries.length === 0) return null;

  const duration = totalDuration(entries);
  const failed = entries.filter((entry) => entry.status === 'error').length;

  return (
    <section className={`trace ${className ?? ''}`}>
      <button
        type="button"
        className="trace__head"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        {open ? <DownOutlined /> : <RightOutlined />}
        <span>过程</span>
        <span className="trace__meta">
          {entries.length} 步{duration > 0 ? ` · ${formatDuration(duration)}` : ''}
          {failed > 0 ? ` · ${failed} 失败` : ''}
        </span>
      </button>

      {open && (
        <ThoughtChain
          className="trace__chain"
          line="solid"
          items={entries.map((entry) => ({
            key: entry.id,
            title: (
              <span className="trace__title">
                <code>{entry.name}</code>
                <span className="trace__agent">{entry.agent}</span>
              </span>
            ),
            description: <span className="trace__desc">{entry.inputPreview}</span>,
            status:
              entry.status === 'running'
                ? 'loading'
                : entry.status === 'ok'
                  ? 'success'
                  : 'error',
            blink: entry.status === 'running',
            collapsible: true,
            content: <TraceDetail entry={entry} />,
          }))}
          expandedKeys={expanded}
          onExpand={(keys) => setExpanded(keys)}
        />
      )}
    </section>
  );
}
