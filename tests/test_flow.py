"""Offline async smoke tests for catalog pagination and empty-state cleanup."""
import sys,types,unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    import aiogram
    import aiogram.types
    import aiogram.filters
    import aiogram.exceptions
except ImportError:
    aiogram=sys.modules.setdefault('aiogram',types.ModuleType('aiogram'))
    for name in ('Bot','Dispatcher','Router'):
        if not hasattr(aiogram,name):setattr(aiogram,name,type(name,(),{}))
    if not hasattr(aiogram,'F'):
        aiogram.F=SimpleNamespace(data=SimpleNamespace(startswith=lambda x:x))
    filters=sys.modules.setdefault('aiogram.filters',types.ModuleType('aiogram.filters'))
    filters.CommandStart=lambda *x,**k:None
    filters.Command=lambda *x,**k:None
    at=sys.modules.setdefault('aiogram.types',types.ModuleType('aiogram.types'))
    for name in ('Message','CallbackQuery'):
        if not hasattr(at,name):setattr(at,name,type(name,(),{}))
    if not hasattr(at,'InputMediaPhoto'):at.InputMediaPhoto=lambda **k:k
    if not hasattr(at,'InlineKeyboardMarkup'):
        at.InlineKeyboardMarkup=lambda inline_keyboard:SimpleNamespace(inline_keyboard=inline_keyboard)
    if not hasattr(at,'InlineKeyboardButton'):
        at.InlineKeyboardButton=lambda **k:SimpleNamespace(**k)
    errors=sys.modules.setdefault('aiogram.exceptions',types.ModuleType('aiogram.exceptions'))
    errors.TelegramBadRequest=type('TelegramBadRequest',(Exception,),{})
    errors.TelegramForbiddenError=type('TelegramForbiddenError',(Exception,),{})
try:import aiosqlite
except ImportError:sys.modules.setdefault('aiosqlite',types.ModuleType('aiosqlite'))

from phbot.bot import Application,Catalog
from phbot.filters import clean,DEFAULT,matches_local
from phbot.propertyhub import price_of


def sample(i):
    return {'id':str(i),'title':f'Condo {i}','slug':f'condo-{i}',
            'coverPicture':'/a.jpg','createdAt':'2026-10-27T12:00:00Z',
            'price':{'forRent':{'monthly':{'price':15000}}},
            'roomInformation':{'roomArea':30,'onFloor':'6','numberOfBed':1},
            'project':{'nameEnglish':'Condo Test','address':'Bangkok'},
            'location':{'lat':13.706,'lng':100.602}}

class FakeDB:
    def __init__(self):self.saved={};self.count=0
    async def metric(self,*args):self.count+=1
    async def listing_ids(self,*args):return []
    async def put_listings(self,rows):self.saved.update((x['id'],x) for x in rows)
    async def get_listing(self,id):return self.saved.get(str(id)),None
    async def listings_by_ids(self,ids):return [self.saved[i] for i in ids if i in self.saved]
    async def is_marked(self,*args):return False

class FakeAPI:
    def __init__(self, pages):self.pages=pages;self.requests=[]
    async def search_page(self,f, page=1,per_page=60):
        self.requests.append(page)
        return [x for x in self.pages.get(page,[]) if matches_local(x,f)],{'totalPages':max(self.pages),'totalCount':sum(map(len,self.pages.values()))}

class FakeBot:
    def __init__(self):self.n=100;self.deleted=[];self.messages=[]
    async def send_message(self,*a,**k):
        self.n+=1;self.messages.append((self.n,a,k))
        return SimpleNamespace(message_id=self.n)
    async def send_photo(self,*a,**k):
        self.n+=1;return SimpleNamespace(message_id=self.n,photo=[])
    async def delete_message(self,chat_id,message_id):self.deleted.append(message_id)
    async def edit_message_reply_markup(self,*a,**k):return None
    async def edit_message_media(self,*a,**k):return None
    async def edit_message_text(self,*a,**k):return None

class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_incremental_pagination_over_previous_limit(self):
        app=Application.__new__(Application)
        app.db=FakeDB();app.api=FakeAPI({i:[sample(j) for j in range((i-1)*60+1,i*60+1)] for i in range(1,13)})
        app.bot=FakeBot();app.fx=SimpleNamespace(rate=None);app.photo_cache={}
        c=Catalog(1,1,'search','Search',clean(DEFAULT))
        await app._load(c)
        self.assertEqual(len(c.ids),480)
        self.assertFalse(c.done)
        self.assertEqual(app.api.requests,list(range(1,9)))
        c.index=479;c.want_next=True
        await app._load(c)
        self.assertEqual(len(c.ids),540)
        self.assertEqual(c.index,480)

    async def test_first_card_before_second_page_completes(self):
        """Do not wait for rare-filter page 48 or target 80 before UI."""
        import asyncio
        page2_entered=asyncio.Event()
        release_page2=asyncio.Event()
        class SlowAPI(FakeAPI):
            async def search_page(self,f,page=1,per_page=60):
                if page==2:
                    page2_entered.set()
                    await release_page2.wait()
                return await super().search_page(f,page,per_page)
        entry=sample(1)
        entry['priceLandAndHouse']={'forRent':{'deposit':{'type':'MONTH','month':1}}}
        app=Application.__new__(Application)
        app.db=FakeDB();app.api=SlowAPI({1:[entry],2:[sample(2)]})
        app.bot=FakeBot();app.fx=SimpleNamespace(rate=None);app.photo_cache={}
        c=Catalog(1,1,'latest','Новые',clean({'deposit_max_months':1}))
        work=asyncio.create_task(app._load(c))
        try:
            await asyncio.wait_for(page2_entered.wait(),2)
            self.assertIsNotNone(c.message_id,'First listing must already be visible')
            self.assertEqual(c.ids,['1'])
            self.assertIn(c.progress_message,(None,))
            self.assertIn(101,app.bot.deleted,'Loading message should be gone after first card')
        finally:
            release_page2.set()
            await work

    async def test_no_results_message_is_removed(self):
        app=Application.__new__(Application)
        app.db=FakeDB();app.api=FakeAPI({1:[]});app.bot=FakeBot();app.fx=SimpleNamespace(rate=None);app.photo_cache={}
        c=Catalog(1,1,'search','Search',clean(DEFAULT))
        await app._load(c)
        self.assertTrue(c.done)
        self.assertIsNotNone(c.empty_message)
        empty=c.empty_message
        await app._cleanup_catalog(c)
        self.assertIn(empty,app.bot.deleted)

    async def test_rare_deposit_search_reaches_page_nine_without_manual_resume(self):
        pages={}
        for i in range(1,13):
            x=sample(i)
            x['priceLandAndHouse']={'forRent':{'deposit':{'type':'MONTH','month':1 if i in (3,9) else 2}}}
            pages[i]=[x]
        app=Application.__new__(Application)
        app.db=FakeDB();app.api=FakeAPI(pages)
        app.bot=FakeBot();app.fx=SimpleNamespace(rate=None);app.photo_cache={}
        c=Catalog(1,1,'latest','Новые',clean({'deposit_max_months':1}))
        await app._load(c)
        self.assertEqual(c.ids,['3','9'])
        self.assertIn(9,app.api.requests)
        self.assertEqual(c.scanned_pages,12)
        self.assertTrue(c.done)
        self.assertIsNotNone(c.message_id)

    async def test_collections_are_built_offline_and_open_without_http(self):
        from phbot.collections import signature
        from datetime import datetime, timezone
        class CacheDB(FakeDB):
            def __init__(self):
                super().__init__()
                self.filters=clean({})
                self.cache=None
            async def get_filters(self,uid,kind='latest'):return self.filters.copy()
            async def get_collection_cache(self,uid):return self.cache
            async def begin_collection_build(self,uid,sig,reset=False):
                self.cache={'filter_signature':sig,'status':'building',
                            'buckets':{},'completed_at':None,'scanned_pages':0,'candidates':0,'candidate_ids':[],'skipped_corrupt':0}
                return True
            async def collection_progress(self,uid,sig,pages,candidates,skipped,ids=None,window_counts=None):
                self.cache['scanned_pages']=pages
                self.cache['candidates']=candidates
                if ids is not None:self.cache['candidate_ids']=list(ids)
            async def finish_collection_build(self,uid,sig,buckets,pages,candidates,skipped):
                if self.cache['filter_signature']!=sig:return False
                self.cache.update(status='ready',buckets=buckets,scanned_pages=pages,
                                  candidates=candidates,completed_at=datetime.now(timezone.utc).isoformat())
                return True
            async def fail_collection_build(self,*args):raise AssertionError('Build must succeed')
        pages={i:[sample(i*10+j) for j in range(1,3)] for i in range(1,5)}
        now=datetime.now(timezone.utc).isoformat()
        for page,items in pages.items():
            for item in items:
                item['createdAt']=now
                item['price']['forRent']['monthly']['price']=6000 if page==4 else 30000
        api=FakeAPI(pages)
        db=CacheDB()
        app=Application.__new__(Application)
        app.db=db;app.api=api;app.bot=FakeBot()
        app.fx=SimpleNamespace(rate=None);app.photo_cache={}
        app.collection_semaphore=__import__('asyncio').Semaphore(1)
        app.catalogs={};app.panel_kind={}
        with patch('phbot.bot.COLLECTION_SCAN_PAGES',4):
            await app._prepare_collections(1,delay=0)
        self.assertEqual(api.requests,[1,2,3,4])
        self.assertEqual(db.cache['status'],'ready')
        self.assertEqual(set(db.cache['buckets']),{'1','7','30'})
        self.assertEqual(len(db.cache['buckets']['7']),8)
        self.assertIn(db.cache['buckets']['7'][0],('41','42'))
        api.requests.clear()
        await app.collection_from_cache(1,1,7)
        self.assertEqual(api.requests,[], 'Tapping ready collection must never call PropertyHub')
        self.assertIsNotNone(app.catalogs[1].message_id)
        self.assertTrue(app.catalogs[1].done)

    async def test_settings_change_only_triggers_permanent_collection_rebuild(self):
        class FilterDB:
            def __init__(self):self.state={'latest':clean({}),'search':clean({})}
            async def save_filters(self,uid,kind,f):
                current=clean(f)
                changed=current!=self.state[kind]
                self.state[kind]=current
                return changed
        app=Application.__new__(Application)
        app.db=FilterDB()
        calls=[]
        app.schedule_collection=lambda uid,replace=False: calls.append((uid,replace))
        await app._save_filters(1,'search',{'price_min':12000})
        self.assertEqual(calls,[])
        await app._save_filters(1,'latest',{'price_min':12000})
        await app._save_filters(1,'latest',{'price_min':12000})
        await app._save_filters(1,'latest',clean({}))
        self.assertEqual(calls,[(1,True),(1,True)])


if __name__=='__main__':unittest.main()
