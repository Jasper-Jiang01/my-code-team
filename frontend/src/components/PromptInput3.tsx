import { useEffect, useRef, useState, type ChangeEvent, type KeyboardEvent } from 'react';
import {
  ArrowUp,
  BarChart3,
  Code2,
  FileText,
  History,
  ListChecks,
  Paperclip,
  Square,
  X,
} from 'lucide-react';

interface Attachment {
  name: string;
  size: string;
}

interface PromptInput3Props {
  disabled: boolean;
  streaming: boolean;
  onSend: (text: string, intent?: string) => void;
  onStop: () => void;
  autoFocus?: boolean;
  recentPrompts?: string[];
}

// 带 intent 的项点击后直接发送并跳过意图分类，直接进入对应子图
const SUGGESTIONS = [
  {
    label: '数据分析专家',
    prompt: '我想进行数据分析，请介绍你的数据分析能力，并告诉我如何提供数据文件开始分析',
    icon: BarChart3,
    intent: 'data_analysis',
  },
  {
    label: '分析经营数据',
    prompt: '请帮我梳理经营数据分析的关键指标、口径和分析框架',
    icon: BarChart3,
  },
  {
    label: '代码审查',
    prompt: "review 这段代码：var a = 1; if (a == '1') console.log('ok')",
    icon: Code2,
  },
  {
    label: '整理需求方案',
    prompt: '请帮我梳理这个需求的目标、范围、验收标准和实施步骤',
    icon: ListChecks,
  },
] as const;

const MAX_COMPOSER_HEIGHT = 200;

function formatFileSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * React Bits Pro Prompt Input 3 adapted for CodePilot's welcome state.
 * The composer delegates sending and cancellation to the existing chat workflow,
 * while suggestion chips and local recent-session titles make the empty state actionable.
 */
export function PromptInput3({
  disabled,
  streaming,
  onSend,
  onStop,
  autoFocus = false,
  recentPrompts = [],
}: PromptInput3Props) {
  const [value, setValue] = useState('');
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (autoFocus) textareaRef.current?.focus();
  }, [autoFocus]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    const resize = () => {
      textarea.style.height = 'auto';
      textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_COMPOSER_HEIGHT)}px`;
    };

    resize();
    const frame = requestAnimationFrame(resize);
    const observer = new ResizeObserver(resize);
    observer.observe(textarea);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [value]);

  const canSend = value.trim().length > 0 && !disabled && !streaming;
  const history = Array.from(
    new Set(recentPrompts.map((prompt) => prompt.trim()).filter(Boolean)),
  ).slice(0, 3);

  const focusWithValue = (nextValue: string) => {
    setValue(nextValue);
    requestAnimationFrame(() => textareaRef.current?.focus({ preventScroll: true }));
  };

  const submit = () => {
    if (!canSend) return;
    onSend(value.trim());
    setValue('');
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  const handleFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(event.target.files ?? []);
    if (!selectedFiles.length) return;

    setAttachments((current) => [
      ...current,
      ...selectedFiles.map((file) => ({ name: file.name, size: formatFileSize(file.size) })),
    ]);
    // Reset so selecting the same file again still triggers a change event.
    event.target.value = '';
  };

  return (
    <section className="prompt-input3" aria-label="开始新的 CodePilot 对话">
      <div className="prompt-input3__heading">
        <span className="prompt-input3__eyebrow">CODEPILOT WORKSPACE</span>
        <h2>今天想让 CodePilot 帮你完成什么？</h2>
        <p>描述目标，或从一个常用任务开始。</p>
      </div>

      <div className="prompt-input3__composer">
        {attachments.length > 0 && (
          <div className="prompt-input3__attachments" aria-label="已选择的参考附件">
            {attachments.map((file, index) => (
              <span key={`${file.name}-${index}`} className="prompt-input3__attachment">
                <Paperclip aria-hidden="true" />
                <span className="prompt-input3__attachment-name">{file.name}</span>
                <span className="prompt-input3__attachment-size">{file.size}</span>
                <button
                  type="button"
                  aria-label={`移除 ${file.name}`}
                  onClick={() => setAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                >
                  <X aria-hidden="true" />
                </button>
              </span>
            ))}
          </div>
        )}

        <textarea
          ref={textareaRef}
          rows={1}
          value={value}
          disabled={disabled && !streaming}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="例如：帮我为本周经营复盘整理数据分析框架"
          aria-label="给 CodePilot 发送消息"
        />

        <div className="prompt-input3__toolbar">
          <input
            ref={fileInputRef}
            className="prompt-input3__file-input"
            type="file"
            multiple
            onChange={handleFiles}
            tabIndex={-1}
            aria-hidden="true"
          />
          <button
            type="button"
            className="prompt-input3__icon-button"
            aria-label="添加本地参考附件"
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled && !streaming}
          >
            <Paperclip aria-hidden="true" />
          </button>
          <span className="prompt-input3__hint">Enter 发送 · Shift + Enter 换行</span>
          {streaming ? (
            <button
              type="button"
              className="prompt-input3__send-button prompt-input3__send-button--stop"
              onClick={onStop}
              aria-label="停止生成"
            >
              <Square aria-hidden="true" />
            </button>
          ) : (
            <button
              type="button"
              className="prompt-input3__send-button"
              disabled={!canSend}
              onClick={submit}
              aria-label="发送"
            >
              <ArrowUp aria-hidden="true" />
            </button>
          )}
        </div>

      </div>

      <div className="prompt-input3__suggestions" aria-label="常用任务">
        {SUGGESTIONS.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.label}
              type="button"
              onClick={() => {
                // 带 intent 的快捷入口（如“数据分析专家”）直接发送，跳过意图分类进入子图；
                // 其余保持原行为：填充输入框待用户编辑
                if ('intent' in item && item.intent) {
                  onSend(item.prompt, item.intent);
                } else {
                  focusWithValue(item.prompt);
                }
              }}
            >
              <Icon aria-hidden="true" />
              <span>{item.label}</span>
            </button>
          );
        })}
      </div>

      {history.length > 0 && (
        <div className="prompt-input3__history" aria-label="最近使用的提示词">
          <span className="prompt-input3__history-label">
            <History aria-hidden="true" />
            最近使用
          </span>
          <div>
            {history.map((prompt) => (
              <button key={prompt} type="button" onClick={() => focusWithValue(prompt)} title={prompt}>
                <FileText aria-hidden="true" />
                <span>{prompt}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {attachments.length > 0 && (
        <p className="prompt-input3__attachment-note" aria-live="polite">
          附件仅在本地显示；当前对话服务会发送你的文本描述。
        </p>
      )}
    </section>
  );
}
