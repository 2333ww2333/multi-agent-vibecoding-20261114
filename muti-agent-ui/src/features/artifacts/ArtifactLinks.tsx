import { FileTextOutlined } from '@ant-design/icons';
import type { Artifact } from '../../shared/api/types';
import { useUiStore } from '../../shared/stores/ui';

const LABEL: Record<Artifact['kind'], string> = {
  html: 'HTML',
  markdown: 'Markdown',
  text: '文本',
  xlsx: 'Excel',
  pdf: 'PDF',
};

export function ArtifactLinks({ artifacts }: { artifacts: Artifact[] }) {
  const openPreview = useUiStore((state) => state.openPreview);
  if (artifacts.length === 0) return null;

  return (
    <div className="artifact-links" aria-label="生成的文件">
      {artifacts.map((artifact) => (
        <button
          key={artifact.id}
          type="button"
          className="artifact-chip"
          onClick={() =>
            openPreview({
              id: artifact.id,
              title: artifact.title,
              kind: artifact.kind,
              filename: artifact.filename,
            })
          }
          title={`预览 ${artifact.filename}`}
        >
          <FileTextOutlined />
          <span>{artifact.title}</span>
          <span className="artifact-chip__kind">{LABEL[artifact.kind]}</span>
        </button>
      ))}
    </div>
  );
}
