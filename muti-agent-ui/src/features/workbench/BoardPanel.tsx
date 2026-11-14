import { useState } from 'react';
import { Button, Dropdown, Empty, Input, Modal, Select, Skeleton, Tag } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useBoard } from './queries';
import { useCreateTask, useUpdateTask } from './mutations';
import { ALLOWED, STATUS_META } from './taskStatus';
import { Panel } from './Panel';
import type { Task, TaskStatus } from '../../shared/api/types';

interface PendingAction {
  task: Task;
  status: TaskStatus;
}

export function BoardPanel() {
  const { data, isLoading, isError, refetch } = useBoard(false);
  const createTask = useCreateTask();
  const updateTask = useUpdateTask();

  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [priority, setPriority] = useState(3);

  const [pending, setPending] = useState<PendingAction | null>(null);
  const [note, setNote] = useState('');
  const [owner, setOwner] = useState('alice');

  const openAction = (task: Task, status: TaskStatus) => {
    if (status === 'done' || status === 'blocked' || status === 'claimed') {
      setPending({ task, status });
      setNote('');
      setOwner(task.owner ?? 'alice');
      return;
    }
    updateTask.mutate({ taskId: task.id, status });
  };

  const submitAction = () => {
    if (!pending) return;
    updateTask.mutate({
      taskId: pending.task.id,
      status: pending.status,
      owner: pending.status === 'claimed' ? owner.trim() : undefined,
      result: pending.status === 'done' ? note.trim() : undefined,
      note: pending.status === 'blocked' ? note.trim() : undefined,
    });
    setPending(null);
  };

  const actionTitle =
    pending?.status === 'done'
      ? '完成任务：填写结论摘要'
      : pending?.status === 'blocked'
        ? '标记阻塞：填写原因'
        : '认领任务';

  return (
    <Panel
      title="看板"
      extra={
        <Button size="small" type="text" icon={<PlusOutlined />} onClick={() => setCreating(true)}>
          新建
        </Button>
      }
    >
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 5 }} title={false} />
      ) : isError ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="看板加载失败">
          <button type="button" className="link-button" onClick={() => void refetch()}>
            重试
          </button>
        </Empty>
      ) : (data?.length ?? 0) === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有待处理任务（已完成/已取消已隐藏）" />
      ) : (
        <div className="board-list">
          {data?.map((task) => {
            const options = ALLOWED[task.status].map((status) => ({
              key: status,
              label: STATUS_META[status].label,
            }));
            const meta = STATUS_META[task.status];
            return (
              <article key={task.id} className="board-item">
                <div className="board-item__head">
                  <span className="board-item__title" title={task.title}>
                    {task.title}
                  </span>
                  <Tag variant="filled" color={meta.color}>
                    {meta.label}
                  </Tag>
                </div>
                <div className="board-item__meta">
                  <span className="board-item__id">{task.id}</span>
                  <span>p{task.priority}</span>
                  <span>{task.owner ?? '未分配'}</span>
                  {options.length > 0 && (
                    <Dropdown
                      trigger={['click']}
                      menu={{
                        items: options,
                        onClick: ({ key }) => openAction(task, key as TaskStatus),
                      }}
                    >
                      <Button size="small" type="text" className="board-item__action">
                        推进
                      </Button>
                    </Dropdown>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}

      <Modal
        open={creating}
        title="新建任务"
        okText="创建"
        cancelText="取消"
        confirmLoading={createTask.isPending}
        onCancel={() => setCreating(false)}
        onOk={() => {
          if (!title.trim()) return;
          createTask.mutate(
            { title: title.trim(), description: description.trim(), priority },
            {
              onSuccess: () => {
                setCreating(false);
                setTitle('');
                setDescription('');
                setPriority(3);
              },
            },
          );
        }}
      >
        <div className="form-stack">
          <label className="form-label" htmlFor="task-title">
            标题
          </label>
          <Input
            id="task-title"
            value={title}
            maxLength={120}
            placeholder="一句话任务标题"
            onChange={(event) => setTitle(event.target.value)}
          />
          <label className="form-label" htmlFor="task-desc">
            描述 / 验收标准
          </label>
          <Input.TextArea
            id="task-desc"
            value={description}
            autoSize={{ minRows: 2, maxRows: 5 }}
            onChange={(event) => setDescription(event.target.value)}
          />
          <label className="form-label" htmlFor="task-priority">
            优先级（1 最高）
          </label>
          <Select
            id="task-priority"
            value={priority}
            style={{ width: 120 }}
            onChange={setPriority}
            options={[1, 2, 3, 4, 5].map((value) => ({ value, label: `p${value}` }))}
          />
        </div>
      </Modal>

      <Modal
        open={pending !== null}
        title={actionTitle}
        okText="确认"
        cancelText="取消"
        confirmLoading={updateTask.isPending}
        onCancel={() => setPending(null)}
        onOk={submitAction}
      >
        {pending?.status === 'claimed' ? (
          <Input
            value={owner}
            placeholder="认领人，例如 alice"
            onChange={(event) => setOwner(event.target.value)}
          />
        ) : (
          <Input.TextArea
            value={note}
            autoSize={{ minRows: 3, maxRows: 6 }}
            placeholder={pending?.status === 'done' ? '结论摘要（必填）' : '阻塞原因（必填）'}
            onChange={(event) => setNote(event.target.value)}
          />
        )}
      </Modal>
    </Panel>
  );
}
