/** 前后端共享的最小聊天契约。 */

export type MessageRole = 'user' | 'assistant';

export interface ChatMessage {
  id: string;
  role: MessageRole;
  /** 仅保存面向用户的最终答复，不包含工具或推理过程。 */
  content: string;
}

export interface ChatRequest {
  message: string;
  session_id?: string;
  /** 可选：显式意图（如快捷入口“数据分析专家”传 data_analysis），跳过后端意图分类直接进入对应子图。 */
  intent?: string;
}

export interface ResumeRequest {
  session_id: string;
  approved: boolean;
  comment?: string;
}

/** HumanInTheLoop 审批请求（写文件等敏感操作执行前挂起）。 */
export interface PendingInterrupt {
  prompt: string;
  reason: string | null;
}

export type SSEEvent =
  | { type: 'session'; session_id: string }
  | { type: 'token'; content: string }
  | { type: 'interrupt'; prompt: string; reason: string | null }
  | { type: 'done'; session_id: string }
  | { type: 'error'; code: string; message: string };
