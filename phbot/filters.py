"""Search profiles; only verified GraphQL fields go over the wire.
Additional constraints are checked locally against full search result summaries.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import date, datetime, timezone
from math import isfinite
from .geography import CITIES, AREAS_BY_ID, city_of, district_of

AMENITIES = {
    'hasAir':'Кондиционер', 'hasDigitalDoorLock':'Электронный замок',
    'hasFurniture':'Мебель', 'hasHotTub':'Ванна/джакузи в квартире',
    'hasInternet':'Интернет', 'hasKitchenHood':'Вытяжка',
    'hasKitchenStove':'Плита', 'hasPhone':'Телефон',
    'hasRefrigerator':'Холодильник', 'hasTV':'Телевизор',
    'hasWasher':'Стиральная машина', 'hasWaterHeater':'Водонагреватель',
    'hasMicrowave':'Микроволновка', 'hasLift':'Лифт',
    'hasParking':'Парковка', 'hasMotorcycleParking':'Мотопарковка',
    'hasSecurity':'Охрана', 'hasCctv':'Видеонаблюдение',
    'hasPool':'Бассейн', 'hasFitness':'Тренажёрный зал',
    'hasSauna':'Сауна', 'hasSteam':'Паровая комната',
    'hasShuttle':'Шаттл', 'hasEvCharger':'Зарядка EV',
    'hasPark':'Парк', 'hasPlayground':'Детская площадка',
    'hasStadium':'Стадион', 'hasLibrary':'Библиотека',
    'hasMeeting':'Переговорная', 'hasShop':'Магазин',
    'hasRestaurant':'Ресторан', 'hasLaundry':'Прачечная',
}
FACILITIES = {
    'cctv':'Камеры', 'evCharger':'Зарядка EV', 'fitness':'Фитнес',
    'laundry':'Прачечная', 'library':'Библиотека', 'lift':'Лифт',
    'meetingRoom':'Переговорная', 'motorcycleParking':'Мотопарковка',
    'park':'Парк', 'parking':'Парковка', 'playground':'Детская площадка',
    'pool':'Бассейн', 'restaurant':'Ресторан', 'sauna':'Сауна',
    'security':'Охрана', 'shop':'Магазин', 'shuttle':'Шаттл',
    'stadium':'Стадион', 'steam':'Паровая комната', 'wifi':'Wi-Fi в ЖК',
}
ROOM_TYPES = {
    'ANY':'Любой', 'STUDIO':'Студия', 'ONE_BED_ROOM':'1 спальня',
    'TWO_BED_ROOM':'2 спальни', 'THREE_BED_ROOM':'3 спальни',
    'FOUR_BED_ROOM':'4 спальни', 'PENTHOUSE':'Пентхаус',
}
SORTS = {'CREATED_AT':'🆕 Сначала новые', 'REFRESHED_AT':'🔄 Обновлённые',
         'PRICE_MONTHLY_LOW_TO_HIGH':'💰 Сначала дешёвые'}
# Truly empty default profile: no price/area/city/date/deposit restrictions.
DEFAULT = {
    'price_min':None, 'price_max':None, 'area_min':None, 'area_max':None,
    'room_type':'ANY', 'sort':'CREATED_AT', 'amenities':[], 'facilities':[],
    'zone_ids':[], 'project_id':None, 'location_text':'', 'region':'',
    'district_ids':[], 'deposit_max_months':None, 'advance_max_months':None, 'date_from':None, 'date_to':None,
}

def _date(value):
    if not value:return None
    try:return date.fromisoformat(str(value)).isoformat()
    except (ValueError,TypeError):return None

def clean(raw:dict|None)->dict:
    out=deepcopy(DEFAULT)
    if isinstance(raw,dict):
        for k in out:
            if k in raw:out[k]=raw[k]
    for k in ('price_min','price_max','area_min','area_max','project_id','deposit_max_months','advance_max_months'):
        try:
            v=out[k]
            out[k]=float(v) if v is not None and v!='' else None
            if out[k] is not None:
                # Never serialize NaN/Infinity or any negative GraphQL bounds.
                if not isfinite(out[k]) or out[k]<0:
                    out[k]=None
                elif k in ('price_min','price_max'):
                    out[k]=min(out[k],2_147_483_647)
                elif k in ('area_min','area_max'):
                    out[k]=min(out[k],1_000_000)
                elif k in ('deposit_max_months','advance_max_months'):
                    out[k]=min(out[k],12)
                elif k=='project_id' and out[k]>1_000_000_000:
                    out[k]=None
            if k in ('price_min','price_max','project_id') and out[k] is not None:out[k]=int(out[k])
        except (ValueError,TypeError,OverflowError):out[k]=None
    for a,b in [('price_min','price_max'),('area_min','area_max')]:
        if out[a] is not None and out[b] is not None and out[a]>out[b]:out[a],out[b]=out[b],out[a]
    out['amenities']=sorted(set(out['amenities'] or []) & AMENITIES.keys())
    out['facilities']=sorted(set(out['facilities'] or []) & FACILITIES.keys())
    out['room_type']=out['room_type'] if out['room_type'] in ROOM_TYPES else 'ANY'
    out['sort']=out['sort'] if out['sort'] in SORTS else 'CREATED_AT'
    out['location_text']=str(out['location_text'] or '')[:80]
    out['region']=out['region'] if out['region'] in CITIES else ''
    zones=out['zone_ids'] or []
    out['zone_ids']=[str(x) for x in zones if str(x).isdigit()][:20]
    out['district_ids']=[x for x in (out['district_ids'] or []) if x in AREAS_BY_ID and (not out['region'] or AREAS_BY_ID[x].city==out['region'])][:60]
    out['date_from']=_date(out['date_from']);out['date_to']=_date(out['date_to'])
    if out['date_from'] and out['date_to'] and out['date_from']>out['date_to']:
        out['date_from'],out['date_to']=out['date_to'],out['date_from']
    return out

def attributes(filters:dict)->dict:
    f=clean(filters)
    result={'zoneIds':f['zone_ids'], 'postType':'FOR_RENT', 'propertyType':'CONDO'}
    if f['project_id']:result['projectId']=str(f['project_id'])
    if f['price_min'] is not None or f['price_max'] is not None:
        result['price']={'min':f['price_min'] if f['price_min'] is not None else 0,
                         'max':f['price_max'] if f['price_max'] is not None else 1000000000}
    room={}
    if f['room_type']!='ANY':room['roomType']=f['room_type']
    if f['area_min'] is not None or f['area_max'] is not None:
        def api_number(value):
            number=float(value)
            return int(number) if number.is_integer() else number
        room['roomArea']={'min':api_number(f['area_min']) if f['area_min'] is not None else 0,
                          'max':api_number(f['area_max']) if f['area_max'] is not None else 100000}
    if room:result['roomInformation']=room
    # PropertyHub actually distinguishes apartment amenities (hasWasher, etc.)
    # from building facilities (pool, fitness). Both are separate GraphQL keys.
    # Old profiles used apartment keys for building facilities. Map these
    # to confirmed GraphQL facilities.pool/fitness (not amenities.hasPool).
    building_aliases={'hasPool':'pool','hasFitness':'fitness','hasSauna':'sauna',
                      'hasSteam':'steam','hasSecurity':'security','hasCctv':'cctv',
                      'hasLift':'lift','hasParking':'parking','hasPlayground':'playground',
                      'hasShuttle':'shuttle','hasLaundry':'laundry','hasPark':'park',
                      'hasStadium':'stadium','hasLibrary':'library','hasRestaurant':'restaurant',
                      'hasShop':'shop','hasEvCharger':'evCharger','hasMotorcycleParking':'motorcycleParking'}
    apartment=[k for k in f['amenities'] if k not in building_aliases]
    facility=set(f['facilities'])|{building_aliases[k] for k in f['amenities'] if k in building_aliases}
    if apartment:result['amenities']={k:True for k in apartment}
    if facility:result['facilities']={k:True for k in sorted(facility)}
    return result

def payment_terms(item, field):
    """Rental payment as (THB, monthly-rent equivalents).

    Missing/CALL values are unknown, never zero. Supports PropertyHub
    MONTH or AMOUNT structures in both search and legacy result formats.
    """
    if field not in ('deposit', 'advancePayment'):
        raise ValueError('Unsupported rental payment field')
    rental=((item.get('priceLandAndHouse') or {}).get('forRent') or {})
    old=((item.get('price') or {}).get('forRent') or {})
    payment=rental.get(field) or old.get(field) or {}
    if not isinstance(payment,dict):return None,None
    typ=payment.get('type')
    if typ in ('CALL', 'NO_INFORMATION', 'UNKNOWN'):return None,None
    amount=payment.get('amount')
    month=payment.get('month')
    from .propertyhub import price_of
    price=price_of(item)
    try:
        if amount is not None and (typ in ('AMOUNT',None) or month is None):
            amount=float(amount)
            if not isfinite(amount) or amount<0:return None,None
            monthly=float(price) if price is not None else None
            if monthly is None or not isfinite(monthly) or monthly<=0:
                return amount,None
            return amount,amount/monthly
        if month is not None:
            month=float(month)
            if not isfinite(month) or month<0:return None,None
            monthly=float(price) if price is not None else None
            if monthly is None or not isfinite(monthly) or monthly<=0:
                return None,month
            return month*monthly,month
    except (ValueError,TypeError,OverflowError,ZeroDivisionError):pass
    return None,None

def deposit_terms(item):
    """Backward-compatible (deposit THB, months)."""
    return payment_terms(item,'deposit')

def advance_terms(item):
    """(advance rent THB, months), independent from the deposit."""
    return payment_terms(item,'advancePayment')

def matches_local(item:dict,filters:dict)->bool:
    f=clean(filters)
    if f['region'] and city_of(item)!=f['region']:return False
    if f['district_ids']:
        district=district_of(item)
        if not district or district.id not in f['district_ids']:return False
    term=f['location_text'].strip().casefold()
    if term:
        p=item.get('project') or {}
        hay=' '.join(str(x or '') for x in (p.get('name'),p.get('nameEnglish'),p.get('address'),item.get('address'))).casefold()
        if term not in hay:return False
    if f['deposit_max_months'] is not None:
        _,months=deposit_terms(item)
        if months is None or months>f['deposit_max_months']:return False
    if f['advance_max_months'] is not None:
        _,months=advance_terms(item)
        if months is None or months>f['advance_max_months']:return False
    if f['date_from'] or f['date_to']:
        try:
            day=datetime.fromisoformat(str(item.get('createdAt')).replace('Z','+00:00')).date().isoformat()
        except (ValueError,TypeError):return False
        if f['date_from'] and day<f['date_from']:return False
        if f['date_to'] and day>f['date_to']:return False
    # Local validation also guards exact settings when a provider ignores a key.
    from .propertyhub import price_of
    price=price_of(item)
    if f['price_min'] is not None and (price is None or float(price)<f['price_min']):return False
    if f['price_max'] is not None and (price is None or float(price)>f['price_max']):return False
    room=item.get('roomInformation') or {}
    if f['room_type']!='ANY' and room.get('roomType')!=f['room_type']:return False
    size=room.get('roomArea')
    if f['area_min'] is not None and (size is None or float(size)<f['area_min']):return False
    if f['area_max'] is not None and (size is None or float(size)>f['area_max']):return False
    return True

def concise(filters:dict)->str:
    f=clean(filters);words=[]
    if f['price_min'] is not None or f['price_max'] is not None:
        words.append(f"{f['price_min'] if f['price_min'] is not None else 0:g}–{f['price_max'] if f['price_max'] is not None else '∞'} ฿")
    if f['region']:words.append('📍 '+CITIES[f['region']].title)
    if f['district_ids']:words.append(f'Районов: {len(f["district_ids"])}')
    if f['room_type']!='ANY':words.append(ROOM_TYPES[f['room_type']])
    if f['area_min'] is not None or f['area_max'] is not None:
        words.append(f"{f['area_min'] if f['area_min'] is not None else 0:g}–{f['area_max'] if f['area_max'] is not None else '∞'} м²")
    if f['deposit_max_months'] is not None:words.append(f'Депозит ≤ {f["deposit_max_months"]:g} мес.')
    if f['advance_max_months'] is not None:words.append(f'Предоплата ≤ {f["advance_max_months"]:g} мес.')
    if f['date_from'] or f['date_to']:words.append(f"📅 {f['date_from'] or '…'} — {f['date_to'] or '…'}")
    if f['location_text']:words.append('🔤 '+f['location_text'])
    n=len(f['amenities'])+len(f['facilities'])
    if n:words.append(f'✓ {n} удобств')
    if f['zone_ids']:words.append(f'Зон: {len(f["zone_ids"])}')
    if f['project_id']:words.append('ЖК #'+str(f['project_id']))
    return ' · '.join(words) if words else 'Без ограничений · весь Таиланд'
