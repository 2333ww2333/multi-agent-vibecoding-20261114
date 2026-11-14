import { useEffect, useRef } from 'react';
import { useParams } from 'react-router-dom';
import { App } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../shared/api/endpoints';
import { toChatMessage, useChatStore } from '../../shared/stores/chat';
import { useUiStore } from '../../shared/stores/ui';
import type { ChatMessage } from '../../shared/events/view';
import { ChatHeader } from './components/ChatHeader';
import { MessageList } from './components/MessageList';
import { LiveBubble } from './components/LiveBubble';
import { EmptyState } from './components/EmptyState';
import { Composer } from './components/Composer';
import { ApprovalModal } from './components/ApprovalModal';
import { useRunStream } from './hooks/useRunStream';
import { useSessions } from '../sessions/useSessions';
import { useArtifacts } from '../artifacts/queries';
import './chat.css';

const EMPTY_MESSAGES: ChatMessage[] = [];

export function ChatPage() {
  const { sessionId } = useParams();
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { data: sessions = [] } = useSessions();
  const session = sessions.find((item) => item.id === sessionId);

  const ensure = useChatStore((state) => state.ensure);
  const setMessages = useChatStore((state) => state.setMessages);
  const startRun = useChatStore((state) => state.startRun);
  const clearRun = useChatStore((state) => state.clearRun);

  const messages = useChatStore((state) =>
    sessionId ? state.bySession[sessionId]?.messages ?? EMPTY_MESSAGES : EMPTY_MESSAGES,
  );
  const run = useChatStore((state) =>
    sessionId ? state.bySession[sessionId]?.run ?? null : null,
  );
  const paletteOpen = useUiStore((state) => state.paletteOpen);

  const messagesQuery = useQuery({
    queryKey: ['messages', sessionId],
    queryFn: () => api.listMessages(sessionId as string),
    enabled: Boolean(sessionId),
  });
  const artifactsQuery = useArtifacts(sessionId ?? null);

  useRunStream(sessionId ?? null, run?.id ?? null);

  useEffect(() => {
    if (!sessionId) return;
    ensure(sessionId);
  }, [sessionId, ensure]);

  // 服务端消息同步；运行中不覆盖本地乐观消息
  useEffect(() => {
    if (!sessionId || !messagesQuery.data) return;
    const current = useChatStore.getState().bySession[sessionId]?.run;
    if (current && current.status === 'running') return;

    setMessages(sessionId, messagesQuery.data.map(toChatMessage));

    if (
      current &&
      (messagesQuery.data.some((item) => item.id === current.messageId) ||
        messagesQuery.data.some((item) => item.run_id === current.id))
    ) {
      clearRun(sessionId);
    }
  }, [sessionId, messagesQuery.data, setMessages, clearRun]);

  // 刷新后恢复仍在运行的 run
  useEffect(() => {
    if (!sessionId) return;
    const lastRun = session?.last_run;
    if (!lastRun || lastRun.status !== 'running') return;
    const current = useChatStore.getState().bySession[sessionId]?.run;
    if (!current) startRun(sessionId, lastRun.id);
  }, [sessionId, session?.last_run, startRun]);

  // Esc 停止（命令面板打开时不抢）
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || paletteOpen) return;
      if (!run || run.status !== 'running' || !sessionId) return;
      void api.cancelRun(run.id).catch(() => undefined);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [run, sessionId, paletteOpen]);

  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);

  const handleScroll = () => {
    const element = scrollRef.current;
    if (!element) return;
    stickRef.current =
      element.scrollHeight - element.scrollTop - element.clientHeight < 80;
  };

  useEffect(() => {
    if (!stickRef.current) return;
    const element = scrollRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [messages.length, run?.text.length, run?.toolOrder.length, run?.status, sessionId]);

  const retry = async () => {
    if (!sessionId || !run) return;
    try {
      const result = await api.retryRun(run.id);
      clearRun(sessionId);
      startRun(sessionId, result.run_id);
      await queryClient.invalidateQueries({ queryKey: ['messages', sessionId] });
    } catch (error) {
      message.error(error instanceof Error ? error.message : '重试失败');
    }
  };

  const isEmpty = !sessionId || (messages.length === 0 && !run);

  return (
    <div className="chat">
      <ChatHeader
        sessionId={sessionId}
        title={session?.title ?? '新会话'}
        onRetry={() => void retry()}
      />

      <div className="chat__scroll" ref={scrollRef} onScroll={handleScroll}>
        {isEmpty ? (
          <EmptyState />
        ) : (
          <div className="chat__column">
            <MessageList messages={messages} artifacts={artifactsQuery.data ?? []} />
            {run && sessionId && <LiveBubble sessionId={sessionId} onRetry={() => void retry()} />}
          </div>
        )}
      </div>

      <div className="chat__composer">
        <Composer sessionId={sessionId} />
      </div>

      <ApprovalModal sessionId={sessionId} />
    </div>
  );
}
