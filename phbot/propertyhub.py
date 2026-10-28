"""HTTP-only PropertyHub adapter. No Playwright or AI required."""
import asyncio
from math import ceil
import json
import logging
import re
import os
from urllib.parse import urlparse
from html import unescape
from urllib.parse import quote
import httpx
from .filters import attributes, matches_local
from .geography import CITIES

log=logging.getLogger(__name__)
API='https://api.propertyhub.in.th/graphql'
ROOT='https://propertyhub.in.th'
CDN='https://bcdn.propertyhub.in.th'
HASH='0583b83bf32bf57a9f58948244f716f1829de4f824e5fc7d75d550404dadaec8'
BUILD_ID=os.getenv('PROPERTYHUB_BUILD_ID','propertyhub-web-4fc47c2fe2d086968b89c888d13d50f55fe2bd9c')
BUILD_RE=re.compile(r'propertyhub-web-[a-f0-9]{32,64}')
NEXT_RE=re.compile(r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',re.S|re.I)

class ProviderError(RuntimeError):pass

# PropertyHub's GraphQL response contains some listings whose fields exceed
# GraphQL Int's 32-bit range.  Recover only this known provider-data error.
GRAPHQL_INT_SERIALIZATION_ERROR = 'Int cannot represent non 32-bit signed integer value'
GRAPHQL_INT_MAX = 2_147_483_647

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
        # PropertyHub's GraphQL host and its Next.js JSON host do not always
        # accept the same request fingerprint.  A bare httpx call with
        # Accept: application/json and UA: Mozilla/5.0 was verified to return
        # JSON, whereas browser-like Origin/Referer headers can receive 403.
        # Keep the two HTTP clients completely independent.
        self.page_client=httpx.AsyncClient(
            timeout=httpx.Timeout(20,connect=8), transport=transport,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=5,max_keepalive_connections=3),
            headers={'Accept':'application/json','User-Agent':'Mozilla/5.0'})
        self.semaphore=asyncio.Semaphore(5)
        self.last_error=''
        self.build_id=BUILD_ID
        self.region_zones={}
        self.region_lock=asyncio.Lock()
    async def close(self):
        await self.client.aclose()
        await self.page_client.aclose()
    def _next_url(self,path):
        """Next.js page-data endpoint (HTML pages often respond with 403)."""
        path=path.lstrip('/')
        if path.startswith('en/'):path=path[3:]
        return f'{ROOT}/_next/data/{self.build_id}/en/{path}.json'

    async def _next_json(self,path):
        url=self._next_url(path)
        try:
            async with self.semaphore:
                # Do NOT add Origin or Referer.  This mirrors the previously
                # successful minimal httpx verification from PowerShell.
                response=await self.page_client.get(url)
            if response.status_code == 404:
                # A 404 may mean a stale Next.js build.  Probe the page once,
                # and retry only if a *different* build ID is actually found.
                try:
                    page=await self.page_client.get(
                        ROOT+'/en/'+path.lstrip('/').removeprefix('en/'),
                        headers={'Accept':'text/html'})
                    match=BUILD_RE.search(page.text) if page.status_code==200 else None
                    if match and match.group(0)!=self.build_id:
                        log.info('PropertyHub build updated %s -> %s',self.build_id,match.group(0))
                        self.build_id=match.group(0)
                        async with self.semaphore:
                            response=await self.page_client.get(self._next_url(path))
                except httpx.HTTPError as exc:
                    log.debug('Build discovery unavailable: %s',exc)
            if response.status_code == 403:
                raise ProviderError(
                    'PropertyHub отклонил запрос к Next.js JSON (HTTP 403). '
                    'Это не доказывает, что BUILD_ID устарел: возможно ограничение '
                    'доступа к endpoint. Поиск GraphQL при этом может работать.')
            response.raise_for_status()
            data=response.json()
            props=data.get('pageProps') or (data.get('props') or {}).get('pageProps') or {}
            if not isinstance(props,dict):raise ProviderError('Неверный формат Next.js JSON')
            return props
        except (httpx.HTTPError,ValueError) as exc:
            raise ProviderError(f'PropertyHub Next.js JSON недоступен: {exc}') from exc

    async def resolve_region(self,region):
        if not region:return []
        if region not in CITIES:raise ProviderError('Неизвестный регион')
        if region in self.region_zones:return self.region_zones[region]
        async with self.region_lock:
            if region in self.region_zones:return self.region_zones[region]
            props=await self._next_json('condo-for-rent/'+CITIES[region].slug)
            zones=props.get('zoneIds') or []
            if not zones:
                zone=props.get('zone') or {}
                if isinstance(zone,dict) and zone.get('id'):zones=[zone['id']]
            zones=[str(z.get('id') if isinstance(z,dict) else z) for z in zones]
            zones=[z for z in zones if z.isdigit()]
            if not zones:
                # Do not return nationwide results while UI claims "Phuket".
                raise ProviderError('PropertyHub не вернул zoneIds для '+CITIES[region].title+'. Географический поиск не запущен, чтобы не показывать другие города.')
            self.region_zones[region]=zones
            return zones

    async def search_page(self,filters:dict,page:int=1,per_page:int=60,local_filter:bool=True)->tuple[list[dict],dict]:
        attrs=attributes(filters)
        if filters.get('region') and not attrs['zoneIds'] and not attrs.get('projectId'):
            attrs['zoneIds']=await self.resolve_region(filters['region'])
        payload={
            'operationName':'zoneListings',
            'variables':{'page':page,'perPage':min(max(per_page,1),60),'locale':'EN',
                         'order':filters.get('sort','CREATED_AT'),
                         'listingAttributes':attrs},
            'extensions':{'persistedQuery':{'version':1,'sha256Hash':HASH}}
        }
        async with self.semaphore:
            last=None
            for attempt in range(3):
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
                    return [x for x in rows if isinstance(x,dict) and (not local_filter or matches_local(x,filters))],block.get('pagination') or {}
                except ProviderError as ex:
                    # GraphQL validation/serialization errors are deterministic
                    # for this particular request; retrying it cannot help and
                    # makes recursive recovery unnecessarily expensive.
                    self.last_error=str(ex)
                    raise ProviderError(f'PropertyHub: {ex}') from ex
                except (httpx.HTTPError,ValueError) as ex:
                    last=ex
                    if attempt<2:await asyncio.sleep(0.5*(attempt+1))
            self.last_error=str(last)
            raise ProviderError(f'PropertyHub: {last}')

    async def search_page_resilient(self, filters:dict, page:int=1, per_page:int=60, local_filter:bool=True)->tuple[list[dict],dict]:
        """Return a page even when one provider listing has invalid GraphQL Int fields.

        Split a failing page into *aligned, non-overlapping* smaller pages using
        the exact same order/filters.  Only an irrecoverable single listing is
        omitted.  All other errors (auth, rate limits, network) propagate.
        """
        if not isinstance(page,int) or page<1 or isinstance(page,bool):
            raise ValueError('GraphQL page must be a positive integer')
        if not isinstance(per_page,int) or not (1<=per_page<=60) or isinstance(per_page,bool):
            raise ValueError('GraphQL per_page must be 1..60')
        start=(page-1)*per_page

        async def collect(offset:int,size:int):
            # Every recursive child must have the same absolute starting offset.
            assert offset%size==0
            try:
                rows,meta=await self.search_page(filters,page=offset//size+1,per_page=size,local_filter=local_filter)
                return rows,meta,0
            except ProviderError as exc:
                if GRAPHQL_INT_SERIALIZATION_ERROR not in str(exc):
                    raise
                if size==1:
                    log.warning('PropertyHub invalid listing at offset %d (skipped): %s',offset,exc)
                    return [],{},1
                child_size=next((n for n in (20,10,5,1) if n<size and size%n==0),1)
                if size%child_size:
                    # This never happens with 60, 20, 10 or 5; for arbitrary
                    # per_page values, 1 is always a safe aligned divisor.
                    child_size=1
                log.warning('PropertyHub bad GraphQL Int at offset %d, size %d: retrying in chunks of %d',
                            offset,size,child_size)
                items=[];usable_meta={};bad=0
                for child_offset in range(offset,offset+size,child_size):
                    chunk,meta,skipped=await collect(child_offset,child_size)
                    items.extend(chunk)
                    if meta and not usable_meta:usable_meta=meta
                    bad+=skipped
                return items,usable_meta,bad

        rows,meta,skipped=await collect(start,per_page)
        if not meta:
            # Do not report a totally broken page as an empty/full catalog.
            raise ProviderError(f'PropertyHub: no healthy listing or pagination metadata '
                                f'could be recovered from page {page}')
        meta=dict(meta or {})
        if skipped:
            meta['skippedCorruptListings']=skipped
            meta['partialPage']=True
            self.last_error=f'PropertyHub: skipped {skipped} invalid GraphQL listing(s) on page {page}'
        if meta.get('totalCount') is not None:
            # Child requests have a different perPage: do not leak the child's
            # pagination totalPages into the parent 60-item cursor.
            meta['totalPages']=ceil(int(meta['totalCount'])/per_page)
        return rows,meta

    async def search_page_unfiltered(self, filters:dict, page:int=1, per_page:int=60):
        """Unfiltered provider results for the shared cache, with corrupt-row recovery."""
        return await self.search_page_resilient(filters,page,per_page,local_filter=False)

    async def details(self,x:dict)->dict:
        """Confirmed live endpoint: /_next/data/<buildId>/en/listings/<slug>---<id>.json.
        Full page HTML is 403 from httpx, so never fetch it as the primary source.
        """
        path=urlparse(listing_url(x)).path
        props=await self._next_json(path)
        item=props.get('listing')
        if not isinstance(item,dict) or str(item.get('id'))!=str(x.get('id')):
            raise ProviderError('Детали объявления не найдены либо не совпадает ID')
        return item

def plain_html(text:str)->str:
    text=re.sub(r'<\s*br\s*/?>|</p>|</div>|</li>', '\n', text or '',flags=re.I)
    return re.sub(r'\n{3,}','\n\n',unescape(re.sub(r'<[^>]*>','',text))).strip()
