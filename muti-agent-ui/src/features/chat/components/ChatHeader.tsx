import { useState } from 'react';
import { App, Button, Tooltip } from 'antd';
import { LayoutOutlined, MenuOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons';
import { api } from '../../../shared/api/endpoints';
import { useChatStore } from '../../../shared/stores/chat';
import { useUiStore } from '../../../shared/stores/ui';
import { StatusDot } from '../../../shared/ui/StatusDot';
import { hintFor } from '../../../shared/lib/errorHints';

export function ChatHeader({
  sessionId,
  title,
  onRetry,
}: {
  sessionId?: string;
  title: string;
  onRetry?: () => void;
}) {
  const run = useChatStore((state) => (sessionId ? state.bySession[sessionId]?.run ?? null : null));
  const railOpen = useUiStore((state) => state.railOpen);
  const setRailOpen = useUiStore((state) => state.setRailOpen);
  const setPaletteOpen = useUiStore((state) => state.setPaletteOpen);
  const { message } = App.useApp();
  const [stopping, setStopping] = useState(false);

  const running = run?.status === 'running';
  const failed = run?.status === 'failed';

  const stop = async () => {
    if (!run) return;
    setStopping(true);
    try {
      await api.cancelRun(run.id);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '停止失败');
    } finally {
      setStopping(false);
    }
  };

  return (
    <header className="chat-header">
      <div className="chat-header__main">
        <h1 className="chat-header__title" title={title}>
          {title}
        </h1>
        <span className="chat-header__model">deepseek-flash</span>
      </div>

      <div className="chat-header__actions">
        <Button
          className="chat-header__menu"
          type="text"
          size="small"
          aria-label="会话与命令"
          icon={<MenuOutlined />}
          onClick={() => setPaletteOpen(true)}
        />

        {running && (
          <span className="chat-header__status">
            <StatusDot status="running" />
            <span className="muted">
              运行中 · 第 {Math.max(1, run?.turn ?? 1)}/{run?.maxTurns ?? 40} 轮
            </span>
          </span>
        )}
        {run?.status === 'completed' && (
          <span className="chat-header__status">
            <StatusDot status="ok" />
            <span className="muted">已完成</span>
          </span>
        )}
        {failed && (
          <span className="chat-header__status">
            <StatusDot status="error" />
            <span className="muted">{run?.error?.code ?? '失败'}</span>
          </span>
        )}
        {run?.status === 'cancelled' && (
          <span className="chat-header__status">
            <StatusDot status="idle" />
            <span className="muted">已停止</span>
          </span>
        )}

        {running ? (
          <Button
            size="small"
            danger
            type="text"
            icon={<StopOutlined />}
            loading={stopping}
            onClick={() => void stop()}
          >
            停止
          </Button>
        ) : failed && onRetry ? (
          <Tooltip title={hintFor(run?.error?.code)}>
            <Button size="small" type="text" icon={<ReloadOutlined />} onClick={onRetry}>
              重试
            </Button>
          </Tooltip>
        ) : null}

        <Tooltip title={railOpen ? '隐藏右侧面板' : '显示右侧面板'}>
          <Button
            type="text"
            size="small"
            aria-label="切换右侧面板"
            icon={<LayoutOutlined />}
            onClick={() => setRailOpen(!railOpen)}
          />
        </Tooltip>
      </div>
    </header>
  );
}
