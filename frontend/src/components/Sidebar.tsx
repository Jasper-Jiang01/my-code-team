import { memo } from 'react';
import type { StoredSession } from '../lib/session-store';

interface Props {
  onNewChat: () => void;
  /** 移动端抽屉是否展开 */
  open: boolean;
  /** 关闭抽屉（点击遮罩或选中会话后触发） */
  onClose: () => void;
  /** 桌面端折叠为 70px 图标栏 */
  collapsed: boolean;
  onToggleCollapse: () => void;
  /** 历史会话索引（localStorage 持久化，最新在前） */
  sessions: StoredSession[];
  /** 回看 / 继续某个历史会话 */
  onOpenSession: (threadId: string) => void;
  /** 删除历史会话 */
  onDeleteSession: (threadId: string) => void;
}

/**
 * 左侧会话侧边栏（规格对齐灵光：250px / 折叠 70px / 菜单项 36px 高 12px 圆角）。
 * 品牌区 + 新建对话（渐变主按钮）+ 历史会话列表 + 用户信息。
 * 移动端（<= 768px）收起为抽屉，通过 open/onClose 控制展开与收起。
 *
 * memo 化：流式期间 token 每帧更新 ChatPage 状态，本组件 props 均为
 * 固定引用/数组，可完全跳过重渲染。
 */
export const Sidebar = memo(function Sidebar({
  onNewChat,
  open,
  onClose,
  collapsed,
  onToggleCollapse,
  sessions,
  onOpenSession,
  onDeleteSession,
}: Props) {
  const cls = ['sidebar', open ? 'open' : '', collapsed ? 'collapsed' : '']
    .filter(Boolean)
    .join(' ');

  return (
    <>
      {open && <div className="sidebar-backdrop" onClick={onClose} />}
      <aside className={cls}>
        <div className="sidebar-brand">
          <div className="logo-mark">C</div>
          <span className="logo-text">CodePilot</span>
          <button
            type="button"
            className="btn-collapse"
            onClick={onToggleCollapse}
            aria-label={collapsed ? '展开侧边栏' : '折叠侧边栏'}
            title={collapsed ? '展开侧边栏' : '折叠侧边栏'}
          >
            {collapsed ? '»' : '«'}
          </button>
        </div>

        <button
          type="button"
          className="btn-new-chat"
          onClick={() => {
            onNewChat();
            onClose();
          }}
          title="新建对话"
        >
          <span className="plus">＋</span>
          <span className="label">新建对话</span>
        </button>

        <div className="sidebar-section-label">历史会话</div>
        <div className="session-list">
          {sessions.length ? (
            sessions.map((s) => (
              <div
                key={s.threadId}
                className="session-item"
                role="button"
                tabIndex={0}
                onClick={() => {
                  onOpenSession(s.threadId);
                  onClose();
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onOpenSession(s.threadId);
                    onClose();
                  }
                }}
                title={s.title}
              >
                <span className="session-icon">💬</span>
                <span className="session-title">{s.title}</span>
                <button
                  type="button"
                  className="session-delete"
                  aria-label={`删除会话：${s.title}`}
                  title="删除会话"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteSession(s.threadId);
                  }}
                >
                  ✕
                </button>
              </div>
            ))
          ) : (
            <div className="session-empty">暂无历史会话</div>
          )}
        </div>

        <div className="sidebar-footer">
          <div className="avatar-sm">你</div>
        </div>
      </aside>
    </>
  );
});
