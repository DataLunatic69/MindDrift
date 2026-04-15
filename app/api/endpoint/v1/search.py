from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.fragments import FragmentRead
from app.core.database import get_db
from app.models.user import User
from app.security import get_current_user
from app.services.search_service import SearchService

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/", response_model=list[dict])
async def search_fragments(
    q: str = Query(..., min_length=1, max_length=500),
    mode: str = Query("hybrid", pattern="^(semantic|keyword|hybrid)$"),
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Search fragments by meaning (semantic), text match (keyword), or both (hybrid).
    """
    service = SearchService(db)

    if mode == "semantic":
        results = await service.semantic_search(q, user.id, limit)
    elif mode == "keyword":
        fragments = await service.keyword_search(q, user.id, limit)
        results = [{"fragment": f, "score": 0.0} for f in fragments]
    else:
        results = await service.hybrid_search(q, user.id, limit)

    # Serialize
    return [
        {
            "fragment": FragmentRead.model_validate(r["fragment"]).model_dump(mode="json"),
            "score": r["score"],
        }
        for r in results
    ]
