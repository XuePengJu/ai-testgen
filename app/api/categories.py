"""任务分类 API：多级分类树 CRUD + 拖拽移动 + 任务归类。

规则：
- 每个用户独立的分类树（user_id 隔离，guest 亦然）
- 删除分类：子分类级联删除，其下任务全部回落「未分类」（category_id=NULL）
- 移动分类（改 parent）：沿祖先链防环——不能把自己挂到自己的子孙下
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func, delete, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models.category import Category
from app.models.task import Task
from app.models.user import User

router = APIRouter(prefix="/categories", tags=["分类"])


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    parent_id: int | None = None


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    parent_id: int | None = None      # None=移到顶级；显式传数字=挂到该分类下


class TaskMove(BaseModel):
    category_id: int | None = None    # None=移回未分类


def _own_cat(db: Session, user: User, cat_id: int) -> Category:
    c = db.get(Category, cat_id)
    if not c or c.user_id != user.id:
        raise HTTPException(status_code=404, detail="分类不存在")
    return c


def _out(c: Category, task_count: int = 0) -> dict:
    return {"id": c.id, "name": c.name, "parent_id": c.parent_id,
            "is_auto": bool(c.is_auto), "task_count": task_count}


def _descendant_ids(db: Session, root_id: int, user_id: int) -> set[int]:
    """root_id 及其全部子孙 id（用于防环校验与级联删除）。"""
    ids = {root_id}
    while True:
        children = db.execute(
            select(Category.id).where(
                Category.user_id == user_id, Category.parent_id.in_(ids),
                ~Category.id.in_(ids))
        ).all()
        new = {c[0] for c in children} - ids
        if not new:
            return ids
        ids |= new


@router.get("")
def list_categories(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Category).where(Category.user_id == user.id).order_by(Category.sort, Category.id)
    ).scalars().all()
    counts: dict[int, int] = {}
    for row in db.execute(select(Task.category_id).where(
            Task.user_id == user.id, Task.category_id.isnot(None))).all():
        cid = row[0]
        counts[cid] = counts.get(cid, 0) + 1
    return [_out(c, counts.get(c.id, 0)) for c in rows]


@router.post("", status_code=201)
def create_category(body: CategoryIn, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if body.parent_id is not None:
        _own_cat(db, user, body.parent_id)
    c = Category(name=body.name.strip(), parent_id=body.parent_id, user_id=user.id,
                 is_auto=False)
    db.add(c)
    db.commit()
    db.refresh(c)
    return _out(c, 0)


@router.patch("/{cat_id}")
def patch_category(cat_id: int, body: CategoryPatch,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = _own_cat(db, user, cat_id)
    if body.name is not None:
        c.name = body.name.strip()
    if "parent_id" in body.model_fields_set:   # 显式传了 parent_id 才动层级（重命名不触碰）
        if body.parent_id is not None:
            target = _own_cat(db, user, body.parent_id)
            if target.id == c.id:
                raise HTTPException(status_code=400, detail="不能移动到自己下面")
            if target.id in _descendant_ids(db, c.id, user.id):
                raise HTTPException(status_code=400, detail="不能移动到自己的子分类下（会形成循环）")
        c.parent_id = body.parent_id            # 显式 null = 移到顶级，合法
    db.commit()
    return {"ok": True, "id": c.id, "name": c.name, "parent_id": c.parent_id,
            "is_auto": bool(c.is_auto)}


@router.delete("/{cat_id}")
def delete_category(cat_id: int, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    c = _own_cat(db, user, cat_id)
    ids = _descendant_ids(db, c.id, user.id)          # 含自身，级联删子树
    result = db.execute(
        update(Task).where(
            Task.user_id == user.id, Task.category_id.in_(ids)
        ).values(category_id=None)
    )
    moved = result.rowcount
    db.execute(delete(Category).where(Category.id.in_(ids)))
    db.commit()
    return {"ok": True, "deleted_categories": len(ids), "tasks_to_uncategorized": moved}


@router.put("/move-task/{task_id}")
def move_task_by_id(task_id: str, body: TaskMove,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """任务归类：拖拽任务到分类节点时调用；category_id=None 表示移回未分类。"""
    t = db.get(Task, task_id)
    if not t or t.user_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    if body.category_id is not None:
        _own_cat(db, user, body.category_id)   # 只能归到自己名下的分类
    t.category_id = body.category_id
    db.commit()
    return {"ok": True, "task_id": t.id, "category_id": t.category_id}
