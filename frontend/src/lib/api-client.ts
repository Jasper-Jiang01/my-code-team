import type { ChatRequest, ResumeRequest, SSEEvent } from '../types/chat';

const TALOS_TEST_HOST = 'ws.cloud.test.sankuai.com';
const OCEANUS_TEST_ORIGIN = 'https://codepilot.ai.test.sankuai.com';

function resolveApiBase(): string {
  const fromEnv = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
  if (fromEnv) return fromEnv;
  if (typeof window !== 'undefined' && window.location.hostname === TALOS_TEST_HOST) {
    return OCEANUS_TEST_ORIGIN;
  }
  return '';
}

const BASE_URL = resolveApiBase();

export class ApiError extends Error {
  constructor(public status: number, public body: unknown) {
    super(`API error ${status}`);
    this.name = 'ApiError';
  }
}

export const apiClient = {
  health: async (): Promise<{ status?: string }> => {
    const response = await fetch(`${BASE_URL}/api/health`);
    if (!response.ok) throw new ApiError(response.status, await response.json().catch(() => null));
    return response.json();
  },
};

export async function streamChat(
  payload: ChatRequest,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
  onThreadReady?: (threadId: string) => void,
): Promise<void> {
  return streamSse(`${BASE_URL}/api/chat`, payload, onEvent, signal, onThreadReady);
}

/** 审批挂起的 HumanInTheLoop 中断并继续接收流式回复。 */
export async function resumeChat(
  payload: ResumeRequest,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
  onThreadReady?: (threadId: string) => void,
): Promise<void> {
  return streamSse(`${BASE_URL}/api/resume`, payload, onEvent, signal, onThreadReady);
}

async function streamSse(
  url: string,
  payload: ChatRequest | ResumeRequest,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal,
  onThreadReady?: (threadId: string) => void,
): Promise<void> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new ApiError(response.status, await response.json().catch(() => null));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let threadId = payload.session_id ?? '';

  const emitFrame = (frame: string) => {
    for (const event of parseSseFrame(frame, threadId)) {
      if (event.type === 'session' || event.type === 'done') {
        threadId = event.session_id;
        onThreadReady?.(threadId);
      }
      onEvent(event);
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n');
    let separator: number;
    while ((separator = buffer.indexOf('\n\n')) !== -1) {
      emitFrame(buffer.slice(0, separator));
      buffer = buffer.slice(separator + 2);
    }
  }
  buffer += decoder.decode();
  if (buffer.trim()) emitFrame(buffer);
}

function parseSseFrame(frame: string, fallbackThread: string): SSEEvent[] {
  let eventName = '';
  let dataRaw = '';
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) eventName = line.slice(6).trim();
    if (line.startsWith('data:')) dataRaw += (dataRaw ? '\n' : '') + line.slice(5).replace(/^ /, '');
  }
  if (!dataRaw || dataRaw === '[DONE]') {
    return eventName === 'done' ? [{ type: 'done', session_id: fallbackThread }] : [];
  }
  try {
    return mapSseEvent(eventName, JSON.parse(dataRaw), fallbackThread);
  } catch {
    return [];
  }
}

function mapSseEvent(eventName: string, data: unknown, fallbackThread: string): SSEEvent[] {
  if (!isRecord(data)) return [];
  if (eventName === 'session' && typeof data.session_id === 'string') {
    return [{ type: 'session', session_id: data.session_id }];
  }
  if (eventName === 'token' && typeof data.content === 'string' && data.content) {
    return [{ type: 'token', content: data.content }];
  }
  if (eventName === 'interrupt' && typeof data.prompt === 'string') {
    return [
      {
        type: 'interrupt',
        prompt: data.prompt,
        reason: typeof data.reason === 'string' ? data.reason : null,
      },
    ];
  }
  if (eventName === 'done') {
    return [{ type: 'done', session_id: typeof data.session_id === 'string' ? data.session_id : fallbackThread }];
  }
  if (eventName === 'error') {
    return [{ type: 'error', code: 'AGENT_ERROR', message: String(data.message ?? 'Agent run failed') }];
  }
  return [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
