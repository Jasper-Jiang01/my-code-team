import { memo } from 'react';
import type { ChatMessage } from '../types/chat';

/** 单条消息气泡：仅展示用户输入或助手的最终答复。 */
export const MessageBubble = memo(function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';
  // 最终答复尚未返回时仅显示页面底部状态，不渲染空白消息气泡。
  if (!isUser && !message.content) return null;
  return (
    <div className={`msg-row ${isUser ? 'msg-user' : 'msg-assistant'}`}>
      <div className="avatar">{isUser ? '你' : 'C'}</div>
      <div className="bubble">
        <div className="content">{message.content}</div>
      </div>
    </div>
  );
});
