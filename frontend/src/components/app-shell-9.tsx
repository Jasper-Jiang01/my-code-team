import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type Ref,
} from "react";
import {
  ArrowUp,
  ArrowUpRight,
  BarChart3,
  Check,
  Code2,

  ListChecks,
  Menu,
  MessageSquare,
  PanelLeft,
  Plus,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { useChat } from "../hooks/useChat";
import type { StoredSession } from "../lib/session-store";
import ThreeDLetterSwap from "./3d-letter-swap";
import type { AIBlobProps } from "./ai-blob";

// three.js 体积大（~500kB），logo 场景懒加载，不进首屏主包
const AIBlob = lazy(() => import("./ai-blob"));

// Blob logo 配色：对齐工程设计 Token 的品牌色系
// （--brand-gradient #3ac0fd→#3964ff、--brand-hover #2f52e0）
const BLOB_COLORS = ["#3ac0fd", "#3964ff", "#2f52e0", "#3ac0fd"];

/** Blob logo：懒加载期间用品牌渐变圆占位，避免布局抖动 */
function LogoBlob({ size, ...blob }: AIBlobProps) {
  return (
    <Suspense
      fallback={
        <span
          className="shell9-logo-blob-fallback"
          style={{ width: size, height: size }}
          aria-hidden="true"
        />
      }
    >
      <AIBlob size={size} {...blob} />
    </Suspense>
  );
}

// 快捷任务与欢迎页 PromptInput3 保持一致
// 带 intent 的项点击后直接发送并跳过意图分类，直接进入对应子图
const SUGGESTIONS = [
  {
    label: "数据分析专家",
    prompt: "我想进行数据分析，请介绍你的数据分析能力，并告诉我如何提供数据文件开始分析",
    icon: BarChart3,
    intent: "data_analysis",
  },
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
  // useRef<T | null> 返回 MutableRefObject（current 可写），供 ref 回调中赋值
  const ref = useRef<T | null>(null);
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
  onSend: (text: string, intent?: string) => void;
  onStop: () => void;
  inputRef: Ref<HTMLTextAreaElement>;
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
        <span className="shell9-logo-blob" aria-hidden="true">
          <LogoBlob
            size={32}
            colors={BLOB_COLORS}
            animationSpeed={0.8}
            glowIntensity={0.9}
            resolution={0.75}
          />
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
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
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
  (text: string, intent?: string) => {
    if (!text || streaming) return;
    send(text, intent);
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
          {/* Hero 18 同款背景层（React Bits Pro）：网格 + 点阵 + 光晕，纯装饰不拦截交互 */}
          <div className="bg-hero" aria-hidden="true">
            <div className="bg-hero-grid" />
            <div className="bg-hero-dots bg-hero-dots--left" />
            <div className="bg-hero-dots bg-hero-dots--right" />
            <div className="bg-hero-glow" />
          </div>

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
                  className="shell9-logo-blob"
                  aria-hidden="true"
                >
                    <LogoBlob
                      size={120}
                      colors={BLOB_COLORS}
                      animationSpeed={0.7}
                    glowIntensity={1.0}
                  />
                </span>
                {/* 3D Letter Swap（React Bits Pro）：hover 将主标题逐字符翻转为副标题，移开翻回；
                    背面不单独设类，直接继承标题字号/颜色，保证翻转前后观感一致 */}
                <ThreeDLetterSwap
                  as="h2"
                  className="shell9-welcome-title"
                  swapText="描述目标，或从一个常用任务开始。"
                  flipDirection="top"
                  staggerOrigin="center"
                  staggerInterval={0.03}
                  blur
                >
                  今天想让 CodePilot 帮你完成什么？
                </ThreeDLetterSwap>

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
                          // 带 intent 的快捷入口（如“数据分析专家”）直接发送，跳过意图分类进入子图；
                          // 其余保持原行为：填充输入框待用户编辑
                          if ("intent" in item && item.intent) {
                            submit(item.prompt, item.intent);
                          } else {
                            setDraft(item.prompt);
                            composerRef.current?.focus({ preventScroll: true });
                          }
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
