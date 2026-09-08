# App Shell 9（CodePilot 适配版）

基于 React Bits Pro `app-shell-9` 骨架，按当前工程结构与后端能力重制的 AI 对话框架：桌面端可折叠会话侧栏 + 顶栏 + 流式消息区 + 全宽 Composer，移动端抽屉导航。

- Type: React Bits Pro block（本地适配版，不通过 registry 安装）
- Category: App Shell
- 原始文档: https://pro.reactbits.dev/docs/app-ui/app-shell/app-shell-9
- 原始 registry item: @reactbits-pro/app-shell-9（需要 license key，本版本不依赖）

## 能力适配说明

原始 Block 是一份带假数据的纯展示组件，本版本逐项对齐 CodePilot 前后端真实能力：

| 原始 Block | 适配后 | 依据 |
| --- | --- | --- |
| ModelMenu 三模型切换 + 侧栏模型九宫格 | 移除；顶栏改为静态 `DeepAgent` 徽标（带在线状态点） | 后端单一模型（`settings.default_model`），`/api/chat` 无模型参数 |
| 硬编码 CHATS / Pinned / QUICK_ACTIONS | `sessions: StoredSession[]`（localStorage 索引）+ 相对时间 + 单条删除 | `src/lib/session-store.ts`、`useChat()` |
| 静态 turns 回放 | `useChat()` 消息流：SSE token 增量渲染、接近底部自动跟随、上滑不打断 | `src/hooks/useChat.ts` |
| —（无中断处理） | interrupt 审批卡（通过并继续 / 驳回），展示 prompt 与 reason | HumanInTheLoop 写操作审批，`POST /api/resume` |
| 仅发送按钮 | 发送 / 停止生成切换（AbortController） | `useChat().stop` |
| Attachment / Search / Mic / Share chat | 移除 | `ChatRequest` 仅接受 `message` 文本，无分享 / 联网 / 语音接口 |
| Tailwind + 明暗双主题 + `--rb-*` token | SCSS + 暗色唯一主题 + 工程设计 Token（`--brand` / `--bg-*` / `--text-l*`） | `src/styles.scss` 设计 Token；工程未安装 Tailwind |
| 英文示例数据与文案 | 中文文案；快捷任务与欢迎页一致 | `PromptInput3` 的 SUGGESTIONS |
| `inert` 属性（React 19 写法） | 移除（折叠态 width:0 + overflow:hidden 已不可达） | 工程使用 React 18.3 |

保留的原始能力：滚动渐隐遮罩（scroll fade）、侧栏折叠/展开的焦点归还、移动端抽屉（backdrop + Escape 关闭 + Tab 焦点圈定）、键盘可访问的会话项。

## Dependencies

```bash
npm install lucide-react
```

组件本身依赖工程内的 `useChat` hook 与 `StoredSession` 类型，无需其他安装。

## Usage

```tsx
import AppShell9 from "./app-shell-9";

// 组件内部通过 useChat() 自管理全部聊天状态（流式、审批、停止、会话索引），
// 直接替换 ChatPage 内容即可；样式（shell9-* 类）追加到 src/styles.scss。
export default function Page() {
  return <AppShell9 />;
}
```

文件放置：`src/components/app-shell-9.tsx`（下述 import 路径按此书写）。

## Source

```tsx
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type Ref,
  type RefObject,
} from "react";
import {
  ArrowUp,
  ArrowUpRight,
  BarChart3,
  Check,
  Code2,
  LayoutTemplate,
  ListChecks,
  Menu,
  MessageSquare,
  PanelLeft,
  Plus,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { useChat } from "../../hooks/useChat";
import type { StoredSession } from "../../lib/session-store";

// 快捷任务与欢迎页 PromptInput3 保持一致
const SUGGESTIONS = [
  { label: "设计数据工作台", prompt: "帮我设计并生成一个数据工作台的页面原型", icon: LayoutTemplate },
  { label: "分析经营数据", prompt: "请帮我梳理经营数据分析的关键指标、口径和分析框架", icon: BarChart3 },
  { label: "代码审查", prompt: "review 这段代码：var a = 1; if (a == '1') console.log('ok')", icon: Code2 },
  { label: "整理需求方案", prompt: "请帮我梳理这个需求的目标、范围、验收标准和实施步骤", icon: ListChecks },
] as const;

const MAX_COMPOSER_HEIGHT = 160;

const cx = (...c: (string | false | null | undefined)[]) =>
  c.filter(Boolean).join(" ");

/** 会话索引只有 updatedAt，用相对时间表达「最近」 */
function relativeTime(ts: number): string {
  const diff = Date.now() - ts;
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} 天前`;
  return new Date(ts).toLocaleDateString("zh-CN");
}

/** 检测滚动容器上下边缘，驱动渐隐遮罩显隐 */
function useScrollFade<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [edges, setEdges] = useState({ start: false, end: false });

  const update = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const { scrollTop, scrollHeight, clientHeight } = el;
    setEdges({
      start: scrollTop > 1,
      end: Math.ceil(scrollTop + clientHeight) < scrollHeight - 1,
    });
  }, []);

  useEffect(() => {
    update();
    const el = ref.current;
    const view = el?.ownerDocument.defaultView;
    if (!el || !view?.ResizeObserver) return;
    const observer = new view.ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, [update]);

  return { ref, edges, onScroll: update };
}

function Overline({ children }: { children: ReactNode }) {
  return <p className="shell9-overline">{children}</p>;
}

function Composer({
  draft,
  onDraft,
  streaming,
  onSend,
  onStop,
  inputRef,
}: {
  draft: string;
  onDraft: (value: string) => void;
  streaming: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  inputRef: RefObject<HTMLTextAreaElement | null>;
}) {
  const canSend = draft.trim().length > 0 && !streaming;

  const submit = () => {
    const text = draft.trim();
    if (!text || streaming) return;
    onSend(text);
    onDraft("");
  };

  return (
    <div className="shell9-composer">
      <textarea
        ref={inputRef}
        rows={1}
        value={draft}
        aria-label="给 CodePilot 发送消息"
        placeholder="给 CodePilot 发送消息"
        onChange={(event) => {
          onDraft(event.target.value);
          const el = event.target;
          el.style.height = "auto";
          el.style.height = `${Math.min(el.scrollHeight, MAX_COMPOSER_HEIGHT)}px`;
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
      />
      <div className="shell9-composer-bar">
        <span className="shell9-hint">Enter 发送 · Shift + Enter 换行</span>
        {streaming ? (
          <button
            type="button"
            className="shell9-stop"
            onClick={onStop}
            aria-label="停止生成"
          >
            <Square aria-hidden="true" />
          </button>
        ) : (
          <button
            type="button"
            className="shell9-send"
            onClick={submit}
            disabled={!canSend}
            aria-label="发送"
          >
            <ArrowUp aria-hidden="true" />
          </button>
        )}
      </div>
    </div>
  );
}

function SidebarBody({
  sessions,
  activeThreadId,
  onOpenSession,
  onDeleteSession,
  onNew,
  onClose,
  onCollapse,
  collapseRef,
}: {
  sessions: StoredSession[];
  activeThreadId: string | null;
  onOpenSession: (threadId: string) => void;
  onDeleteSession: (threadId: string) => void;
  onNew: () => void;
  onClose?: () => void;
  onCollapse?: () => void;
  collapseRef?: Ref<HTMLButtonElement>;
}) {
  const nav = useScrollFade<HTMLDivElement>();

  return (
    <>
      <div className="shell9-brand">
        <span className="shell9-logo-mark" aria-hidden="true">
          C
        </span>
        <p className="shell9-brand-name">CodePilot</p>
        {onClose ? (
          <button
            type="button"
            className="shell9-iconbtn"
            onClick={onClose}
            aria-label="关闭导航"
          >
            <X aria-hidden="true" />
          </button>
        ) : (
          <button
            ref={collapseRef}
            type="button"
            className="shell9-iconbtn shell9-collapse"
            onClick={onCollapse}
            aria-label="折叠侧边栏"
            aria-expanded={true}
          >
            <PanelLeft aria-hidden="true" />
          </button>
        )}
      </div>

      <div className="shell9-new-wrap">
        <button type="button" className="shell9-new-chat" onClick={onNew}>
          <Plus aria-hidden="true" />
          新建对话
        </button>
      </div>

      <div className="shell9-scroll-wrap">
        <div ref={nav.ref} onScroll={nav.onScroll} className="shell9-scroll">
          <Overline>最近会话</Overline>
          {sessions.length ? (
            <ul className="shell9-sessions">
              {sessions.map((session) => (
                <li key={session.threadId}>
                  <div
                    role="button"
                    tabIndex={0}
                    className={cx(
                      "shell9-session",
                      session.threadId === activeThreadId && "is-active",
                    )}
                    title={session.title}
                    onClick={() => onOpenSession(session.threadId)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onOpenSession(session.threadId);
                      }
                    }}
                  >
                    <MessageSquare aria-hidden="true" className="shell9-session-icon" />
                    <span className="shell9-session-title">{session.title}</span>
                    <span className="shell9-session-time">
                      {relativeTime(session.updatedAt)}
                    </span>
                    <button
                      type="button"
                      className="shell9-session-delete"
                      aria-label={`删除会话：${session.title}`}
                      title="删除会话"
                      onClick={(event) => {
                        event.stopPropagation();
                        onDeleteSession(session.threadId);
                      }}
                    >
                      <Trash2 aria-hidden="true" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="shell9-empty">暂无历史会话，发送第一条消息开始</p>
          )}
        </div>
        <div
          aria-hidden="true"
          className={cx("shell9-fade shell9-fade--top", nav.edges.start && "is-visible")}
        />
        <div
          aria-hidden="true"
          className={cx("shell9-fade shell9-fade--bottom", nav.edges.end && "is-visible")}
        />
      </div>

      <div className="shell9-user">
        <span className="shell9-user-avatar" aria-hidden="true">
          你
        </span>
        <span className="shell9-user-meta">
          <span className="shell9-user-name">CodePilot Agent</span>
          <span className="shell9-user-status">在线 · 写操作需人工确认</span>
        </span>
      </div>
    </>
  );
}

export default function AppShell9() {
  const {
    messages,
    streaming,
    hasReceivedToken,
    error,
    pendingInterrupt,
    sessions,
    send,
    resume,
    stop,
    reset,
    openSession,
    deleteSession,
  } = useChat();

  const [draft, setDraft] = useState("");
  // 当前会话高亮：useChat 未暴露 threadId，由打开动作本地记录
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  // 桌面端侧栏折叠（宽度动画到 0）
  const [sidebarOpen, setSidebarOpen] = useState(true);
  // 移动端抽屉
  const [navOpen, setNavOpen] = useState(false);
  const [navShown, setNavShown] = useState(false);
  // 接近底部时跟随流式输出，用户上滑查看历史时不强行拉回
  const [autoScroll, setAutoScroll] = useState(true);

  const expandRef = useRef<HTMLButtonElement>(null);
  const collapseRef = useRef<HTMLButtonElement>(null);
  const railFocusRef = useRef(false);
  const menuTriggerRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const thread = useScrollFade<HTMLDivElement>();

  const isWelcome = messages.length === 0;

  // 折叠/展开后把焦点还到对应按钮（键盘可用性）
  useEffect(() => {
    if (!railFocusRef.current) return;
    railFocusRef.current = false;
    const target = sidebarOpen ? collapseRef.current : expandRef.current;
    target?.focus({ preventScroll: true });
  }, [sidebarOpen]);

  const closeNav = useCallback(() => {
    setNavShown(false);
    setNavOpen(false);
    // 桌面端该按钮隐藏，focus 为无操作，不影响
    menuTriggerRef.current?.focus({ preventScroll: true });
  }, []);

  // 移动端抽屉：进入动画 + Escape 关闭 + Tab 焦点圈定
  useEffect(() => {
    if (!navOpen) return;
    const drawer = drawerRef.current;
    const frame = requestAnimationFrame(() => setNavShown(true));

    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeNav();
        return;
      }
      if (event.key !== "Tab" || !drawer) return;
      const nodes = drawer.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), textarea, [role="button"]:not([tabindex="-1"])',
      );
      if (nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus({ preventScroll: true });
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus({ preventScroll: true });
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [navOpen, closeNav]);

  // 流式期间即时跟随（smooth 会不断重启追不上），结束后 smooth 收尾
  const scrollToBottom = useCallback((smooth: boolean) => {
    bottomRef.current?.scrollIntoView({ behavior: smooth ? "smooth" : "auto" });
  }, []);

  useEffect(() => {
    if (autoScroll) scrollToBottom(!streaming);
  }, [messages, autoScroll, streaming, scrollToBottom]);

  useEffect(() => {
    if (streaming) setAutoScroll(true);
  }, [streaming]);

  // 消息容器仅在会话态挂载，需跟随布局切换重绑
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = container;
      const nearBottom = scrollHeight - scrollTop - clientHeight < 120;
      setAutoScroll((prev) => (prev === nearBottom ? prev : nearBottom));
    };
    container.addEventListener("scroll", handleScroll, { passive: true });
    return () => container.removeEventListener("scroll", handleScroll);
  }, [isWelcome]);

  const openChat = useCallback(
    (threadId: string) => {
      openSession(threadId);
      setActiveThreadId(threadId);
      closeNav();
    },
    [openSession, closeNav],
  );

  const startNew = useCallback(() => {
    reset();
    setActiveThreadId(null);
    setDraft("");
    closeNav();
    composerRef.current?.focus({ preventScroll: true });
  }, [reset, closeNav]);

  const submit = useCallback(
    (text: string) => {
      if (!text || streaming) return;
      send(text);
      setDraft("");
    },
    [send, streaming],
  );

  const approve = useCallback(() => resume(true, "UI 确认通过"), [resume]);
  const reject = useCallback(() => resume(false, "UI 驳回"), [resume]);

  const currentTitle =
    sessions.find((session) => session.threadId === activeThreadId)?.title ?? "新对话";

  return (
    <div className="shell9">
      <div className="shell9-frame">
        {/* 桌面侧栏：折叠到 0 宽 + margin 过渡 */}
        <aside className={cx("shell9-aside", !sidebarOpen && "is-collapsed")}>
          <div className="shell9-panel shell9-sidebar">
            <SidebarBody
              sessions={sessions}
              activeThreadId={activeThreadId}
              onOpenSession={openChat}
              onDeleteSession={deleteSession}
              onNew={startNew}
              onCollapse={() => {
                railFocusRef.current = true;
                setSidebarOpen(false);
              }}
              collapseRef={collapseRef}
            />
          </div>
        </aside>

        <div className="shell9-panel shell9-main">
          <header className="shell9-header">
            <button
              ref={menuTriggerRef}
              type="button"
              className="shell9-iconbtn shell9-menu"
              aria-label="打开会话列表"
              aria-expanded={navOpen}
              onClick={() => setNavOpen(true)}
            >
              <Menu aria-hidden="true" />
            </button>

            <div className={cx("shell9-expand", !sidebarOpen && "is-visible")}>
              <button
                ref={expandRef}
                type="button"
                className="shell9-iconbtn"
                aria-label="展开侧边栏"
                aria-expanded={sidebarOpen}
                tabIndex={sidebarOpen ? -1 : undefined}
                onClick={() => {
                  railFocusRef.current = true;
                  setSidebarOpen(true);
                }}
              >
                <PanelLeft aria-hidden="true" />
              </button>
            </div>

            {/* 单一 DeepAgent（服务端 settings.default_model），无模型切换 */}
            <span className="shell9-badge">
              <span className="shell9-badge-dot" aria-hidden="true" />
              DeepAgent
            </span>
            <h1 className="shell9-title">{currentTitle}</h1>
          </header>

          {isWelcome ? (
            <div className="shell9-thread">
              <div className="shell9-welcome-scroll">
                <span
                  className="shell9-logo-mark shell9-logo-mark--lg"
                  aria-hidden="true"
                >
                  C
                </span>
                <h2 className="shell9-welcome-title">
                  今天想让 CodePilot 帮你完成什么？
                </h2>
                <p className="shell9-welcome-sub">描述目标，或从一个常用任务开始。</p>

                <div className="shell9-welcome-composer">
                  <Composer
                    draft={draft}
                    onDraft={setDraft}
                    streaming={streaming}
                    onSend={submit}
                    onStop={stop}
                    inputRef={composerRef}
                  />
                </div>

                <div className="shell9-cards">
                  {SUGGESTIONS.map((item) => {
                    const Icon = item.icon;
                    return (
                      <button
                        key={item.label}
                        type="button"
                        className="shell9-card"
                        onClick={() => {
                          setDraft(item.prompt);
                          composerRef.current?.focus({ preventScroll: true });
                        }}
                      >
                        <Icon aria-hidden="true" />
                        <span>{item.label}</span>
                        <ArrowUpRight aria-hidden="true" className="shell9-card-arrow" />
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          ) : (
            <>
              <div className="shell9-thread">
                <div
                  className="shell9-thread-scroll"
                  ref={(el) => {
                    scrollContainerRef.current = el;
                    thread.ref.current = el;
                  }}
                  onScroll={thread.onScroll}
                >
                  <div className="shell9-thread-inner">
                    {messages.map((message) =>
                      message.role === "user" ? (
                        <div key={message.id} className="shell9-row-user">
                          <p className="shell9-bubble-user">{message.content}</p>
                        </div>
                      ) : message.content ? (
                        <p key={message.id} className="shell9-msg-assistant">
                          {message.content}
                        </p>
                      ) : null,
                    )}
                    {streaming && !hasReceivedToken && (
                      <div className="shell9-thinking" aria-live="polite">
                        <span className="shell9-dot" aria-hidden="true" />
                        正在生成…
                      </div>
                    )}
                    <div ref={bottomRef} />
                  </div>
                </div>
                <div
                  aria-hidden="true"
                  className={cx(
                    "shell9-fade shell9-fade--top",
                    thread.edges.start && "is-visible",
                  )}
                />
                <div
                  aria-hidden="true"
                  className={cx(
                    "shell9-fade shell9-fade--bottom",
                    thread.edges.end && "is-visible",
                  )}
                />
              </div>

              {error && (
                <div className="shell9-error" role="alert">
                  {error}
                </div>
              )}

              {/* HumanInTheLoop：写文件等敏感操作执行前的人工审批 */}
              {pendingInterrupt && !streaming && (
                <div className="shell9-interrupt" role="alertdialog" aria-label="人工审批">
                  <p className="shell9-interrupt-title">需要你的确认</p>
                  <p className="shell9-interrupt-prompt">{pendingInterrupt.prompt}</p>
                  {pendingInterrupt.reason && (
                    <p className="shell9-interrupt-reason">{pendingInterrupt.reason}</p>
                  )}
                  <div className="shell9-interrupt-actions">
                    <button
                      type="button"
                      className="shell9-btn shell9-btn--approve"
                      onClick={approve}
                    >
                      <Check aria-hidden="true" />
                      通过并继续
                    </button>
                    <button
                      type="button"
                      className="shell9-btn shell9-btn--reject"
                      onClick={reject}
                    >
                      <X aria-hidden="true" />
                      驳回
                    </button>
                  </div>
                </div>
              )}

              <div className="shell9-composer-wrap">
                <div className="shell9-composer-inner">
                  <Composer
                    draft={draft}
                    onDraft={setDraft}
                    streaming={streaming}
                    onSend={submit}
                    onStop={stop}
                    inputRef={composerRef}
                  />
                </div>
              </div>
            </>
          )}

          <p className="shell9-disclaimer">内容由 AI 生成，请仔细甄别</p>
        </div>
      </div>

      {/* 移动端抽屉导航 */}
      {navOpen && (
        <div className="shell9-drawer-root">
          <button
            type="button"
            className={cx("shell9-backdrop", navShown && "is-visible")}
            aria-label="关闭导航"
            tabIndex={-1}
            onClick={closeNav}
          />
          <aside
            ref={drawerRef}
            role="dialog"
            aria-modal="true"
            aria-label="会话导航"
            className={cx("shell9-drawer", navShown && "is-open")}
          >
            <SidebarBody
              sessions={sessions}
              activeThreadId={activeThreadId}
              onOpenSession={openChat}
              onDeleteSession={deleteSession}
              onNew={startNew}
              onClose={closeNav}
            />
          </aside>
        </div>
      )}
    </div>
  );
}
```

## Styles

以下 SCSS 追加到 `src/styles.scss` 末尾（复用文件中已有的设计 Token 与 `scrollbar-thin` mixin、`$bp-mobile` / `$bp-small` 断点变量）：

```scss
// ==========================================================================
// App Shell 9（CodePilot 适配版）
// React Bits Pro app-shell-9 骨架的暗色重制：布局结构保留（双卡片、可折叠
// 侧栏、顶栏、消息流、全宽 Composer、移动端抽屉），视觉全部映射到工程
// 设计 Token。类名统一 shell9- 前缀。
// ==========================================================================

@keyframes shell9-pulse {
  0%,
  100% {
    opacity: 0.3;
  }
  50% {
    opacity: 1;
  }
}

.shell9 {
  position: relative;
  display: flex;
  height: 100%;
  width: 100%;
  overflow: hidden;
  background: var(--bg-primary);

  button {
    font: inherit;
    cursor: pointer;
  }

  button:focus-visible,
  [role='button']:focus-visible {
    outline: 2px solid var(--brand);
    outline-offset: 2px;
  }

  // ---- 外框与双卡片面板 ----
  &-frame {
    display: flex;
    width: 100%;
    min-width: 0;
    padding: 12px;
  }

  &-panel {
    display: flex;
    flex-direction: column;
    min-width: 0;
    border: 1px solid var(--border-light);
    border-radius: 18px;
    background: var(--bg-secondary);
  }

  // ---- 桌面侧栏：折叠到 0 宽 ----
  &-aside {
    display: none;
    flex-shrink: 0;
    overflow: hidden;
    width: 288px;
    margin-right: 12px;
    transition: width 240ms var(--ease-soft), margin-right 240ms var(--ease-soft);

    &.is-collapsed {
      width: 0;
      margin-right: 0;
      transition-duration: 200ms;
    }

    @media (min-width: $bp-mobile + 1) {
      display: block;
    }
  }

  &-sidebar {
    height: 100%;
    width: 288px;
  }

  &-main {
    flex: 1;
    overflow: hidden;
  }

  // ---- 顶栏 ----
  &-header {
    display: flex;
    align-items: center;
    height: 60px;
    flex-shrink: 0;
    padding: 0 12px;
  }

  &-iconbtn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    flex-shrink: 0;
    border: none;
    border-radius: 8px;
    background: transparent;
    color: var(--text-l3);
    transition: background-color 150ms var(--ease-soft), color 150ms var(--ease-soft);

    &:hover {
      background: var(--bg-hover);
      color: var(--text-l1);
    }

    &:active {
      transform: scale(0.97);
    }
  }

  &-menu {
    margin-right: 8px;

    @media (min-width: $bp-mobile + 1) {
      display: none;
    }
  }

  &-expand {
    display: none;
    margin-right: 8px;

    &.is-visible {
      display: inline-flex;
    }

    @media (max-width: $bp-mobile) {
      display: none !important;
    }
  }

  // 单一 DeepAgent 徽标（无模型切换能力，仅状态展示）
  &-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 36px;
    padding: 0 12px;
    flex-shrink: 0;
    border: 1px solid var(--border-light);
    border-radius: 8px;
    color: var(--text-l2);
    font-size: 13px;
    font-weight: 500;
  }

  &-badge-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #34d399;
  }

  &-title {
    flex: 1;
    min-width: 0;
    margin: 0 0 0 8px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 13px;
    font-weight: 400;
    color: var(--text-l3);
  }

  // ---- 消息流 ----
  &-thread {
    position: relative;
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
  }

  &-thread-scroll {
    height: 100%;
    overflow-y: auto;
    padding: 0 16px;
    @include scrollbar-thin;

    @media (min-width: $bp-small) {
      padding: 0 24px;
    }
  }

  &-thread-inner {
    display: flex;
    flex-direction: column;
    gap: 20px;
    max-width: 672px;
    margin: 0 auto;
    padding: 24px 0;
  }

  &-row-user {
    display: flex;
    justify-content: flex-end;
  }

  &-bubble-user {
    max-width: 85%;
    margin: 0;
    padding: 10px 14px;
    border-radius: 14px;
    background: var(--bg-bubble-user);
    color: var(--text-l1);
    font-size: 14px;
    line-height: 1.65;
    white-space: pre-wrap;
    word-break: break-word;
  }

  &-msg-assistant {
    max-width: 95%;
    margin: 0;
    color: var(--text-l2);
    font-size: 14px;
    line-height: 1.7;
    white-space: pre-wrap;
    word-break: break-word;
  }

  &-thinking {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: var(--text-l4);
  }

  &-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--brand);
    animation: shell9-pulse 1s ease-in-out infinite;
  }

  // ---- 滚动渐隐遮罩 ----
  &-fade {
    position: absolute;
    left: 0;
    right: 0;
    height: 32px;
    pointer-events: none;
    opacity: 0;
    transition: opacity 200ms ease-out;

    &.is-visible {
      opacity: 1;
    }
  }

  &-fade--top {
    top: 0;
    background: linear-gradient(to bottom, var(--bg-secondary), transparent);
  }

  &-fade--bottom {
    bottom: 0;
    background: linear-gradient(to top, var(--bg-secondary), transparent);
  }

  // ---- Composer ----
  &-composer-wrap {
    flex-shrink: 0;
    padding: 8px 16px 0;

    @media (min-width: $bp-small) {
      padding: 8px 24px 0;
    }
  }

  &-composer-inner {
    max-width: 672px;
    margin: 0 auto;
  }

  &-welcome-composer {
    width: 100%;
    max-width: 672px;
    margin-top: 24px;
  }

  &-composer {
    border: 1px solid var(--border-light);
    border-radius: 16px;
    background: var(--bg-input);
    transition: border-color 150ms var(--ease-soft);

    &:focus-within {
      border-color: var(--border-strong);
    }

    textarea {
      display: block;
      width: 100%;
      max-height: 160px;
      padding: 12px 14px 4px;
      border: none;
      background: transparent;
      color: var(--text-l1);
      font: inherit;
      font-size: 14px;
      line-height: 1.6;
      resize: none;
      outline: none;

      &::placeholder {
        color: var(--text-l4);
      }
    }
  }

  &-composer-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 8px 8px;
  }

  &-hint {
    margin-right: auto;
    font-size: 12px;
    color: var(--text-l5);
  }

  &-send,
  &-stop {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    flex-shrink: 0;
    border: none;
    border-radius: 999px;
    transition: background-color 150ms var(--ease-soft), transform 150ms var(--ease-soft);
  }

  &-send {
    background: var(--brand);
    color: #fff;

    &:hover:not(:disabled) {
      background: var(--brand-hover);
    }

    &:active:not(:disabled) {
      transform: scale(0.95);
    }

    &:disabled {
      background: var(--bg-tertiary);
      color: var(--text-l5);
      pointer-events: none;
    }
  }

  &-stop {
    background: var(--bg-tertiary);
    color: var(--text-l2);

    &:hover {
      color: var(--text-l1);
    }
  }

  // ---- 错误与审批 ----
  &-error {
    flex-shrink: 0;
    margin: 8px 16px 0;
    padding: 10px 14px;
    border: 1px solid var(--semantic-error-border);
    border-radius: 10px;
    background: var(--semantic-error-bg);
    color: var(--semantic-error);
    font-size: 13px;
  }

  // HumanInTheLoop：写操作执行前的审批卡
  &-interrupt {
    flex-shrink: 0;
    margin: 8px 16px 0;
    padding: 14px 16px;
    border: 1px solid var(--border-strong);
    border-radius: 14px;
    background: var(--bg-tertiary);
  }

  &-interrupt-title {
    margin: 0 0 6px;
    font-size: 13px;
    font-weight: 600;
    color: var(--text-l1);
  }

  &-interrupt-prompt {
    margin: 0;
    font-size: 13px;
    line-height: 1.6;
    color: var(--text-l2);
    white-space: pre-wrap;
    word-break: break-word;
  }

  &-interrupt-reason {
    margin: 4px 0 0;
    font-size: 12px;
    color: var(--text-l4);
  }

  &-interrupt-actions {
    display: flex;
    gap: 8px;
    margin-top: 12px;
  }

  &-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 32px;
    padding: 0 14px;
    border: none;
    border-radius: 8px;
    font-size: 13px;
    transition: background-color 150ms var(--ease-soft), color 150ms var(--ease-soft);
  }

  &-btn--approve {
    background: var(--brand);
    color: #fff;

    &:hover {
      background: var(--brand-hover);
    }

    &:active {
      transform: scale(0.98);
    }
  }

  &-btn--reject {
    border: 1px solid var(--border-light);
    background: transparent;
    color: var(--text-l3);

    &:hover {
      color: var(--semantic-error);
      border-color: var(--semantic-error-border);
    }
  }

  &-disclaimer {
    flex-shrink: 0;
    margin: 0;
    padding: 8px 16px 12px;
    text-align: center;
    font-size: 12px;
    color: var(--text-l5);
  }

  // ---- 欢迎态 ----
  &-welcome-scroll {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    overflow-y: auto;
    padding: 24px 16px;
    @include scrollbar-thin;
  }

  &-welcome-title {
    margin: 16px 0 0;
    font-size: 22px;
    font-weight: 500;
    letter-spacing: -0.015em;
    color: var(--text-l1);
    text-align: center;
  }

  &-welcome-sub {
    margin: 6px 0 0;
    font-size: 14px;
    color: var(--text-l4);
  }

  &-cards {
    display: grid;
    grid-template-columns: 1fr;
    gap: 8px;
    width: 100%;
    max-width: 672px;
    margin-top: 12px;

    @media (min-width: 640px) {
      grid-template-columns: 1fr 1fr;
    }
  }

  &-card {
    display: flex;
    align-items: center;
    gap: 10px;
    height: 48px;
    padding: 0 12px;
    border: 1px solid var(--border-light);
    border-radius: 8px;
    background: transparent;
    color: var(--text-l2);
    font-size: 13px;
    text-align: left;
    transition: background-color 150ms var(--ease-soft);

    &:hover {
      background: var(--bg-hover);
    }

    &:active {
      transform: scale(0.99);
    }

    > span {
      flex: 1;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  &-card-arrow {
    flex-shrink: 0;
    color: var(--text-l5);
    transition: color 150ms var(--ease-soft);
  }

  &-card:hover &-card-arrow {
    color: var(--text-l2);
  }

  // ---- 侧栏内容 ----
  &-brand {
    display: flex;
    align-items: center;
    gap: 10px;
    height: 60px;
    flex-shrink: 0;
    padding: 0 12px;
  }

  &-logo-mark {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    flex-shrink: 0;
    border-radius: 8px;
    background: var(--brand-gradient);
    color: #fff;
    font-size: 14px;
    font-weight: 600;
  }

  &-logo-mark--lg {
    width: 44px;
    height: 44px;
    border-radius: 12px;
    font-size: 18px;
  }

  &-brand-name {
    flex: 1;
    min-width: 0;
    margin: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 15px;
    font-weight: 500;
    letter-spacing: -0.01em;
    color: var(--text-l1);
  }

  &-new-wrap {
    flex-shrink: 0;
    padding: 0 12px 12px;
  }

  &-new-chat {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    height: 36px;
    width: 100%;
    padding: 0 12px;
    border: none;
    border-radius: 8px;
    background: var(--brand);
    color: #fff;
    font-size: 13px;
    font-weight: 500;
    transition: background-color 150ms var(--ease-soft), transform 150ms var(--ease-soft);

    &:hover {
      background: var(--brand-hover);
    }

    &:active {
      transform: scale(0.98);
    }
  }

  &-scroll-wrap {
    position: relative;
    flex: 1;
    min-height: 0;
  }

  &-scroll {
    height: 100%;
    overflow-y: auto;
    padding: 0 12px 12px;
    @include scrollbar-thin;
  }

  &-overline {
    margin: 0 0 6px;
    padding: 0 8px;
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-l5);
  }

  &-sessions {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  &-session {
    display: flex;
    align-items: center;
    gap: 8px;
    height: 36px;
    padding: 0 8px;
    border-radius: 8px;
    color: var(--text-l3);
    font-size: 13px;
    cursor: pointer;
    transition: background-color 150ms var(--ease-soft), color 150ms var(--ease-soft);

    &:hover,
    &.is-active {
      background: var(--bg-hover);
      color: var(--text-l1);
    }
  }

  &-session-icon {
    flex-shrink: 0;
    color: var(--text-l4);
  }

  &-session-title {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &-session-time {
    flex-shrink: 0;
    font-size: 11px;
    color: var(--text-l5);
  }

  &-session-delete {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    flex-shrink: 0;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: var(--text-l5);
    opacity: 0;
    transition: opacity 150ms var(--ease-soft), color 150ms var(--ease-soft);

    .shell9-session:hover & {
      opacity: 1;
    }

    &:hover {
      color: var(--semantic-error);
    }
  }

  &-empty {
    margin: 0;
    padding: 8px;
    font-size: 12px;
    color: var(--text-l5);
  }

  &-user {
    display: flex;
    align-items: center;
    gap: 10px;
    height: 64px;
    flex-shrink: 0;
    padding: 0 12px;
  }

  &-user-avatar {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    flex-shrink: 0;
    border-radius: 50%;
    background: var(--bg-tertiary);
    color: var(--text-l2);
    font-size: 12px;

    &::after {
      content: '';
      position: absolute;
      right: -1px;
      bottom: -1px;
      width: 10px;
      height: 10px;
      border: 2px solid var(--bg-secondary);
      border-radius: 50%;
      background: #34d399;
    }
  }

  &-user-meta {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }

  &-user-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 13px;
    color: var(--text-l1);
  }

  &-user-status {
    font-size: 12px;
    color: var(--text-l5);
  }

  // ---- 移动端抽屉 ----
  &-drawer-root {
    display: none;

    @media (max-width: $bp-mobile) {
      display: block;
    }
  }

  &-backdrop {
    position: absolute;
    inset: 0;
    z-index: 30;
    padding: 0;
    border: none;
    background: rgba(0, 0, 0, 0.5);
    backdrop-filter: blur(2px);
    opacity: 0;
    transition: opacity 200ms ease-out;
    cursor: pointer;

    &.is-visible {
      opacity: 1;
    }
  }

  &-drawer {
    position: absolute;
    top: 0;
    bottom: 0;
    left: 0;
    z-index: 40;
    display: flex;
    flex-direction: column;
    width: 288px;
    max-width: 85%;
    border-radius: 0 18px 18px 0;
    background: var(--bg-secondary);
    box-shadow: var(--shadow-overlay);
    transform: translateX(-100%);
    transition: transform 200ms var(--ease-soft);

    &.is-open {
      transform: translateX(0);
    }
  }
}
```
