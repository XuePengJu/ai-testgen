"""提示词自定义 API（V5.10 FR-AK）。

- 登录用户编辑自己的提示词（对话系统提示词 + 用例生成模板）
- 访客 403（对齐模型配置权限）
- GET    /api/prompts           可编辑项清单（含是否已自定义）
- GET    /api/prompts/{key}     单项详情（默认内容 + 当前生效内容）
- PUT    /api/prompts/{key}     保存自定义（生成模板强校验占位符）
- DELETE /api/prompts/{key}     恢复默认（删 override 记录）
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_user
from app.core.db import get_db
from app.models.user import User
from app.services import prompt_service

router = APIRouter(prefix="/prompts", tags=["提示词"])


@router.get("")
def list_prompts(user: User = Depends(require_user),
                 db: Session = Depends(get_db)):
    items = []
    for key, meta in prompt_service.ALL_KEYS.items():
        items.append({
            "key": key,
            "group": meta["group"],
            "name": meta["name"],
            "description": meta["description"],
            "has_override": prompt_service.get_override(db, user.id, key) is not None,
        })
    return {"items": items}


@router.get("/{key}")
def get_prompt(key: str, user: User = Depends(require_user),
               db: Session = Depends(get_db)):
    if key not in prompt_service.ALL_KEYS:
        raise HTTPException(status_code=404, detail="提示词条目不存在")
    meta = prompt_service.ALL_KEYS[key]
    override = prompt_service.get_override(db, user.id, key)
    return {
        "key": key,
        "group": meta["group"],
        "name": meta["name"],
        "description": meta["description"],
        "default": prompt_service.default_of(key),
        "override": override,
        "content": override if override is not None else prompt_service.default_of(key),
    }


class PromptContentIn(BaseModel):
    content: str = Field(min_length=1)


@router.put("/{key}")
def put_prompt(key: str, body: PromptContentIn,
               user: User = Depends(require_user),
               db: Session = Depends(get_db)):
    if key not in prompt_service.ALL_KEYS:
        raise HTTPException(status_code=404, detail="提示词条目不存在")
    try:
        prompt_service.put_override(db, user.id, key, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "key": key}


@router.delete("/{key}")
def delete_prompt(key: str, user: User = Depends(require_user),
                  db: Session = Depends(get_db)):
    if key not in prompt_service.ALL_KEYS:
        raise HTTPException(status_code=404, detail="提示词条目不存在")
    removed = prompt_service.delete_override(db, user.id, key)
    return {"ok": True, "removed": removed}
