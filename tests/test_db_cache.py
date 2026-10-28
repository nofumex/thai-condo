"""SQLite integration tests run where the project's aiosqlite dependency exists.

The offline assembly environment may omit aiosqlite; full integration runs on
normal project installations with requirements.txt installed.
"""
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
try:
    import aiosqlite
    HAS_DRIVER=hasattr(aiosqlite,'connect')
except ImportError:
    HAS_DRIVER=False
from phbot.collections import signature
from phbot.filters import clean

@unittest.skipUnless(HAS_DRIVER,'Install aiosqlite to run SQLite integration tests')
class CollectionPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_persists_and_old_workers_cannot_publish_new_filters(self):
        from phbot.db import Database
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'saved.sqlite3')
            db=Database(path)
            await db.init()
            try:
                await db.user(SimpleNamespace(id=123,username='test',first_name='Test'))
                first=clean({'region':'bangkok','price_min':10000})
                self.assertTrue(await db.save_filters(123,'latest',first))
                sig1=signature(first)
                self.assertEqual((await db.get_collection_cache(123))['status'],'pending')
                self.assertTrue(await db.begin_collection_build(123,sig1))
                second=clean({'region':'bangkok','price_min':12000})
                self.assertTrue(await db.save_filters(123,'latest',second))
                sig2=signature(second)
                self.assertFalse(await db.finish_collection_build(
                    123,sig1,{'1':['old'],'7':['old'],'30':['old']},1,1,0))
                self.assertTrue(await db.begin_collection_build(123,sig2))
                self.assertTrue(await db.finish_collection_build(
                    123,sig2,{'1':['new'],'7':['new'],'30':['new']},10,50,0))
            finally:await db.close()
            db=Database(path)
            await db.init()
            try:
                current=await db.get_collection_cache(123)
                self.assertEqual(current['status'],'ready')
                self.assertEqual(current['filter_signature'],sig2)
                self.assertEqual(current['buckets']['7'],['new'])
                # A temporary search profile must not invalidate collections.
                await db.save_filters(123,'search',{'price_max':15000})
                self.assertEqual((await db.get_collection_cache(123))['status'],'ready')
                # Reset to empty defaults replaces all old buckets at once.
                self.assertTrue(await db.save_filters(123,'latest',clean({})))
                cache=await db.get_collection_cache(123)
                self.assertEqual(cache['status'],'pending')
                self.assertEqual(cache['buckets'],{})
                self.assertFalse(await db.finish_collection_build(
                    123,sig2,{'1':['old'],'7':['old'],'30':['old']},1,1,0))
            finally:await db.close()

if __name__=='__main__':unittest.main()

@unittest.skipUnless(HAS_DRIVER,'Install aiosqlite to run SQLite integration tests')
class LegacyCacheMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_signature_rebuilds_even_if_filters_unchanged(self):
        from phbot.db import Database
        with tempfile.TemporaryDirectory() as temp:
            db=Database(str(Path(temp)/'legacy.sqlite3'))
            await db.init()
            try:
                await db.user(SimpleNamespace(id=1234,username='test',first_name='Test'))
                await db.conn.execute('''INSERT INTO collection_cache
                    (user_id,filter_signature,status,buckets_json,updated_at)
                    VALUES(1234,'legacy_v23','ready','{"1":[],"7":[],"20":[]}',?)''',
                    (datetime.now(timezone.utc).isoformat(),))
                await db.conn.commit()
                sig=signature(await db.get_filters(1234,'latest'))
                self.assertTrue(await db.begin_collection_build(1234,sig))
                cache=await db.get_collection_cache(1234)
                self.assertEqual(cache['filter_signature'],sig)
                self.assertEqual(cache['status'],'building')
                self.assertEqual(cache['buckets'],{})
                self.assertEqual(cache['candidate_ids'],[])
            finally:
                await db.close()
