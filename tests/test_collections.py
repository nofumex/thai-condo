"""Prepared collection cache semantics, pure ranking, stable signatures."""
import unittest
from datetime import datetime, timedelta, timezone
from phbot.collections import signature, build_buckets, cache_expired, WINDOWS
from phbot.filters import clean
from phbot.ranking import age_days,comparables,score

NOW=datetime(2026,10,28,12,tzinfo=timezone.utc)

def item(id, age):
    return {'id':str(id),'createdAt':(NOW-timedelta(days=age)).isoformat(),
            'price':{'forRent':{'monthly':{'price':10000+int(id)*100}}},
            'roomInformation':{'roomArea':40,'numberOfBed':1,'onFloor':'12',
                               'roomType':'ONE_BED_ROOM'},
            'project':{'address':'Bangkok','nameEnglish':'Example'}}

class CacheTests(unittest.TestCase):
    def test_one_scan_produces_all_windows_and_expiration_cutoffs(self):
        data=[item(1,.25),item(2,4),item(3,15),item(4,25)]
        buckets=build_buckets(data,NOW)
        self.assertEqual(set(buckets),{'1','7','30'})
        self.assertEqual(set(buckets['1']),{'1'})
        self.assertEqual(set(buckets['7']),{'1','2'})
        self.assertEqual(set(buckets['30']),{'1','2','3','4'})

    def test_permanent_settings_signature_is_canonical(self):
        self.assertEqual(signature({'region':'bangkok','price_min':12000}),
                         signature({'price_min':12000,'region':'bangkok'}))
        self.assertNotEqual(signature({'price_min':12000}),signature({'price_min':15000}))
        self.assertEqual(signature(clean({})),signature({}))

    def test_summary_comparables_have_identical_rating(self):
        data=[item(i,.2) for i in range(1,16)]
        raw=comparables(data)
        summary=comparables(data,summarize=True)
        for x in data:
            self.assertEqual(score(x,raw,NOW),score(x,summary,NOW))

    def test_staleness_and_time_age(self):
        ready={'completed_at':(NOW-timedelta(hours=5)).isoformat()}
        self.assertFalse(cache_expired(ready,NOW))
        ready={'completed_at':(NOW-timedelta(hours=7)).isoformat()}
        self.assertTrue(cache_expired(ready,NOW))
        self.assertGreater(age_days(item(1,.5),NOW+timedelta(days=1)),1)
        self.assertTrue(cache_expired(None,NOW))
