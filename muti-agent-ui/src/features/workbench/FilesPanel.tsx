import { Suspense, lazy, useState } from 'react';
import { Empty, Skeleton, Tree } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../shared/api/endpoints';
import { Panel } from './Panel';
import { ErrorState } from '../../shared/ui/ErrorState';

const XMarkdown = lazy(() =>
  import('@ant-design/x-markdown').then((module) => ({ default: module.XMarkdown })),
);

const MARKDOWN_EXT = /\.(md|markdown)$/i;
const HTML_EXT = /\.(html?|htm)$/i;

export function FilesPanel() {
  const [path, setPath] = useState<string | null>(null);
  const treeQuery = useQuery({ queryKey: ['files'], queryFn: () => api.getFileTree() });
  const contentQuery = useQuery({
    queryKey: ['file', path],
    queryFn: () => api.getFileContent(path as string),
    enabled: Boolean(path),
    retry: false,
  });

  const content = contentQuery.data?.content ?? '';
  const isMarkdown = path ? MARKDOWN_EXT.test(path) : false;
  const isHtml = path ? HTML_EXT.test(path) : false;

  return (
    <Panel title="文件" extra={<span className="muted">只读</span>}>
      {treeQuery.isLoading ? (
        <Skeleton active paragraph={{ rows: 6 }} title={false} />
      ) : treeQuery.isError ? (
        <ErrorState message="目录加载失败" onRetry={() => void treeQuery.refetch()} />
      ) : (
        <Tree
          treeData={treeQuery.data ?? []}
          showIcon
          blockNode
          height={220}
          onSelect={(_, info) => {
            if (info.node.isLeaf) setPath(String(info.node.key));
          }}
        />
      )}

      <div className="file-viewer">
        {!path && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选一个文件查看" />}
        {path && contentQuery.isLoading && <Skeleton active paragraph={{ rows: 6 }} title={false} />}
        {path && contentQuery.isError && (
          <ErrorState
            title="读取失败"
            message={contentQuery.error instanceof Error ? contentQuery.error.message : '未知错误'}
          />
        )}
        {path && contentQuery.data && (
          <>
            <div className="file-viewer__head">
              <code>{contentQuery.data.path}</code>
              <span className="muted">{contentQuery.data.size} B</span>
            </div>
            {isMarkdown && (
              <div className="file-viewer__render">
                <Suspense fallback={<pre className="code-block file-viewer__body">{content}</pre>}>
                  <XMarkdown content={content} openLinksInNewTab />
                </Suspense>
              </div>
            )}
            {isHtml && (
              <iframe
                className="file-viewer__frame"
                title={contentQuery.data.path}
                sandbox=""
                srcDoc={content}
              />
            )}
            {!isMarkdown && !isHtml && (
              <pre className="code-block file-viewer__body">{content}</pre>
            )}
          </>
        )}
      </div>
    </Panel>
  );
}
