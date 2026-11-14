import { memo } from 'react';
import Bubble from '@ant-design/x/es/bubble';
import { AssistantContent } from './AssistantContent';
import { entriesFromTrace } from '../trace';
import type { ChatMessage } from '../../../shared/events/view';
import type { Artifact } from '../../../shared/api/types';

export const MessageList = memo(function MessageList({
  messages,
  artifacts = [],
}: {
  messages: ChatMessage[];
  artifacts?: Artifact[];
}) {
  return (
    <div className="messages">
      {messages.map((message) => {
        const messageArtifacts = artifacts.filter(
          (item) =>
            item.message_id === message.id ||
            (message.runId !== undefined && message.runId !== null && item.run_id === message.runId),
        );
        return message.role === 'user' ? (
          <Bubble
            key={message.id}
            placement="end"
            variant="filled"
            rootClassName="bubble bubble--user"
            content={<span className="user-text">{message.content}</span>}
          />
        ) : (
          <Bubble
            key={message.id}
            placement="start"
            variant="borderless"
            rootClassName="bubble bubble--assistant"
            content={
              <AssistantContent
                text={message.content}
                entries={entriesFromTrace(message.trace)}
                status={
                  message.status === 'cancelled'
                    ? 'cancelled'
                    : message.status === 'error'
                      ? 'failed'
                      : 'completed'
                }
                usage={message.usage}
                artifacts={messageArtifacts}
              />
            }
          />
        );
      })}
    </div>
  );
});
