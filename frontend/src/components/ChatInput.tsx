import { memo, useEffect, useRef, useState } from 'react';

interface Props {
  disabled: boolean;
  streaming: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  /** 挂载后自动聚焦（欢迎屏 Hero 中的输入框使用） */
  autoFocus?: boolean;
}

/** 输入框：Enter 发送、Shift+Enter 换行，流式中可中断。 */
export const ChatInput = memo(function ChatInput({
  disabled,
  streaming,
  onSend,
  onStop,
  autoFocus = false,
}: Props) {
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (autoFocus) textareaRef.current?.focus();
  }, [autoFocus]);

  const handleSend = () => {
    if (!value.trim() || disabled) return;
    onSend(value);
    setValue('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
  };

  const handleInput = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    setValue(event.target.value);
    event.target.style.height = 'auto';
    event.target.style.height = `${Math.min(event.target.scrollHeight, 200)}px`;
  };

  return (
    <div className="chat-input">
      <textarea
        ref={textareaRef}
        value={value}
        placeholder="给 CodePilot 发送消息"
        rows={1}
        onChange={handleInput}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            handleSend();
          }
        }}
      />
      <div className="chat-input-toolbar">
        <span className="input-hint">Enter 发送 · Shift + Enter 换行</span>
        {streaming ? (
          <button type="button" className="btn-stop" onClick={onStop} aria-label="停止生成">
            <span className="stop-icon" />
          </button>
        ) : (
          <button
            type="button"
            className="btn-send"
            onClick={handleSend}
            disabled={disabled || !value.trim()}
            aria-label="发送"
          >
            ↑
          </button>
        )}
      </div>
    </div>
  );
});
