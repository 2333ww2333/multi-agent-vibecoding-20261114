import { create } from 'zustand';
import type { Message } from '../api/types';
import { reduceRun } from '../events/reducer';
import type { RunEvent } from '../events/types';
import { emptyRun, type ChatMessage, type SessionView } from '../events/view';

interface ChatState {
  bySession: Record<string, SessionView>;
  ensure: (sessionId: string) => void;
  setLoaded: (sessionId: string, loaded: boolean) => void;
  setMessages: (sessionId: string, messages: ChatMessage[]) => void;
  addMessage: (sessionId: string, message: ChatMessage) => void;
  replaceMessage: (sessionId: string, message: ChatMessage) => void;
  removeMessage: (sessionId: string, messageId: string) => void;
  startRun: (sessionId: string, runId: string, maxTurns?: number) => void;
  apply: (sessionId: string, event: RunEvent) => void;
  applyMany: (sessionId: string, events: RunEvent[]) => void;
  clearApproval: (sessionId: string) => void;
  clearRun: (sessionId: string) => void;
  removeSession: (sessionId: string) => void;
  resetAll: () => void;
}

function emptyView(): SessionView {
  return { messages: [], run: null, loaded: false };
}

export const useChatStore = create<ChatState>((set) => ({
  bySession: {},

  ensure: (sessionId) =>
    set((state) =>
      state.bySession[sessionId]
        ? state
        : { bySession: { ...state.bySession, [sessionId]: emptyView() } },
    ),

  setLoaded: (sessionId, loaded) =>
    set((state) => {
      const view = state.bySession[sessionId] ?? emptyView();
      return { bySession: { ...state.bySession, [sessionId]: { ...view, loaded } } };
    }),

  setMessages: (sessionId, messages) =>
    set((state) => {
      const view = state.bySession[sessionId] ?? emptyView();
      return {
        bySession: { ...state.bySession, [sessionId]: { ...view, messages, loaded: true } },
      };
    }),

  addMessage: (sessionId, message) =>
    set((state) => {
      const view = state.bySession[sessionId] ?? emptyView();
      return {
        bySession: {
          ...state.bySession,
          [sessionId]: { ...view, messages: [...view.messages, message] },
        },
      };
    }),

  replaceMessage: (sessionId, message) =>
    set((state) => {
      const view = state.bySession[sessionId] ?? emptyView();
      return {
        bySession: {
          ...state.bySession,
          [sessionId]: {
            ...view,
            messages: view.messages.map((m) => (m.id === message.id ? message : m)),
          },
        },
      };
    }),

  removeMessage: (sessionId, messageId) =>
    set((state) => {
      const view = state.bySession[sessionId];
      if (!view) return state;
      return {
        bySession: {
          ...state.bySession,
          [sessionId]: {
            ...view,
            messages: view.messages.filter((m) => m.id !== messageId),
          },
        },
      };
    }),

  startRun: (sessionId, runId, maxTurns = 40) =>
    set((state) => {
      const view = state.bySession[sessionId] ?? emptyView();
      return {
        bySession: { ...state.bySession, [sessionId]: { ...view, run: emptyRun(runId, maxTurns) } },
      };
    }),

  apply: (sessionId, event) =>
    set((state) => {
      const view = state.bySession[sessionId];
      if (!view?.run) return state;
      const nextRun = reduceRun(view.run, event);
      if (nextRun === view.run) return state;
      return { bySession: { ...state.bySession, [sessionId]: { ...view, run: nextRun } } };
    }),

  applyMany: (sessionId, events) =>
    set((state) => {
      const view = state.bySession[sessionId];
      if (!view?.run || events.length === 0) return state;
      const nextRun = events.reduce((run, event) => reduceRun(run, event), view.run);
      if (nextRun === view.run) return state;
      return { bySession: { ...state.bySession, [sessionId]: { ...view, run: nextRun } } };
    }),

  clearApproval: (sessionId) =>
    set((state) => {
      const view = state.bySession[sessionId];
      if (!view?.run?.approval) return state;
      return {
        bySession: {
          ...state.bySession,
          [sessionId]: {
            ...view,
            run: { ...view.run, approval: null },
          },
        },
      };
    }),

  clearRun: (sessionId) =>
    set((state) => {
      const view = state.bySession[sessionId];
      if (!view) return state;
      return { bySession: { ...state.bySession, [sessionId]: { ...view, run: null } } };
    }),

  removeSession: (sessionId) =>
    set((state) => {
      const next = { ...state.bySession };
      delete next[sessionId];
      return { bySession: next };
    }),

  resetAll: () => set({ bySession: {} }),
}));

/** 后端 Message → 视图消息。 */
export function toChatMessage(message: Message): ChatMessage {
  return {
    id: message.id,
    role: message.role,
    content: message.content,
    status:
      message.status === 'cancelled'
        ? 'cancelled'
        : message.status === 'error'
          ? 'error'
          : 'done',
    createdAt: message.created_at,
    runId: message.run_id,
    usage: message.usage ?? null,
    trace: message.trace ?? null,
  };
}
