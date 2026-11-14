import Bubble from '@ant-design/x/es/bubble';
import { useChatStore } from '../../../shared/stores/chat';
import { AssistantContent } from './AssistantContent';
import { entriesFromRun } from '../trace';

export function LiveBubble({
  sessionId,
  onRetry,
}: {
  sessionId: string;
  onRetry?: () => void;
}) {
  const run = useChatStore((state) => state.bySession[sessionId]?.run);
  if (!run) return null;

  return (
    <Bubble
      placement="start"
      variant="borderless"
      rootClassName="bubble bubble--assistant bubble--live"
      streaming={run.status === 'running'}
      content={
        <AssistantContent
          text={run.text}
          reasoning={run.reasoning}
          entries={entriesFromRun(run)}
          status={run.status}
          usage={run.usage}
          error={run.error}
          streaming={run.status === 'running'}
          onRetry={onRetry}
          artifacts={run.artifacts}
        />
      }
    />
  );
}
