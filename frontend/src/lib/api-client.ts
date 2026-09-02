/**
 * LangGraph Platform 客户端：创建 thread，再 POST /threads/{id}/runs/stream。
 * 把平台 SSE（updates / messages / end / interrupt）映射成聊天 UI 事件。
 */
import type { ChatRequest, ResumePayload, SSEEvent } from '../types/chat';

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:2024').replace(/\/$/, '');
const ASSISTANT_ID = import.meta.env.VITE_ASSISTANT_ID ?? 'main_workflow';

export class ApiError extends Error {
  constructor(public status: number, public body: unknown) {
    super(`API error ${status}`);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!res.ok) {
    throw new ApiError(res.status, await res.json().catch(() => null));
  }
  return res.json();
}

export const apiClient = {
  health: () => request<{ ok?: boolean; status?: string }>('/ok'),
};

async function ensureThread(threadId?: string): Promise<string> {
  if (threadId) return threadId;
  const created = await request<{ thread_id: string }>('/threads', {
    method: 'POST',
    body: JSON.stringify({}),
  });
  return created.thread_id;
}

async function streamRun(
  threadId: string,
  body: Record<string, unknown>,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE_URL}/threads/${threadId}/runs/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok || !res.body) {
    throw new ApiError(res.status, await res.json().catch(() => null));
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      for (const event of parseLangGraphFrame(frame, threadId)) {
        onEvent(event);
      }
    }
  }
}

/**
 * 流式运行主工作流：POST /threads/{id}/runs/stream。
 *
 * ``onThreadReady`` 在线程创建/复用后立即回调，使调用方能在流式
 * 开始前就持久化 session_id，避免用户中断后丢失会话上下文。
 */
export async function streamChat(
  payload: ChatRequest,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
  onThreadReady?: (threadId: string) => void,
): Promise<void> {
  const threadId = await ensureThread(payload.session_id);
  onThreadReady?.(threadId);
  await streamRun(
    threadId,
    {
      assistant_id: ASSISTANT_ID,
      input: { userMessage: payload.message },
      stream_mode: ['updates', 'messages'],
    },
    onEvent,
    signal,
  );
}

/**
 * 对同一 thread 发送 Command(resume=...)，恢复 interrupt 后的工作流。
 */
export async function resumeChat(
  threadId: string,
  payload: ResumePayload,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  await streamRun(
    threadId,
    {
      assistant_id: ASSISTANT_ID,
      command: { resume: payload },
      stream_mode: ['updates', 'messages'],
    },
    onEvent,
    signal,
  );
}

function parseLangGraphFrame(frame: string, threadId: string): SSEEvent[] {
  let eventName = '';
  let dataRaw = '';
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) eventName = line.slice(6).trim();
    else if (line.startsWith('data:')) dataRaw += line.slice(5).trim();
  }
  if (!dataRaw || dataRaw === '[DONE]') {
    return eventName === 'end' ? [{ type: 'done', session_id: threadId }] : [];
  }
  let data: unknown;
  try {
    data = JSON.parse(dataRaw);
  } catch {
    return [];
  }
  return mapLangGraphEvent(eventName, data, threadId);
}

function mapLangGraphEvent(eventName: string, data: unknown, threadId: string): SSEEvent[] {
  if (eventName === 'error' || (isRecord(data) && typeof data.error === 'string')) {
    const message = isRecord(data)
      ? String(data.message ?? data.error ?? 'LangGraph run failed')
      : 'LangGraph run failed';
    return [{ type: 'error', code: 'AGENT_ERROR', message }];
  }
  if (eventName === 'end') {
    return [{ type: 'done', session_id: threadId }];
  }
  if (eventName === 'messages' || eventName === 'messages/partial') {
    // 中间节点的 LLM JSON 会把气泡撑成「思考链」；只放行短路作答节点。
    if (!isAnswerStreamNode(data)) return [];
    const text = extractMessageText(streamMessagePayload(data));
    return text ? [{ type: 'token', content: text }] : [];
  }
  if (eventName === 'updates' || eventName === 'data' || eventName === 'events') {
    return mapUpdates(data);
  }
  if (eventName === 'metadata') {
    return [];
  }
  const text = extractMessageText(data);
  return text ? [{ type: 'token', content: text }] : [];
}

const ANSWER_STREAM_NODES = new Set(['chitchat', 'fast_qa']);

function streamMessageMeta(data: unknown): Record<string, unknown> | undefined {
  if (Array.isArray(data) && data.length >= 2 && isRecord(data[1])) {
    return data[1];
  }
  if (isRecord(data) && isRecord(data.metadata)) {
    return data.metadata;
  }
  return undefined;
}

function isAnswerStreamNode(data: unknown): boolean {
  const meta = streamMessageMeta(data);
  const node = meta && typeof meta.langgraph_node === 'string' ? meta.langgraph_node : '';
  // 无节点元数据时宁可丢掉中间 token，避免把 classify/producer JSON 拼进回复。
  return ANSWER_STREAM_NODES.has(node);
}

function streamMessagePayload(data: unknown): unknown {
  if (Array.isArray(data) && data.length > 0) return data[0];
  return data;
}

function mapUpdates(data: unknown): SSEEvent[] {
  const payload = unwrapUpdate(data);
  if (!isRecord(payload)) return [];
  const events: SSEEvent[] = [];
  for (const [node, value] of Object.entries(payload)) {
    if (node === '__interrupt__') {
      const info = interruptInfo(value);
      events.push({ type: 'interrupt', prompt: info.prompt, reason: info.reason });
      events.push({ type: 'token', content: `\n[interrupt] ${info.prompt}\n` });
      continue;
    }
    if (node === 'triage' || node === 'classify' || node === 'mark_decision' || node === 'mark_production' || node === 'execute_produce' || node === 'execute_research') {
      continue;
    }
    // 闲聊 / 简单问答：只推最终回复，不把节点状态当成思考链。
    if (isRecord(value) && typeof value.chitchat_reply === 'string' && value.chitchat_reply) {
      events.push({ type: 'token', content: value.chitchat_reply });
      continue;
    }
    events.push({ type: 'tool_call', name: node, args: isRecord(value) ? value : {} });
    events.push({
      type: 'tool_result',
      name: node,
      result: stringifyResult(value),
    });
  }
  return events;
}

function unwrapUpdate(data: unknown): unknown {
  if (Array.isArray(data) && data.length > 0) return data[0];
  if (isRecord(data) && 'data' in data) return data.data;
  return data;
}

function extractMessageText(data: unknown): string {
  if (typeof data === 'string') return data;
  if (Array.isArray(data)) {
    return data.map(extractMessageText).filter(Boolean).join('');
  }
  if (!isRecord(data)) return '';
  if (typeof data.content === 'string') return data.content;
  if (Array.isArray(data.content)) {
    return data.content
      .map((part) => (isRecord(part) && typeof part.text === 'string' ? part.text : ''))
      .join('');
  }
  if (isRecord(data.kwargs) && typeof data.kwargs.content === 'string') {
    return data.kwargs.content;
  }
  return '';
}

function interruptInfo(value: unknown): { prompt: string; reason?: string } {
  const first = Array.isArray(value) ? value[0] : value;
  if (isRecord(first) && isRecord(first.value)) {
    const prompt =
      typeof first.value.prompt === 'string'
        ? first.value.prompt
        : '工作流等待人工确认';
    const reason = typeof first.value.reason === 'string' ? first.value.reason : undefined;
    return { prompt, reason };
  }
  return { prompt: '工作流等待人工确认，请审批后继续。' };
}

function stringifyResult(value: unknown): string {
  try {
    const text = JSON.stringify(value);
    return text.length > 800 ? `${text.slice(0, 800)}…` : text;
  } catch {
    return String(value);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
