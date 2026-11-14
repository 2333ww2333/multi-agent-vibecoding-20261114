import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { Empty, Input, Modal } from 'antd';
import {
  LayoutOutlined,
  MessageOutlined,
  MoonOutlined,
  PlusOutlined,
  SettingOutlined,
  SunOutlined,
} from '@ant-design/icons';
import { useSessions } from '../../features/sessions/useSessions';
import { useUiStore } from '../../shared/stores/ui';

interface CommandItem {
  key: string;
  label: string;
  hint?: string;
  icon: ReactNode;
  run: () => void;
}

export function CommandPalette() {
  const open = useUiStore((state) => state.paletteOpen);
  const setOpen = useUiStore((state) => state.setPaletteOpen);
  const toggleTheme = useUiStore((state) => state.toggleTheme);
  const theme = useUiStore((state) => state.theme);
  const railOpen = useUiStore((state) => state.railOpen);
  const setRailOpen = useUiStore((state) => state.setRailOpen);
  const { data: sessions = [] } = useSessions();
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);

  useEffect(() => {
    if (open) {
      setQuery('');
      setActive(0);
    }
  }, [open]);

  const items = useMemo<CommandItem[]>(() => {
    const sessionItems = sessions.map((session) => ({
      key: `session:${session.id}`,
      label: session.title,
      hint: `${session.message_count} 条消息`,
      icon: <MessageOutlined />,
      run: () => navigate(`/sessions/${session.id}`),
    }));

    const actions: CommandItem[] = [
      {
        key: 'new',
        label: '新建会话',
        hint: '开一个空白对话',
        icon: <PlusOutlined />,
        run: () => navigate('/'),
      },
      {
        key: 'theme',
        label: theme === 'dark' ? '切换到浅色' : '切换到深色',
        icon: theme === 'dark' ? <SunOutlined /> : <MoonOutlined />,
        run: toggleTheme,
      },
      {
        key: 'rail',
        label: railOpen ? '隐藏右侧面板' : '显示右侧面板',
        icon: <LayoutOutlined />,
        run: () => setRailOpen(!railOpen),
      },
      {
        key: 'settings',
        label: '设置',
        icon: <SettingOutlined />,
        run: () => navigate('/settings'),
      },
    ];

    const all = [...sessionItems, ...actions];
    const keyword = query.trim().toLowerCase();
    if (!keyword) return all;
    return all.filter((item) => item.label.toLowerCase().includes(keyword));
  }, [sessions, query, theme, toggleTheme, railOpen, setRailOpen, navigate]);

  const runAt = (index: number) => {
    const item = items[index];
    if (!item) return;
    item.run();
    setOpen(false);
  };

  return (
    <Modal
      open={open}
      onCancel={() => setOpen(false)}
      footer={null}
      width={560}
      closable={false}
      styles={{ body: { padding: 0 } }}
      className="palette"
    >
      <Input
        autoFocus
        variant="borderless"
        placeholder="搜索会话或命令…"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setActive(0);
        }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown') {
            event.preventDefault();
            setActive((index) => Math.min(index + 1, items.length - 1));
          } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setActive((index) => Math.max(index - 1, 0));
          } else if (event.key === 'Enter') {
            event.preventDefault();
            runAt(active);
          } else if (event.key === 'Escape') {
            setOpen(false);
          }
        }}
        className="palette__input"
      />
      <div className="palette__list">
        {items.length === 0 && (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配项" />
        )}
        {items.map((item, index) => (
          <button
            key={item.key}
            type="button"
            className={`palette__item ${index === active ? 'is-active' : ''}`}
            onMouseEnter={() => setActive(index)}
            onClick={() => runAt(index)}
          >
            <span className="palette__icon">{item.icon}</span>
            <span className="palette__label">{item.label}</span>
            {item.hint && <span className="palette__hint">{item.hint}</span>}
          </button>
        ))}
      </div>
      <div className="palette__foot">
        <span>
          <kbd>↑</kbd>
          <kbd>↓</kbd> 选择
        </span>
        <span>
          <kbd>↵</kbd> 打开
        </span>
        <span>
          <kbd>esc</kbd> 关闭
        </span>
      </div>
    </Modal>
  );
}
