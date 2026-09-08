import { useCallback, useEffect, useRef, useState } from 'react';
import { resumeChat, streamChat } from '../lib/api-client';
import {
  loadMessages,
  loadSessionIndex,
  persistMessages,
  removeSession,
  upsertSession,
  type StoredSession,
} from '../lib/session-store';
import type { ChatMessage, ChatRequest, PendingInterrupt, SSEEvent } from '../types/chat';

function uid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

export interface UseChatResult {
  messages: ChatMessage[];
  streaming: boolean;
  hasReceivedToken: boolean;
  error: string | null;
  pendingInterrupt: PendingInterrupt | null;
  sessions: StoredSession[];
  send: (text: string, intent?: string) => void;
  resume: (approved: boolean, comment?: string) => void;
  stop: () => void;
  reset: () => void;
  openSession: (threadId: string) => void;
  deleteSession: (threadId: string) => void;
}

/** 最小聊天状态管理：发送、流式聚合、停止和本地会话记录。 */
export function useChat(): UseChatResult {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [hasReceivedToken, setHasReceivedToken] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingInterrupt, setPendingInterrupt] = useState<PendingInterrupt | null>(null);
  const [sessions, setSessions] = useState<StoredSession[]>(() => loadSessionIndex());
  const sessionRef = useRef<string | undefined>(undefined);
  const abortRef = useRef<AbortController | null>(null);
  const assistantIdRef = useRef<string | null>(null);
  const pendingTokensRef = useRef('');
  const rafIdRef = useRef<number | null>(null);
  const messagesRef = useRef<ChatMessage[]>([]);

  const applyMessages = useCallback((updater: (previous: ChatMessage[]) => ChatMessage[]) => {
    setMessages((previous) => {
      const next = updater(previous);
      messagesRef.current = next;
      return next;
    });
  }, []);

  useEffect(
    () => () => {
      abortRef.current?.abort();
      if (rafIdRef.current !== null) cancelAnimationFrame(rafIdRef.current);
    },
    [],
  );

  const updateAssistant = useCallback(
    (updater: (message: ChatMessage) => ChatMessage) => {
      const assistantId = assistantIdRef.current;
      if (!assistantId) return;
      applyMessages((previous) =>
        previous.map((message) => (message.id === assistantId ? updater(message) : message)),
      );
    },
    [applyMessages],
  );

  const flushTokens = useCallback(() => {
    rafIdRef.current = null;
    const delta = pendingTokensRef.current;
    pendingTokensRef.current = '';
    if (delta) updateAssistant((message) => ({ ...message, content: message.content + delta }));
  }, [updateAssistant]);

  const scheduleTokenFlush = useCallback(() => {
    if (rafIdRef.current === null) rafIdRef.current = requestAnimationFrame(flushTokens);
  }, [flushTokens]);

  const onEvent = useCallback(
    (event: SSEEvent) => {
      switch (event.type) {
        case 'token':
          setHasReceivedToken(true);
          pendingTokensRef.current += event.content;
          scheduleTokenFlush();
          break;
        case 'interrupt':
          // HumanInTheLoop：写文件等敏感操作等待审批，流挂起但连接仍保持
          setPendingInterrupt({ prompt: event.prompt, reason: event.reason });
          break;
        case 'session':
        case 'done':
          sessionRef.current = event.session_id;
          break;
        case 'error':
          setError(event.message);
          updateAssistant((message) => ({
            ...message,
            content: message.content || '（生成失败，请重试）',
          }));
          break;
      }
    },
    [scheduleTokenFlush, updateAssistant],
  );

  const startStream = useCallback(
    (request: () => Promise<void>) => {
      setStreaming(true);
      setHasReceivedToken(false);
      pendingTokensRef.current = '';
      const controller = new AbortController();
      abortRef.current = controller;
      request()
        .catch((reason: unknown) => {
          if ((reason as Error)?.name === 'AbortError') return;
          setError('无法连接后端，请确认已启动 FastAPI（默认 http://localhost:8000）');
          updateAssistant((message) => ({ ...message, content: message.content || '（连接失败）' }));
        })
        .finally(() => {
          // 审批挂起时保持 pendingInterrupt，不视为流结束失败
          if (rafIdRef.current !== null) {
            cancelAnimationFrame(rafIdRef.current);
            rafIdRef.current = null;
          }
          flushTokens();
          setStreaming(false);
          abortRef.current = null;
          const threadId = sessionRef.current;
          if (threadId) {
            persistMessages(threadId, messagesRef.current);
            setSessions(loadSessionIndex());
          }
        });
    },
    [flushTokens, updateAssistant],
  );

  const send = useCallback(
    (text: string, intent?: string) => {
      const trimmed = text.trim();
      if (!trimmed || streaming) return;
      setError(null);
      setPendingInterrupt(null);

      const userMessage: ChatMessage = { id: uid(), role: 'user', content: trimmed };
      const assistantId = uid();
      assistantIdRef.current = assistantId;
      applyMessages((previous) => [
        ...previous,
        userMessage,
        { id: assistantId, role: 'assistant', content: '' },
      ]);

      // intent 来自快捷入口（如“数据分析专家”卡片）：跳过后端意图分类，直接进入指定子图
      const payload: ChatRequest = { message: trimmed, session_id: sessionRef.current };
      if (intent) payload.intent = intent;

      startStream(() =>
        streamChat(
          payload,
          onEvent,
          abortRef.current?.signal,
          (threadId) => {
            if (!sessionRef.current) setSessions(upsertSession(threadId, trimmed));
            sessionRef.current = threadId;
          },
        ),
      );
    },
    [applyMessages, onEvent, startStream, streaming],
  );

  /** 审批挂起的 HumanInTheLoop 中断：approve / reject 后继续接收回复。 */
  const resume = useCallback(
    (approved: boolean, comment?: string) => {
      const threadId = sessionRef.current;
      if (!threadId || streaming || !pendingInterrupt) return;
      setPendingInterrupt(null);
      setError(null);

      const assistantId = uid();
      assistantIdRef.current = assistantId;
      applyMessages((previous) => [...previous, { id: assistantId, role: 'assistant', content: '' }]);

      startStream(() =>
        resumeChat(
          { session_id: threadId, approved, comment },
          onEvent,
          abortRef.current?.signal,
        ),
      );
    },
    [applyMessages, onEvent, pendingInterrupt, startStream, streaming],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    sessionRef.current = undefined;
    assistantIdRef.current = null;
    pendingTokensRef.current = '';
    setHasReceivedToken(false);
    setPendingInterrupt(null);
    messagesRef.current = [];
    if (rafIdRef.current !== null) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
    setMessages([]);
    setStreaming(false);
    setError(null);
  }, []);

  const openSession = useCallback(
    (threadId: string) => {
      if (streaming) return;
      const restored = loadMessages(threadId);
      if (!restored.length) return;
      sessionRef.current = threadId;
      assistantIdRef.current = null;
      messagesRef.current = restored;
      setMessages(restored);
      setError(null);
    },
    [streaming],
  );

  const deleteSession = useCallback(
    (threadId: string) => {
      setSessions(removeSession(threadId));
      if (sessionRef.current === threadId) reset();
    },
    [reset],
  );

  return {
    messages,
    streaming,
    hasReceivedToken,
    error,
    pendingInterrupt,
    sessions,
    send,
    resume,
    stop,
    reset,
    openSession,
    deleteSession,
  };
}
