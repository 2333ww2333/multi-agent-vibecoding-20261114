import { Suspense, lazy, useMemo, type ComponentType } from 'react';
import { Collapse } from 'antd';
import type { ComponentProps as MarkdownComponentProps } from '@ant-design/x-markdown';
import { TraceChain } from './TraceChain';
import { MessageFooter } from './MessageFooter';
import { ArtifactLinks } from '../../artifacts/ArtifactLinks';
import type { TraceEntry } from '../trace';
import { hintFor } from '../../../shared/lib/errorHints';
import type { Artifact, RunStatus } from '../../../shared/api/types';
import { useUiStore, type PreviewTarget } from '../../../shared/stores/ui';

// Markdown 渲染器体积较大，单独切一个 chunk；首屏只有对话骨架。
const XMarkdown = lazy(() =>
  import('@ant-design/x-markdown').then((module) => ({ default: module.XMarkdown })),
);

export function AssistantContent({
  text,
  reasoning,
  entries,
  status,
  usage,
  error,
  streaming,
  onRetry,
  artifacts = [],
}: {
  text: string;
  reasoning?: string;
  entries: TraceEntry[];
  status: RunStatus | 'done';
  usage?: { input_tokens: number; output_tokens: number } | null;
  error?: { code: string; message: string } | null;
  streaming?: boolean;
  onRetry?: () => void;
  artifacts?: Artifact[];
}) {
  const isRunning = status === 'running';
  const openPreview = useUiStore((state) => state.openPreview);

  const components = useMemo<Record<string, ComponentType<MarkdownComponentProps>>>(() => {
    const resolve = (href: string): PreviewTarget | null => {
      const artifactId = href.startsWith('artifact://')
        ? decodeURIComponent(href.slice('artifact://'.length))
        : href.match(/\/api\/artifacts\/([^/?#]+)/)?.[1];
      if (!artifactId) return null;
      const known = artifacts.find((item) => item.id === artifactId);
      return {
        id: artifactId,
        title: known?.title ?? '生成的文件',
        kind: known?.kind ?? 'html',
        filename: known?.filename,
      };
    };

    return {
      a: (props) => {
        const raw = props as MarkdownComponentProps & { href?: string };
        const href = String(raw.href ?? '');
        const target = resolve(href);
        if (target) {
          return (
            <button
              type="button"
              className="artifact-inline-link"
              onClick={() => openPreview(target)}
            >
              {raw.children}
            </button>
          );
        }
        return (
          <a href={href} target="_blank" rel="noreferrer">
            {raw.children}
          </a>
        );
      },
    };
  }, [artifacts, openPreview]);

  return (
    <div className={`assistant ${isRunning ? 'assistant--running' : ''}`}>
      {reasoning && (
        <Collapse
          ghost
          size="small"
          className="reasoning"
          items={[
            {
              key: 'reasoning',
              label: <span className="muted">思考过程</span>,
              children: <div className="reasoning__text">{reasoning}</div>,
            },
          ]}
        />
      )}

      <TraceChain entries={entries} running={isRunning} />

      {text && (
        <div className={`assistant__body ${isRunning ? 'is-streaming' : ''}`}>
          <Suspense fallback={<div className="assistant__plain">{text}</div>}>
            <XMarkdown
              content={text.replace(
                /artifact:\/\/([A-Za-z0-9_-]+)/g,
                '/api/artifacts/$1',
              )}
              components={components}
              streaming={
                streaming
                  ? {
                      hasNextChunk: true,
                      incremental: true,
                      incompleteMarkdown: 'complete',
                      typewriter: false,
                    }
                  : false
              }
              openLinksInNewTab
            />
          </Suspense>
        </div>
      )}

      {isRunning && !text && entries.length === 0 && (
        <p className="assistant__thinking">
          <span className="caret" aria-hidden /> 正在处理…
        </p>
      )}

      {status === 'cancelled' && <p className="assistant__notice">已停止生成。</p>}

      <ArtifactLinks artifacts={artifacts} />

      {error && (
        <div className="assistant__error" role="alert">
          <code>{error.code}</code>
          <span>{error.message}</span>
          <p className="muted">{hintFor(error.code)}</p>
        </div>
      )}

      {(text || usage || status === 'completed' || status === 'failed') && (
        <MessageFooter text={text} usage={usage} onRetry={onRetry} />
      )}
    </div>
  );
}
