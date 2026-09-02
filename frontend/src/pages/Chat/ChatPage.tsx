import { useEffect, useRef, useState } from 'react';
import { ChatInput } from '../../components/ChatInput';
import { MessageBubble } from '../../components/MessageBubble';
import { Sidebar } from '../../components/Sidebar';
import { useChat } from '../../hooks/useChat';

/** 聊天页面：侧边栏 + 消息列表自动滚底 + 流式渲染 + 错误提示（DeepSeek 风格）。 */
export function ChatPage() {
  const { messages, streaming, error, pendingInterrupt, send, resume, stop } = useChat();
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  // 是否在本次流式开始前用户处于"接近底部"的位置；若用户主动上滑查看
  // 历史消息，则不强行拉回底部
  const [autoScroll, setAutoScroll] = useState(true);
  // 移动端（<= 768px）侧边栏以抽屉形式展示，默认收起
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // 滚动到底部的函数（仅在 autoScroll 为 true 时执行）
  const scrollToBottom = () => {
    if (!autoScroll) return;
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  // messages 更新时滚动到底部（但受 autoScroll 控制）
  useEffect(() => {
    scrollToBottom();
  }, [messages, autoScroll]);

  // 检测用户是否主动滚动离开底部
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = container;
      // 距底部小于 120px 视为"接近底部"
      const nearBottom = scrollHeight - scrollTop - clientHeight < 120;
      setAutoScroll(nearBottom);
    };
    container.addEventListener('scroll', handleScroll, { passive: true });
    return () => container.removeEventListener('scroll', handleScroll);
  }, []);

  // 流式开始时重置 autoScroll（用户发送新消息时默认回到跟随模式）
  useEffect(() => {
    if (streaming) setAutoScroll(true);
  }, [streaming]);

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

        <div className="messages" ref={scrollContainerRef}>
          {messages.length === 0 && (
            <div className="empty-hint">
              <div className="empty-logo">C</div>
              <div className="empty-title">今天能帮你做点什么？</div>
              <div className="empty-examples">
                试着问：经营周报点击率口径怎么定
                <br />
                或者：做一个商家供给冷启动 Demo
              </div>
            </div>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}
          {streaming && (
            <div className="streaming-hint">
              <span className="dot-flashing" />
              处理中…
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {error && <div className="error-banner">{error}</div>}

        {pendingInterrupt && !streaming && (
          <div className="interrupt-banner">
            <div className="interrupt-prompt">{pendingInterrupt.prompt}</div>
            <div className="interrupt-actions">
              <button
                type="button"
                className="btn-approve"
                onClick={() => resume({ approved: true, comment: 'UI 确认通过' })}
              >
                通过并继续
              </button>
              <button
                type="button"
                className="btn-reject"
                onClick={() => resume({ approved: false, comment: 'UI 驳回' })}
              >
                驳回
              </button>
            </div>
          </div>
        )}

        <div className="chat-input-wrap">
          <ChatInput
            disabled={streaming || !!pendingInterrupt}
            streaming={streaming}
            onSend={send}
            onStop={stop}
          />
          <div className="disclaimer">内容由 AI 生成，请仔细甄别</div>
        </div>
      </div>
    </div>
  );
}
