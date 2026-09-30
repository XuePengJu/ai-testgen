/**
 * 单条消息气泡：用户 / AI 流式 / AI 完成（含思考折叠 + 生成按钮）/ 任务卡。
 */
import { useEffect, useRef, useState } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import type { ChatMsg } from "../../store/chatStore";
import { useChatStore } from "../../store/chatStore";
import { toast } from "../../api/client";
import TaskStepsCard from "./TaskStepsCard";
import Citations from "./Citations";

// GFM：表格 / 任务列表 / 删除线；breaks：单个换行也换行（保持旧版 <br> 的观感，行距不变）
marked.setOptions({ gfm: true, breaks: true });

/** 思考面板收起时的高度上限（与 base.css 的 .think-body max-height 保持一致） */
const THINK_MAX_HEIGHT = 220;

/**
 * 思考面板：默认展开；内容超过高度上限时给「展开全部 / 收起」；
 * 点标题行整体折叠/展开。
 */
function ThinkingPanel({ text }: { text: string }) {
  const [collapsed, setCollapsed] = useState(false); // 整体折叠（点标题行）
  const [expanded, setExpanded] = useState(false);   // 展开全部（超出上限时）
  const [overflow, setOverflow] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  // 用固定上限比较而非 scrollHeight/clientHeight：展开后 clientHeight 也变大，
  // 那样按钮会在点开的瞬间自己消失
  useEffect(() => {
    const el = bodyRef.current;
    if (!el || collapsed) return;
    setOverflow(el.scrollHeight > THINK_MAX_HEIGHT + 4);
  }, [text, collapsed, expanded]);

  return (
    <div className={`think-panel ${collapsed ? "" : "open"}`}>
      <button className="think-head" type="button" onClick={() => setCollapsed(!collapsed)}>
        <span className="arrow">{collapsed ? "▸" : "▾"}</span>
        <span className="label">思考过程</span>
      </button>
      {!collapsed && (
        <>
          {/* 思考内容同样走 markdown（模型推理里常带 `###` 小标题与列表），样式靠 .think-body 内选择器压成紧凑版 */}
          <div
            className={`think-body ${expanded ? "expanded" : ""}`}
            ref={bodyRef}
            dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }}
          />
          {overflow && (
            <button className="think-more" type="button" onClick={() => setExpanded(!expanded)}>
              {expanded ? "收起" : "展开全部"}
            </button>
          )}
        </>
      )}
    </div>
  );
}

/**
 * markdown → 安全 HTML。
 *
 * 旧版是手写简易渲染（只认 **加粗** + 换行），`###` 标题 / 有序列表 / 代码块 / 表格
 * 全部原样当文本吐出来。现改用 marked 解析 + DOMPurify 消毒。
 *
 * 消毒不是可选项：marked 会把 AI 回复里的 HTML 原样输出，而模型输出属不可信内容，
 * 不过一遍 DOMPurify 等于把 innerHTML 直接交给它（旧版是先 escape 再替换，本身安全）。
 */
function renderMarkdown(text: string): string {
  if (!text) return "";
  const html = marked.parse(text, { async: false }) as string;
  return DOMPurify.sanitize(html);
}

/**
 * 流式生成期间的轻量渲染：escape + **加粗** + 换行。
 *
 * 为什么流式不直接上完整 markdown：
 *  1. 外层是 <span>（要跟打字光标同行），只能放行内元素，塞 <h3>/<p>/<ul> 属非法嵌套；
 *  2. 流式文本每帧都在变，` ``` ` 代码块、表格这类结构未闭合时会剧烈抖动；
 *  3. 收起时才是一次性成型，排版「突然生效」比逐帧重排更顺眼。
 */
function renderInline(text: string): string {
  const esc = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return esc
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br>");
}

export default function MessageView({
  msg,
  showCitations = false,
  onCiteClick,
}: {
  msg: ChatMsg;
  showCitations?: boolean;
  onCiteClick?: (knowledgeId: string) => void;
}) {
  const streaming = msg.state === "streaming";
  const confirmCreateTask = useChatStore((s) => s.confirmCreateTask);

  if (msg.role === "user") {
    return (
      <div className="msg msg-user">
        <div className="avatar">你</div>
        <div className="bubble">
          {/* 附件单独成行显示，不混进正文字本；只传附件时气泡就只有这枚徽标 */}
          {msg.fileName && <div className="msg-file">📎 {msg.fileName}</div>}
          {msg.content && msg.content.split("\n").map((l, i) => <p key={i}>{l}</p>)}
        </div>
      </div>
    );
  }

  // ---- AI 消息 ----
  // 演示/未配模型时在标题行给一枚显式徽标（放头像里会被 34px 圆形裁掉，看不出来）
  const mockTag = msg.source === "mock" ? <span className="mock-tag">演示模式</span> : null;
  // AI 身份标签：根据参与角色动态显示（kb知识库助手/qa测试/pm产品/dev开发），默认测试工程师
  const personaLabel = msg.persona === "kb" ? "知识库助手"
    : msg.persona === "pm" ? "AI 产品经理"
    : msg.persona === "dev" ? "AI 开发工程师"
    : "AI 测试工程师";

  return (
    <div className="msg msg-ai">
      <div className="avatar">AI</div>
      <div className="bubble chat-bubble">
        <div className="bubble-title">
          {personaLabel}
          {mockTag}
        </div>

        {msg.notice && <div className="msg-notice">⚠ {msg.notice}</div>}

        {msg.thinking && <ThinkingPanel text={msg.thinking} />}

        {streaming ? (
          <div className="reply-body streaming">
            {msg.content ? (
              <span dangerouslySetInnerHTML={{ __html: renderInline(msg.content) }} />
            ) : (
              <span className="thinking-dots">
                <span />
                <span />
                <span />
              </span>
            )}
            <span className="type-cursor" />
          </div>
        ) : msg.state === "error" ? (
          <div className="reply-body msg-error">⚠ {msg.error || "生成失败"}</div>
        ) : (
          <>
            <div className="reply-body" dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }} />
            {msg.state === "stopped" && <div className="msg-stopped">（已停止生成）</div>}
          </>
        )}

        {/* V4.1 引用溯源：showCitations 开启且消息带 citations 时渲染（知识库问答） */}
        {showCitations && msg.citations?.length ? (
          <Citations items={msg.citations} onCiteClick={onCiteClick} />
        ) : null}

        {/* 任务卡（消息升级后） */}
        {msg.task && <TaskStepsCard task={msg.task} showIterate />}

        {/* 回复完成且未消费草稿 → 生成用例确认按钮 */}
        {msg.state === "done" && !msg.task && msg.draft && (
          <div className="quick-row">
            <button
              className="qtag confirm-btn"
              type="button"
              onClick={() => void confirmCreateTask(msg.id)}
            >
              ✨ 生成测试用例
            </button>
            <button
              className="qtag"
              type="button"
              onClick={() => toast("可在输入框继续补充规则后发送")}
            >
              继续补充
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
