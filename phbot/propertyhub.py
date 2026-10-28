"""HTTP-only PropertyHub adapter. No Playwright or AI required."""
import asyncio
import json
import logging
import re
from html import unescape
from urllib.parse import quote
import httpx
from .filters import attributes, matches_local

log=logging.getLogger(__name__)
API='https://api.propertyhub.in.th/graphql'
ROOT='https://propertyhub.in.th'
CDN='https://bcdn.propertyhub.in.th'
HASH='0583b83bf32bf57a9f58948244f716f1829de4f824e5fc7d75d550404dadaec8'
NEXT_RE=re.compile(r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',re.S|re.I)

class ProviderError(RuntimeError):pass

def listing_url(x:dict)->str:
    slug=x.get('slug') or ''
    # The ID remains the canonical stable suffix even for non-ASCII slugs.
    return f"{ROOT}/en/listings/{quote(str(slug),safe='-')}---{x['id']}" if slug else f"{ROOT}/en/listings/{x['id']}"

def image_url(path):
    if not path:return None
    if str(path).startswith('http'):return path
    return CDN+'/'+str(path).lstrip('/')

def price_of(x):
    return (((x.get('price') or {}).get('forRent') or {}).get('monthly') or {}).get('price')

class PropertyHub:
    def __init__(self, transport=None):
        self.client=httpx.AsyncClient(timeout=httpx.Timeout(18,connect=8),transport=transport,
            follow_redirects=True,limits=httpx.Limits(max_connections=12,max_keepalive_connections=6),
            headers={'Accept':'application/json','Origin':ROOT,'Referer':ROOT+'/',
                     'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36'})
        self.semaphore=asyncio.Semaphore(5)
        self.last_error=''
    async def close(self):await self.client.aclose()
    async def search_page(self,filters:dict,page:int=1,per_page:int=60)->tuple[list[dict],dict]:
        payload={
            'operationName':'zoneListings',
            'variables':{'page':page,'perPage':min(max(per_page,1),60),'locale':'EN',
                         'order':filters.get('sort','CREATED_AT'),
                         'listingAttributes':attributes(filters)},
            'extensions':{'persistedQuery':{'version':1,'sha256Hash':HASH}}
        }
        async with self.semaphore:
            last=None
            for attempt in range(2):
                try:
                    response=await self.client.post(API,json=payload)
                    response.raise_for_status()
                    data=response.json()
                    if data.get('errors'):
                        raise ProviderError('; '.join(str(x.get('message','GraphQL error')) for x in data['errors']))
                    block=(data.get('data') or {}).get('zoneListings') or {}
                    if block.get('status')!='SUCCESS':raise ProviderError(str(block.get('error') or 'PropertyHub returned no SUCCESS'))
                    self.last_error=''
                    rows=block.get('result') or []
                    return [x for x in rows if isinstance(x,dict) and matches_local(x,filters)],block.get('pagination') or {}
                except (httpx.HTTPError,ValueError,ProviderError) as ex:
                    last=ex
                    if attempt==0 and not isinstance(ex,ProviderError):await asyncio.sleep(.5)
            self.last_error=str(last)
            raise ProviderError(f'PropertyHub: {last}')
    async def details(self,x:dict)->dict:
        url=listing_url(x)
        # HTML detail pages expose complete listing data in __NEXT_DATA__.
        async with self.semaphore:
            try:
                r=await self.client.get(url,headers={'Accept':'text/html','Referer':ROOT+'/'})
                r.raise_for_status()
                m=NEXT_RE.search(r.text)
                if not m:raise ProviderError('На странице нет __NEXT_DATA__')
                item=json.loads(m.group(1)).get('props',{}).get('pageProps',{}).get('listing')
                if not item:raise ProviderError('Детали не найдены')
                return item
            except (httpx.HTTPError,ValueError,KeyError) as e:
                raise ProviderError(f'Не удалось открыть объявление: {e}') from e

def plain_html(text:str)->str:
    text=re.sub(r'<\s*br\s*/?>|</p>|</div>|</li>', '\n', text or '',flags=re.I)
    return re.sub(r'\n{3,}','\n\n',unescape(re.sub(r'<[^>]*>','',text))).strip()
