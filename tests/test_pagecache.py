"""Shared raw-page cache: same provider query, independent local filters."""
import asyncio
import unittest
from types import SimpleNamespace
from phbot.bot import Application
from phbot.filters import clean


def listing(i,months):
    return {'id':str(i),'createdAt':'2026-10-28T08:00:00Z',
        'price':{'forRent':{'monthly':{'price':12000}}},
        'priceLandAndHouse':{'forRent':{'deposit':{'type':'MONTH','month':months}}},
        'roomInformation':{'roomArea':30},
        'project':{'nameEnglish':'Test','address':'Bangkok'},
        'location':{'lat':13.7,'lng':100.5}}

class CachedDB:
    def __init__(self):self.cache={};self.saved={}
    async def get_search_page(self,key,page,max_age_minutes=25):
        return self.cache.get((key,page))
    async def store_search_page(self,key,page,rows,meta):
        self.cache[(key,page)]=(rows,meta)
        self.saved.update((x['id'],x) for x in rows)

class RawAPI:
    def __init__(self):self.calls=0;self.gate=asyncio.Event();self.gate.set()
    async def search_page_unfiltered(self,f,page=1,per_page=60):
        self.calls+=1
        await self.gate.wait()
        return [listing(1,1),listing(2,2)],{'totalPages':12,'totalCount':720}

class SharedCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_provider_page_reused_across_deposit_filters(self):
        app=Application.__new__(Application)
        app.db=CachedDB();app.api=RawAPI();app.page_inflight={}
        first=clean({'deposit_max_months':1})
        second=clean({'deposit_max_months':2})
        one,_=await app._search_page(first,1)
        two,_=await app._search_page(second,1)
        self.assertEqual([x['id'] for x in one],['1'])
        self.assertEqual([x['id'] for x in two],['1','2'])
        self.assertEqual(app.api.calls,1)
        self.assertEqual(len(app.db.saved),2)

    async def test_concurrent_users_share_one_in_flight_request(self):
        app=Application.__new__(Application)
        app.db=CachedDB();app.api=RawAPI();app.page_inflight={}
        app.api.gate.clear()
        first=asyncio.create_task(app._search_page(clean({'deposit_max_months':1}),1))
        second=asyncio.create_task(app._search_page(clean({'deposit_max_months':2}),1))
        await asyncio.sleep(.02)
        self.assertEqual(app.api.calls,1)
        app.api.gate.set()
        out=await asyncio.gather(first,second)
        self.assertEqual([len(pair[0]) for pair in out],[1,2])
        self.assertEqual(app.api.calls,1)

    async def test_cancelled_user_does_not_kill_shared_page_for_another(self):
        app=Application.__new__(Application)
        app.db=CachedDB();app.api=RawAPI();app.page_inflight={}
        app.api.gate.clear()
        first=asyncio.create_task(app._search_page(clean({}),1))
        second=asyncio.create_task(app._search_page(clean({}),1))
        await asyncio.sleep(.03)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):await first
        app.api.gate.set()
        rows,_=await second
        self.assertEqual(len(rows),2)
        self.assertEqual(app.api.calls,1)

if __name__=='__main__':unittest.main()
