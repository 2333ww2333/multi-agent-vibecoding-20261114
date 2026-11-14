/** 错误码 → 人话。每条都必须给出「下一步做什么」。 */

const HINTS: Record<string, string> = {
  E_UNAUTHORIZED: '未授权。请在设置里填写访问令牌，或让管理员检查 MA_WEB_TOKEN。',
  E_FORBIDDEN: '没有权限执行这个操作。',
  E_WORKSPACE_BUSY: '当前工作区有任务在运行。等它结束，或先停止它。',
  E_RUN_NOT_ACTIVE: '这个运行已经结束，无法再操作。',
  E_STREAM_EXPIRED: '事件流已过期，正在重新加载会话。',
  E_APPROVAL_EXPIRED: '审批已超时，按默认策略被拒绝。',
  E_TOOL_TIMEOUT: '工具超时了。可以缩小范围（更少文件、更短命令）后重试。',
  E_TOOL_DENIED: '操作被策略拦截。确实需要时，在审批弹窗里放行，或换一种做法。',
  E_TOOL_LIMIT: '单轮工具调用太多，下一轮会继续。',
  E_PATH_ESCAPE: '路径超出了工作区。只能访问当前项目目录内的文件。',
  E_FILE_NOT_FOUND: '文件不存在，刷新目录树再试。',
  E_LLM_AUTH: '模型凭证无效。检查 .env 里的 DEEPSEEK_API_KEY。',
  E_LLM_MODEL: '端点没有这个模型。让管理员检查 DEEPSEEK_MODEL。',
  E_LLM_RATE_LIMIT: '触发限流，已自动重试。稍等片刻再发。',
  E_LLM_TIMEOUT: '模型响应超时。可以重试，或把任务拆小。',
  E_LLM_CONNECTION: '连不上模型端点。检查网络、代理与 DEEPSEEK_BASE_URL。',
  E_BUDGET_EXCEEDED: '今日 token 预算用尽。明天再试，或让管理员调整 daily_token_budget。',
  E_RUN_CRASHED: '运行异常中断。可以直接重试。',
  E_RUN_ENDED: '运行结束时这个工具还没返回，结果未知。',
  E_TASK_ILLEGAL_TRANSITION: '这个状态流转不合法，请选择允许的目标状态。',
  E_TASK_NO_RESULT: '标记完成需要填写结论摘要。',
  E_TASK_DEPS_UNMET: '前置任务还没完成，暂时不能认领。',
  E_MEMBER_UNKNOWN: '没有这个队友。刷新队友列表再试。',
  E_RATE_LIMITED: '请求过于频繁，请稍后再试。',
  E_INTERNAL: '服务内部错误。把请求 ID 发给维护者。',
};

export function hintFor(code: string | null | undefined): string {
  if (!code) return '请稍后重试；若持续失败，把错误码发给维护者。';
  return HINTS[code] ?? '请稍后重试；若持续失败，把错误码发给维护者。';
}

export function isTerminalStatus(status: string): boolean {
  return status === 'completed' || status === 'failed' || status === 'cancelled';
}
