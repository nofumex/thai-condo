"""UI and staged prepared collections (no external requests)."""
import asyncio
import unittest
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import patch
from phbot.bot import Application
from phbot.collections import signature
from phbot.filters import clean
from test_flow import FakeAPI, FakeBot, FakeDB, sample

class StageDB(FakeDB):
    def __init__(self):
        super().__init__()
        self.filters=clean({})
        self.cache={'filter_signature':signature(self.filters),'status':'pending',
           'buckets':{},'scanned_pages':0,'candidates':0,'candidate_ids':[],
           'skipped_corrupt':0,'completed_at':None}
        self.stages=[]
    async def get_filters(self,uid,kind='latest'):return self.filters.copy()
    async def get_collection_cache(self,uid):return self.cache.copy()
    async def begin_collection_build(self,uid,sig,reset=False):
        self.cache['status']='building'
        return True
    async def collection_progress(self,uid,sig,pages,candidates,skipped,ids=None,window_counts=None):
        self.cache.update(scanned_pages=pages,candidates=candidates,window_counts=window_counts or {})
        if ids is not None:self.cache['candidate_ids']=list(ids)
    async def finish_collection_stage(self,uid,sig,buckets,pages,ids,skipped,window_counts=None):
        if sig!=self.cache['filter_signature']:return False
        self.cache.update(status='ready',buckets=buckets,scanned_pages=pages,
                          candidates=len(ids),candidate_ids=list(ids),
                          completed_at=datetime.now(timezone.utc).isoformat())
        self.stages.append((pages,{x:tuple(y) for x,y in buckets.items()}))
        return True
    async def fail_collection_build(self,*args):raise AssertionError('Unexpected collection error')

class SlowAPI(FakeAPI):
    def __init__(self,pages):
        super().__init__(pages)
        self.third_started=asyncio.Event()
        self.third_release=asyncio.Event()
    async def search_page(self,filters,page=1,per_page=60):
        if page==3:
            self.third_started.set()
            await self.third_release.wait()
        return await super().search_page(filters,page,per_page)

class PreparedCollectionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_panel_only_three_period_buttons_and_back(self):
        app=Application.__new__(Application)
        app.panel_kind={};app.panels={};app.bot=FakeBot()
        await app.collections_panel(1,1)
        msg=app.bot.messages[-1]
        self.assertNotIn('Пересборка',msg[1][1])
        keyboard=msg[2]['reply_markup'].inline_keyboard
        self.assertEqual(len(keyboard),2)
        self.assertEqual([x.callback_data for x in keyboard[0]],
            ['go:collection:1','go:collection:7','go:collection:30'])
        self.assertEqual(keyboard[1][0].callback_data,'go:home')

    async def test_snapshot_published_while_later_pages_continue(self):
        pages={i:[sample(i*10+j) for j in range(1,3)] for i in range(1,5)}
        now=datetime.now(timezone.utc).isoformat()
        for group in pages.values():
            for x in group:x['createdAt']=now
        for x in pages[3]+pages[4]:x['price']['forRent']['monthly']['price']=5000
        api=SlowAPI(pages)
        app=Application.__new__(Application)
        app.db=StageDB();app.api=api;app.bot=FakeBot()
        app.collection_semaphore=asyncio.Semaphore(1)
        with patch('phbot.bot.COLLECTION_PAGE_BATCH',2),patch('phbot.bot.COLLECTION_MAX_PAGES',4):
            job=asyncio.create_task(app._prepare_collections(1,delay=0))
            try:
                await asyncio.wait_for(api.third_started.wait(),3)
                self.assertEqual([s[0] for s in app.db.stages],[2])
                self.assertEqual(app.db.cache['status'],'ready')
                first=app.db.cache['buckets']['7'][:]
                self.assertEqual(len(first),4)
            finally:
                api.third_release.set()
                await job
        self.assertEqual([s[0] for s in app.db.stages],[2,4])
        self.assertEqual(len(app.db.cache['buckets']['7']),8)
        self.assertEqual(set(app.db.cache['buckets']),{'1','7','30'})

if __name__=='__main__':unittest.main()

class PendingCollectionUITest(unittest.IsolatedAsyncioTestCase):
    async def test_wait_panel_auto_opens_once_first_snapshot_is_published(self):
        app=Application.__new__(Application)
        app.panel_kind={1:'collection-wait:7'}
        app.panels={};app.catalogs={};app.bot=FakeBot()
        app.fx=SimpleNamespace(rate=None);app.photo_cache={}
        app.db=StageDB();app.api=FakeAPI({1:[]})
        sample_listing=sample(123)
        sample_listing['createdAt']=datetime.now(timezone.utc).isoformat()
        await app.db.put_listings([sample_listing])
        with patch('phbot.bot.COLLECTION_WAIT_POLL_SECONDS',.01):
            worker=asyncio.create_task(app._collection_wait(1,1,7,signature(app.db.filters)))
            await asyncio.sleep(.05)
            self.assertIn('Формируется подборка',app.bot.messages[-1][1][1])
            self.assertIn('проверено 0 из 80',app.bot.messages[-1][1][1])
            app.db.cache.update(status='ready',buckets={'1':['123'],'7':['123'],'30':['123']},
                                scanned_pages=80,candidates=1)
            await asyncio.wait_for(worker,2)
        self.assertEqual(app.panel_kind[1],'catalog')
        self.assertEqual(app.catalogs[1].ids,['123'])
        self.assertIsNotNone(app.catalogs[1].message_id)
        self.assertEqual(app.api.requests,[])
