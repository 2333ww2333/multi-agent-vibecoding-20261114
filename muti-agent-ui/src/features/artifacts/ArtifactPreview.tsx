import { Suspense, lazy, useEffect, useState } from 'react';
import { Alert, Button, Drawer, Skeleton, Typography } from 'antd';
import { DownloadOutlined, FileExcelOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../shared/api/endpoints';
import { useUiStore } from '../../shared/stores/ui';

const XMarkdown = lazy(() =>
  import('@ant-design/x-markdown').then((module) => ({ default: module.XMarkdown })),
);

const KIND_LABEL = {
  html: 'HTML 报告',
  markdown: 'Markdown 文档',
  text: '文本文件',
  xlsx: 'Excel 报告',
  pdf: 'PDF 报告',
} as const;
const MIME = {
  html: 'text/html;charset=utf-8',
  markdown: 'text/markdown;charset=utf-8',
  text: 'text/plain;charset=utf-8',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  pdf: 'application/pdf',
} as const;
const BINARY_KINDS = new Set(['xlsx', 'pdf']);

export function ArtifactPreview() {
  const preview = useUiStore((state) => state.preview);
  const closePreview = useUiStore((state) => state.closePreview);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  const isBinary = preview ? BINARY_KINDS.has(preview.kind) : false;
  const query = useQuery<string | Blob>({
    queryKey: ['artifact', preview?.id, isBinary ? 'blob' : 'text'],
    queryFn: async (): Promise<string | Blob> =>
      isBinary
        ? api.getArtifactBlob(preview?.id as string)
        : api.getArtifactText(preview?.id as string),
    enabled: Boolean(preview?.id),
    retry: false,
  });

  useEffect(() => {
    const blob = !isBinary ? undefined : query.data instanceof Blob ? query.data : undefined;
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    setBlobUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [query.data, isBinary]);

  if (!preview) return null;

  const download = () => {
    if (query.data === undefined) return;
    const blob =
      query.data instanceof Blob
        ? query.data
        : new Blob([query.data], { type: MIME[preview.kind] });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    const ext =
      preview.kind === 'markdown'
        ? 'md'
        : preview.kind === 'text'
          ? 'txt'
          : (['html', 'xlsx', 'pdf'] as const).includes(preview.kind)
            ? preview.kind
            : 'html';
    link.download = preview.filename ?? `${preview.title}.${ext}`;
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
          srcDoc={query.data as string}
        />
      )}
      {query.data !== undefined && preview.kind === 'markdown' && (
        <div className="artifact-preview__markdown">
          <Suspense fallback={<pre className="artifact-preview__plain">{query.data as string}</pre>}>
            <XMarkdown content={query.data as string} openLinksInNewTab />
          </Suspense>
        </div>
      )}
      {query.data !== undefined && preview.kind === 'text' && (
        <pre className="artifact-preview__plain">{query.data as string}</pre>
      )}
      {query.data !== undefined && preview.kind === 'pdf' && (
        <iframe
          className="artifact-preview__frame"
          title={preview.title}
          src={blobUrl ?? undefined}
        />
      )}
      {query.data !== undefined && preview.kind === 'xlsx' && (
        <div className="artifact-preview__binary">
          <FileExcelOutlined />
          <Typography.Title level={5}>Excel 报告</Typography.Title>
          <Typography.Text className="muted">
            浏览器不支持在线预览 xlsx，请点击右上角「下载」用 Excel / WPS 打开。
          </Typography.Text>
        </div>
      )}
      {query.data !== undefined && (
        <div className="artifact-preview__meta">
          <Typography.Text className="muted">
            {preview.filename}
            {preview.kind === 'pdf' && !blobUrl ? '（正在准备预览…）' : ''}
          </Typography.Text>
        </div>
      )}
    </Drawer>
  );
}
