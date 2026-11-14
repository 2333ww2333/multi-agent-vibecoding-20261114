import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { App, Button, Input, Modal, Tooltip } from 'antd';
import Conversations from '@ant-design/x/es/conversations';
import {
  DeleteOutlined,
  EditOutlined,
  MoonOutlined,
  PlusOutlined,
  SettingOutlined,
  SunOutlined,
} from '@ant-design/icons';
import {
  useDeleteSession,
  useRenameSession,
  useSessions,
} from '../../features/sessions/useSessions';
import { USE_MOCK } from '../../shared/api/client';
import { useUiStore } from '../../shared/stores/ui';
import { relativeDay } from '../../shared/lib/format';

export function Sidebar() {
  const { data: sessions = [], isLoading } = useSessions();
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const { modal, message } = App.useApp();
  const rename = useRenameSession();
  const remove = useDeleteSession();
  const theme = useUiStore((state) => state.theme);
  const toggleTheme = useUiStore((state) => state.toggleTheme);
  const setPaletteOpen = useUiStore((state) => state.setPaletteOpen);

  const [renaming, setRenaming] = useState<{ id: string; title: string } | null>(null);

  const items = sessions.map((session) => ({
    key: session.id,
    label: session.title,
    group: relativeDay(session.updated_at),
  }));

  const confirmDelete = (id: string, title: string) => {
    modal.confirm({
      title: '删除会话',
      content: `「${title}」的消息与轨迹会被删除，不可恢复。`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        await remove.mutateAsync(id);
        if (sessionId === id) navigate('/');
        message.success('已删除');
      },
    });
  };

  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <span className="sidebar__mark" aria-hidden />
        <span className="sidebar__name">multi-agent</span>
        {USE_MOCK && <span className="badge badge--muted">MOCK</span>}
      </div>

      <div className="sidebar__list">
        <Conversations
          items={items}
          activeKey={sessionId ?? ''}
          onActiveChange={(key) => navigate(`/sessions/${key}`)}
          groupable
          creation={{
            label: '新建会话',
            icon: <PlusOutlined />,
            onClick: () => navigate('/'),
          }}
          menu={(conversation) => ({
            items: [
              { key: 'rename', label: '重命名', icon: <EditOutlined /> },
              { key: 'delete', label: '删除', icon: <DeleteOutlined />, danger: true },
            ],
            onClick: ({ key, domEvent }) => {
              domEvent.stopPropagation();
              if (key === 'rename') {
                setRenaming({ id: String(conversation.key), title: String(conversation.label ?? '') });
              } else if (key === 'delete') {
                confirmDelete(String(conversation.key), String(conversation.label ?? '会话'));
              }
            },
          })}
          className="sidebar__conversations"
        />
        {!isLoading && sessions.length === 0 && (
          <p className="sidebar__empty">还没有会话。说清目标，我会先出计划。</p>
        )}
      </div>

      <footer className="sidebar__foot">
        <div className="sidebar__status">
          <span className="dot dot--ok" aria-hidden />
          <span className="muted">deepseek-flash</span>
        </div>
        <div className="sidebar__actions">
          <Tooltip title={theme === 'dark' ? '浅色主题' : '深色主题'}>
            <Button
              type="text"
              size="small"
              aria-label="切换主题"
              icon={theme === 'dark' ? <SunOutlined /> : <MoonOutlined />}
              onClick={toggleTheme}
            />
          </Tooltip>
          <Tooltip title="设置">
            <Button
              type="text"
              size="small"
              aria-label="设置"
              icon={<SettingOutlined />}
              onClick={() => navigate('/settings')}
            />
          </Tooltip>
          <Tooltip title="命令面板 ⌘K">
            <Button
              type="text"
              size="small"
              aria-label="命令面板"
              onClick={() => setPaletteOpen(true)}
            >
              <kbd>⌘K</kbd>
            </Button>
          </Tooltip>
        </div>
      </footer>

      <Modal
        open={renaming !== null}
        title="重命名会话"
        okText="保存"
        cancelText="取消"
        onCancel={() => setRenaming(null)}
        onOk={async () => {
          if (!renaming) return;
          const title = renaming.title.trim();
          if (!title) {
            message.warning('标题不能为空');
            return;
          }
          await rename.mutateAsync({ sessionId: renaming.id, title });
          setRenaming(null);
        }}
      >
        <Input
          value={renaming?.title ?? ''}
          maxLength={80}
          autoFocus
          onChange={(event) =>
            setRenaming((current) => (current ? { ...current, title: event.target.value } : current))
          }
          onPressEnter={async () => {
            if (!renaming) return;
            const title = renaming.title.trim();
            if (!title) return;
            await rename.mutateAsync({ sessionId: renaming.id, title });
            setRenaming(null);
          }}
        />
      </Modal>
    </aside>
  );
}
