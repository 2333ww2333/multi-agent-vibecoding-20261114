import { useCallback, useState } from 'react';
import { App } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../../../shared/api/client';
import { api } from '../../../shared/api/endpoints';
import { useChatStore } from '../../../shared/stores/chat';
import { hintFor } from '../../../shared/lib/errorHints';

/**
 * 发送消息。draft 模式（没有 sessionId）会先建会话再发。
 * 返回最终使用的 sessionId；失败返回 null。
 */
export function useSendMessage() {
  const [sending, setSending] = useState(false);
  const addMessage = useChatStore((state) => state.addMessage);
  const removeMessage = useChatStore((state) => state.removeMessage);
  const startRun = useChatStore((state) => state.startRun);
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  const navigate = useNavigate();

  const send = useCallback(
    async (sessionId: string | undefined, text: string): Promise<string | null> => {
      const trimmed = text.trim();
      if (!trimmed || sending) return null;
      setSending(true);

      let target = sessionId;
      const tempId = `tmp-${crypto.randomUUID()}`;

      try {
        if (!target) {
          const session = await api.createSession(trimmed.slice(0, 24));
          target = session.id;
          queryClient.invalidateQueries({ queryKey: ['sessions'] });
        }

        addMessage(target, {
          id: tempId,
          role: 'user',
          content: trimmed,
          status: 'done',
          createdAt: new Date().toISOString(),
        });

        const result = await api.sendMessage(target, trimmed, crypto.randomUUID());
        startRun(target, result.run_id);
        queryClient.invalidateQueries({ queryKey: ['sessions'] });

        if (!sessionId) navigate(`/sessions/${target}`);
        return target;
      } catch (error) {
        if (target) removeMessage(target, tempId);

        if (error instanceof ApiError) {
          if (error.code === 'E_WORKSPACE_BUSY') {
            const busyRun = error.details?.run_id;
            message.warning(
              `当前工作区有任务在运行${busyRun ? `（${String(busyRun)}）` : ''}，请先停止它。`,
            );
          } else {
            message.error(`${error.message} ${hintFor(error.code)}`);
          }
        } else {
          message.error('发送失败，请重试');
        }
        return null;
      } finally {
        setSending(false);
      }
    },
    [sending, addMessage, removeMessage, startRun, queryClient, message, navigate],
  );

  return { send, sending };
}
