/**
 * 分类树（M4）：任务列表侧栏顶部。
 * - 「全部 / 未分类」固定项 + 用户分类树
 * - 新建（顶级 / 子分类）、重命名、删除；点击分类行 = 过滤任务列表（filter 存 categoryStore）
 */
import { useEffect, useMemo, useState } from "react";
import { buildTree, useCategoryStore, type CategoryFilter } from "../../store/categoryStore";
import { useTaskStore } from "../../store/taskStore";
import { toast } from "../../api/client";
import type { CategoryNode } from "../../types";

type CatTreeNode = CategoryNode & { depth: number; children: CategoryNode[] };

function CatRow({ cat }: { cat: CatTreeNode }) {
  const categories = useCategoryStore((s) => s.categories);
  const filter = useCategoryStore((s) => s.filter);
  const setFilter = useCategoryStore((s) => s.setFilter);
  const rename = useCategoryStore((s) => s.rename);
  const remove = useCategoryStore((s) => s.remove);
  const create = useCategoryStore((s) => s.create);
  const collapsed = useCategoryStore((s) => s.collapsed);
  const toggleCollapse = useCategoryStore((s) => s.toggleCollapse);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(cat.name);
  const isOn = filter === cat.id;

  const commitRename = async () => {
    const v = name.trim();
    setEditing(false);
    if (!v || v === cat.name) return;
    if (await rename(cat.id, v)) toast("已重命名");
  };

  return (
    <>
      <div
        className={"cat-row" + (isOn ? " on" : "")}
        style={{ paddingLeft: 8 + cat.depth * 14 }}
        data-cat-id={cat.id}
        onClick={() => setFilter(isOn ? "all" : (cat.id as CategoryFilter))}
        title="点击筛选该分类下任务"
      >
        {cat.children.length > 0 && (
          <button
            className="cat-twisty"
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              toggleCollapse(cat.id);
            }}
          >
            {collapsed[cat.id] ? "▸" : "▾"}
          </button>
        )}
        <span className="cat-name">{cat.name}</span>
        {cat.is_auto && <span className="cat-auto-tag" title="页面自动派生分类">自动</span>}
        <span className="cat-count">{cat.task_count}</span>
        <button
          className="h-del cat-add"
          type="button"
          title="新建子分类"
          onClick={(e) => {
            e.stopPropagation();
            const v = window.prompt(`在「${cat.name}」下新建子分类名称：`);
            if (v && v.trim()) void create(v.trim(), cat.id);
          }}
        >
          ＋
        </button>
        <button
          className="h-del cat-ren"
          type="button"
          title="重命名"
          onClick={(e) => {
            e.stopPropagation();
            setName(cat.name);
            setEditing(true);
          }}
        >
          ✎
        </button>
        <button
          className="h-del cat-rm"
          type="button"
          title="删除分类（子分类级联，任务回落未分类）"
          onClick={(e) => {
            e.stopPropagation();
            void remove(cat.id);
          }}
        >
          🗑
        </button>
      </div>
      {editing && (
        <div className="cat-edit" style={{ paddingLeft: 8 + cat.depth * 14 }}>
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void commitRename();
              if (e.key === "Escape") setEditing(false);
            }}
            onBlur={() => void commitRename()}
            data-testid={`cat-rename-${cat.id}`}
          />
        </div>
      )}
      {!collapsed[cat.id] &&
        cat.children.length > 0 &&
        buildTree(categories, cat.id, cat.depth + 1).map((child) => (
          <CatRow key={child.id} cat={child} />
        ))}
    </>
  );
}

export default function CategoryTree() {
  const categories = useCategoryStore((s) => s.categories);
  const loaded = useCategoryStore((s) => s.loaded);
  const filter = useCategoryStore((s) => s.filter);
  const setFilter = useCategoryStore((s) => s.setFilter);
  const create = useCategoryStore((s) => s.create);
  const refresh = useCategoryStore((s) => s.refresh);
  // 计数语义修正（V5.12.3）：全部/未分类 直接从任务列表统计（与库页标题一致），
  // 旧「全部=各分类 task_count 之和」漏掉未分类任务，数字对不上
  const tasks = useTaskStore((s) => s.tasks);
  const allCount = tasks.length;
  const noneCount = tasks.filter((t) => t.category_id == null).length;
  const [newName, setNewName] = useState("");
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const tree = useMemo(() => buildTree(categories, null), [categories]);

  const submitNew = async () => {
    const v = newName.trim();
    setAdding(false);
    setNewName("");
    if (!v) return;
    // 头部「＋ 新建」= 顶级分类
    await create(v, null);
  };

  return (
    <div className="cat-panel" data-testid="category-tree">
      <div className="cat-head">
        <span>分类</span>
        <button
          className="h-del"
          type="button"
          title="新建顶级分类"
          data-testid="cat-new-btn"
          onClick={() => setAdding(true)}
        >
          ＋ 新建
        </button>
      </div>
      <div
        className={"cat-row fixed" + (filter === "all" ? " on" : "")}
        onClick={() => setFilter("all")}
        data-testid="cat-all"
      >
        <span className="cat-name">全部</span>
        <span className="cat-count">{allCount}</span>
      </div>
      {tree.map((c) => (
        <CatRow key={c.id} cat={c} />
      ))}
      <div
        className={"cat-row fixed" + (filter === "none" ? " on" : "")}
        onClick={() => setFilter("none")}
        data-testid="cat-none"
        title="未归入任何分类的任务"
      >
        <span className="cat-name">未分类</span>
        <span className="cat-count">{noneCount}</span>
      </div>
      {adding && (
        <div className="cat-edit">
          <input
            autoFocus
            placeholder="新分类名称"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void submitNew();
              if (e.key === "Escape") {
                setAdding(false);
                setNewName("");
              }
            }}
            onBlur={() => void submitNew()}
            data-testid="cat-new-input"
          />
        </div>
      )}
      {loaded && categories.length === 0 && !adding && (
        <div className="cat-empty">暂无分类，点「＋ 新建」整理任务</div>
      )}
    </div>
  );
}
