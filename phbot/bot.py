"""UI-first Telegram interface. Callback ACKs precede network work; mutable views are per user.
Never runs HTTP to PropertyHub inside navigation callbacks.
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass,field
from datetime import datetime,timezone,timedelta
from html import escape
from statistics import mean
from collections import defaultdict
from typing import Optional
from aiogram import Bot,Dispatcher,F,Router
from aiogram.filters import CommandStart,Command
from aiogram.types import Message,CallbackQuery,InputMediaPhoto
from aiogram.exceptions import TelegramBadRequest,TelegramForbiddenError
from .config import Config
from .db import Database
from .propertyhub import PropertyHub,ProviderError,image_url,price_of,plain_html
from .filters import DEFAULT,AMENITIES,FACILITIES,ROOM_TYPES,SORTS,clean,concise,matches_local
from .ranking import score,comparables,age_days
from .fx import FX
from .render import (MAIN,main_kb,filter_kb,filter_summary,choices_kb,card_text,card_kb,
                     details_text,detail_kb,kb,b,link,esc,money)
log=logging.getLogger(__name__)

@dataclass
class Catalog:
    chat_id:int
    owner:int
    source:str
    title:str
    filters:dict
    ids:list[str]=field(default_factory=list)
    index:int=0
    message_id:Optional[int]=None
    kind:str='photo'
    fetching:bool=False
    done:bool=False
    generation:int=0
    scores:dict=field(default_factory=dict)
    extra_messages:list[int]=field(default_factory=list)
    worker:Optional[asyncio.Task]=None
    view_epoch:int=0
    detail_mode:bool=False
    detail_task:Optional[asyncio.Task]=None

class Application:
    def __init__(self,cfg:Config):
        self.cfg=cfg;self.bot=Bot(cfg.token);self.dp=Dispatcher();self.router=Router()
        self.dp.include_router(self.router)
        self.db=Database(cfg.db_path);self.api=PropertyHub();self.fx=FX()
        self.panels:dict[int,int]={};self.panel_kind:dict[int,str]={}
        self.catalogs:dict[int,Catalog]={};self.pending:dict[int,tuple[str,str,int]]={}
        self.tasks:set[asyncio.Task]=set();self.started=datetime.now(timezone.utc)
        self.photo_cache:dict[str,str]={};self.sync_lock=asyncio.Lock()
        self._setup_handlers()

    def task(self,coro):
        t=asyncio.create_task(coro)
        self.tasks.add(t);t.add_done_callback(self._background_done)
        return t
    def _background_done(self,t):
        self.tasks.discard(t)
        if not t.cancelled():
            try:t.result()
            except Exception:log.exception('background operation failed')

    async def safe_delete(self,chat,msg):
        if msg:
            try:await self.bot.delete_message(chat_id=chat,message_id=msg)
            except (TelegramBadRequest,TelegramForbiddenError):pass

    async def panel(self,uid,chat,text,markup):
        mid=self.panels.get(uid)
        if mid:
            try:
                await self.bot.edit_message_text(text,chat_id=chat,message_id=mid,reply_markup=markup,parse_mode='HTML')
                return mid
            except TelegramBadRequest as e:
                if 'message is not modified' in str(e):return mid
        m=await self.bot.send_message(chat,text,reply_markup=markup,parse_mode='HTML',disable_web_page_preview=True)
        self.panels[uid]=m.message_id
        return m.message_id

    async def home(self,uid,chat):
        self.pending.pop(uid,None)
        self.panel_kind[uid]='home'
        await self.panel(uid,chat,MAIN,main_kb(uid==self.cfg.admin_id))

    async def close_catalog(self,uid):
        c=self.catalogs.pop(uid,None)
        if not c:return
        c.generation+=1
        c.detail_mode=False
        if c.worker and not c.worker.done():c.worker.cancel()
        if c.detail_task and not c.detail_task.done():c.detail_task.cancel()
        # UI callbacks never wait for potentially large gallery cleanup.
        self.task(self._cleanup_catalog(c))

    async def _cleanup_catalog(self,c):
        await self._clear_details(c)
        await self.safe_delete(c.chat_id,c.message_id)

    async def filters(self,uid,chat,kind):
        self.panel_kind[uid]='filter:'+kind
        f=await self.db.get_filters(uid,kind)
        await self.panel(uid,chat,filter_summary(kind,f),filter_kb(kind,f))

    async def _clear_details(self,c:Catalog):
        c.detail_mode=False
        if c.detail_task and not c.detail_task.done() and c.detail_task is not asyncio.current_task():
            c.detail_task.cancel()
        mids=c.extra_messages[:]
        c.extra_messages=[]
        sem=asyncio.Semaphore(4)
        async def remove(mid):
            async with sem:await self.safe_delete(c.chat_id,mid)
        if mids:await asyncio.gather(*(remove(mid) for mid in mids),return_exceptions=True)
        c.detail_mode=False

    async def _send_card(self,c:Catalog,index:int,replace:bool=True):
        if not c.ids:return
        index=max(0,min(index,len(c.ids)-1))
        c.index=index;c.view_epoch+=1;epoch=c.view_epoch
        item,_=await self.db.get_listing(c.ids[index])
        if item is None:return
        uid=c.owner
        favorite=await self.db.is_marked(uid,item['id'],'favorites')
        text=card_text(item,favorite,c.scores.get(str(item['id'])),self.fx.rate)
        markup=card_kb(item,index,len(c.ids),favorite,c.fetching)
        photo=image_url(item.get('coverPicture'))
        cached_photo=self.photo_cache.get(str(item['id']))
        if replace and c.message_id:
            if c.kind=='photo' and photo:
                try:
                    await self.bot.edit_message_media(chat_id=c.chat_id,message_id=c.message_id,
                        media=InputMediaPhoto(media=cached_photo or photo,caption=text,parse_mode='HTML'),reply_markup=markup)
                    await self.db.metric(uid,'cards');return
                except TelegramBadRequest as e:log.debug('edit media fallback: %s',e)
            if c.kind=='text' and not photo:
                try:
                    await self.bot.edit_message_text(chat_id=c.chat_id,message_id=c.message_id,
                        text=text,parse_mode='HTML',reply_markup=markup,disable_web_page_preview=True)
                    await self.db.metric(uid,'cards');return
                except TelegramBadRequest as e:
                    if 'message is not modified' in str(e):return
            await self.safe_delete(c.chat_id,c.message_id);c.message_id=None
        if c.view_epoch!=epoch:return
        if photo:
            try:
                m=await self.bot.send_photo(c.chat_id,cached_photo or photo,caption=text,parse_mode='HTML',reply_markup=markup)
                c.kind='photo'
                if m.photo:self.photo_cache[str(item['id'])]=m.photo[-1].file_id
            except TelegramBadRequest:
                m=await self.bot.send_message(c.chat_id,text+'\n\n🖼 '+esc(photo),parse_mode='HTML',reply_markup=markup,
                    disable_web_page_preview=True)
                c.kind='text'
        else:
            m=await self.bot.send_message(c.chat_id,text,parse_mode='HTML',reply_markup=markup)
            c.kind='text'
        c.message_id=m.message_id
        await self.db.metric(uid,'cards')

    async def _load(self,c:Catalog):
        """First page makes first card visible; subsequent pages append in background."""
        uid=c.owner;progress=None;gen=c.generation
        try:
            progress=await self.bot.send_message(c.chat_id,'🔎 <b>Поиск по фильтрам выполняется…</b>\n<i>Первое объявление появится сразу после ответа PropertyHub.</i>',parse_mode='HTML')
            await self.db.metric(uid,'requests')
            page=1
            target=100
            total_pages=5 if c.filters.get('location_text') else 3
            c.fetching=True
            while page<=total_pages and len(c.ids)<target and gen==c.generation:
                rows,pag=await self.api.search_page(c.filters,page=page,per_page=60)
                await self.db.put_listings(rows)
                hidden=set(await self.db.listing_ids(uid,'hidden'))
                have=set(c.ids)
                for x in rows:
                    id=str(x.get('id') or '')
                    if id and id not in hidden and id not in have:
                        c.ids.append(id);have.add(id)
                # GraphQL can promote sponsored posts; re-sort the sampled pool locally.
                if c.filters.get('sort') in ('CREATED_AT','REFRESHED_AT','PRICE_MONTHLY_LOW_TO_HIGH'):
                    active_id = c.ids[c.index] if c.message_id and c.ids else None
                    items = await self.db.listings_by_ids(c.ids)
                    if c.filters['sort']=='PRICE_MONTHLY_LOW_TO_HIGH':
                        items.sort(key=lambda x:float(price_of(x) or 1e30))
                    else:
                        field='createdAt' if c.filters['sort']=='CREATED_AT' else 'refreshedAt'
                        items.sort(key=lambda x:str(x.get(field) or ''),reverse=True)
                    sorted_ids=[str(x['id']) for x in items[:target]]
                    if active_id and active_id not in sorted_ids and active_id in have:
                        sorted_ids[-1]=active_id
                    c.ids=sorted_ids
                    if active_id in c.ids:c.index=c.ids.index(active_id)
                if page==1:
                    await self.safe_delete(c.chat_id,progress.message_id);progress=None
                    if c.ids:await self._send_card(c,0,replace=False)
                    else:
                        m=await self.bot.send_message(c.chat_id,'⏳ На первой странице подходящих вариантов нет, проверяю следующие…')
                        progress=m
                if page >= int(pag.get('totalPages') or 1):break
                page+=1
                if len(c.ids)<target:await asyncio.sleep(.25)
            c.fetching=False;c.done=True
            # Update the counter without re-sending the card/photo.
            if c.message_id and c.ids:
                try:
                    x,_=await self.db.get_listing(c.ids[c.index])
                    fav=await self.db.is_marked(uid,x['id'],'favorites')
                    await self.bot.edit_message_reply_markup(chat_id=c.chat_id,message_id=c.message_id,
                        reply_markup=card_kb(x,c.index,len(c.ids),fav,False))
                except (TelegramBadRequest,TelegramForbiddenError):pass
            if progress:
                await self.safe_delete(c.chat_id,progress.message_id)
                if not c.ids:
                    await self.bot.send_message(c.chat_id,'Ничего не найдено. Попробуйте ослабить фильтры.',reply_markup=kb([[b('← Фильтры','filter:search')],[b('🏠 Меню','go:home')]]))
            # No unsolicited extra messages on successful completion.
        except asyncio.CancelledError:
            if progress:await self.safe_delete(c.chat_id,progress.message_id)
            raise
        except Exception as e:
            c.fetching=False;c.done=True
            log.warning('search error: %s',e)
            if progress:
                try:await self.bot.edit_message_text(f'⚠️ Не удалось получить объявления.\n<code>{esc(str(e)[:260])}</code>',
                    chat_id=c.chat_id,message_id=progress.message_id,parse_mode='HTML',
                    reply_markup=kb([[b('↻ Повторить','go:'+('latest' if c.source=='latest' else 'searchrun'))],[b('← Меню','go:home')]]))
                except TelegramBadRequest:pass
            elif c.message_id:await self.bot.send_message(c.chat_id,'⚠️ Ошибка догрузки. Уже найденные объявления доступны.')

    async def launch(self,uid,chat,kind,filters=None):
        await self.close_catalog(uid)
        if kind in ('search','latest'):
            f=clean(filters or await self.db.get_filters(uid,'latest' if kind=='latest' else 'search'))
            c=Catalog(chat,uid,kind,'Новые' if kind=='latest' else 'Поиск',f)
            self.catalogs[uid]=c;c.worker=self.task(self._load(c));return
        if kind=='favorites':
            ids=await self.db.listing_ids(uid,'favorites')
            hidden=set(await self.db.listing_ids(uid,'hidden'))
            ids=[x for x in ids if x not in hidden]
            if not ids:
                await self.panel(uid,chat,'<b>❤️ Избранное</b>\n\nПока ничего нет. Добавляйте объявления кнопкой 🤍.',kb([[b('← Главное меню','go:home')]]))
                return
            c=Catalog(chat,uid,'favorites','Избранное',clean(DEFAULT),ids=ids,done=True)
            self.catalogs[uid]=c;await self._send_card(c,0,replace=False)
            return
        if kind.startswith('collection:'):
            days=int(kind.split(':')[-1]);all_items=await self.db.all_listings()
            filters=await self.db.get_filters(uid,'latest')
            matches=[]
            for x in all_items:
                rent=price_of(x);size=(x.get('roomInformation') or {}).get('roomArea')
                try:
                    if not (filters['price_min']<=rent<=filters['price_max']):continue
                    if filters['room_type']!='ANY' and (x.get('roomInformation') or {}).get('roomType')!=filters['room_type']:continue
                    if filters['area_min'] is not None and (size is None or float(size)<filters['area_min']):continue
                    if filters['area_max'] is not None and (size is None or float(size)>filters['area_max']):continue
                    if not matches_local(x,filters):continue
                    if age_days(x)>days:continue
                except (TypeError,ValueError):continue
                matches.append(x)
            hidden=set(await self.db.listing_ids(uid,'hidden'))
            groups=comparables(all_items)
            ranked=[]
            for x in matches:
                if str(x['id']) in hidden:continue
                val,conf=score(x,groups)
                if conf>=.45:ranked.append((val,conf,x))
            ranked.sort(key=lambda t:(t[0],t[1]),reverse=True)
            if not ranked:
                await self.panel(uid,chat,f'<b>🏆 Подборка · {days} дн.</b>\n\nПока нет достаточно полных объявлений в локальной выборке. Можно запустить «Новые», чтобы пополнить данные.',kb([[b('← Подборки','go:collections')],[b('🏠 Меню','go:home')]]))
                return
            top=ranked[:100]
            c=Catalog(chat,uid,'collection','Подборки',filters,ids=[str(x[2]['id']) for x in top],done=True,
                      scores={str(x[2]['id']):x[0] for x in top})
            self.catalogs[uid]=c;await self._send_card(c,0,False)

    async def show_detail(self,c:Catalog):
        if not c.ids:return
        c.detail_mode=True
        item,detail=await self.db.get_listing(c.ids[c.index]);loader=None
        if not item:return
        try:
            loader=await self.bot.send_message(c.chat_id,'📸 <b>Загружаю детали и фотографии…</b>',parse_mode='HTML')
            if detail is None:
                try:
                    detail=await self.api.details(item)
                    await self.db.store_detail(item['id'],detail)
                except ProviderError as e:
                    log.warning('detail fallback: %s',e)
                    detail=item
            if not c.detail_mode:return
            # Summary and full original description are separated to remain within Telegram limits.
            v=dict(detail);description=plain_html(v.pop('detail', '') or '')
            summary=details_text(item,v,self.fx.rate)
            # Protect Telegram's 4096-character HTML limit.
            if len(summary)>3650:summary=summary[:3600]+'…'
            images=(detail.get('images') or []) if isinstance(detail,dict) else []
            paths=[image_url(v.get('pictureUrl')) for v in images if isinstance(v,dict) and v.get('pictureUrl')]
            # Real gallery is sent only on Details, not on every card flip.
            if len(paths)>1:
                try:
                    for pos in range(0,len(paths),10):
                        part=paths[pos:pos+10]
                        if len(part)==1:
                            mm=await self.bot.send_photo(c.chat_id,part[0]);c.extra_messages.append(mm.message_id)
                        else:
                            mm=await self.bot.send_media_group(c.chat_id,[InputMediaPhoto(media=p) for p in part])
                            c.extra_messages.extend(x.message_id for x in mm)
                except TelegramBadRequest as e:
                    log.warning('gallery remote photo failed: %s',e)
                    for start in range(0,len(paths),12):
                        mm=await self.bot.send_message(c.chat_id,'🖼 Фотографии:\n'+'\n'.join(paths[start:start+12]),disable_web_page_preview=True)
                        c.extra_messages.append(mm.message_id)
            elif len(paths)==1 and paths[0]!=image_url(item.get('coverPicture')):
                mm=await self.bot.send_photo(c.chat_id,paths[0]);c.extra_messages.append(mm.message_id)
            # Keep the return button below the gallery and description, not buried above photos.
            if not c.detail_mode:return
            if description:
                for start in range(0,min(len(description),12000),2900):
                    piece=escape(description[start:start+2900])
                    mm=await self.bot.send_message(c.chat_id,'📝 <b>Описание</b>\n'+piece,parse_mode='HTML',disable_web_page_preview=True)
                    c.extra_messages.append(mm.message_id)
            if not c.detail_mode:return
            msg=await self.bot.send_message(c.chat_id,summary,parse_mode='HTML',
                reply_markup=detail_kb(item),disable_web_page_preview=True)
            c.extra_messages.append(msg.message_id)
        except (TelegramBadRequest,TelegramForbiddenError) as e:
            log.warning('Detail UI error %s',e)
        finally:
            if loader:await self.safe_delete(c.chat_id,loader.message_id)

    async def market(self,uid,chat,by='district'):
        rows=await self.db.all_listings()
        grouped=defaultdict(list)
        for x in rows:
            rent=price_of(x)
            if rent is None:continue
            p=x.get('project') or {}
            key=(p.get('address') if by=='district' else p.get('nameEnglish') or p.get('name'))
            if key:grouped[key].append(float(rent))
        ranked=sorted(grouped.items(),key=lambda t:len(t[1]),reverse=True)[:15]
        lines=[f'<b>📊 Рынок · {"Районы" if by=="district" else "Жилые комплексы"}</b>',
               f'📦 В локальной базе: {len(rows)} объявлений',
               '<i>Средние цены только по сохранённой выборке, а не по всему рынку. Повторные объявления могут влиять на оценку.</i>','']
        for name,prices in ranked:
            avg=round(mean(prices))
            rub=self.fx.rub(avg)
            value=f'{money(avg)} ฿'+(f' · ≈{money(rub)} ₽' if rub else '')
            lines.append(f'<b>{esc(str(name)[:48])}</b>\n{value} · {len(prices)} объявл.')
        if not ranked:lines.append('Пока недостаточно данных. Запустите «Новые» или «Поиск».')
        text='\n\n'.join(lines)
        if len(text)>3950:text=text[:3900]+'…'
        self.panel_kind[uid]='market'
        await self.panel(uid,chat,text,kb([[b('📍 Районы','market:district'),b('🏙 ЖК','market:project')],[b('← Меню','go:home')]]))

    async def admin(self,uid,chat):
        if uid!=self.cfg.admin_id:return
        st=await self.db.stats();disabled=await self.db.get_config('maintenance',False)
        sync=await self.db.get_config('sync_enabled',True)
        uptime=datetime.now(timezone.utc)-self.started
        text=('<b>🛠 Админка</b>\n\n'
              f'👥 Пользователи: <b>{st["users"]}</b> · за 24ч: {st["daily"]}\n'
              f'🔎 Запросов: {st["requests"] or 0} · просмотров: {st["cards"] or 0}\n'
              f'🏘 Объявлений в базе: {st["total"]} · детальных: {st["details"]}\n'
              f'❤️ Избранных: {st["favorites"]} · скрытых: {st["hidden"]}\n'
              f'⏱ Аптайм: {int(uptime.total_seconds()//60)} мин.\n'
              f'🤖 Режим обслуживания: {"вкл." if disabled else "выкл."}\n'
              f'📡 Фоновое обновление: {"вкл." if sync else "выкл."}\n'
              f'💱 THB/RUB: {self.fx.rate or "недоступен"} (ЦБ РФ)\n'
              f'🌐 API: {esc(self.api.last_error[:150] or "последний запрос успешен")}')
        await self.panel(uid,chat,text,kb([
            [b('🔁 Обновить базу сейчас','admin:sync'),b('📊 Обновить статистику','admin:stats')],
            [b(('⛔ Обслуживание: вкл.' if disabled else '✅ Обслуживание: выкл.'),'admin:maintenance')],
            [b(('📡 Автосбор: вкл.' if sync else '📡 Автосбор: выкл.'),'admin:background')],
            [b('💱 Обновить курс','admin:fx')],[b('← Меню','go:home')]]))

    async def sync_once(self,pages=3):
        # Public sampling profile, not global complete-market coverage.
        if self.sync_lock.locked():return 0
        async with self.sync_lock:
            f=clean(DEFAULT)
            n=0
            try:
                for page in range(1,pages+1):
                    rows,pag=await self.api.search_page(f,page=page)
                    await self.db.put_listings(rows);n+=len(rows)
                    if page>=int(pag.get('totalPages') or 1):break
                    await asyncio.sleep(.35)
                log.info('Index sync: %s listings',n)
            except Exception as e:log.warning('Background sync: %s',e)
            return n

    async def background_sync(self):
        while True:
            if await self.db.get_config('sync_enabled',True):
                await self.sync_once(3)
            await asyncio.sleep(20*60)

    def _setup_handlers(self):
        r=self.router

        @r.message(CommandStart())
        async def start(message:Message):
            if not message.from_user:return
            uid=message.from_user.id;await self.db.user(message.from_user)
            await self.close_catalog(uid)
            await self.home(uid,message.chat.id)
            await self.safe_delete(message.chat.id,message.message_id)

        @r.message(Command('menu'))
        async def menu(message:Message):
            if not message.from_user:return
            await self.db.user(message.from_user);await self.home(message.from_user.id,message.chat.id)
            await self.safe_delete(message.chat.id,message.message_id)

        @r.callback_query(F.data.startswith('go:'))
        async def go(q:CallbackQuery):
            uid=q.from_user.id;chat=q.message.chat.id;action=q.data.split(':',1)[1]
            if action=='admin' and uid!=self.cfg.admin_id:
                await q.answer('🔒 Только для администратора',show_alert=True);return
            await q.answer()
            await self.db.user(q.from_user)
            if await self.db.get_config('maintenance',False) and uid!=self.cfg.admin_id:
                await self.panel(uid,chat,'🔧 Бот временно на обслуживании.',kb([[b('↻ Проверить','go:home')]]));return
            if action=='home':await self.close_catalog(uid);await self.home(uid,chat)
            elif action=='latest':await self.launch(uid,chat,'latest')
            elif action=='searchrun':await self.launch(uid,chat,'search')
            elif action=='favorites':await self.launch(uid,chat,'favorites')
            elif action=='collections':
                await self.close_catalog(uid)
                self.panel_kind[uid]='collections'
                await self.panel(uid,chat,'<b>🏆 Подборки</b>\n\nЛучшие варианты по соотношению цены к похожим квартирам, площади, свежести и полноты данных.\n\n<i>Рейтинг считается по локальной выборке, не по всему PropertyHub. Используются настройки раздела «Новые».</i>',kb([
                    [b('📅 1 день','go:collection:1'),b('🗓 7 дней','go:collection:7'),b('📆 30 дней','go:collection:30')],
                    [b('← Меню','go:home')]]))
            elif action.startswith('collection:'):await self.launch(uid,chat,action)
            elif action=='market':await self.close_catalog(uid);await self.market(uid,chat)
            elif action=='admin':await self.close_catalog(uid);await self.admin(uid,chat)

        @r.callback_query(F.data.startswith('filter:'))
        async def filter_open(q:CallbackQuery):
            await q.answer()
            uid=q.from_user.id
            await self.db.user(q.from_user)
            await self.close_catalog(uid)
            await self.filters(uid,q.message.chat.id,q.data.split(':')[1])

        @r.callback_query(F.data.startswith('f:'))
        async def filter_action(q:CallbackQuery):
            await q.answer()
            uid=q.from_user.id;chat=q.message.chat.id
            bits=q.data.split(':');kind='search' if bits[1]=='s' else 'latest'
            action=bits[2] if len(bits)>2 else 'menu'
            f=await self.db.get_filters(uid,kind)
            prefix='f:'+bits[1]+':'
            if action=='noop':return
            if action=='run':
                await self.launch(uid,chat,kind,f);return
            if action=='menu':await self.filters(uid,chat,kind);return
            if action=='locpreset':
                val=bits[3] if len(bits)>3 else ''
                locations={'bangkok':'Bangkok','phuket':'Phuket','pattaya':'Pattaya','chiangmai':'Chiang Mai','samui':'Samui','all':''}
                if val in locations:
                    f['location_text']=locations[val]
                    await self.db.save_filters(uid,kind,f)
                await self.filters(uid,chat,kind);return
            if action=='location':
                rows=[[b('🏙 Бангкок',prefix+'locpreset:bangkok'),b('🌊 Паттайя',prefix+'locpreset:pattaya')],
                      [b('🏝 Пхукет',prefix+'locpreset:phuket'),b('🌿 Чиангмай',prefix+'locpreset:chiangmai')],
                      [b('🌴 Самуи',prefix+'locpreset:samui'),b('🌏 Весь Таиланд',prefix+'locpreset:all')],
                      [b('✍️ Ввести район вручную',prefix+'loccustom')],[b('← Назад',prefix+'menu')]]
                await self.panel(uid,chat,'<b>📍 География</b>\n\n<i>Быстрый выбор фильтрует загруженные объявления по адресу. Для строгого серверного поиска можно указать ID зоны.</i>',kb(rows))
                return
            if action in ('price','area','loccustom','zone'):
                prompts={'price':'Введите цену от и до (THB), через пробел. Например: 10000 15000',
                    'area':'Введите площадь от и до (м²): 25 45. Для сброса: -',
                    'loccustom':'Введите название района / города / ЖК латиницей. Для сброса: -',
                    'zone':'Введите ID зон через запятую или ID ЖК как project:3212. Для сброса: -'}
                self.pending[uid]=(kind,action,0)
                m=await self.bot.send_message(chat,'✍️ '+prompts[action]+'\n<i>Сообщение с ответом будет удалено.</i>',parse_mode='HTML')
                self.pending[uid]=(kind,action,m.message_id)
                return
            if action=='reset':f=clean(DEFAULT)
            elif action=='copy':
                other='latest' if kind=='search' else 'search'
                await self.db.save_filters(uid,other,f)
            elif action=='room':
                rows=[]
                values=list(ROOM_TYPES.items())
                for i in range(0,len(values),2):
                    rows.append([b(('✓ ' if f['room_type']==k else '')+name,prefix+'setroom:'+k) for k,name in values[i:i+2]])
                rows.append([b('← Назад',prefix+'menu')])
                await self.panel(uid,chat,'<b>🛏 Тип квартиры</b>',kb(rows));return
            elif action=='sort':
                rows=[[b(('✓ ' if f['sort']==k else '')+name,prefix+'setsort:'+k)] for k,name in SORTS.items()]
                rows.append([b('← Назад',prefix+'menu')])
                await self.panel(uid,chat,'<b>↕️ Сортировка</b>',kb(rows));return
            elif action in ('amen','fac'):
                off=int(bits[3]) if len(bits)>3 else 0
                await self.panel(uid,chat,('<b>🧰 Удобства квартиры</b>' if action=='amen' else '<b>🏢 Инфраструктура ЖК</b>')+'\nВыбирайте сколько угодно параметров.',choices_kb(kind,action,f,off));return
            elif action=='toggle':
                category,key,off=bits[3],bits[4],int(bits[5]);which='amenities' if category=='amen' else 'facilities'
                allowed=AMENITIES if category=='amen' else FACILITIES
                if key not in allowed:return
                if key in f[which]:f[which].remove(key)
                else:f[which].append(key)
                await self.db.save_filters(uid,kind,f)
                await self.panel(uid,chat,'<b>Выберите удобства</b>\n✓ — фильтр включён.',choices_kb(kind,category,f,off));return
            elif action=='clear':f['amenities' if bits[3]=='amen' else 'facilities']=[]
            elif action=='setroom':f['room_type']=bits[3]
            elif action=='setsort':f['sort']=bits[3]
            await self.db.save_filters(uid,kind,f)
            await self.filters(uid,chat,kind)

        @r.message(F.text)
        async def input_message(m:Message):
            if not m.from_user:return
            uid=m.from_user.id
            if uid not in self.pending:return
            kind,field,prompt=self.pending.pop(uid)
            f=await self.db.get_filters(uid,kind)
            raw=(m.text or '').strip()
            try:
                if field in ('price','area'):
                    if raw=='-' and field=='area':f['area_min']=f['area_max']=None
                    else:
                        parts=raw.replace(',','.').split()
                        if len(parts)!=2:raise ValueError('Введите два числа через пробел')
                        nums=[float(x) for x in parts]
                        if any(x<0 for x in nums) or nums[0]>nums[1]:raise ValueError('Некорректный диапазон')
                        if field=='price':f['price_min'],f['price_max']=int(nums[0]),int(nums[1])
                        else:f['area_min'],f['area_max']=nums
                elif field=='loccustom':f['location_text']='' if raw=='-' else raw[:80]
                elif field=='zone':
                    if raw=='-':f['zone_ids']=[];f['project_id']=None
                    elif raw.lower().startswith('project:'):
                        f['project_id']=int(raw.split(':',1)[1]);f['zone_ids']=[]
                    else:
                        zones=[x.strip() for x in raw.split(',')]
                        if not all(x.isdigit() for x in zones):raise ValueError('Нужны числовые ID зон через запятую')
                        f['zone_ids']=zones;f['project_id']=None
                await self.db.save_filters(uid,kind,f)
                await self.safe_delete(m.chat.id,m.message_id)
                await self.safe_delete(m.chat.id,prompt)
                await self.filters(uid,m.chat.id,kind)
            except (ValueError,TypeError) as e:
                await self.safe_delete(m.chat.id,m.message_id)
                await self.safe_delete(m.chat.id,prompt)
                await self.bot.send_message(m.chat.id,'⚠️ '+esc(str(e))+'\nПопробуйте ещё раз через настройки.',parse_mode='HTML')

        @r.callback_query(F.data.startswith('card:'))
        async def card(q:CallbackQuery):
            await q.answer()
            uid=q.from_user.id
            c=self.catalogs.get(uid)
            if not c or c.message_id!=q.message.message_id and q.data!='card:detailback':return
            action=q.data.split(':',1)[1]
            if action=='noop':return
            if action=='back':
                await self.close_catalog(uid);await self.home(uid,q.message.chat.id);return
            if not c.ids:return
            id=c.ids[c.index]
            if action in ('prev','next'):
                if c.detail_mode:await self._clear_details(c)
                index=c.index+(-1 if action=='prev' else 1)
                if index<0 or index>=len(c.ids):return
                await self._send_card(c,index)
            elif action=='heart':
                await self.db.toggle(uid,id,'favorites')
                await self._send_card(c,c.index)
            elif action=='hide':
                await self.db.toggle(uid,id,'hidden')
                c.ids.pop(c.index)
                if not c.ids:
                    await self.close_catalog(uid)
                    await self.bot.send_message(c.chat_id,'🙈 Все объявления скрыты. Вы можете найти новые через поиск.')
                    return
                await self._send_card(c,min(c.index,len(c.ids)-1))
            elif action=='detail':
                if c.detail_mode:return
                c.detail_mode=True
                c.detail_task=self.task(self.show_detail(c))
            elif action=='detailback':
                await self._clear_details(c)
                # The original card remains in place, so no new message needed.

        @r.callback_query(F.data.startswith('market:'))
        async def market_callback(q:CallbackQuery):
            await q.answer();await self.market(q.from_user.id,q.message.chat.id,q.data.split(':')[1])

        @r.callback_query(F.data.startswith('admin:'))
        async def admin_callback(q:CallbackQuery):
            await q.answer()
            if q.from_user.id!=self.cfg.admin_id:return
            action=q.data.split(':')[1]
            if action=='maintenance':await self.db.set_config('maintenance',not await self.db.get_config('maintenance',False))
            elif action=='background':await self.db.set_config('sync_enabled',not await self.db.get_config('sync_enabled',True))
            elif action=='sync':
                self.task(self.sync_once(3))
                await self.bot.send_message(q.message.chat.id,'🔄 Обновление индекса запущено в фоне.')
            elif action=='fx':self.task(self.fx.refresh())
            await self.admin(q.from_user.id,q.message.chat.id)

    async def run(self):
        await self.db.init()
        self.task(self.background_sync());self.task(self.fx.loop())
        try:
            await self.bot.delete_webhook(drop_pending_updates=False)
            await self.dp.start_polling(self.bot,handle_as_tasks=True)
        finally:
            for t in list(self.tasks):t.cancel()
            if self.tasks:await asyncio.gather(*self.tasks,return_exceptions=True)
            await self.api.close();await self.db.close();await self.bot.session.close()
