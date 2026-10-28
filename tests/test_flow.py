"""Offline async smoke tests for catalog pagination and empty-state cleanup."""
import sys,types,unittest
from types import SimpleNamespace

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
        app.db=FakeDB();app.api=FakeAPI({i:[sample(j) for j in range((i-1)*60+1,i*60+1)] for i in range(1,5)})
        app.bot=FakeBot();app.fx=SimpleNamespace(rate=None);app.photo_cache={}
        c=Catalog(1,1,'search','Search',clean(DEFAULT))
        await app._load(c)
        self.assertEqual(len(c.ids),60)
        self.assertFalse(c.done)
        c.index=59;c.want_next=True
        await app._load(c)
        self.assertEqual(len(c.ids),120)
        self.assertEqual(c.index,60)
        await app._load(c)
        self.assertEqual(len(c.ids),180)
        self.assertEqual(app.api.requests,[1,2,3])

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

    async def test_collection_is_ranked_once_before_first_card(self):
        from unittest.mock import patch
        import phbot.bot as module
        pages={i:[sample(i*10+j) for j in range(1,3)] for i in range(1,5)}
        for page,items in pages.items():
            for item in items:
                # A bargain on the final page should be considered against
                # the whole pool, not ignored after 100 early candidates.
                item['price']['forRent']['monthly']['price']=6000 if page==4 else 30000
        api=FakeAPI(pages)
        class GuardedBot(FakeBot):
            async def send_photo(self,*a,**k):
                assert api.requests==[1,2,3,4], f'Premature card: {api.requests}'
                return await super().send_photo(*a,**k)
        app=Application.__new__(Application)
        app.db=FakeDB();app.api=api
        app.bot=GuardedBot();app.fx=SimpleNamespace(rate=None);app.photo_cache={}
        c=Catalog(1,1,'collection','Collection',clean({}))
        with patch.object(module,'COLLECTION_SCAN_PAGES',4):
            await app._load(c)
        self.assertTrue(c.done)
        self.assertEqual(c.scanned_pages,4)
        self.assertEqual(len(c.ids),8)
        self.assertEqual(len(c.scores),8)
        self.assertIn(c.ids[0],('41','42'))
        first_order=list(c.ids)
        await app._load(c)
        self.assertEqual(api.requests,[1,2,3,4])
        self.assertEqual(c.ids,first_order)
        self.assertIsNotNone(c.message_id)

if __name__=='__main__':unittest.main()
