"""Compare confirmed minimal HTTP headers vs the old GraphQL header set.
Does not require Telegram secrets. Read-only, two GET requests.
"""
import asyncio
import httpx
from phbot.propertyhub import BUILD_ID, ROOT

LISTING='forrent-nue-district-r9-mat-rama9---6522838'
URL=f'{ROOT}/_next/data/{BUILD_ID}/en/listings/{LISTING}.json'

async def main():
    variants={
        'minimal_worked_before': {'Accept':'application/json','User-Agent':'Mozilla/5.0'},
        'former_browser_like': {
            'Accept':'application/json','Origin':ROOT,'Referer':ROOT+'/',
            'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36'},
    }
    print('Endpoint:',URL)
    for name,headers in variants.items():
        try:
            async with httpx.AsyncClient(timeout=20,follow_redirects=True,headers=headers) as client:
                response=await client.get(URL)
            print(f'{name}: HTTP {response.status_code}, type={response.headers.get("content-type")}, bytes={len(response.content)}')
            if response.status_code==200 and 'json' in response.headers.get('content-type',''):
                item=response.json().get('pageProps',{}).get('listing',{})
                print('  listing ID:',item.get('id'),'photos:',len(item.get('images') or []))
            else:
                print('  reason:',response.text[:120].replace('\n',' '))
        except httpx.HTTPError as exc:
            print(name,'exception:',type(exc).__name__,str(exc)[:250])

if __name__=='__main__':asyncio.run(main())
