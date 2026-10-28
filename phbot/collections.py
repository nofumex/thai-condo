"""Deterministic, cacheable ranking for precomputed collections.

Shared page cache supports successive stages of 1/7/30-day buckets.
No provider requests are needed to open any of the buckets.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from .filters import clean
from .ranking import age_days, comparables, score

WINDOWS = (1, 7, 30)
COLLECTION_CACHE_HOURS = 6
COLLECTION_SCAN_PAGES = 80
COLLECTION_MAX_PAGES = 480
COLLECTION_PAGE_BATCH = 80
COLLECTION_TARGET = 100


def signature(filters: dict) -> str:
    stable = json.dumps({'version': 4, 'filters': clean(filters)}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(stable.encode('utf-8')).hexdigest()


def build_buckets(items: list[dict], now: datetime | None = None, limit: int = COLLECTION_TARGET) -> dict[str, list[str]]:
    """Independent comparable sets per window, stable score tie-breaking.

    Rankings are snapshots: the displayed rating is still recalculated when a
    card is viewed. Expired listings are additionally excluded at read time.
    """
    now = now or datetime.now(timezone.utc)
    result = {}
    for days in WINDOWS:
        eligible = [x for x in items if x.get('id') is not None and age_days(x, now) <= days]
        groups = comparables(eligible, summarize=True)
        ranked = sorted(((score(x, groups, now)[0], str(x['id'])) for x in eligible),
                        key=lambda pair: (-pair[0], pair[1]))
        result[str(days)] = [ident for _, ident in ranked[:limit]]
    return result


def cache_expired(cache: dict | None, now: datetime | None = None) -> bool:
    if not cache or not cache.get('completed_at'):
        return True
    now = now or datetime.now(timezone.utc)
    try:
        from datetime import timedelta
        updated = datetime.fromisoformat(cache['completed_at'])
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return now - updated >= timedelta(hours=COLLECTION_CACHE_HOURS)
    except (TypeError, ValueError):
        return True
