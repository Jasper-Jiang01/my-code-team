interface Props {
  onNewChat: () => void;
  /** 移动端抽屉是否展开 */
  open: boolean;
  /** 关闭抽屉（点击遮罩或选中会话后触发） */
  onClose: () => void;
}

/**
 * 左侧会话侧边栏：品牌区 + 新建对话 + 历史会话列表（占位，暂无持久化）。
 * 视觉对齐 DeepSeek Chat：浅灰底、胶囊按钮、克制的分组标签。
 * 移动端（<= 768px）收起为抽屉，通过 open/onClose 控制展开与收起。
 */
export function Sidebar({ onNewChat, open, onClose }: Props) {
  return (
    <>
      {open && <div className="sidebar-backdrop" onClick={onClose} />}
      <aside className={`sidebar ${open ? 'open' : ''}`}>
        <div className="sidebar-brand">
          <div className="logo-mark">C</div>
          <span className="logo-text">CodePilot</span>
        </div>

        <button
          className="btn-new-chat"
          onClick={() => {
            onNewChat();
            onClose();
          }}
        >
          <span className="plus">+</span>
          新建对话
        </button>

        <div className="sidebar-section-label">历史会话</div>
        <div className="session-list">
          <div className="session-item active" onClick={onClose}>
            当前会话
          </div>
        </div>

        <div className="sidebar-footer">
          <div className="avatar-sm">你</div>
          <span className="footer-text">本地开发环境</span>
        </div>
      </aside>
    </>
  );
}
