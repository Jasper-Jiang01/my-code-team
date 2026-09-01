import { useCallback, useRef, useState } from 'react';
import { streamChat } from '../lib/api-client';
import type { ChatMessage, SSEEvent } from '../types/chat';

function uid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

export interface UseChatResult {
  messages: ChatMessage[];
  streaming: boolean;
  error: string | null;
  send: (text: string) => void;
  stop: () => void;
}

/** 聊天状态管理：流式聚合 token，透出工具调用轨迹。 */
export function useChat(): UseChatResult {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sessionRef = useRef<string | undefined>(undefined);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || streaming) return;
      setError(null);

      const userMsg: ChatMessage = { id: uid(), role: 'user', content: trimmed };
      const assistantId = uid();
      const assistantMsg: ChatMessage = { id: assistantId, role: 'assistant', content: '', toolTrace: [] };
      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setStreaming(true);

      const controller = new AbortController();
      abortRef.current = controller;

      const updateAssistant = (updater: (m: ChatMessage) => ChatMessage) => {
        setMessages((prev) => prev.map((m) => (m.id === assistantId ? updater(m) : m)));
      };

      const onEvent = (event: SSEEvent) => {
        switch (event.type) {
          case 'token':
            updateAssistant((m) => ({ ...m, content: m.content + event.content }));
            break;
          case 'tool_call':
            updateAssistant((m) => ({
              ...m,
              toolTrace: [...(m.toolTrace ?? []), { name: event.name, args: event.args }],
            }));
            break;
          case 'tool_result':
            updateAssistant((m) => {
              const trace = [...(m.toolTrace ?? [])];
              const idx = trace.findIndex((t) => t.name === event.name && t.result === undefined);
              if (idx !== -1) trace[idx] = { ...trace[idx], result: event.result };
              return { ...m, toolTrace: trace };
            });
            break;
          case 'done':
            sessionRef.current = event.session_id;
            break;
          case 'error':
            setError(event.message);
            updateAssistant((m) => ({
              ...m,
              content: m.content || '（生成失败，请重试）',
            }));
            break;
        }
      };

      streamChat({ message: trimmed, session_id: sessionRef.current }, onEvent, controller.signal)
        .catch((err: unknown) => {
          if ((err as Error)?.name === 'AbortError') return;
          setError('无法连接 LangGraph，请确认已启动（默认 http://localhost:2024，backend 目录执行 make run）');
          updateAssistant((m) => ({ ...m, content: m.content || '（连接失败）' }));
        })
        .finally(() => {
          setStreaming(false);
          abortRef.current = null;
        });
    },
    [streaming],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { messages, streaming, error, send, stop };
}
