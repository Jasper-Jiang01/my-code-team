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

  // 流式 token 增量缓冲 + rAF 合并刷新，避免每个 token 都触发
  // 一次完整的 messages 状态更新导致长会话卡顿。
  const pendingTokensRef = useRef<string>('');
  const rafIdRef = useRef<number | null>(null);

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

      // 将 token 增量累加到缓冲，在一帧内合并为一次状态更新
      const flushTokens = () => {
        rafIdRef.current = null;
        const delta = pendingTokensRef.current;
        pendingTokensRef.current = '';
        if (delta) {
          updateAssistant((m) => ({ ...m, content: m.content + delta }));
        }
      };
      const scheduleTokenFlush = () => {
        if (rafIdRef.current !== null) return;
        rafIdRef.current = requestAnimationFrame(flushTokens);
      };

      const onEvent = (event: SSEEvent) => {
        switch (event.type) {
          case 'token':
            // 先写入缓冲，下一帧统一刷新，避免逐 token setState
            pendingTokensRef.current += event.content;
            scheduleTokenFlush();
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

      streamChat(
        { message: trimmed, session_id: sessionRef.current },
        onEvent,
        controller.signal,
        // 线程创建/复用后立即保存 session_id，避免用户中断流式
        // 时丢失会话上下文（此前仅在 done 事件才保存）
        (threadId) => {
          sessionRef.current = threadId;
        },
      )
        .catch((err: unknown) => {
          if ((err as Error)?.name === 'AbortError') return;
          setError('无法连接 LangGraph，请确认已启动（默认 http://localhost:2024，backend 目录执行 make run）');
          updateAssistant((m) => ({ ...m, content: m.content || '（连接失败）' }));
        })
        .finally(() => {
          // 取消可能挂起的 rAF，避免 abort 后残留一次空刷新
          if (rafIdRef.current !== null) {
            cancelAnimationFrame(rafIdRef.current);
            rafIdRef.current = null;
          }
          // 刷新缓冲区剩余 token
          if (pendingTokensRef.current) {
            const delta = pendingTokensRef.current;
            pendingTokensRef.current = '';
            updateAssistant((m) => ({ ...m, content: m.content + delta }));
          }
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
