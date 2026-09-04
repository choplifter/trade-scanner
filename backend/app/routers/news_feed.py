"""REST half of the live news feed -- the initial snapshot a NewsFeedWidget
seeds itself with on mount. New items after that arrive over
/ws/news-feed (see ws/news_feed_ws.py) instead of a repeat poll here.
"""

from fastapi import APIRouter, Depends, Query, Request

from app.auth.dependency import get_current_user

router = APIRouter(prefix="/api/news-feed", tags=["news-feed"])


@router.get("/recent")
async def get_recent_news_feed(
    request: Request,
    limit: int = 50,
    ranked_only: bool = Query(default=False, description="Only articles tagged to a currently ranked scanner symbol"),
    user: dict = Depends(get_current_user),
) -> dict:
    tracker = request.app.state.news_feed_tracker
    stream = getattr(request.app.state, "news_stream", None)
    return {
        "items": [item.model_dump(mode="json") for item in tracker.recent(limit, ranked_only=ranked_only)],
        "stream_connected": bool(getattr(stream, "connected", False)),
    }
