/**
 * LangGraph Platform 客户端：创建 thread，再 POST /threads/{id}/runs/stream。
 * 把平台 SSE（updates / messages / end / interrupt）映射成聊天 UI 事件。
 */
import type { ChatRequest, SSEEvent } from '../types/chat';

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

/**
 * 流式运行主工作流：POST /threads/{id}/runs/stream。
 */
export async function streamChat(
  payload: ChatRequest,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const threadId = await ensureThread(payload.session_id);
  const res = await fetch(`${BASE_URL}/threads/${threadId}/runs/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({
      assistant_id: ASSISTANT_ID,
      input: { goal: payload.message, scope: payload.scope ?? '' },
      stream_mode: ['updates', 'messages'],
    }),
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
    const text = extractMessageText(data);
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

function mapUpdates(data: unknown): SSEEvent[] {
  const payload = unwrapUpdate(data);
  if (!isRecord(payload)) return [];
  const events: SSEEvent[] = [];
  for (const [node, value] of Object.entries(payload)) {
    if (node === '__interrupt__') {
      const prompt = interruptPrompt(value);
      events.push({ type: 'token', content: prompt });
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

function interruptPrompt(value: unknown): string {
  const first = Array.isArray(value) ? value[0] : value;
  if (isRecord(first) && isRecord(first.value) && typeof first.value.prompt === 'string') {
    return `\n[interrupt] ${first.value.prompt}\n`;
  }
  return '\n[interrupt] 工作流等待人工确认，请用 Command(resume=...) 恢复。\n';
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
