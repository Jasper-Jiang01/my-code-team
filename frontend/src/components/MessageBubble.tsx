import { memo } from 'react';
import type { ChatMessage } from '../types/chat';

/** 单条消息气泡：user 右侧、assistant 左侧；assistant 支持工具轨迹折叠展示。 */
export const MessageBubble = memo(function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';
  return (
    <div className={`msg-row ${isUser ? 'msg-user' : 'msg-assistant'}`}>
      <div className="avatar">{isUser ? '你' : 'C'}</div>
      <div className="bubble">
        {message.toolTrace && message.toolTrace.length > 0 && (
          <details className="tool-trace">
            <summary>🔧 工具调用（{message.toolTrace.length}）</summary>
            {message.toolTrace.map((t, i) => (
              <pre key={i}>
                {t.name}({JSON.stringify(t.args ?? {})}
                {t.result ? `) → ${t.result.slice(0, 200)}` : ') …'}
              </pre>
            ))}
          </details>
        )}
        <div className="content">{message.content || '…'}</div>
      </div>
    </div>
  );
});
