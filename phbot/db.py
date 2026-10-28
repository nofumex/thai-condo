"""Durable SQLite state; only short async statements, WAL, no background locks in Telegram callbacks."""
import json
from .collections import signature, cache_expired
from datetime import datetime, timezone, timedelta
from pathlib import Path
import aiosqlite
from .filters import clean,DEFAULT

def utc():return datetime.now(timezone.utc).isoformat()
class Database:
    def __init__(self,path):self.path=path;self.conn=None
    async def init(self):
        Path(self.path).parent.mkdir(parents=True,exist_ok=True)
        self.conn=await aiosqlite.connect(self.path)
        self.conn.row_factory=aiosqlite.Row
        await self.conn.execute('PRAGMA journal_mode=WAL')
        await self.conn.execute('PRAGMA busy_timeout=5000')
        await self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, created_at TEXT,
          last_seen TEXT, search_json TEXT NOT NULL, latest_json TEXT NOT NULL,
          requests INTEGER DEFAULT 0, cards INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS listings (
          id TEXT PRIMARY KEY, json TEXT NOT NULL, created_at TEXT, refreshed_at TEXT,
          first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, detail_json TEXT, detail_saved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_listings_created ON listings(created_at DESC);
        CREATE TABLE IF NOT EXISTS favorites(user_id INTEGER NOT NULL,listing_id TEXT NOT NULL,
          created_at TEXT NOT NULL, PRIMARY KEY(user_id,listing_id));
        CREATE TABLE IF NOT EXISTS hidden(user_id INTEGER NOT NULL,listing_id TEXT NOT NULL,
          created_at TEXT NOT NULL, PRIMARY KEY(user_id,listing_id));
        CREATE TABLE IF NOT EXISTS collection_cache (
          user_id INTEGER PRIMARY KEY,
          filter_signature TEXT NOT NULL,
          status TEXT NOT NULL,
          buckets_json TEXT NOT NULL DEFAULT '{}',
          scanned_pages INTEGER NOT NULL DEFAULT 0,
          candidates INTEGER NOT NULL DEFAULT 0,
          skipped_corrupt INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL,
          completed_at TEXT,
          error TEXT,
          candidate_ids_json TEXT NOT NULL DEFAULT '[]',
          window_counts_json TEXT NOT NULL DEFAULT '{}',
          target_pages INTEGER NOT NULL DEFAULT 480,
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS search_pages (
          query_key TEXT NOT NULL, page INTEGER NOT NULL, ids_json TEXT NOT NULL,
          meta_json TEXT NOT NULL, fetched_at TEXT NOT NULL,
          PRIMARY KEY(query_key,page)
        );
        CREATE INDEX IF NOT EXISTS idx_search_pages_fetched ON search_pages(fetched_at);
        CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
        # Safe migration for databases created by earlier bot versions.
        async with self.conn.execute('PRAGMA table_info(listings)') as cursor:
            cols={r['name'] for r in await cursor.fetchall()}
        if 'detail_saved_at' not in cols:
            await self.conn.execute('ALTER TABLE listings ADD COLUMN detail_saved_at TEXT')
        async with self.conn.execute('PRAGMA table_info(collection_cache)') as cursor:
            cc={r['name'] for r in await cursor.fetchall()}
        if 'candidate_ids_json' not in cc:
            await self.conn.execute("ALTER TABLE collection_cache ADD COLUMN candidate_ids_json TEXT NOT NULL DEFAULT '[]'")
        if 'window_counts_json' not in cc:
            await self.conn.execute("ALTER TABLE collection_cache ADD COLUMN window_counts_json TEXT NOT NULL DEFAULT '{}'")
        if 'target_pages' not in cc:
            await self.conn.execute('ALTER TABLE collection_cache ADD COLUMN target_pages INTEGER NOT NULL DEFAULT 480')
        await self.conn.commit()
    async def close(self):
        if self.conn:await self.conn.close()
    async def _one(self,query,args=()):
        async with self.conn.execute(query,args) as c:return await c.fetchone()
    async def _all(self,query,args=()):
        async with self.conn.execute(query,args) as c:return await c.fetchall()
    async def user(self,tg):
        uid=tg.id
        await self.conn.execute('''INSERT OR IGNORE INTO users
          (id,username,first_name,created_at,last_seen,search_json,latest_json)
          VALUES(?,?,?,?,?,?,?)''',
          (uid,tg.username or '',tg.first_name or '',utc(),utc(),json.dumps(DEFAULT),json.dumps(DEFAULT)))
        await self.conn.execute('UPDATE users SET last_seen=?,username=?,first_name=? WHERE id=?',
                                (utc(),tg.username or '',tg.first_name or '',uid))
        await self.conn.commit()
    async def get_filters(self,uid,kind='search'):
        row=await self._one('SELECT search_json, latest_json FROM users WHERE id=?',(uid,))
        if not row:return clean(DEFAULT)
        return clean(json.loads(row['latest_json' if kind=='latest' else 'search_json']))
    async def save_filters(self,uid,kind,f):
        """Returns True only if normalized profile values actually changed.

        Invalidation and profile update are atomic, so stale background jobs
        cannot publish results for a different settings version.
        """
        col='latest_json' if kind=='latest' else 'search_json'
        norm=clean(f)
        previous=await self.get_filters(uid,kind)
        changed=norm!=previous
        await self.conn.execute(f'UPDATE users SET {col}=? WHERE id=?',
                                (json.dumps(norm,ensure_ascii=False),uid))
        if kind=='latest' and changed:
            sig=signature(norm)
            await self.conn.execute('''INSERT INTO collection_cache
              (user_id,filter_signature,status,buckets_json,updated_at,completed_at,error)
              VALUES(?,?,'pending','{}',?,NULL,NULL)
              ON CONFLICT(user_id) DO UPDATE SET
                filter_signature=excluded.filter_signature,status='pending',
                buckets_json='{}',scanned_pages=0,candidates=0,skipped_corrupt=0,
                updated_at=excluded.updated_at,completed_at=NULL,error=NULL,
                candidate_ids_json='[]',window_counts_json='{}',target_pages=480''',
              (uid,sig,utc()))
        await self.conn.commit()
        return changed

    async def get_collection_cache(self,uid):
        row=await self._one('SELECT * FROM collection_cache WHERE user_id=?',(uid,))
        if not row:return None
        cache=dict(row)
        try:cache['buckets']=json.loads(cache['buckets_json'])
        except (TypeError,ValueError):cache['buckets']={}
        try:cache['candidate_ids']=json.loads(cache['candidate_ids_json'])
        except (TypeError,ValueError):cache['candidate_ids']=[]
        try:cache['window_counts']=json.loads(cache['window_counts_json'])
        except (TypeError,ValueError):cache['window_counts']={}
        return cache

    async def collection_needs_refresh(self,uid):
        cache=await self.get_collection_cache(uid)
        if not cache:return True
        f=await self.get_filters(uid,'latest')
        if cache['filter_signature']!=signature(f):return True
        if cache['status']=='error':
            try:
                elapsed=datetime.now(timezone.utc)-datetime.fromisoformat(cache['updated_at'])
                return elapsed>=timedelta(minutes=15)
            except (ValueError,TypeError):return True
        if cache['status'] in ('building','pending'):return True
        if not all(str(d) in cache['buckets'] for d in (1,7,30)):return True
        return int(cache.get('scanned_pages') or 0)<int(cache.get('target_pages') or 480) or cache_expired(cache)

    async def active_collection_users(self,days=30,limit=200):
        cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
        rows=await self._all('''SELECT u.id,u.latest_json,c.user_id AS cached
                     FROM users u LEFT JOIN collection_cache c ON c.user_id=u.id
                     WHERE u.last_seen>=? ORDER BY u.last_seen DESC LIMIT ?''',
                     (cutoff,limit))
        # Do not crawl 80 pages for every dormant/default user at first upgrade.
        # Profiles changed by the user and users with previous cache are recovered.
        return [r['id'] for r in rows if r['cached'] is not None or
                clean(json.loads(r['latest_json']))!=clean(DEFAULT)]

    async def begin_collection_build(self,uid,sig,reset=False):
        """Begin/resume without exposing unfinished bucket snapshots."""
        now=utc()
        # Upgrade or settings changes can invalidate an existing signature.
        # INSERT OR IGNORE would retain the old row forever and block builds.
        await self.conn.execute('''INSERT INTO collection_cache
          (user_id,filter_signature,status,updated_at) VALUES(?,?,'pending',?)
          ON CONFLICT(user_id) DO UPDATE SET
            filter_signature=excluded.filter_signature,
            status='pending',buckets_json='{}',candidate_ids_json='[]',window_counts_json='{}',
            scanned_pages=0,candidates=0,skipped_corrupt=0,
            completed_at=NULL,error=NULL,updated_at=excluded.updated_at
          WHERE collection_cache.filter_signature != excluded.filter_signature''',
          (uid,sig,now))
        if reset:
            # Expired snapshot remains visible until its replacement is ready.
            cursor=await self.conn.execute('''UPDATE collection_cache SET
                scanned_pages=0,candidates=0,skipped_corrupt=0,candidate_ids_json='[]',window_counts_json='{}',
                updated_at=?,error=NULL
                WHERE user_id=? AND filter_signature=?''',(now,uid,sig))
        else:
            cursor=await self.conn.execute('''UPDATE collection_cache SET
                status=CASE WHEN buckets_json != '{}'
                            THEN 'ready' ELSE 'building' END,
                updated_at=?,error=NULL
                WHERE user_id=? AND filter_signature=?''',(now,uid,sig))
        await self.conn.commit()
        return cursor.rowcount>0

    async def collection_progress(self,uid,sig,pages,candidates,skipped,ids=None,window_counts=None):
        if ids is None:
            await self.conn.execute('''UPDATE collection_cache SET
                scanned_pages=?,candidates=?,skipped_corrupt=?,updated_at=?
                WHERE user_id=? AND filter_signature=?''',
                (pages,candidates,skipped,utc(),uid,sig))
        else:
            await self.conn.execute('''UPDATE collection_cache SET
                scanned_pages=?,candidates=?,skipped_corrupt=?,candidate_ids_json=?,
                window_counts_json=?,updated_at=?
                WHERE user_id=? AND filter_signature=?''',
                (pages,candidates,skipped,json.dumps(ids),json.dumps(window_counts or {}),utc(),uid,sig))
        await self.conn.commit()

    async def finish_collection_stage(self,uid,sig,buckets,pages,ids,skipped,window_counts=None):
        """Publish every 80-page milestone; keep scanning to 480 in background."""
        now=utc()
        cursor=await self.conn.execute('''UPDATE collection_cache SET
          status='ready',buckets_json=?,scanned_pages=?,candidates=?,
          candidate_ids_json=?,window_counts_json=?,skipped_corrupt=?,
          updated_at=?,completed_at=?,error=NULL
          WHERE user_id=? AND filter_signature=?''',
          (json.dumps(buckets,ensure_ascii=False),pages,len(ids),json.dumps(ids),
           json.dumps(window_counts or {}),skipped,now,now,uid,sig))
        await self.conn.commit()
        return cursor.rowcount>0

    async def finish_collection_build(self,uid,sig,buckets,pages,candidates,skipped):
        """Compatibility for existing callers and tests."""
        now=utc()
        cursor=await self.conn.execute('''UPDATE collection_cache SET
          status='ready',buckets_json=?,scanned_pages=?,candidates=?,
          skipped_corrupt=?,updated_at=?,completed_at=?,error=NULL
          WHERE user_id=? AND filter_signature=? AND status IN ('building','ready')''',
          (json.dumps(buckets,ensure_ascii=False),pages,candidates,skipped,
           now,now,uid,sig))
        await self.conn.commit()
        return cursor.rowcount>0

    async def fail_collection_build(self,uid,sig,error):
        await self.conn.execute('''UPDATE collection_cache SET
          status=CASE WHEN buckets_json != '{}' THEN 'ready' ELSE 'error' END,
          error=?,updated_at=? WHERE user_id=? AND filter_signature=?''',
          (str(error)[:450],utc(),uid,sig))
        await self.conn.commit()

    async def get_search_page(self,key,page,max_age_minutes=30):
        row=await self._one('''SELECT ids_json,meta_json,fetched_at FROM search_pages
            WHERE query_key=? AND page=?''',(key,page))
        if not row:return None
        try:
            age=datetime.now(timezone.utc)-datetime.fromisoformat(row['fetched_at'])
            if age>timedelta(minutes=max_age_minutes):return None
            ids=json.loads(row['ids_json'])
            items=await self.listings_by_ids(ids)
            if len(items)!=len(ids):return None
            return items,json.loads(row['meta_json'])
        except (ValueError,TypeError):return None

    async def store_search_page(self,key,page,items,meta):
        # Listings are global and may be referenced by any user/profile.
        await self.put_listings(items)
        await self.conn.execute('''INSERT INTO search_pages
            (query_key,page,ids_json,meta_json,fetched_at) VALUES(?,?,?,?,?)
            ON CONFLICT(query_key,page) DO UPDATE SET
            ids_json=excluded.ids_json,meta_json=excluded.meta_json,fetched_at=excluded.fetched_at''',
            (key,page,json.dumps([str(i['id']) for i in items]),json.dumps(meta),utc()))
        await self.conn.commit()

    async def prune_search_pages(self,max_age_hours=24):
        cutoff=(datetime.now(timezone.utc)-timedelta(hours=max_age_hours)).isoformat()
        await self.conn.execute('DELETE FROM search_pages WHERE fetched_at<?',(cutoff,))
        await self.conn.commit()

    async def put_listings(self,items:list[dict]):
        now=utc()
        await self.conn.executemany('''INSERT INTO listings(id,json,created_at,refreshed_at,first_seen,last_seen)
           VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET json=excluded.json,
           created_at=excluded.created_at,refreshed_at=excluded.refreshed_at,last_seen=excluded.last_seen''',
           [(str(x['id']),json.dumps(x,ensure_ascii=False),x.get('createdAt'),x.get('refreshedAt'),now,now) for x in items if x.get('id')])
        await self.conn.commit()
    async def store_detail(self,id,detail):
        await self.conn.execute('UPDATE listings SET detail_json=?,detail_saved_at=? WHERE id=?',(json.dumps(detail,ensure_ascii=False),utc(),str(id)))
        await self.conn.commit()
    async def get_listing(self,id):
        row=await self._one('SELECT json,detail_json,detail_saved_at FROM listings WHERE id=?',(str(id),))
        if not row:return None,None
        detail=None
        if row['detail_json'] and row['detail_saved_at']:
            try:
                elapsed=datetime.now(timezone.utc)-datetime.fromisoformat(row['detail_saved_at'])
                if elapsed<timedelta(hours=24):detail=json.loads(row['detail_json'])
            except (ValueError,TypeError):pass
        return json.loads(row['json']),detail
    async def all_listings(self,limit=12000):
        rows=await self._all('SELECT json FROM listings ORDER BY created_at DESC LIMIT ?',(limit,))
        return [json.loads(r['json']) for r in rows]
    async def listings_by_ids(self,ids):
        if not ids:return []
        result={}
        # SQLite parameter count differs by build; batches stay conservative.
        for start in range(0,len(ids),400):
            part=ids[start:start+400]
            marks=','.join('?' for _ in part)
            rows=await self._all(f'SELECT id,json FROM listings WHERE id IN ({marks})',tuple(map(str,part)))
            result.update({r['id']:json.loads(r['json']) for r in rows})
        return [result[str(id)] for id in ids if str(id) in result]
    async def listing_ids(self,uid,what='favorites'):
        if what not in ('favorites','hidden'):raise ValueError('bad table')
        rows=await self._all(f'SELECT listing_id FROM {what} WHERE user_id=? ORDER BY created_at DESC',(uid,))
        return [r['listing_id'] for r in rows]
    async def is_marked(self,uid,id,what):
        if what not in ('favorites','hidden'):raise ValueError('bad table')
        return bool(await self._one(f'SELECT 1 FROM {what} WHERE user_id=? AND listing_id=?',(uid,str(id))))
    async def toggle(self,uid,id,what):
        if what not in ('favorites','hidden'):raise ValueError('bad table')
        if await self.is_marked(uid,id,what):
            await self.conn.execute(f'DELETE FROM {what} WHERE user_id=? AND listing_id=?',(uid,str(id)))
            val=False
        else:
            await self.conn.execute(f'INSERT OR IGNORE INTO {what}(user_id,listing_id,created_at) VALUES(?,?,?)',(uid,str(id),utc()))
            val=True
        await self.conn.commit()
        return val
    async def metric(self,uid,kind):
        if kind not in ('requests','cards'):return
        await self.conn.execute(f'UPDATE users SET {kind}={kind}+1 WHERE id=?',(uid,))
        await self.conn.commit()
    async def get_config(self,key,default=None):
        row=await self._one('SELECT value FROM config WHERE key=?',(key,))
        return json.loads(row['value']) if row else default
    async def set_config(self,key,val):
        await self.conn.execute('INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(val)))
        await self.conn.commit()
    async def stats(self):
        row=await self._one('''SELECT COUNT(*) AS users,SUM(requests) AS requests,SUM(cards) AS cards,
                         SUM(CASE WHEN last_seen>=? THEN 1 ELSE 0 END) AS daily FROM users''',
                         ((datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),))
        other=await self._one('SELECT COUNT(*) AS total, COUNT(detail_json) AS details FROM listings')
        counts={}
        for table in ('favorites','hidden'):
            x=await self._one(f'SELECT COUNT(*) AS n FROM {table}')
            counts[table]=x['n']
        return {**dict(row),**dict(other),**counts}
