import { useEffect, useMemo, useState } from 'react';
import { Button, Empty, Input, Modal, Select, Skeleton, Tag } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useTeam, useTeamRoster } from './queries';
import { useSpawnTeammate, useShutdownTeammate } from './mutations';
import { Panel } from './Panel';
import { StatusDot, type DotStatus } from '../../shared/ui/StatusDot';

const DOT: Record<string, DotStatus> = {
  working: 'running',
  idle: 'ok',
  offline: 'offline',
  shutdown: 'idle',
};

const FALLBACK_ROSTER = [
  { name: 'alice', role: 'researcher', tools: ['read_file', 'grep'], max_turns: 10, description: '检索专员' },
  { name: 'bob', role: 'coder', tools: ['read_file', 'write_file', 'run_command'], max_turns: 14, description: '实施工程师' },
];

const ROLE_OPTIONS = [
  { value: 'researcher', label: 'researcher · 只读检索/调研' },
  { value: 'coder', label: 'coder · 改代码/跑测试' },
  { value: 'reviewer', label: 'reviewer · 验收/复核' },
  { value: 'writer', label: 'writer · 文档/报告' },
  { value: 'analyst', label: 'analyst · 数据/分析' },
  { value: 'teammate', label: 'teammate · 通用' },
];

export function TeamPanel() {
  const { data: members = [], isLoading, isError, refetch } = useTeam();
  const { data: roster = [] } = useTeamRoster();
  const spawn = useSpawnTeammate();
  const shutdown = useShutdownTeammate();

  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState(false);
  const [name, setName] = useState('alice');
  const [role, setRole] = useState('researcher');

  const rosterOptions = useMemo(
    () => (roster.length > 0 ? roster : FALLBACK_ROSTER),
    [roster],
  );
  const selected = custom ? undefined : rosterOptions.find((item) => item.name === name);

  useEffect(() => {
    if (custom || rosterOptions.length === 0) return;
    if (rosterOptions.some((item) => item.name === name)) return;
    const first = rosterOptions[0];
    setName(first.name);
    setRole(first.role);
  }, [custom, name, rosterOptions]);

  const nameOptions = [
    ...rosterOptions.map((item) => ({
      value: item.name,
      label: `${item.name} · ${item.role}${item.description ? `（${item.description}）` : ''}`,
    })),
    { value: '__custom__', label: '自定义名字…' },
  ];

  const roleOptions = useMemo(() => {
    const options = [...ROLE_OPTIONS];
    for (const item of rosterOptions) {
      if (!options.some((option) => option.value === item.role)) {
        options.push({ value: item.role, label: item.role });
      }
    }
    return options;
  }, [rosterOptions]);

  const visibleMembers = useMemo(
    () => members.filter((member) => member.status !== 'shutdown'),
    [members],
  );
  const hiddenShutdown = members.length - visibleMembers.length;

  return (
    <Panel
      title="队友"
      extra={
        <Button size="small" type="text" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          召入
        </Button>
      }
    >
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 4 }} title={false} />
      ) : isError ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="队友加载失败">
          <button type="button" className="link-button" onClick={() => void refetch()}>
            重试
          </button>
        </Empty>
      ) : visibleMembers.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有队友" />
      ) : (
        <div className="team-list">
          {visibleMembers.map((member) => (
            <div key={member.name} className="team-item">
              <div className="team-item__main">
                <StatusDot status={DOT[member.status] ?? 'idle'} />
                <div className="team-item__text">
                  <div className="team-item__title">
                    {member.name}
                    <Tag variant="filled">{member.role}</Tag>
                  </div>
                  <div className="team-item__desc muted">
                    {member.status} · 已处理 {member.turns_used} 件
                    {member.last_error ? ` · ${member.last_error.slice(0, 36)}` : ''}
                  </div>
                </div>
              </div>
              <Button
                size="small"
                type="text"
                disabled={member.status === 'offline' || member.status === 'shutdown'}
                onClick={() => shutdown.mutate(member.name)}
              >
                关停
              </Button>
            </div>
          ))}
          {hiddenShutdown > 0 && (
            <p className="muted team-list__hint">已隐藏 {hiddenShutdown} 个已关停队友</p>
          )}
        </div>
      )}

      <Modal
        open={open}
        title="召入队友"
        okText="召入"
        cancelText="取消"
        confirmLoading={spawn.isPending}
        onCancel={() => setOpen(false)}
        onOk={() => {
          if (!name.trim() || !role.trim()) return;
          spawn.mutate(
            { name: name.trim(), role: role.trim() },
            { onSuccess: () => setOpen(false) },
          );
        }}
      >
        <div className="form-stack">
          <label className="form-label" htmlFor="member-name">
            选择队友
          </label>
          <Select
            id="member-name"
            value={custom ? '__custom__' : name}
            options={nameOptions}
            showSearch
            optionFilterProp="label"
            onChange={(value) => {
              if (value === '__custom__') {
                setCustom(true);
                setName('');
                return;
              }
              setCustom(false);
              setName(value);
              const item = rosterOptions.find((entry) => entry.name === value);
              if (item) setRole(item.role);
            }}
            placeholder="选择花名册成员，或选择自定义名字"
          />
          {custom && (
            <Input
              id="member-custom-name"
              value={name}
              maxLength={32}
              placeholder="输入名字，例如 小明 / reviewer_2"
              onChange={(event) => setName(event.target.value)}
            />
          )}
          <label className="form-label" htmlFor="member-role">
            职司
          </label>
          <Select
            id="member-role"
            value={role}
            options={roleOptions}
            onChange={setRole}
          />
          <p className="muted team-form__hint">
            {custom
              ? '自定义队友：名字支持中文/字母/数字/下划线/短横线，最多 32 位；请选择一个职司。'
              : selected
                ? `花名册：${selected.description || selected.role} · 工具 ${selected.tools.length} 个 · 最多 ${selected.max_turns} 轮`
                : '从花名册选择，或选择“自定义名字”。'}
          </p>
          {selected && selected.tools.length > 0 && (
            <div className="team-form__tools">
              {selected.tools.slice(0, 6).map((tool) => (
                <Tag key={tool} variant="filled">
                  {tool}
                </Tag>
              ))}
            </div>
          )}
        </div>
      </Modal>
    </Panel>
  );
}
