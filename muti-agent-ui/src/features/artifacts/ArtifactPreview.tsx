import { Suspense, lazy } from 'react';
import { Alert, Button, Drawer, Skeleton, Typography } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../shared/api/endpoints';
import { useUiStore } from '../../shared/stores/ui';

const XMarkdown = lazy(() =>
  import('@ant-design/x-markdown').then((module) => ({ default: module.XMarkdown })),
);

const KIND_LABEL = { html: 'HTML 报告', markdown: 'Markdown 文档', text: '文本文件' } as const;
const MIME = {
  html: 'text/html;charset=utf-8',
  markdown: 'text/markdown;charset=utf-8',
  text: 'text/plain;charset=utf-8',
} as const;

export function ArtifactPreview() {
  const preview = useUiStore((state) => state.preview);
  const closePreview = useUiStore((state) => state.closePreview);

  const query = useQuery({
    queryKey: ['artifact', preview?.id],
    queryFn: () => api.getArtifactText(preview?.id as string),
    enabled: Boolean(preview?.id),
  });

  if (!preview) return null;

  const download = () => {
    if (query.data === undefined) return;
    const blob = new Blob([query.data], { type: MIME[preview.kind] });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = preview.filename ?? `${preview.title}.${preview.kind === 'markdown' ? 'md' : 'html'}`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Drawer
      open
      onClose={closePreview}
      placement="right"
      size="min(760px, 58vw)"
      rootClassName="artifact-preview"
      title={
        <div className="artifact-preview__title">
          <span>{preview.title}</span>
          <span className="muted">{KIND_LABEL[preview.kind]}</span>
        </div>
      }
      extra={
        <Button
          size="small"
          type="text"
          icon={<DownloadOutlined />}
          disabled={query.data === undefined}
          onClick={download}
        >
          下载
        </Button>
      }
      styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column' } }}
      destroyOnHidden
    >
      {query.isLoading && (
        <div className="artifact-preview__loading">
          <Skeleton active paragraph={{ rows: 8 }} />
        </div>
      )}
      {query.isError && (
        <div className="artifact-preview__error">
          <Alert
            type="error"
            showIcon
            title="文件加载失败"
            description={query.error instanceof Error ? query.error.message : '未知错误'}
          />
        </div>
      )}
      {query.data !== undefined && preview.kind === 'html' && (
        <iframe
          className="artifact-preview__frame"
          title={preview.title}
          sandbox=""
          srcDoc={query.data}
        />
      )}
      {query.data !== undefined && preview.kind === 'markdown' && (
        <div className="artifact-preview__markdown">
          <Suspense fallback={<pre className="artifact-preview__plain">{query.data}</pre>}>
            <XMarkdown content={query.data} openLinksInNewTab />
          </Suspense>
        </div>
      )}
      {query.data !== undefined && preview.kind === 'text' && (
        <pre className="artifact-preview__plain">{query.data}</pre>
      )}
      {query.data !== undefined && (
        <div className="artifact-preview__meta">
          <Typography.Text className="muted">{preview.filename}</Typography.Text>
        </div>
      )}
    </Drawer>
  );
}
