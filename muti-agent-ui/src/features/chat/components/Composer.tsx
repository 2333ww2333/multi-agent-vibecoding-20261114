import { useEffect, useState } from 'react';
import { App } from 'antd';
import Sender from '@ant-design/x/es/sender';
import { api } from '../../../shared/api/endpoints';
import { useChatStore } from '../../../shared/stores/chat';
import { useSendMessage } from '../hooks/useSendMessage';

const MAX_CHARS = 20_000;

export function Composer({ sessionId }: { sessionId?: string }) {
  const [text, setText] = useState('');
  const { send, sending } = useSendMessage();
  const { message } = App.useApp();
  const run = useChatStore((state) => (sessionId ? state.bySession[sessionId]?.run ?? null : null));
  const running = run?.status === 'running';

  useEffect(() => {
    const onFill = (event: Event) => {
      const detail = (event as CustomEvent<string>).detail;
      if (typeof detail === 'string') setText(detail);
    };
    window.addEventListener('composer:fill', onFill);
    return () => window.removeEventListener('composer:fill', onFill);
  }, []);

  const submit = (value: string) => {
    if (running || sending) return;
    if (value.length > MAX_CHARS) {
      message.warning(`单条消息最多 ${MAX_CHARS} 字符`);
      return;
    }
    void send(sessionId, value).then((target) => {
      if (target) setText('');
    });
  };

  const stop = async () => {
    if (!run) return;
    try {
      await api.cancelRun(run.id);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '停止失败');
    }
  };

  return (
    <div className="composer">
      <div className="composer__box">
        <Sender
          value={text}
          onChange={setText}
          onSubmit={submit}
          onCancel={() => void stop()}
          loading={running || sending}
          disabled={sending}
          placeholder={running ? '任务运行中，可以先写好下一条…' : '输入任务，Enter 发送，Shift+Enter 换行'}
          autoSize={{ minRows: 1, maxRows: 8 }}
          rootClassName="composer__sender"
        />
      </div>
      <div className="composer__meta">
        <span className="muted">
          {text.length > 0 ? `${text.length} / ${MAX_CHARS}` : 'Enter 发送 · Shift+Enter 换行'}
        </span>
        <span className="muted">deepseek-flash</span>
      </div>
    </div>
  );
}
