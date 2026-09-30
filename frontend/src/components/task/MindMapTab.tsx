/**
 * M3 / M5-fix：思维导图 Tab（mind-elixir 6.0.0-next.4 npm 版）。
 * 数据来源：前端从 task.cases 现场构建树（后端无 mind_map 字段）。
 * M5-fix4（A + B + D 合成，老板拍板「默认 RIGHT 根贴左」）：
 *   - A：初始不再无条件 toCenter。RIGHT 方向且内容溢出容器时，用库自带 move(dx,0)
 *        把根节点左边缘贴到 40px 内边距，右侧空间得到利用
 *   - D：内容未溢出容器（小任务）时保持居中，观感不变
 *   - B：SIDE / LEFT / DOWN 恒维持库默认居中（SIDE 本身左右对称，再贴左会把左半树推出可视区）
 *   - 放开左上「方向切换」工具栏；切换方向经 bus 的 changeDirection 事件重跑上述定位
 *   - 树结构对齐 V2.7 legacy buildMindmapData：root → 模块 → 用例
 *     → [前置条件 / 测试数据 / 操作步骤 → 逐步预期（兜底整体预期）]
 *
 * 6.x 关键能力（区别于已弃用的 4.6.2）：
 *   - overflowHidden:false 启用原生拖拽平移画布（panHelper 注入 mousemove 平移）
 *   - handleWheel:true：普通滚轮 = 平移，Ctrl/⌘ + 滚轮 = 缩放（与 legacy 一致）
 *   - 定位公式 alignment:"root"：把根节点钉在容器水平正中（故左半屏天然空着 → 需要 A）
 *   - move(dx,dy) 自带钳制：不会把内容推出容器中线之外，可安全调用
 *   - init() 为 async：工具栏在 await document.fonts.ready 后才挂载
 *   - npm 版不内联 CSS，需手动 import 'mind-elixir/style.css'
 *   - bus 接口为 addListener / fire / removeListener（没有 on / emit）
 * 因此此处删除 4.6.2 时期自建的 DOMMatrix 平移 hack（写 .map-container transform 既无效又错位）。
 *
 * V2.12：画布控件（导出 XMind / 全屏 / 缩放 / 百分比 / 居中）从 mind-elixir 右下角浮动工具栏
 *   迁到详情页底部操作栏（与「💬 继续优化」同一行）——节点文字多时浮层会压住内容。
 *   实现：保留 toolBar:true（false 会把左上「方向切换」.lt 一起关掉），仅用 CSS 隐藏右下角 .rb；
 *   控件用公开 API 复刻（scaleVal / scaleSensitivity / scale() / toCenter() / el.requestFullscreen）。
 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import MindElixir from "mind-elixir";
import "mind-elixir/style.css";
import type { MindElixirData, NodeObj } from "mind-elixir";
import { Download, Maximize2, ZoomIn, ZoomOut, Crosshair } from "lucide-react";
import type { CaseItem, Task } from "../../types";
import { downloadTaskFile } from "../../api/client";

interface Props {
  task: Task;
}

let uidSeq = 0;
/** 生成导图内部节点 id（不依赖业务字段，避免空值撞 id） */
const uid = (p: string) => `${p}-${++uidSeq}`;

/** 单个用例 → 完整子树（对齐 V2.7：前置/数据/步骤→逐步预期/兜底整体预期） */
function buildCaseNode(c: CaseItem): NodeObj {
  const kids: NodeObj[] = [];
  if (c.pre_condition) kids.push({ id: uid("pre"), topic: c.pre_condition, tags: ["前置条件"] });
  if (c.test_data) kids.push({ id: uid("data"), topic: c.test_data, tags: ["测试数据"] });
  (c.steps || []).forEach((s, i) => {
    const sn: NodeObj = { id: uid("step"), topic: `${i + 1}. ${s}`, tags: ["操作步骤"] };
    // 优先逐步预期，旧数据兜底整体预期（V2.7 语义）
    const se = c.step_expectations?.[i] || c.expected;
    if (se) sn.children = [{ id: uid("exp"), topic: se, tags: ["预期结果"] }];
    kids.push(sn);
  });
  if (!(c.steps || []).length && c.expected) {
    kids.push({ id: uid("exp"), topic: c.expected, tags: ["预期结果"] });
  }
  return {
    id: `case:${c.case_id}`,
    topic: `${c.case_id} ${c.title}`,
    tags: [c.case_type, c.priority].filter(Boolean) as string[],
    children: kids,
  };
}

/** cases → mind-elixir 树（root → module → case → 字段/步骤/预期） */
function buildMapData(task: Task, cases: CaseItem[]): MindElixirData {
  const byModule = new Map<string, CaseItem[]>();
  for (const c of cases) {
    const mod = (c.module || "").trim() || "未分组";
    const list = byModule.get(mod) || [];
    list.push(c);
    byModule.set(mod, list);
  }
  return {
    nodeData: {
      id: "root",
      topic: task.name,
      expanded: true,
      children: [...byModule.entries()].map(([mod, list]) => ({
        id: uid("mod"),
        topic: mod,
        children: list.map(buildCaseNode),
      })),
    },
  };
}

/** 根节点贴左时的左内边距（px）。想更贴边可调小（如 24）。 */
const MAP_ALIGN_LEFT_PX = 40;

/**
 * 初始视图定位（A + D + B 的合成策略，一个函数内按方向分派，不叠加）。
 *
 * 库的定位（alignment:"root"）会把根节点钉在容器水平正中，子树全往右长，
 * 左半屏必然空着 —— 这是库的默认行为，不是配置错误。因此：
 *   RIGHT + 内容溢出（D 的「是」）→ 根贴左 MAP_ALIGN_LEFT_PX（A）
 *   RIGHT + 内容未溢出          → 居中（D 的「否」，与现状一致）
 *   SIDE / LEFT / DOWN          → 居中（B 自身左右对称，贴左会把左半树推出可视区）
 *
 * 位移量推导：居中态下根节点左边缘位于 (containerW - rootW)/2（scale=1），
 * 目标是 MAP_ALIGN_LEFT_PX，故 dx = MAP_ALIGN_LEFT_PX - (containerW - rootW)/2。
 * 用库自带的 move() 施加位移（不写 transform，不破坏内部状态；自带钳制）。
 */
function fitInitialView(mind: MindElixir) {
  try {
    mind.toCenter(); // 先回到居中基准，保证 move 的增量可预测
    if (mind.direction !== MindElixir.RIGHT) return; // B：其它方向保持居中
    const container = mind.container;
    const nodes = mind.nodes;
    if (!container || !nodes) return;
    // D：.me-nodes 是 width:max-content，offsetWidth 即内容总宽；未溢出则不动
    if (nodes.offsetWidth <= container.offsetWidth) return;
    const root = container.querySelector<HTMLElement>(".me-root");
    if (!root) return;
    const dx = MAP_ALIGN_LEFT_PX - (container.offsetWidth - root.offsetWidth) / 2;
    if (dx < -1) mind.move(dx, 0);
  } catch {
    /* 定位失败不影响导图可用性，静默兜底 */
  }
}

export default function MindMapTab({ task }: Props) {
  const elRef = useRef<HTMLDivElement | null>(null);
  const mindRef = useRef<MindElixir | null>(null);
  const cases = task.cases || [];
  const canRender = cases.length > 0;

  /** 底栏控件插槽（父组件 DOM 提交后才存在，故在 effect 里取） */
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const [scale, setScale] = useState(1);
  const [full, setFull] = useState(false);

  useEffect(() => {
    if (!canRender || !elRef.current) return;
    let destroyed = false;
    const el = elRef.current;
    const mind = new MindElixir({
      el,
      direction: MindElixir.RIGHT,
      editable: false, // 只读导图：节点不可编辑
      contextMenu: false,
      toolBar: true, // 保留左上「方向切换」.lt；右下 .rb 由 CSS 隐藏（控件已迁到抽屉底栏）
      keypress: false,
      overflowHidden: false, // 关键：false 才启用原生拖拽平移画布（panHelper 注入）
      handleWheel: true, // 滚轮直接缩放（无需 Ctrl）
    });
    mindRef.current = mind;

    // 方向切换（库的 initLeft/initRight/initSide 内部会 toCenter 并重置 direction）后重跑定位，
    // 保证 A（RIGHT 贴左）与 B（SIDE 居中）互不打架。bus 接口是 addListener/fire/removeListener。
    const onDirectionChange = () => {
      requestAnimationFrame(() => {
        if (destroyed) return;
        fitInitialView(mind);
      });
    };
    mind.bus.addListener("changeDirection", onDirectionChange);

    // 滚轮缩放后同步百分比（库内部改 scaleVal 无事件，故监听 wheel 后取下一帧）
    const syncScale = () =>
      requestAnimationFrame(() => {
        if (!destroyed) setScale(mind.scaleVal || 1);
      });
    el.addEventListener("wheel", syncScale, { passive: true });

    // 全屏切换后补一次定位（内置 .rb 被隐藏，其自带的居中补偿不再生效）
    const onFsChange = () => {
      setFull(document.fullscreenElement === el);
      requestAnimationFrame(() => {
        if (!destroyed) fitInitialView(mind);
      });
    };
    document.addEventListener("fullscreenchange", onFsChange);

    // 6.x init 为 async：工具栏/pan 在 await document.fonts.ready 后才就绪。
    (async () => {
      await mind.init(buildMapData(task, cases));
      if (destroyed) return;
      setScale(mind.scaleVal || 1);
      // 等一帧让弹窗动画结束、布局稳定后做初始定位（A+D）。
      // ⚠️ 不要调 scaleFit()：大任务（示例·DBERP 77 用例 ≈ 386 节点）会被缩到 scale≈0.04（蚂蚁大小），
      //    且 scaleFit 不受 scaleMin(0.2) 约束。与 legacy 行为一致：保持 scale=1，用户自行缩放。
      requestAnimationFrame(() => {
        if (destroyed) return;
        fitInitialView(mind);
      });
    })();

    return () => {
      destroyed = true;
      el.removeEventListener("wheel", syncScale);
      document.removeEventListener("fullscreenchange", onFsChange);
      try {
        mind.bus.removeListener("changeDirection", onDirectionChange);
      } catch {
        /* 兜底 */
      }
      try {
        mind.destroy();
      } catch {
        /* 卸载兜底 */
      }
      mindRef.current = null;
    };
    // task.id + cases 数变化才重建（导图渲染重，不随其他字段抖动）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.id, cases.length]);

  // 底栏插槽：DOM 提交后才能取到（父组件先渲染子组件，再插入自身 DOM）
  useEffect(() => {
    setSlot(document.getElementById("mm-ctrl-slot"));
  }, [task.id]);

  /** 缩放：步长与库内置工具栏一致（scaleSensitivity），边界由 scale() 内部钳制 */
  const zoomBy = (dir: 1 | -1) => {
    const m = mindRef.current;
    if (!m) return;
    m.scale((m.scaleVal || 1) + dir * (m.scaleSensitivity || 0.2));
    setScale(m.scaleVal || 1);
  };

  const toggleFullscreen = () => {
    const el = elRef.current;
    if (!el) return;
    if (document.fullscreenElement === el) void document.exitFullscreen();
    else void el.requestFullscreen?.();
  };

  const center = () => {
    const m = mindRef.current;
    if (!m) return;
    m.toCenter();
    setScale(m.scaleVal || 1);
  };

  const resetScale = () => {
    const m = mindRef.current;
    if (!m) return;
    m.scale(1);
    setScale(m.scaleVal || 1);
  };

  const controls = (
    <div className="mm-ctrl-bar">
      <button
        type="button"
        className="mm-ctrl-dl"
        title="下载 XMind 思维导图文件"
        onClick={() => void downloadTaskFile(task.id, "xmind", task.name)}
      >
        <Download size={14} /> 导出 XMind
      </button>
      <span className="mm-ctrl-sep" />
      <button
        type="button"
        className="mm-ctrl-icon"
        title={full ? "退出全屏" : "全屏查看"}
        aria-label={full ? "退出全屏" : "全屏查看"}
        onClick={toggleFullscreen}
      >
        <Maximize2 size={15} />
      </button>
      <button
        type="button"
        className="mm-ctrl-icon"
        title="缩小"
        aria-label="缩小"
        onClick={() => zoomBy(-1)}
      >
        <ZoomOut size={15} />
      </button>
      <button type="button" className="mm-ctrl-pct" title="点击回到 100%" onClick={resetScale}>
        {Math.round(scale * 100)}%
      </button>
      <button
        type="button"
        className="mm-ctrl-icon"
        title="放大"
        aria-label="放大"
        onClick={() => zoomBy(1)}
      >
        <ZoomIn size={15} />
      </button>
      <button
        type="button"
        className="mm-ctrl-icon"
        title="回到中心"
        aria-label="回到中心"
        onClick={center}
      >
        <Crosshair size={15} />
      </button>
    </div>
  );

  if (!canRender) {
    return <div className="drawer-empty">暂无用例，无法生成思维导图</div>;
  }

  return (
    <div className="mindmap-wrap">
      <div className="mindmap-hint">拖拽画布平移 · Ctrl/⌘ + 滚轮缩放</div>
      <div ref={elRef} className="map-container" />
      {slot && createPortal(controls, slot)}
    </div>
  );
}
