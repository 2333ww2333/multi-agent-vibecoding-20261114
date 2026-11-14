import { useMemo, useState } from 'react';
import { Button, Empty, Select, Skeleton } from 'antd';
import { useInbox, useTeam } from './queries';
import { useDrainInbox } from './mutations';
import { Panel } from './Panel';
import { formatClock } from '../../shared/lib/format';

export function InboxPanel() {
  const { data: members = [] } = useTeam();
  const [member, setMember] = useState('lead');
  const { data: messages = [], isLoading } = useInbox(member);
  const drain = useDrainInbox();

  const options = useMemo(() => {
    const names = new Set<string>(['lead', ...members.map((m) => m.name)]);
    return [...names].map((name) => ({ value: name, label: name }));
  }, [members]);

  return (
    <Panel
      title="收件箱"
      extra={
        <Button
          size="small"
          type="text"
          disabled={messages.length === 0}
          onClick={() => drain.mutate(member)}
        >
          全部已读
        </Button>
      }
    >
      <Select
        size="small"
        value={member}
        style={{ width: '100%', marginBottom: 8 }}
        options={options}
        onChange={setMember}
      />
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 3 }} title={false} />
      ) : messages.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="inbox 为空" />
      ) : (
        <div className="inbox-list">
          {messages.map((message) => (
            <article key={message.id} className="inbox-item">
              <header className="inbox-item__head">
                <span className="inbox-item__type">{message.type}</span>
                <span className="inbox-item__route">
                  {message.sender} → {message.recipient}
                </span>
                <time className="muted">{formatClock(message.ts)}</time>
              </header>
              {message.subject && <p className="inbox-item__subject">{message.subject}</p>}
              <p className="inbox-item__body">{message.body}</p>
            </article>
          ))}
        </div>
      )}
    </Panel>
  );
}
