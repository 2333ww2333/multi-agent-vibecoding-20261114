import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ApiError, USE_MOCK } from '../../../shared/api/client';
import { streamRun } from '../../../shared/api/endpoints';
import { isTerminalEvent, type RunEvent } from '../../../shared/events/types';
import { useChatStore } from '../../../shared/stores/chat';

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * 订阅一个 run 的 SSE。要点：
 * - 按 seq 去重；断线后从 lastSeq 续传；
 * - 事件用 rAF 批量写入 store，避免每 token 一次渲染；
 * - 终态事件后主动 abort，并让消息/账单等 query 失效。
 */
export function useRunStream(sessionId: string | null, runId: string | null) {
  const applyMany = useChatStore((state) => state.applyMany);
  const clearRun = useChatStore((state) => state.clearRun);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!sessionId || !runId) return;

    const abort = new AbortController();
    let disposed = false;
    let seq = useChatStore.getState().bySession[sessionId]?.run?.lastSeq ?? 0;

    const pending: RunEvent[] = [];
    let frame: number | null = null;

    const flush = () => {
      frame = null;
      if (pending.length === 0) return;
      const events = pending.splice(0, pending.length);
      applyMany(sessionId, events);
    };

    const schedule = (event: RunEvent) => {
      pending.push(event);
      if (frame === null) frame = requestAnimationFrame(flush);
    };

    const resync = () => {
      queryClient.invalidateQueries({ queryKey: ['messages', sessionId] });
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
      clearRun(sessionId);
    };

    const handleEvent = (event: RunEvent) => {
      if (event.seq <= seq) return;
      if (seq !== 0 && event.seq > seq + 1) {
        // gap：事件被丢弃或缓冲过期，走全量重取
        disposed = true;
        abort.abort();
        resync();
        return;
      }
      seq = event.seq;
      schedule(event);

      if (event.type === 'plan.updated') {
        queryClient.invalidateQueries({ queryKey: ['plan'] });
      }
      if (event.type === 'board.updated') {
        queryClient.invalidateQueries({ queryKey: ['board'] });
      }
      if (event.type === 'artifact.created') {
        queryClient.invalidateQueries({ queryKey: ['artifacts', sessionId] });
      }

      if (isTerminalEvent(event.type)) {
        disposed = true;
        flush();
        abort.abort();
        queryClient.invalidateQueries({ queryKey: ['messages', sessionId] });
        queryClient.invalidateQueries({ queryKey: ['sessions'] });
        queryClient.invalidateQueries({ queryKey: ['usage'] });
        queryClient.invalidateQueries({ queryKey: ['board'] });
        queryClient.invalidateQueries({ queryKey: ['plan'] });
        queryClient.invalidateQueries({ queryKey: ['artifacts', sessionId] });
      }
    };

    const run = async () => {
      let attempt = 0;
      while (!disposed && !abort.signal.aborted) {
        try {
          await streamRun(runId, seq, handleEvent, abort.signal);
          if (disposed) return;
          if (USE_MOCK) {
            // mock 的流正常结束时一定已有终态；没有就说明 run 丢了
            resync();
            return;
          }
        } catch (error) {
          if (disposed || abort.signal.aborted) return;
          if (error instanceof ApiError && error.status === 401) {
            resync();
            return;
          }
          if (error instanceof ApiError && (error.code === 'E_STREAM_EXPIRED' || error.status === 404)) {
            resync();
            return;
          }
          if (USE_MOCK) {
            resync();
            return;
          }
        }

        const delay = Math.min(8000, 500 * 2 ** attempt) + Math.random() * 250;
        attempt += 1;
        await sleep(delay);
      }
    };

    void run();

    return () => {
      disposed = true;
      abort.abort();
      if (frame !== null) cancelAnimationFrame(frame);
      pending.length = 0;
    };
  }, [sessionId, runId, applyMany, clearRun, queryClient]);
}
