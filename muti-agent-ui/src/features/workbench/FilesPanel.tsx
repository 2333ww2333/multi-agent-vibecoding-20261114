import { useState } from 'react';
import { Empty, Skeleton, Tree } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../shared/api/endpoints';
import { Panel } from './Panel';
import { ErrorState } from '../../shared/ui/ErrorState';

export function FilesPanel() {
  const [path, setPath] = useState<string | null>(null);
  const treeQuery = useQuery({ queryKey: ['files'], queryFn: () => api.getFileTree() });
  const contentQuery = useQuery({
    queryKey: ['file', path],
    queryFn: () => api.getFileContent(path as string),
    enabled: Boolean(path),
    retry: false,
  });

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
            <pre className="code-block file-viewer__body">{contentQuery.data.content}</pre>
          </>
        )}
      </div>
    </Panel>
  );
}
