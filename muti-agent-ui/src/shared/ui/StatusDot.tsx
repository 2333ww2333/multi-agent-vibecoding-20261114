import clsx from 'clsx';

export type DotStatus = 'running' | 'ok' | 'error' | 'idle' | 'offline' | 'warning';

const CLASS: Record<DotStatus, string> = {
  running: 'dot--busy',
  ok: 'dot--ok',
  error: 'dot--error',
  idle: 'dot--idle',
  offline: 'dot--offline',
  warning: 'dot--warning',
};

export function StatusDot({ status, label }: { status: DotStatus; label?: string }) {
  return (
    <span className="status-dot">
      <span className={clsx('dot', CLASS[status])} aria-hidden />
      {label && <span className="status-dot__label">{label}</span>}
    </span>
  );
}
