import { Empty, Progress, Skeleton } from 'antd';
import { useParams } from 'react-router-dom';
import { usePlan } from './queries';
import { Panel } from './Panel';

const ICON: Record<string, string> = {
  pending: '☐',
  in_progress: '◐',
  completed: '☑',
  blocked: '☒',
};

export function PlanPanel() {
  const { sessionId } = useParams();
  const { data, isLoading, isError, refetch } = usePlan(sessionId);

  if (isLoading) {
    return (
      <Panel title="计划">
        <Skeleton active paragraph={{ rows: 4 }} title={false} />
      </Panel>
    );
  }

  if (isError) {
    return (
      <Panel title="计划">
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="计划加载失败">
          <button type="button" className="link-button" onClick={() => void refetch()}>
            重试
          </button>
        </Empty>
      </Panel>
    );
  }

  if (!data || data.items.length === 0) {
    return (
      <Panel title="计划">
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="还没有计划。多步骤任务会自动生成。"
        />
      </Panel>
    );
  }

  const done = data.items.filter((item) => item.status === 'completed').length;
  const current = data.items.find((item) => item.status === 'in_progress');
  const percent = Math.round((done / data.items.length) * 100);

  return (
    <Panel title="计划" extra={<span className="muted">{done}/{data.items.length}</span>}>
      {data.goal && <p className="panel__goal">{data.goal}</p>}
      <Progress percent={percent} showInfo={false} size="small" />
      {current && <p className="muted panel__current">正在做：{current.content}</p>}

      <ol className="plan-list">
        {data.items.map((item) => (
          <li key={item.id} className={`plan-item plan-item--${item.status}`}>
            <span className="plan-item__icon" aria-hidden>
              {ICON[item.status] ?? '☐'}
            </span>
            <span className="plan-item__text">{item.content}</span>
            {item.note && <span className="plan-item__note">{item.note}</span>}
          </li>
        ))}
      </ol>
    </Panel>
  );
}
