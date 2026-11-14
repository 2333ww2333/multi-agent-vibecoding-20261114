import { useEffect, useState } from 'react';
import { Alert, App, Checkbox, Modal, Typography } from 'antd';
import { ApiError } from '../../../shared/api/client';
import { api } from '../../../shared/api/endpoints';
import { useChatStore } from '../../../shared/stores/chat';

export function ApprovalModal({ sessionId }: { sessionId?: string }) {
  const approval = useChatStore((state) =>
    sessionId ? state.bySession[sessionId]?.run?.approval ?? null : null,
  );
  const { message } = App.useApp();
  const clearApproval = useChatStore((state) => state.clearApproval);
  const [remember, setRemember] = useState(false);
  const [seconds, setSeconds] = useState(180);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!approval) return;
    setRemember(false);
    setBusy(false);
    const deadline = Date.parse(approval.expiresAt);
    const tick = () => {
      const left = Number.isNaN(deadline)
        ? 0
        : Math.max(0, Math.round((deadline - Date.now()) / 1000));
      setSeconds(left);
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [approval]);

  const decide = async (approved: boolean) => {
    if (!approval || busy) return;
    setBusy(true);
    try {
      await api.resolveApproval(approval.id, approved, remember);
      // 不依赖 SSE 的 approval.resolved：请求成功就先关弹窗，避免一直转圈。
      if (sessionId) clearApproval(sessionId);
      setBusy(false);
    } catch (error) {
      if (error instanceof ApiError &&
          (error.code === 'E_APPROVAL_EXPIRED' || error.status === 410 || error.status === 404)) {
        if (sessionId) clearApproval(sessionId);
        message.warning('审批已过期，已按拒绝处理');
      } else {
        message.error(error instanceof Error ? error.message : '审批失败');
      }
      setBusy(false);
    }
  };

  return (
    <Modal
      open={Boolean(approval)}
      title="需要确认"
      okText="允许"
      cancelText="拒绝"
      okButtonProps={{ danger: true, disabled: busy || seconds <= 0, loading: busy }}
      cancelButtonProps={{ disabled: busy }}
      onOk={() => void decide(true)}
      onCancel={() => void decide(false)}
      mask={{ closable: false }}
      keyboard={false}
      closable={false}
    >
      <Alert
        type="warning"
        showIcon
        title="这个操作可能修改文件或影响外部系统"
        description={<Typography.Text code>{approval?.reason}</Typography.Text>}
      />
      <p className="muted approval__countdown">
        剩余 {seconds}s，超时将自动拒绝。
      </p>
      <Checkbox checked={remember} onChange={(event) => setRemember(event.target.checked)}>
        本次运行内不再询问同类操作
      </Checkbox>
    </Modal>
  );
}
