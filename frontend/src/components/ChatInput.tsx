import { useRef, useState } from 'react';

interface Props {
  disabled: boolean;
  streaming: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}

/** 输入框：悬浮卡片式（仿 DeepSeek），Enter 发送、Shift+Enter 换行，流式中可中断。 */
export function ChatInput({ disabled, streaming, onSend, onStop }: Props) {
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = () => {
    if (!value.trim() || disabled) return;
    onSend(value);
    setValue('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setValue(e.target.value);
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  };

  return (
    <div className="chat-input">
      <textarea
        ref={textareaRef}
        value={value}
        placeholder="给 CodePilot 发送消息"
        rows={1}
        onChange={handleInput}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
          }
        }}
      />
      <div className="chat-input-toolbar">
        {streaming ? (
          <button className="btn-stop" onClick={onStop} aria-label="停止生成">
            <span className="stop-icon" />
          </button>
        ) : (
          <button className="btn-send" onClick={handleSend} disabled={disabled || !value.trim()} aria-label="发送">
            ↑
          </button>
        )}
      </div>
    </div>
  );
}
