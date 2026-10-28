"""Read-only E2E against PropertyHub. No Telegram token required; ~5 HTTP calls."""
from pathlib import Path
import asyncio
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent/'.env')
from phbot.propertyhub import PropertyHub,ProviderError
from phbot.filters import clean,DEFAULT
from phbot.geography import city_of

async def main():
    api=PropertyHub()
    try:
        rows,meta=await api.search_page(clean(DEFAULT),page=1)
        print('Thailand search:',len(rows),'first-page items;',meta.get('totalCount'),'overall')
        if not rows:raise AssertionError('Default search is empty')
        full=await api.details(rows[0])
        print('Full JSON:',full.get('id'),'photos:',len(full.get('images') or []))
        if str(full.get('id'))!=str(rows[0].get('id')) or not full.get('images'):
            raise AssertionError('Full listing JSON did not return a gallery')
        for region in ('phuket','pattaya'):
            results,meta=await api.search_page(clean({'region':region}),page=1)
            cities={city_of(x) for x in results}
            print(region,'server zone IDs:',api.region_zones.get(region),
                  'results:',len(results),'reported total:',meta.get('totalCount'),
                  'coordinate city check:',cities)
            if not api.region_zones.get(region):raise AssertionError('No server zone IDs for '+region)
            if not results:raise AssertionError('No first-page matches for '+region)
            if cities!={region}:raise AssertionError('Wrong region returned by '+region)
        print('LIVE E2E: OK')
    finally:
        await api.close()

if __name__=='__main__':
    asyncio.run(main())
