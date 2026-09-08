/**
 * 会话持久化：localStorage 保存会话索引与消息体。
 *
 * 后端 checkpointer 已持久化 thread 状态（续聊上下文在服务端），
 * 前端此模块补齐"消息展示层"的恢复能力：刷新页面后历史会话可回看、
 * 可继续。存储分两层：
 * - 索引（codepilot.sessions.v1）：threadId + 标题 + 更新时间，限量 30 个
 * - 消息体（codepilot.msgs.<threadId>）：每会话最多 200 条，防止撑爆配额
 */
import type { ChatMessage } from '../types/chat';

export interface StoredSession {
  threadId: string;
  title: string;
  updatedAt: number;
}

const INDEX_KEY = 'codepilot.sessions.v1';
const MSG_PREFIX = 'codepilot.msgs.';
const MAX_SESSIONS = 30;
const MAX_MESSAGES = 200;

function safeGetJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function safeSetJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 配额满 / 隐私模式：持久化是尽力而为，不阻塞聊天主链路
  }
}

export function loadSessionIndex(): StoredSession[] {
  const list = safeGetJson<StoredSession[]>(INDEX_KEY, []);
  return Array.isArray(list) ? list.filter((s) => typeof s?.threadId === 'string') : [];
}

function saveSessionIndex(sessions: StoredSession[]): void {
  safeSetJson(INDEX_KEY, sessions.slice(0, MAX_SESSIONS));
}

/** 新增或置顶一个会话；标题取首条用户消息前 40 字符。返回更新后的索引。 */
export function upsertSession(threadId: string, title: string): StoredSession[] {
  const list = loadSessionIndex().filter((s) => s.threadId !== threadId);
  const next: StoredSession[] = [
    { threadId, title: title.slice(0, 40) || '新会话', updatedAt: Date.now() },
    ...list,
  ];
  saveSessionIndex(next);
  return next;
}

export function loadMessages(threadId: string): ChatMessage[] {
  const list = safeGetJson<ChatMessage[]>(MSG_PREFIX + threadId, []);
  return Array.isArray(list) ? list.filter((m) => typeof m?.id === 'string') : [];
}

export function persistMessages(threadId: string, messages: ChatMessage[]): void {
  if (!threadId) return;
  safeSetJson(MSG_PREFIX + threadId, messages.slice(-MAX_MESSAGES));
  // 同步刷新索引里的更新时间（保持列表按最近使用排序）
  const list = loadSessionIndex();
  const idx = list.findIndex((s) => s.threadId === threadId);
  if (idx > 0) {
    const [hit] = list.splice(idx, 1);
    saveSessionIndex([{ ...hit, updatedAt: Date.now() }, ...list]);
  }
}

/** 删除会话（索引项 + 消息体）。返回更新后的索引。 */
export function removeSession(threadId: string): StoredSession[] {
  try {
    localStorage.removeItem(MSG_PREFIX + threadId);
  } catch {
    // ignore
  }
  const next = loadSessionIndex().filter((s) => s.threadId !== threadId);
  saveSessionIndex(next);
  return next;
}
