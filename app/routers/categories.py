from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models import Category, Course, User
from app.schemas import (
    ApiResponse,
    CategoryCreate,
    CategoryOut,
    CategoryTreeNode,
    CategoryUpdate,
)

router = APIRouter(prefix="/api/categories", tags=["categories"])


# ----------------------------------------------------------------- helpers
def _is_teacher(user: User) -> bool:
    return user.role == "teacher"


def _get_or_404(db: Session, category_id: int, user: User) -> Category:
    cat = db.get(Category, category_id)
    # Students must not even learn that a hidden category exists
    if cat is None or (not cat.visible and not _is_teacher(user)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found.")
    return cat


def _check_parent(db: Session, parent_id: int | None, self_id: int | None = None):
    """Parent must exist, and a category can't be moved under itself/its descendants."""
    if parent_id is None:
        return
    node = db.get(Category, parent_id)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent category not found.")
    while node is not None:
        if self_id is not None and node.id == self_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "A category cannot be moved inside itself or its own subcategory.",
            )
        node = node.parent


def _check_unique(
    db: Session,
    name: str | None,
    parent_id: int | None,
    id_number: str | None,
    exclude_id: int | None = None,
):
    if name is not None:
        q = select(Category.id).where(
            func.lower(Category.name) == name.lower(),
            Category.parent_id.is_(None)
            if parent_id is None
            else Category.parent_id == parent_id,
        )
        if exclude_id:
            q = q.where(Category.id != exclude_id)
        if db.scalar(q):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "A category with this name already exists in the same parent.",
            )
    if id_number:
        q = select(Category.id).where(Category.id_number == id_number)
        if exclude_id:
            q = q.where(Category.id != exclude_id)
        if db.scalar(q):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Category ID number already in use."
            )


def descendant_ids(db: Session, category_id: int) -> list[int]:
    """The category itself plus all nested subcategories."""
    rows = db.execute(select(Category.id, Category.parent_id)).all()
    children: dict[int | None, list[int]] = {}
    for cid, pid in rows:
        children.setdefault(pid, []).append(cid)
    result, stack = [], [category_id]
    while stack:
        cur = stack.pop()
        result.append(cur)
        stack.extend(children.get(cur, []))
    return result


# --------------------------------------------------------------------- TREE
@router.get("/tree", response_model=ApiResponse[list[CategoryTreeNode]])
def category_tree(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Whole category hierarchy with course counts (good for a sidebar/menu)."""
    q = select(Category).order_by(Category.sort_order, Category.name)
    if not _is_teacher(user):
        q = q.where(Category.visible.is_(True))
    cats = db.scalars(q).all()

    cq = select(Course.category_id, func.count(Course.id)).group_by(Course.category_id)
    if not _is_teacher(user):
        cq = cq.where(Course.visible.is_(True))
    counts = dict(db.execute(cq).all())

    nodes = {
        c.id: CategoryTreeNode(
            **CategoryOut.model_validate(c).model_dump(),
            course_count=counts.get(c.id, 0),
            children=[],
        )
        for c in cats
    }
    roots: list[CategoryTreeNode] = []
    for c in cats:
        parent = nodes.get(c.parent_id) if c.parent_id else None
        (parent.children if parent else roots).append(nodes[c.id])
    return ApiResponse(data=roots)


# ------------------------------------------------------------------- CREATE
@router.post(
    "",
    response_model=ApiResponse[CategoryOut],
    status_code=status.HTTP_201_CREATED,
)
def create_category(
    payload: CategoryCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    _check_parent(db, payload.parent_id)
    _check_unique(db, payload.name, payload.parent_id, payload.id_number)

    cat = Category(**payload.model_dump(), created_by=user.id)
    try:
        db.add(cat)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Category already exists.")
    return ApiResponse(
        message="Category created.", data=CategoryOut.model_validate(cat)
    )


# --------------------------------------------------------------------- LIST
@router.get("", response_model=ApiResponse[list[CategoryOut]])
def list_categories(
    parent_id: int | None = Query(
        None, alias="parentId", description="Only direct children of this category"
    ),
    top_level: bool = Query(False, alias="topLevel", description="Only root categories"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(Category).order_by(Category.sort_order, Category.name)
    if parent_id is not None:
        q = q.where(Category.parent_id == parent_id)
    elif top_level:
        q = q.where(Category.parent_id.is_(None))
    if not _is_teacher(user):
        q = q.where(Category.visible.is_(True))
    return ApiResponse(data=[CategoryOut.model_validate(c) for c in db.scalars(q)])


# ---------------------------------------------------------------------- GET
@router.get("/{category_id}", response_model=ApiResponse[CategoryOut])
def get_category(
    category_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return ApiResponse(
        data=CategoryOut.model_validate(_get_or_404(db, category_id, user))
    )


# ------------------------------------------------------------------- UPDATE
@router.patch("/{category_id}", response_model=ApiResponse[CategoryOut])
def update_category(
    category_id: int,
    payload: CategoryUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    cat = _get_or_404(db, category_id, user)
    changes = payload.model_dump(exclude_unset=True)

    # name / visible / sort_order can't be null
    for field in ("name", "visible", "sort_order"):
        if field in changes and changes[field] is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"{field} cannot be null."
            )

    new_parent = changes.get("parent_id", cat.parent_id)
    if "parent_id" in changes:
        _check_parent(db, new_parent, self_id=cat.id)
    _check_unique(
        db,
        changes.get("name", cat.name) if ("name" in changes or "parent_id" in changes) else None,
        new_parent,
        changes.get("id_number"),
        exclude_id=cat.id,
    )

    for key, value in changes.items():
        setattr(cat, key, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Category update conflicts with existing data.")
    return ApiResponse(
        message="Category updated.", data=CategoryOut.model_validate(cat)
    )


# ------------------------------------------------------------------- DELETE
@router.delete("/{category_id}")
def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    cat = _get_or_404(db, category_id, user)

    has_children = db.scalar(select(Category.id).where(Category.parent_id == cat.id).limit(1))
    has_courses = db.scalar(select(Course.id).where(Course.category_id == cat.id).limit(1))
    if has_children or has_courses:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Category is not empty. Move or delete its subcategories and courses first.",
        )

    db.delete(cat)
    db.commit()
    return {"success": True, "message": "Category deleted."}