/**
 * 增量 SSE 解析器。
 * 关键点：帧以空行分隔；data 可跨行；注释行（: ping）忽略；半帧必须留在 buffer。
 */

import type { RawEvent } from './types';

export type SseEventHandler = (event: RawEvent) => void;

export function createSseParser(onEvent: SseEventHandler): (chunk: string) => void {
  let buffer = '';

  return function feed(chunk: string): void {
    buffer += chunk.replace(/\r\n/g, '\n');

    let boundary = buffer.indexOf('\n\n');
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);

      let id: number | null = null;
      let type = 'message';
      const dataLines: string[] = [];

      for (const line of frame.split('\n')) {
        if (!line || line.startsWith(':')) continue;
        const colon = line.indexOf(':');
        const field = colon === -1 ? line : line.slice(0, colon);
        const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '');

        if (field === 'id') id = Number(value);
        else if (field === 'event') type = value;
        else if (field === 'data') dataLines.push(value);
      }

      if (id === null || !Number.isFinite(id) || dataLines.length === 0) {
        boundary = buffer.indexOf('\n\n');
        continue;
      }

      try {
        onEvent({ id, type, data: JSON.parse(dataLines.join('\n')) });
      } catch {
        // 坏帧不炸整条流；客户端会靠 seq gap 走全量重取
      }
      boundary = buffer.indexOf('\n\n');
    }
  };
}
