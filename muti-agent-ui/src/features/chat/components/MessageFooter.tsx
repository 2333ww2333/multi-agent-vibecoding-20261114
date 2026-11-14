import { useState } from 'react';
import { Button, Tooltip } from 'antd';
import { CheckOutlined, CopyOutlined, ReloadOutlined } from '@ant-design/icons';
import { copyText } from '../../../shared/lib/clipboard';
import { formatTokens } from '../../../shared/lib/format';

export function MessageFooter({
  text,
  usage,
  onRetry,
  note,
}: {
  text: string;
  usage?: { input_tokens: number; output_tokens: number } | null;
  onRetry?: () => void;
  note?: string;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    const ok = await copyText(text);
    if (!ok) return;
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="msg-footer">
      {note && <span className="msg-footer__note">{note}</span>}
      {usage && (
        <span className="msg-footer__usage mono">
          {formatTokens(usage.input_tokens)} in / {formatTokens(usage.output_tokens)} out
        </span>
      )}
      <Tooltip title={copied ? '已复制' : '复制'}>
        <Button
          type="text"
          size="small"
          aria-label="复制回答"
          icon={copied ? <CheckOutlined /> : <CopyOutlined />}
          onClick={() => void handleCopy()}
        />
      </Tooltip>
      {onRetry && (
        <Tooltip title="重试">
          <Button
            type="text"
            size="small"
            aria-label="重试"
            icon={<ReloadOutlined />}
            onClick={onRetry}
          />
        </Tooltip>
      )}
    </div>
  );
}
