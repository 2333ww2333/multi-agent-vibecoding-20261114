import { Empty, Progress, Skeleton, Table, type TableProps } from 'antd';
import { useUsage } from './queries';
import { Panel } from './Panel';
import { formatTokens } from '../../shared/lib/format';

interface AgentRow {
  agent: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
}

export function UsagePanel() {
  const { data, isLoading, isError, refetch } = useUsage(7);

  if (isLoading) {
    return (
      <Panel title="账单">
        <Skeleton active paragraph={{ rows: 4 }} title={false} />
      </Panel>
    );
  }

  if (isError || !data) {
    return (
      <Panel title="账单">
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="账单加载失败">
          <button type="button" className="link-button" onClick={() => void refetch()}>
            重试
          </button>
        </Empty>
      </Panel>
    );
  }

  const percent = Math.min(100, Math.round(data.budget_ratio * 100));
  const rows: AgentRow[] = Object.entries(data.by_agent)
    .map(([agent, value]) => ({ agent, ...value }))
    .sort((a, b) => b.input_tokens + b.output_tokens - (a.input_tokens + a.output_tokens));

  const columns: TableProps<AgentRow>['columns'] = [
    { title: 'agent', dataIndex: 'agent', ellipsis: true },
    { title: 'calls', dataIndex: 'calls', width: 44, align: 'right' },
    {
      title: 'in',
      dataIndex: 'input_tokens',
      width: 68,
      align: 'right',
      render: (value: number) => formatTokens(value),
    },
    {
      title: 'out',
      dataIndex: 'output_tokens',
      width: 58,
      align: 'right',
      render: (value: number) => formatTokens(value),
    },
  ];

  const dayEntries = Object.entries(data.by_day).sort(([a], [b]) => a.localeCompare(b));
  const maxDay = Math.max(1, ...dayEntries.map(([, value]) => value));

  return (
    <Panel title="账单" extra={<span className="muted">近 7 天</span>}>
      <div className="usage-head">
        <span className="muted">
          今日 {formatTokens(data.today_tokens)} / {formatTokens(data.daily_budget)}
        </span>
        <span className="muted">{percent}%</span>
      </div>
      <Progress
        percent={percent}
        showInfo={false}
        size="small"
        status={percent > 85 ? 'exception' : 'normal'}
      />

      <div className="sparkline" aria-hidden>
        {dayEntries.map(([day, value]) => (
          <div key={day} className="sparkline__col" title={`${day} · ${formatTokens(value)}`}>
            <span
              className="sparkline__bar"
              style={{ height: `${Math.max(4, (value / maxDay) * 100)}%` }}
            />
            <span className="sparkline__label">{day.slice(5)}</span>
          </div>
        ))}
      </div>

      <Table
        size="small"
        rowKey="agent"
        pagination={false}
        dataSource={rows}
        columns={columns}
        tableLayout="fixed"
        className="usage-table"
      />
    </Panel>
  );
}
