import { useCallback, useRef, useState } from 'react';
import { resumeChat, streamChat } from '../lib/api-client';
import type { ChatMessage, InterruptInfo, ResumePayload, SSEEvent } from '../types/chat';

function uid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

export interface UseChatResult {
  messages: ChatMessage[];
  streaming: boolean;
  error: string | null;
  pendingInterrupt: InterruptInfo | null;
  send: (text: string) => void;
  resume: (payload: ResumePayload) => void;
  stop: () => void;
}

/** 聊天状态管理：流式聚合 token，透出工具调用轨迹与 interrupt 恢复。 */
export function useChat(): UseChatResult {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingInterrupt, setPendingInterrupt] = useState<InterruptInfo | null>(null);
  const sessionRef = useRef<string | undefined>(undefined);
  const abortRef = useRef<AbortController | null>(null);
  const assistantIdRef = useRef<string | null>(null);

  const pendingTokensRef = useRef<string>('');
  const rafIdRef = useRef<number | null>(null);

  const updateAssistant = useCallback((updater: (m: ChatMessage) => ChatMessage) => {
    const assistantId = assistantIdRef.current;
    if (!assistantId) return;
    setMessages((prev) => prev.map((m) => (m.id === assistantId ? updater(m) : m)));
  }, []);

  const flushTokens = useCallback(() => {
    rafIdRef.current = null;
    const delta = pendingTokensRef.current;
    pendingTokensRef.current = '';
    if (delta) {
      updateAssistant((m) => ({ ...m, content: m.content + delta }));
    }
  }, [updateAssistant]);

  const scheduleTokenFlush = useCallback(() => {
    if (rafIdRef.current !== null) return;
    rafIdRef.current = requestAnimationFrame(flushTokens);
  }, [flushTokens]);

  const finishStream = useCallback(() => {
    if (rafIdRef.current !== null) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
    if (pendingTokensRef.current) {
      const delta = pendingTokensRef.current;
      pendingTokensRef.current = '';
      updateAssistant((m) => ({ ...m, content: m.content + delta }));
    }
    setStreaming(false);
    abortRef.current = null;
  }, [updateAssistant]);

  const onEvent = useCallback(
    (event: SSEEvent) => {
      switch (event.type) {
        case 'token':
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
        case 'interrupt':
          setPendingInterrupt({ prompt: event.prompt, reason: event.reason });
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
    },
    [scheduleTokenFlush, updateAssistant],
  );

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || streaming) return;
      setError(null);
      setPendingInterrupt(null);

      const userMsg: ChatMessage = { id: uid(), role: 'user', content: trimmed };
      const assistantId = uid();
      assistantIdRef.current = assistantId;
      const assistantMsg: ChatMessage = { id: assistantId, role: 'assistant', content: '', toolTrace: [] };
      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setStreaming(true);

      const controller = new AbortController();
      abortRef.current = controller;

      streamChat(
        { message: trimmed, session_id: sessionRef.current },
        onEvent,
        controller.signal,
        (threadId) => {
          sessionRef.current = threadId;
        },
      )
        .catch((err: unknown) => {
          if ((err as Error)?.name === 'AbortError') return;
          setError('无法连接 LangGraph，请确认已启动（默认 http://localhost:2024，backend 目录执行 make run）');
          updateAssistant((m) => ({ ...m, content: m.content || '（连接失败）' }));
        })
        .finally(finishStream);
    },
    [streaming, onEvent, updateAssistant, finishStream],
  );

  const resume = useCallback(
    (payload: ResumePayload) => {
      const threadId = sessionRef.current;
      if (!threadId || streaming || !pendingInterrupt) return;
      setError(null);
      setPendingInterrupt(null);
      setStreaming(true);

      const assistantId = assistantIdRef.current ?? uid();
      assistantIdRef.current = assistantId;
      setMessages((prev) => {
        if (prev.some((m) => m.id === assistantId)) {
          return prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: `${m.content}\n（已提交人工确认：${payload.approved ? '通过' : '驳回'}）\n` }
              : m,
          );
        }
        return [
          ...prev,
          {
            id: assistantId,
            role: 'assistant',
            content: `（已提交人工确认：${payload.approved ? '通过' : '驳回'}）\n`,
            toolTrace: [],
          },
        ];
      });

      const controller = new AbortController();
      abortRef.current = controller;

      resumeChat(threadId, payload, onEvent, controller.signal)
        .catch((err: unknown) => {
          if ((err as Error)?.name === 'AbortError') return;
          setError('恢复工作流失败，请确认 thread 仍有效且后端已启动');
          updateAssistant((m) => ({ ...m, content: m.content || '（恢复失败）' }));
        })
        .finally(finishStream);
    },
    [streaming, pendingInterrupt, onEvent, updateAssistant, finishStream],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { messages, streaming, error, pendingInterrupt, send, resume, stop };
}
