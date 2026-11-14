/** 与开发文档 03 章对齐的领域类型。前端只认这些，不认后端实现细节。 */

export type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
export type MessageRole = 'user' | 'assistant' | 'system';
export type MessageStatus = 'streaming' | 'done' | 'error' | 'cancelled';
export type Risk = 'read' | 'write' | 'exec' | 'control';
export type Channel = 'text' | 'reasoning';

export type PlanItemStatus = 'pending' | 'in_progress' | 'completed' | 'blocked';
export type TaskStatus =
  | 'pending'
  | 'claimed'
  | 'in_progress'
  | 'review'
  | 'done'
  | 'blocked'
  | 'failed'
  | 'cancelled';
export type MemberStatus = 'offline' | 'idle' | 'working' | 'shutdown';
export type ApprovalStatus = 'pending' | 'approved' | 'denied' | 'expired';

export interface Session {
  id: string;
  title: string;
  status: 'active' | 'archived';
  created_at: string;
  updated_at: string;
  message_count: number;
  last_run?: { id: string; status: RunStatus } | null;
  archived: boolean;
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
}

export type ArtifactKind = 'html' | 'markdown' | 'text';

export interface Artifact {
  id: string;
  artifact_id: string;
  run_id: string;
  session_id: string;
  message_id: string | null;
  kind: ArtifactKind;
  title: string;
  filename: string;
  bytes: number;
  created_at: string;
  preview_url: string;
}

export interface ToolTrace {
  kind: 'tool';
  tool_call_id: string;
  name: string;
  risk: Risk;
  agent: string;
  input_preview: string;
  ok: boolean;
  error_code: string | null;
  duration_ms: number | null;
  output_preview: string;
  truncated: boolean;
}

export interface PlanTrace {
  kind: 'plan';
  revision: number;
  items: PlanItem[];
}

export interface SubagentTrace {
  kind: 'subagent';
  key: string;
  ok: boolean;
  turns: number;
  summary_chars: number;
  duration_ms: number;
}

export type TraceItem = ToolTrace | PlanTrace | SubagentTrace;

export interface Message {
  id: string;
  session_id: string;
  run_id: string | null;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  seq: number;
  created_at: string;
  usage?: Usage | null;
  trace?: TraceItem[] | null;
}

export interface PlanItem {
  id: number;
  content: string;
  status: PlanItemStatus;
  owner: string | null;
  note: string;
}

export interface Plan {
  goal: string;
  revision: number;
  updated_at: string;
  session_id?: string;
  items: PlanItem[];
}

export interface Task {
  id: string;
  title: string;
  description: string;
  status: TaskStatus;
  owner: string | null;
  priority: number;
  depends_on: string[];
  tags: string[];
  created_at: string;
  updated_at: string;
  attempts: number;
  result: string;
  artifacts: string[];
  error: string | null;
}

export interface Member {
  name: string;
  role: string;
  status: MemberStatus;
  tools: string[];
  max_turns: number;
  turns_used: number;
  last_seen: string | null;
  last_error: string | null;
}

export interface RosterMember {
  name: string;
  role: string;
  tools: string[];
  max_turns: number;
  description: string;
}

export interface Envelope {
  id: string;
  type: string;
  sender: string;
  recipient: string;
  subject: string;
  body: string;
  ts: string;
  correlation_id: string | null;
  reply_to: string | null;
  expects_reply: boolean;
}

export interface Run {
  id: string;
  session_id: string;
  status: RunStatus;
  input: string;
  reply: string | null;
  error_code: string | null;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
  usage: Usage | null;
  turn_count: number;
}

export interface Approval {
  id: string;
  run_id: string;
  tool_call_id: string;
  name: string;
  reason: string;
  expires_at: string;
}

export interface Meta {
  api_version: number;
  app_version: string;
  model: string;
  workspace: { id: string; root: string; name: string };
  capabilities: {
    stream: boolean;
    reasoning: boolean;
    approvals: boolean;
    team: boolean;
    mcp: boolean;
  };
  limits: {
    max_turns_per_task: number;
    daily_token_budget: number;
    max_message_chars: number;
  };
  server_time: string;
}

export interface UsageReport {
  today_tokens: number;
  daily_budget: number;
  budget_ratio: number;
  days: string[];
  by_day: Record<string, number>;
  by_agent: Record<string, { calls: number; input_tokens: number; output_tokens: number }>;
  total_tokens: number;
  total_cost_estimate: number;
}

export interface FileNode {
  key: string;
  title: string;
  isLeaf: boolean;
  children?: FileNode[];
}

export interface FileContent {
  path: string;
  content: string;
  truncated: boolean;
  size: number;
}

export interface Skill {
  name: string;
  description: string;
  tags: string[];
  version: string;
}

export interface SendMessageResult {
  run_id: string;
  message_id: string;
  stream_url: string;
  session_seq: number;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  retryable?: boolean;
  request_id?: string;
}
