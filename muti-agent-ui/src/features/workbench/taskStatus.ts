import type { TaskStatus } from '../../shared/api/types';

export const STATUS_META: Record<TaskStatus, { label: string; color: string }> = {
  pending: { label: '待办', color: 'default' },
  claimed: { label: '已认领', color: 'blue' },
  in_progress: { label: '进行中', color: 'processing' },
  review: { label: '待验收', color: 'gold' },
  done: { label: '已完成', color: 'green' },
  blocked: { label: '阻塞', color: 'orange' },
  failed: { label: '失败', color: 'red' },
  cancelled: { label: '已取消', color: 'default' },
};

/** 与运行时 TRANSITIONS 保持一致；前端只决定按钮显示，权威仍在后端。 */
export const ALLOWED: Record<TaskStatus, TaskStatus[]> = {
  pending: ['claimed', 'cancelled'],
  claimed: ['in_progress', 'pending', 'cancelled'],
  in_progress: ['review', 'blocked', 'failed', 'cancelled'],
  review: ['done', 'in_progress', 'failed', 'cancelled'],
  blocked: ['in_progress', 'failed', 'cancelled'],
  done: [],
  failed: ['pending'],
  cancelled: ['pending'],
};
