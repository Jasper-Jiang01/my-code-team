/**
 * 前后端共享契约类型 —— 前端对接 LangGraph Platform（默认 :2024）。
 */

export type MessageRole = 'user' | 'assistant';

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  /** Agent 节点 / 工具调用过程展示（可选） */
  toolTrace?: ToolTrace[];
}

export interface ToolTrace {
  name: string;
  args?: Record<string, unknown>;
  result?: string;
}

/** 聊天请求：message 会作为工作流 goal */
export interface ChatRequest {
  message: string;
  session_id?: string;
  scope?: string;
}

/** UI 事件载荷（由 LangGraph SSE 映射而来） */
export type SSEEvent =
  | { type: 'token'; content: string }
  | { type: 'tool_call'; name: string; args: Record<string, unknown> }
  | { type: 'tool_result'; name: string; result: string }
  | { type: 'done'; session_id: string }
  | { type: 'error'; code: string; message: string };
