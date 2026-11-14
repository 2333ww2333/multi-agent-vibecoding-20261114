import { Button } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';

export function ErrorState({
  title = '加载失败',
  message,
  onRetry,
}: {
  title?: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="state-block state-block--error" role="alert">
      <strong>{title}</strong>
      <p className="muted">{message}</p>
      {onRetry && (
        <Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>
          重试
        </Button>
      )}
    </div>
  );
}
