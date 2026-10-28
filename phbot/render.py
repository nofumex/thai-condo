"""Telegram HTML markup and compact keyboards, always escape external text."""
from html import escape
from datetime import datetime
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from .filters import AMENITIES,FACILITIES,ROOM_TYPES,SORTS,concise,clean
from .propertyhub import image_url,listing_url,price_of,plain_html

def esc(x):return escape(str(x if x is not None else '—'),quote=False)
def money(n):
    try:return f'{int(n):,}'.replace(',',' ')
    except (ValueError,TypeError):return '—'
def pretty_date(s):
    try:return datetime.fromisoformat(str(s).replace('Z','+00:00')).strftime('%d.%m.%Y')
    except (TypeError,ValueError):return '—'
def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t,callback_data=c) if c else InlineKeyboardButton(text=t,url=u) for t,c,u in row] for row in rows])
def b(text,callback):return (text,callback,None)
def link(text,url):return (text,None,url)

def main_kb(admin=False):
    rows=[[b('🆕 Новые','go:latest'),b('🔎 Поиск и фильтры','filter:search')],
          [b('🏆 Подборки','go:collections'),b('❤️ Избранное','go:favorites')],
          [b('📊 Рынок','go:market'),b('⚙️ Настройки','filter:latest')]]
    rows.append([b('🛠 Админка','go:admin')])
    return kb(rows)
MAIN='<b>🏙 Аренда в Тайланде</b>\n\nСвежие объявления из <b>Property Hub</b>\n\n<em>Выберите раздел:</em>'

def filter_summary(kind,f):
    return ('<b>🔎 Поиск и фильтры</b>' if kind=='search' else '<b>⚙️ Настройки · Новые</b>') + '\n\n' + f'🎯 {esc(concise(f))}\n' + f'↕️ {esc(SORTS[f["sort"]])}\n\n' + '<i>Выберите параметры. Активные удобства отмечены ✓. Поиск начнётся только после кнопки «Показать».</i>'

def filter_kb(kind,f):
    prefix='f:'+('s' if kind=='search' else 'l')+':'
    n=len(f['amenities']);m=len(f['facilities'])
    rows=[
        [b('💰 Цена',prefix+'price'),b('📐 Площадь',prefix+'area')],
        [b('🛏 '+ROOM_TYPES.get(f['room_type'],'Тип'),prefix+'room'),b('↕️ Сортировка',prefix+'sort')],
        [b(f'🧺 Удобства ({n})',prefix+'amen'),b(f'🏊 ЖК ({m})',prefix+'fac')],
        [b('📍 Район / город',prefix+'location'),b('🆔 ID зоны / ЖК',prefix+'zone')],
        [b('🧹 Сбросить',prefix+'reset'),b('📋 Копировать в другой раздел',prefix+'copy')],
        [b('🔎 Показать объявления',prefix+'run')],
        [b('← Главное меню','go:home')]
    ]
    return kb(rows)

def choices_kb(kind,category,f,offset=0):
    prefix='f:'+('s' if kind=='search' else 'l')+':'
    if category=='amen':items=list(AMENITIES.items());selected=f['amenities']
    else:items=list(FACILITIES.items());selected=f['facilities']
    chunk=items[offset:offset+10]
    rows=[]
    for i in range(0,len(chunk),2):
        row=[]
        for key,name in chunk[i:i+2]:row.append(b(('✓ ' if key in selected else '▫️ ')+name[:25],f'{prefix}toggle:{category}:{key}:{offset}'))
        rows.append(row)
    paging=[]
    if offset>0:paging.append(b('←',f'{prefix}{category}:{offset-10}'))
    paging.append(b(f'{offset+1}–{min(offset+10,len(items))} / {len(items)}',f'{prefix}noop'))
    if offset+10<len(items):paging.append(b('→',f'{prefix}{category}:{offset+10}'))
    rows.append(paging)
    rows.append([b('✓ Готово',prefix+'menu'),b('Снять все',prefix+'clear:'+category)])
    return kb(rows)

def card_text(x,favorite=False,score=None,rate=None):
    p=x.get('project') or {};r=x.get('roomInformation') or {}
    price=price_of(x);rub=round(price*rate) if price is not None and rate else None
    name=p.get('nameEnglish') or p.get('name') or x.get('title') or 'Без названия'
    text=f'🏙 <b>{esc(name)}</b>\n'
    text+=f'📍 {esc(p.get("address") or "Район не указан")}\n\n'
    text+=f'💰 <b>{money(price)} ฿/мес.</b>' + (f'  ≈ {money(rub)} ₽' if rub else '')+'\n'
    text+=f'📐 {esc(r.get("roomArea"))} м²   ·   🏢 {esc(r.get("onFloor"))} этаж\n'
    text+=f'🛏 {esc(r.get("numberOfBed"))} сп.   ·   🛁 {esc(r.get("numberOfBath"))} ванн.\n'
    text+=f'🗓 Добавлено: {pretty_date(x.get("createdAt"))}\n'
    if score is not None:text+=f'✨ Оценка: <b>{score:.1f}/100</b>\n'
    if favorite:text+='\n✅ <b>Добавлено в избранное</b>'
    return text[:1000]

def card_kb(x,index,total,favorite=False,busy=False):
    id=str(x['id'])
    rows=[[
        b('←','card:prev'),b(f'{index+1}/{total}'+(' ⏳' if busy else ''),'card:noop'),b('→','card:next')],
        [b('✅ В избранном' if favorite else '🤍 В избранное','card:heart'),b('🙈 Скрыть','card:hide')],
        [b('📋 Подробнее','card:detail'),link('🌐 PropertyHub',listing_url(x))],
        [b('← В меню','card:back')]
    ]
    return kb(rows)

def details_text(x,detail,rate=None):
    v=detail or x
    p=v.get('project') or x.get('project') or {}
    room=v.get('roomInformation') or x.get('roomInformation') or {}
    rateobj=((v.get('price') or {}).get('forRent') or {})
    price=((rateobj.get('monthly') or {}).get('price')) or price_of(x)
    def payment(o):
        o=o or {}
        if o.get('type')=='MONTH' and o.get('month') is not None:
            return f'{o["month"]} мес. ({money(float(price)*float(o["month"]))} ฿)' if price else f'{o["month"]} мес.'
        if o.get('amount') is not None:return f'{money(o["amount"])} ฿'
        return 'не указано'
    dep,adv=rateobj.get('deposit') or {},rateobj.get('advancePayment') or {}
    title=p.get('nameEnglish') or p.get('name') or v.get('title') or 'Без названия'
    text=f'🏙 <b>{esc(title)}</b>\n\n'
    text+=f'📍 {esc(p.get("address") or "—")}\n'
    text+=f'💰 <b>{money(price)} ฿/мес.</b>'
    if rate and price:text+=f' ≈ {money(float(price)*rate)} ₽'
    text+=f'\n📐 {esc(room.get("roomArea"))} м² · этаж {esc(room.get("onFloor"))}'
    text+=f'\n🛏 {esc(room.get("numberOfBed"))} сп. · 🛁 {esc(room.get("numberOfBath"))} ванн.\n'
    if room.get('building'):text+='🏢 Корпус: '+esc(room['building'])+'\n'
    if room.get('direction'):text+='🧭 Окна: '+esc(room['direction'])+'\n'
    year=(p.get('projectInfo') or {}).get('completedYear')
    if year:text+='🏗 Год постройки: '+esc(year)+'\n'
    text+='\n<b>Условия аренды</b>\n'
    text+='💳 Депозит: '+esc(payment(dep))+'\n'
    text+='💵 Предоплата: '+esc(payment(adv))+'\n'
    if price and dep.get('month') is not None and adv.get('month') is not None:
        amount=float(price)*(float(dep['month'])+float(adv['month']))
        text+=f'🔑 <b>При заселении: {money(amount)} ฿</b>\n'
    for term,key in [('1 мес.','monthly1'),('3 мес.','monthly3'),('6 мес.','monthly6'),('сутки','daily')]:
        obj=rateobj.get(key) or {}
        if obj.get('price') is not None:text+=f'• {term}: {money(obj["price"])} ฿\n'
    for name,data,names in (('🧰 Оснащение квартиры',v.get('amenities'),AMENITIES),
                            ('🏢 Инфраструктура ЖК',p.get('facilities') or v.get('facilities'),FACILITIES)):
        if isinstance(data,dict):
            positive=[names.get(k,k) for k,val in data.items() if val is True]
            negative=[names.get(k,k) for k,val in data.items() if val is False]
            if positive:text+='\n<b>'+name+'</b>\n✓ '+esc(', '.join(positive))+'\n'
            if negative:text+='Не отмечено: '+esc(', '.join(negative))+'\n'
    contact=v.get('contactInformation') or x.get('contactInformation') or {}
    if isinstance(contact,list):contact=contact[0] if contact else {}
    if contact:
        text+='\n<b>👤 Контакт</b>\n'+esc(contact.get('name'))+'\n'
        if contact.get('phone'):text+='📞 '+esc(', '.join(str(t) for t in contact['phone']))+'\n'
        if contact.get('lineId'):text+='LINE: '+esc(contact['lineId'])+'\n'
        if contact.get('whatsapp'):text+='WhatsApp: '+esc(contact['whatsapp'])+'\n'
        if contact.get('companyName'):text+='Компания: '+esc(contact['companyName'])+'\n'
    point=p.get('location') or v.get('location') or {}
    if point.get('lat') and point.get('lng'):
        text+=f'\n🗺 Координаты: {esc(point["lat"])}, {esc(point["lng"])}'
    text+='\n🗓 Создано: '+pretty_date(v.get('createdAt'))+' · обновлено: '+pretty_date(v.get('refreshedAt'))
    return text

def detail_kb(x):return kb([[link('🌐 Открыть на PropertyHub',listing_url(x))],[b('← Назад к объявлению','card:detailback')]])
