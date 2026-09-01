import { useEffect, useRef, useState } from 'react';
import { ChatInput } from '../../components/ChatInput';
import { MessageBubble } from '../../components/MessageBubble';
import { Sidebar } from '../../components/Sidebar';
import { useChat } from '../../hooks/useChat';

/** 聊天页面：侧边栏 + 消息列表自动滚底 + 流式渲染 + 错误提示（DeepSeek 风格）。 */
export function ChatPage() {
  const { messages, streaming, error, send, stop } = useChat();
  const bottomRef = useRef<HTMLDivElement>(null);
  // 移动端（<= 768px）侧边栏以抽屉形式展示，默认收起
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleNewChat = () => {
    window.location.reload();
  };

  return (
    <div className="app-shell">
      <Sidebar onNewChat={handleNewChat} open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="chat-window">
        <header className="chat-header">
          <button className="btn-menu" onClick={() => setSidebarOpen(true)} aria-label="打开会话列表">
            ☰
          </button>
          <h1>CodePilot</h1>
          <span className="subtitle">AI 编码副驾驶 · React + LangGraph</span>
        </header>

        <div className="messages">
          {messages.length === 0 && (
            <div className="empty-hint">
              <div className="empty-logo">C</div>
              <div className="empty-title">今天能帮你做点什么？</div>
              <div className="empty-examples">
                试着问：帮我找一个 debounce 的实现示例
                <br />
                或者：review 这段代码：var a = 1; if (a == '1') console.log('ok')
              </div>
            </div>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}
          {streaming && (
            <div className="streaming-hint">
              <span className="dot-flashing" />
              思考中…
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {error && <div className="error-banner">{error}</div>}

        <div className="chat-input-wrap">
          <ChatInput disabled={streaming} streaming={streaming} onSend={send} onStop={stop} />
          <div className="disclaimer">内容由 AI 生成，请仔细甄别</div>
        </div>
      </div>
    </div>
  );
}
