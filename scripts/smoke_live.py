"""Read-only live verification of search, region IDs and corrupt-page recovery.

The detailed Next.js JSON endpoint is sometimes blocked independently of the
working GraphQL API.  It is checked, but an external 403 becomes a conspicuous
warning rather than misreporting the entire search backend as broken.
"""
import asyncio
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent/'.env')
from phbot.propertyhub import PropertyHub,ProviderError
from phbot.filters import clean,DEFAULT
from phbot.geography import city_of

async def main():
    api=PropertyHub()
    warnings=[]
    try:
        rows,meta=await api.search_page_resilient(clean(DEFAULT),page=1)
        print('Thailand search:',len(rows),'first-page listings;',meta.get('totalCount'),'reported total',flush=True)
        if not rows:raise AssertionError('Default search is empty')
        try:
            full=await api.details(rows[0])
            print('Full JSON:',full.get('id'),'photos:',len(full.get('images') or []),flush=True)
            if str(full.get('id'))!=str(rows[0].get('id')):
                raise AssertionError('Full listing JSON ID did not match')
        except ProviderError as exc:
            if 'HTTP 403' not in str(exc):raise
            warnings.append('Next.js detail JSON is currently HTTP 403; gallery cannot be loaded')
        for region in ('bangkok','phuket','pattaya'):
            records,meta=await api.search_page_resilient(clean({'region':region}),page=1)
            cities={city_of(x) for x in records}
            print(region,'zoneIds:',api.region_zones.get(region),
                  'page matches:',len(records),'reported total:',meta.get('totalCount'),
                  'coordinate city check:',cities,flush=True)
            if not api.region_zones.get(region):raise AssertionError('No server zone IDs for '+region)
            if not records:raise AssertionError('No first-page matches for '+region)
            if cities!={region}:raise AssertionError('Wrong city result from '+region)
        # Known provider failure in Oct 2026: CREATED_AT page 12/60 of Bangkok
        # contained a listing with 3,000,000,000 in a 32-bit GraphQL Int field.
        recovered,meta=await api.search_page_resilient(clean({'region':'bangkok'}),page=12,per_page=60)
        print('Bangkok page 12 recovered:',len(recovered),'listings;',
              meta.get('skippedCorruptListings',0),'corrupt skipped',flush=True)
        if not recovered:raise AssertionError('Page 12 recovery produced no listings')
        if len(recovered)!=len({str(x['id']) for x in recovered}):
            raise AssertionError('Recovered page contains duplicate IDs')
        for warning in warnings:print('WARNING:',warning,flush=True)
        print('LIVE CORE CHECK: OK'+(' (with provider warnings)' if warnings else ''),flush=True)
    finally:
        await api.close()

if __name__=='__main__':asyncio.run(main())
