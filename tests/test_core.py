import json
import unittest
from datetime import datetime,timezone
import httpx
from phbot.filters import clean,attributes,matches_local,AMENITIES,FACILITIES
from phbot.propertyhub import PropertyHub,image_url,listing_url,price_of,plain_html,HASH
from phbot.ranking import score,comparables

def listing(id,price=12000,area=30,created='2026-10-27T10:00:00Z'):
    return {'id':str(id),'slug':'my-test-condo', 'createdAt':created,'refreshedAt':created,
            'project':{'nameEnglish':'Example','address':'Bang Na Bangkok'},
            'price':{'forRent':{'monthly':{'price':price}}},
            'roomInformation':{'roomArea':area,'roomType':'ONE_BED_ROOM','numberOfBed':1},
            'coverPicture':'/pictures/img.jpg'}

class FiltersTest(unittest.TestCase):
    def test_known_supported_options(self):
        self.assertGreater(len(AMENITIES),20)
        self.assertGreater(len(FACILITIES),15)
        f=clean({'price_min':11000,'price_max':15000,'room_type':'ONE_BED_ROOM',
                 'area_min':25,'area_max':35,'amenities':['hasWasher','fake'],
                 'facilities':['pool','fake'],'project_id':'3212','zone_ids':['123','abc']})
        a=attributes(f)
        self.assertEqual(a['amenities'],{'hasWasher':True})
        self.assertEqual(a['facilities'],{'pool':True})
        self.assertEqual(a['projectId'],'3212')
        self.assertEqual(a['zoneIds'],['123'])
        self.assertEqual(a['roomInformation']['roomArea'],{'min':25,'max':35})
    def test_local_filter(self):
        self.assertTrue(matches_local(listing(1),{'location_text':'bang na'}))
        self.assertFalse(matches_local(listing(1),{'location_text':'phuket'}))
    def test_urls(self):
        self.assertEqual(price_of(listing(1)),12000)
        self.assertIn('---42',listing_url({'id':'42','slug':'hello ไทย'}))
        self.assertEqual(image_url('/path.jpg'),'https://bcdn.propertyhub.in.th/path.jpg')
        self.assertEqual(plain_html('<p>Hello</p><p>World</p>'),'Hello\nWorld')

class RankingTest(unittest.TestCase):
    def test_value_scores_for_similar_apartments(self):
        rows=[listing(i,price=12000,area=30) for i in range(10)]
        groups=comparables(rows)
        high,conf=score(listing('cheap',price=9000),groups,datetime(2026,10,27,tzinfo=timezone.utc))
        low,_=score(listing('expensive',price=16000),groups,datetime(2026,10,27,tzinfo=timezone.utc))
        self.assertGreater(high,low)
        self.assertGreaterEqual(conf,0)
        self.assertLessEqual(high,100)

class APIAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_graphql_contract(self):
        requests=[]
        def handle(req):
            requests.append(json.loads(req.content))
            return httpx.Response(200,json={'data':{'zoneListings':{'status':'SUCCESS','pagination':{'totalPages':2,'totalCount':4},'result':[listing(1),listing(2)]}}})
        api=PropertyHub(transport=httpx.MockTransport(handle))
        try:
            filters=clean({'price_min':10000,'price_max':15000,'room_type':'ONE_BED_ROOM',
                          'amenities':['hasWasher'],'facilities':['pool'],'sort':'CREATED_AT'})
            rows,meta=await api.search_page(filters)
            self.assertEqual(len(rows),2)
            self.assertEqual(meta['totalCount'],4)
            pay=requests[0]
            self.assertEqual(pay['operationName'],'zoneListings')
            self.assertEqual(pay['extensions']['persistedQuery']['sha256Hash'],HASH)
            self.assertEqual(pay['variables']['order'],'CREATED_AT')
            self.assertTrue(pay['variables']['listingAttributes']['amenities']['hasWasher'])
        finally:await api.close()
    async def test_detail_next_data(self):
        def handle(req):
            data={'props':{'pageProps':{'listing':{'id':42,'images':[{'pictureUrl':'/img.jpg'}]}}}}
            return httpx.Response(200,text='<script id="__NEXT_DATA__" type="application/json">'+json.dumps(data)+'</script>')
        api=PropertyHub(transport=httpx.MockTransport(handle))
        try:
            info=await api.details({'id':42,'slug':'hello'})
            self.assertEqual(info['id'],42)
        finally:await api.close()

if __name__=='__main__':unittest.main()
