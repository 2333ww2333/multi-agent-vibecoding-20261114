import { useMutation, useQueryClient } from '@tanstack/react-query';
import { App } from 'antd';
import { api } from '../../shared/api/endpoints';
import type { TaskStatus } from '../../shared/api/types';
import { hintFor } from '../../shared/lib/errorHints';

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败';
}

export function useCreateTask() {
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  return useMutation({
    mutationFn: (body: { title: string; description?: string; priority?: number }) =>
      api.createTask(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['board'] });
      message.success('任务已创建');
    },
    onError: (error: unknown) => message.error(messageOf(error)),
  });
}

export function useUpdateTask() {
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  return useMutation({
    mutationFn: (input: {
      taskId: string;
      status: TaskStatus;
      owner?: string;
      result?: string;
      note?: string;
      artifacts?: string[];
    }) =>
      api.updateTask(input.taskId, {
        status: input.status,
        owner: input.owner,
        result: input.result,
        note: input.note,
        artifacts: input.artifacts,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['board'] });
    },
    onError: (error: unknown) => {
      const code =
        typeof error === 'object' && error !== null && 'code' in error
          ? String((error as { code: string }).code)
          : null;
      const details =
        typeof error === 'object' && error !== null && 'details' in error
          ? (error as { details?: Record<string, unknown> }).details
          : undefined;
      message.error(`${messageOf(error)} ${hintFor(code)}`);
      const allowed = details?.allowed;
      if (Array.isArray(allowed) && allowed.length > 0) {
        message.info(`允许的目标状态：${allowed.join('、')}`);
      }
    },
  });
}

export function useSpawnTeammate() {
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  return useMutation({
    mutationFn: (input: { name: string; role?: string }) => api.spawnTeammate(input.name, input.role),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['team'] });
      message.success(result.message);
    },
    onError: (error: unknown) => message.error(messageOf(error)),
  });
}

export function useShutdownTeammate() {
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  return useMutation({
    mutationFn: (name: string) => api.shutdownTeammate(name),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['team'] });
      message.success(result.message);
    },
    onError: (error: unknown) => message.error(messageOf(error)),
  });
}

export function useDrainInbox() {
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  return useMutation({
    mutationFn: (name: string) => api.drainInbox(name),
    onSuccess: (result, name) => {
      queryClient.invalidateQueries({ queryKey: ['inbox', name] });
      message.success(`已归档 ${result.count} 条`);
    },
    onError: (error: unknown) => message.error(messageOf(error)),
  });
}
