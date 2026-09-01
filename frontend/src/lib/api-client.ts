/**
 * 类型化 API 客户端 + SSE 流式解析。
 * 对话接口是 POST，无法用浏览器原生 EventSource（仅 GET），
 * 故用 fetch + ReadableStream 手动解析 SSE 帧（与 OpenAI SDK 同款做法）。
 */
import type { ChatRequest, SSEEvent } from '../types/chat';

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/$/, '');

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
  health: () => request<{ status: string }>('/health'),
  ready: () => request<{ status: string; checks: Record<string, { status: string; mode?: string }> }>('/ready'),
};

/**
 * 流式对话：POST /api/chat，逐帧回调 SSEEvent。
 * AbortSignal 支持用户中断生成。
 */
export async function streamChat(
  payload: ChatRequest,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(payload),
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

    // SSE 帧以空行分隔；一帧内为 "event: xxx\ndata: {...}"
    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const event = parseSSEFrame(frame);
      if (event) onEvent(event);
    }
  }
}

function parseSSEFrame(frame: string): SSEEvent | null {
  let eventName = '';
  let dataRaw = '';
  for (const line of frame.split('\n')) {
    if (line.startsWith('event: ')) eventName = line.slice(7).trim();
    else if (line.startsWith('data: ')) dataRaw += line.slice(6);
  }
  if (!eventName || !dataRaw) return null;
  try {
    return { type: eventName, ...(JSON.parse(dataRaw) as object) } as SSEEvent;
  } catch {
    return null;
  }
}
