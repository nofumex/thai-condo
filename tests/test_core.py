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
            self.assertIn('/_next/data/', str(req.url))
            self.assertTrue(str(req.url).endswith('/en/listings/hello---42.json'))
            self.assertEqual(req.headers['User-Agent'], 'Mozilla/5.0')
            self.assertNotIn('origin', req.headers)
            self.assertNotIn('referer', req.headers)
            return httpx.Response(200,json={'pageProps':{'listing':{
                'id':42,'images':[{'pictureUrl':'/img.jpg'}]}}})
        api=PropertyHub(transport=httpx.MockTransport(handle))
        try:
            info=await api.details({'id':42,'slug':'hello'})
            self.assertEqual(info['id'],42)
            self.assertEqual(len(info['images']),1)
        finally:await api.close()

    async def test_forbidden_next_json_reports_blocking_not_stale_build(self):
        from phbot.propertyhub import ProviderError
        def handle(req):
            self.assertEqual(req.headers['User-Agent'], 'Mozilla/5.0')
            self.assertNotIn('origin', req.headers)
            return httpx.Response(403, text='Forbidden')
        api=PropertyHub(transport=httpx.MockTransport(handle))
        try:
            with self.assertRaisesRegex(ProviderError, 'HTTP 403') as error:
                await api.details({'id':42, 'slug':'hello'})
            self.assertIn('не доказывает', str(error.exception))
        finally:
            await api.close()

    async def test_geo_zone_server_filter(self):
        requests=[]
        def handle(req):
            if req.method=='GET':
                self.assertIn('condo-for-rent/phuket.json',str(req.url))
                return httpx.Response(200,json={'pageProps':{'zoneIds':['12345']}})
            args=json.loads(req.content)['variables']['listingAttributes']
            requests.append(args)
            return httpx.Response(200,json={'data':{'zoneListings':{'status':'SUCCESS',
                'pagination':{'totalPages':1,'totalCount':0},'result':[]}}})
        api=PropertyHub(transport=httpx.MockTransport(handle))
        try:
            await api.search_page(clean({'region':'phuket'}))
            self.assertEqual(requests[0]['zoneIds'],['12345'])
        finally:await api.close()

class ExtendedFiltersTest(unittest.TestCase):
    def test_true_empty_defaults(self):
        from phbot.filters import DEFAULT
        f=clean(DEFAULT)
        self.assertNotIn('price',attributes(f))
        self.assertFalse(f['region'])

    def test_deposit_amount_and_month(self):
        from phbot.filters import deposit_terms
        one=listing(1)
        one['priceLandAndHouse']={'forRent':{'deposit':{'type':'AMOUNT','amount':24000}}}
        self.assertEqual(deposit_terms(one),(24000.0,2.0))
        self.assertFalse(matches_local(one,{'deposit_max_months':1}))
        self.assertTrue(matches_local(one,{'deposit_max_months':2}))
        two=listing(2)
        two['priceLandAndHouse']={'forRent':{'deposit':{'type':'MONTH','month':1}}}
        self.assertEqual(deposit_terms(two),(12000.0,1.0))

    def test_dates(self):
        x=listing(1,created='2026-10-27T10:00:00Z')
        self.assertTrue(matches_local(x,{'date_from':'2026-10-27','date_to':'2026-10-27'}))
        self.assertFalse(matches_local(x,{'date_to':'2026-10-26'}))

    def test_building_facilities_map(self):
        f=clean({'amenities':['hasPool','hasFitness','hasWasher']})
        a=attributes(f)
        self.assertEqual(a['amenities'],{'hasWasher':True})
        self.assertEqual(a['facilities'],{'pool':True,'fitness':True})

    def test_geo_district(self):
        from phbot.geography import district_of,city_of
        x={'location':{'lat':13.706,'lng':100.602}}
        self.assertEqual(city_of(x),'bangkok')
        self.assertEqual(district_of(x).id,'bkk-onnut')
        self.assertIsNone(district_of({'location':{}}))

    def test_freshness_decreases_while_favorited(self):
        from phbot.ranking import score
        from datetime import timedelta
        x=listing(11,created='2026-10-01T00:00:00Z')
        groups=comparables([x])
        now=datetime(2026,10,27,tzinfo=timezone.utc)
        a,_=score(x,groups,now)
        b,_=score(x,groups,now+timedelta(days=3))
        self.assertLess(b,a)

if __name__=='__main__':unittest.main()
