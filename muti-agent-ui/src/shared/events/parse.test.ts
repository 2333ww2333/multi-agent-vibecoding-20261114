import { describe, expect, it } from 'vitest';
import { createSseParser } from './parse';
import type { RawEvent } from './types';

const FRAME =
  'id: 1\nevent: run.started\ndata: {"run_id":"r_1","seq":1,"type":"run.started","ts":"t","data":{"message_id":"m_1","turn":0,"max_turns":40}}\n\n';

describe('createSseParser', () => {
  it('parses a full frame', () => {
    const events: RawEvent[] = [];
    const feed = createSseParser((event) => events.push(event));
    feed(FRAME);
    expect(events).toHaveLength(1);
    expect(events[0].id).toBe(1);
    expect(events[0].type).toBe('run.started');
    expect((events[0].data as { data: { message_id: string } }).data.message_id).toBe('m_1');
  });

  it('handles a frame split across chunks', () => {
    const events: RawEvent[] = [];
    const feed = createSseParser((event) => events.push(event));
    for (const char of FRAME) feed(char);
    expect(events).toHaveLength(1);
  });

  it('ignores heartbeat comments and blank frames', () => {
    const events: RawEvent[] = [];
    const feed = createSseParser((event) => events.push(event));
    feed(': ping\n\n');
    feed('\n\n');
    feed(FRAME);
    expect(events).toHaveLength(1);
  });

  it('supports CRLF and multi-line data', () => {
    const events: RawEvent[] = [];
    const feed = createSseParser((event) => events.push(event));
    feed('id: 2\r\nevent: message.delta\r\ndata: {"a":\r\ndata: 1}\r\n\r\n');
    expect(events).toHaveLength(1);
    expect(events[0].id).toBe(2);
    expect(events[0].data).toEqual({ a: 1 });
  });

  it('skips bad JSON without breaking the stream', () => {
    const events: RawEvent[] = [];
    const feed = createSseParser((event) => events.push(event));
    feed('id: 3\nevent: x\ndata: {bad}\n\n');
    feed(FRAME);
    expect(events).toHaveLength(1);
    expect(events[0].id).toBe(1);
  });

  it('keeps multiple events from one chunk in order', () => {
    const events: RawEvent[] = [];
    const feed = createSseParser((event) => events.push(event));
    feed(FRAME + FRAME.replace('id: 1', 'id: 2'));
    expect(events.map((event) => event.id)).toEqual([1, 2]);
  });
});
