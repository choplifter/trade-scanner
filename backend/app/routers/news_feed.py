"""REST half of the live cross-symbol news feed -- the initial snapshot a
NewsFeedWidget seeds itself with on mount. New items after that arrive
over /ws/news-feed (see ws/news_feed_ws.py) instead of a repeat poll here.
"""

from fastapi import APIRouter, Depends, Request

from app.auth.dependency import get_current_user

router = APIRouter(prefix="/api/news-feed", tags=["news-feed"])


@router.get("/recent")
async def get_recent_news_feed(
    request: Request, limit: int = 50, user: dict = Depends(get_current_user)
) -> dict:
    tracker = request.app.state.news_feed_tracker
    return {"items": [item.model_dump(mode="json") for item in tracker.recent(limit)]}
