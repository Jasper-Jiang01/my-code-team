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

/** 聊天请求：message 作为工作流 userMessage（纯文本） */
export interface ChatRequest {
  message: string;
  session_id?: string;
  scope?: string;
}

/** 人机确认恢复载荷，对应 Command(resume=...) */
export interface ResumePayload {
  approved: boolean;
  comment?: string;
}

export interface InterruptInfo {
  prompt: string;
  reason?: string;
}

/** UI 事件载荷（由 LangGraph SSE 映射而来） */
export type SSEEvent =
  | { type: 'token'; content: string }
  | { type: 'tool_call'; name: string; args: Record<string, unknown> }
  | { type: 'tool_result'; name: string; result: string }
  | { type: 'interrupt'; prompt: string; reason?: string }
  | { type: 'done'; session_id: string }
  | { type: 'error'; code: string; message: string };
