/**
 * M3 / M5-fix：用例列表 Tab。
 * - 统计条：总数 / 优先级分布 / 类型分布（从 cases 现算，后端 TaskOut 不含 report）
 * - 搜索：按 case_id / 标题 / 模块过滤
 * - M5-fix：折叠卡片 → 恢复 V2.7 表格（一行一条、表头 sticky、步骤+预期内联）
 * - focusCaseId（外部定位）：滚动定位 + 行高亮
 * - V2.8-fix4：删除「预期结果」独立列——整体预期已并入标题（`动作 -> 预期`），
 *   腾出的宽度加给「标题」与「测试数据」列；导图/导出同步用复合标题。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { Task, CaseItem } from "../../types";

interface Props {
  task: Task;
  focusCaseId: string | null;
  focusSeq: number;
}

const PRIORITY_CLS: Record<string, string> = { P0: "fail", P1: "run", P2: "sub" };

// 内存兜底：补全缺失用例ID（等价于后端 ensure_case_ids），防御性双保险——
// 即使后端返回里个别 case_id 为空（存量数据/异常路径），前端表格/搜索/定位也不丢编号。
function ensureCaseIds(list: CaseItem[]): CaseItem[] {
  let maxN = 0;
  for (const c of list) {
    const m = (c.case_id || "").trim().match(/^TC-?0*(\d+)$/);
    if (m) maxN = Math.max(maxN, parseInt(m[1], 10));
  }
  return list.map((c) => {
    if ((c.case_id || "").trim()) return c;
    maxN += 1;
    return { ...c, case_id: `TC-${String(maxN).padStart(3, "0")}` };
  });
}

export default function CaseListTab({ task, focusCaseId, focusSeq }: Props) {
  const rawCases = task.cases || [];
  const cases = useMemo(() => ensureCaseIds(rawCases), [rawCases]);
  const [q, setQ] = useState("");
  const listRef = useRef<HTMLDivElement | null>(null);

  const stats = useMemo(() => {
    const pri: Record<string, number> = {};
    const typ: Record<string, number> = {};
    for (const c of cases) {
      if (c.priority) pri[c.priority] = (pri[c.priority] || 0) + 1;
      if (c.case_type) typ[c.case_type] = (typ[c.case_type] || 0) + 1;
    }
    return { pri, typ };
  }, [cases]);

  const filtered = useMemo(() => {
    const kw = q.trim().toLowerCase();
    if (!kw) return cases;
    return cases.filter(
      (c) =>
        c.case_id.toLowerCase().includes(kw) ||
        (c.title || "").toLowerCase().includes(kw) ||
        (c.module || "").toLowerCase().includes(kw),
    );
  }, [cases, q]);

  // 外部定位：滚动 + 行高亮
  useEffect(() => {
    if (!focusCaseId || !listRef.current) return;
    const t = window.setTimeout(() => {
      const el = listRef.current?.querySelector(`[data-case-id="${focusCaseId}"]`);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.classList.add("flash");
        window.setTimeout(() => el.classList.remove("flash"), 1600);
      }
    }, 80);
    return () => window.clearTimeout(t);
  }, [focusCaseId, focusSeq]);

  if (cases.length === 0) {
    return <div className="drawer-empty">该任务暂无用例数据</div>;
  }

  const dash = <span className="dash">—</span>;

  return (
    <div className="case-tab">
      <div className="case-stats">
        <span className="cs-total">共 {cases.length} 条</span>
        {Object.entries(stats.pri)
          .sort()
          .map(([p, n]) => (
            <span key={p} className={`pill pill-${PRIORITY_CLS[p] || "sub"}`}>
              {p}×{n}
            </span>
          ))}
        {Object.entries(stats.typ).map(([t, n]) => (
          <span key={t} className="pill pill-sub">
            {t}×{n}
          </span>
        ))}
      </div>
      <div className="case-search">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索用例编号 / 标题 / 模块…"
        />
      </div>
      <div className="case-list" ref={listRef}>
        {filtered.length === 0 ? (
          <div className="drawer-empty">无匹配用例</div>
        ) : (
          <div className="case-table-wrap">
            <table className="case-table">
              <colgroup>
                <col style={{ width: "8%" }} />
                <col style={{ width: "24%" }} />
                <col style={{ width: "7%" }} />
                <col style={{ width: "6%" }} />
                <col style={{ width: "14%" }} />
                <col style={{ width: "25%" }} />
                <col style={{ width: "16%" }} />
              </colgroup>
              <thead>
                <tr>
                  <th>编号</th>
                  <th>标题</th>
                  <th>类型</th>
                  <th>优先级</th>
                  <th>前置条件</th>
                  <th>操作步骤 → 预期</th>
                  <th>测试数据</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((c) => {
                  const steps = c.steps || [];
                  const exps = c.step_expectations || [];
                  return (
                    <tr key={c.case_id} data-case-id={c.case_id}>
                      <td className="ct-id">{c.case_id}</td>
                      <td className="ct-name">{c.title || dash}</td>
                      <td className="ct-text">{c.case_type || dash}</td>
                      <td className="ct-text">
                        {c.priority ? (
                          <span className={`pill pill-${PRIORITY_CLS[c.priority] || "sub"}`}>{c.priority}</span>
                        ) : (
                          dash
                        )}
                      </td>
                      <td className="ct-text">{c.pre_condition || dash}</td>
                      <td className="ct-text">
                        {steps.length > 0
                          ? steps.map((s, i) => (
                              <div key={i} className="ct-step">
                                <div>
                                  {i + 1}. {s}
                                </div>
                                {exps[i] && <div className="ct-step-exp">预期：{exps[i]}</div>}
                              </div>
                            ))
                          : dash}
                      </td>
                      <td className="ct-text">{c.test_data || dash}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
