/** 唯一的数据入口：Mock 与真实 BFF 共用同一套函数签名。 */

import { ApiError, USE_MOCK, apiUrl, authToken, http } from './client';
import * as mock from './mock';
import type {
  Approval,
  Artifact,
  Envelope,
  FileContent,
  FileNode,
  Member,
  Message,
  Meta,
  Plan,
  RosterMember,
  Run,
  SendMessageResult,
  Session,
  Skill,
  Task,
  TaskStatus,
  UsageReport,
} from './types';
import type { RunEvent } from '../events/types';
import { createSseParser } from '../events/parse';

const P = '/api';

export const api = {
  // ---------- meta ----------
  meta(): Promise<Meta> {
    return USE_MOCK ? Promise.resolve(mock.mockMeta()) : http.get<Meta>(`${P}/meta`);
  },

  // ---------- sessions ----------
  async listSessions(): Promise<Session[]> {
    if (USE_MOCK) return mock.mockListSessions().sessions;
    const data = await http.get<{ sessions: Session[] } | Session[]>(`${P}/sessions?limit=100`);
    return Array.isArray(data) ? data : data.sessions;
  },

  createSession(title?: string): Promise<Session> {
    return USE_MOCK
      ? Promise.resolve(mock.mockCreateSession(title))
      : http.post<Session>(`${P}/sessions`, { title });
  },

  getSession(sessionId: string): Promise<Session> {
    if (USE_MOCK) {
      const session = mock.mockListSessions().sessions.find((s) => s.id === sessionId);
      if (!session) return Promise.reject(new ApiError(404, { code: 'E_SESSION_NOT_FOUND', message: '会话不存在' }));
      return Promise.resolve(session);
    }
    return http.get<Session>(`${P}/sessions/${sessionId}`);
  },

  patchSession(sessionId: string, patch: { title?: string; archived?: boolean }): Promise<Session> {
    return USE_MOCK
      ? Promise.resolve(mock.mockPatchSession(sessionId, patch))
      : http.patch<Session>(`${P}/sessions/${sessionId}`, patch);
  },

  deleteSession(sessionId: string): Promise<{ ok: true }> {
    return USE_MOCK
      ? Promise.resolve(mock.mockDeleteSession(sessionId))
      : http.delete<{ ok: true }>(`${P}/sessions/${sessionId}`);
  },

  async listMessages(sessionId: string, beforeSeq?: number, limit = 200): Promise<Message[]> {
    if (USE_MOCK) return mock.mockListMessages(sessionId, beforeSeq, limit).messages;
    const query = new URLSearchParams({ limit: String(limit) });
    if (beforeSeq) query.set('before_seq', String(beforeSeq));
    const data = await http.get<{ messages: Message[] } | Message[]>(
      `${P}/sessions/${sessionId}/messages?${query.toString()}`,
    );
    return Array.isArray(data) ? data : data.messages;
  },

  sendMessage(sessionId: string, text: string, clientMsgId: string): Promise<SendMessageResult> {
    return USE_MOCK
      ? Promise.resolve(mock.mockSendMessage(sessionId, text, clientMsgId))
      : http.post<SendMessageResult>(`${P}/sessions/${sessionId}/messages`, {
          text,
          client_msg_id: clientMsgId,
        });
  },

  // ---------- runs ----------
  getRun(runId: string): Promise<Run> {
    return USE_MOCK ? Promise.resolve(mock.mockGetRun(runId)) : http.get<Run>(`${P}/runs/${runId}`);
  },

  cancelRun(runId: string): Promise<{ ok: boolean }> {
    return USE_MOCK
      ? Promise.resolve(mock.mockCancelRun(runId))
      : http.post<{ ok: boolean }>(`${P}/runs/${runId}/cancel`);
  },

  retryRun(runId: string): Promise<SendMessageResult> {
    return USE_MOCK
      ? Promise.resolve(mock.mockRetryRun(runId))
      : http.post<SendMessageResult>(`${P}/runs/${runId}/retry`);
  },

  listApprovals(runId: string): Promise<Approval[]> {
    if (USE_MOCK) return Promise.resolve(mock.mockListApprovals(runId).approvals);
    return http
      .get<{ approvals: Approval[] }>(`${P}/runs/${runId}/approvals`)
      .then((data) => data.approvals);
  },

  resolveApproval(approvalId: string, approved: boolean, remember = false): Promise<{ status: string }> {
    return USE_MOCK
      ? Promise.resolve(mock.mockResolveApproval(approvalId, approved))
      : http.post<{ status: string }>(`${P}/approvals/${approvalId}`, { approved, remember });
  },

  // ---------- workbench ----------
  getPlan(sessionId?: string): Promise<Plan> {
    if (USE_MOCK) return Promise.resolve(mock.mockGetPlan());
    const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
    return http.get<Plan>(`${P}/plan${query}`);
  },

  getBoard(includeDone = false): Promise<Task[]> {
    if (USE_MOCK) return Promise.resolve(mock.mockGetBoard(includeDone).tasks);
    return http
      .get<{ tasks: Task[] }>(`${P}/board?include_done=${includeDone}`)
      .then((data) => data.tasks);
  },

  createTask(body: { title: string; description?: string; priority?: number }): Promise<Task> {
    return USE_MOCK
      ? Promise.resolve(mock.mockCreateTask(body))
      : http.post<Task>(`${P}/board/tasks`, body);
  },

  updateTask(
    taskId: string,
    body: { status: TaskStatus; owner?: string; result?: string; note?: string; artifacts?: string[] },
  ): Promise<Task> {
    return USE_MOCK
      ? Promise.resolve(mock.mockUpdateTask(taskId, body))
      : http.patch<Task>(`${P}/board/tasks/${taskId}`, body);
  },

  getTeam(): Promise<Member[]> {
    if (USE_MOCK) return Promise.resolve(mock.mockGetTeam().members);
    return http.get<{ members: Member[] }>(`${P}/team`).then((data) => data.members);
  },

  getTeamRoster(): Promise<RosterMember[]> {
    if (USE_MOCK) return Promise.resolve(mock.mockGetTeamRoster().members);
    return http
      .get<{ members: RosterMember[] }>(`${P}/team/roster`)
      .then((data) => data.members);
  },

  spawnTeammate(name: string, role?: string): Promise<{ name: string; message: string }> {
    return USE_MOCK
      ? Promise.resolve(mock.mockSpawn(name, role))
      : http.post<{ name: string; message: string }>(`${P}/team/${name}/spawn`, { role });
  },

  shutdownTeammate(name: string): Promise<{ name: string; message: string }> {
    return USE_MOCK
      ? Promise.resolve(mock.mockShutdown(name))
      : http.post<{ name: string; message: string }>(`${P}/team/${name}/shutdown`);
  },

  getInbox(name: string, peek = true): Promise<Envelope[]> {
    if (USE_MOCK) return Promise.resolve(mock.mockGetInbox(name, peek).messages);
    return http
      .get<{ messages: Envelope[] }>(`${P}/inbox/${name}?peek=${peek}&limit=50`)
      .then((data) => data.messages);
  },

  drainInbox(name: string): Promise<{ messages: Envelope[]; count: number }> {
    return USE_MOCK
      ? Promise.resolve(mock.mockDrainInbox(name))
      : http.post<{ messages: Envelope[]; count: number }>(`${P}/inbox/${name}/drain`);
  },

  sendTeamMessage(body: {
    to: string;
    content: string;
    subject?: string;
    msg_type?: string;
  }): Promise<Envelope> {
    return USE_MOCK
      ? Promise.resolve(mock.mockSendTeamMessage(body))
      : http.post<Envelope>(`${P}/messages`, body);
  },

  getSkills(): Promise<Skill[]> {
    if (USE_MOCK) return Promise.resolve(mock.mockGetSkills().skills);
    return http.get<{ skills: Skill[] }>(`${P}/skills`).then((data) => data.skills);
  },

  getFileTree(): Promise<FileNode[]> {
    if (USE_MOCK) return Promise.resolve(mock.mockGetFiles().nodes);
    return http.get<{ nodes: FileNode[] }>(`${P}/files/tree?path=.&depth=2`).then((data) => data.nodes);
  },

  getFileContent(path: string): Promise<FileContent> {
    return USE_MOCK
      ? Promise.resolve(mock.mockGetFileContent(path))
      : http.get<FileContent>(`${P}/files/content?path=${encodeURIComponent(path)}`);
  },

  // ---------- artifacts ----------
  listArtifacts(sessionId: string): Promise<Artifact[]> {
    if (USE_MOCK) return Promise.resolve(mock.mockListArtifacts(sessionId).artifacts);
    return http
      .get<{ artifacts: Artifact[] }>(
        `${P}/artifacts?session_id=${encodeURIComponent(sessionId)}`,
      )
      .then((data) => data.artifacts);
  },

  async getArtifactText(artifactId: string): Promise<string> {
    if (USE_MOCK) return Promise.reject(new ApiError(404, { code: 'E_ARTIFACT_NOT_FOUND', message: 'Mock 没有产物' }));
    const headers: Record<string, string> = {};
    const token = authToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch(apiUrl(`${P}/artifacts/${encodeURIComponent(artifactId)}`), {
      headers,
    });
    if (!response.ok) {
      const text = await response.text();
      let body: { code?: string; message?: string } = {};
      try {
        body = (JSON.parse(text) as { error?: { code?: string; message?: string } }).error ?? {};
      } catch {
        body = {};
      }
      throw new ApiError(response.status, {
        code: body.code ?? 'E_ARTIFACT_LOAD_FAILED',
        message: body.message ?? `产物加载失败（HTTP ${response.status}）`,
      });
    }
    return response.text();
  },

  getUsage(days = 7): Promise<UsageReport> {
    return USE_MOCK
      ? Promise.resolve(mock.mockGetUsage(days))
      : http.get<UsageReport>(`${P}/usage?days=${days}`);
  },
};

/** 订阅一个 run 的事件流；正常结束（终态）或 abort 时 resolve。 */
export async function streamRun(
  runId: string,
  after: number,
  onEvent: (event: RunEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  if (USE_MOCK) {
    await mock.mockStreamRun(runId, after, onEvent, signal);
    return;
  }

  const headers: Record<string, string> = { Accept: 'text/event-stream' };
  const token = authToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(apiUrl(`/api/runs/${runId}/events?after=${after}`), {
    headers,
    signal,
  });

  if (response.status === 410) {
    throw new ApiError(410, {
      code: 'E_STREAM_EXPIRED',
      message: '事件流已过期，需要重新加载会话。',
    });
  }
  if (response.status === 401) {
    throw new ApiError(401, { code: 'E_UNAUTHORIZED', message: '未授权' });
  }
  if (!response.ok || !response.body) {
    throw new ApiError(response.status, {
      code: 'E_INTERNAL',
      message: `事件流连接失败（HTTP ${response.status}）`,
    });
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = createSseParser((raw) => {
    const event = raw.data as RunEvent;
    if (event && typeof event.seq === 'number') onEvent(event);
  });

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    parser(decoder.decode(value, { stream: true }));
  }
}
