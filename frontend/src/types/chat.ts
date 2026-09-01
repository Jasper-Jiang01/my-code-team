/**
 * 前后端共享契约类型 —— 与 backend/app/api/chat.py 的模型一一对应。
 * 后端为契约源头，接口增多后替换为 OpenAPI codegen。
 */

export type MessageRole = 'user' | 'assistant';

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  /** Agent 工具调用过程展示（可选） */
  toolTrace?: ToolTrace[];
}

export interface ToolTrace {
  name: string;
  args?: Record<string, unknown>;
  result?: string;
}

/** POST /api/chat 请求体 */
export interface ChatRequest {
  message: string;
  session_id?: string;
}

/** SSE 事件载荷（event 类型 → data 结构） */
export type SSEEvent =
  | { type: 'token'; content: string }
  | { type: 'tool_call'; name: string; args: Record<string, unknown> }
  | { type: 'tool_result'; name: string; result: string }
  | { type: 'done'; session_id: string }
  | { type: 'error'; code: string; message: string };
