"""Explicitly supported PropertyHub GraphQL filters; never send unsupported keys."""
from __future__ import annotations
from copy import deepcopy

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
SORTS = {'CREATED_AT':'🆕 Сначала новые', 'REFRESHED_AT':'🔄 Обновлённые', 'PRICE_MONTHLY_LOW_TO_HIGH':'💰 Сначала дешёвые'}
DEFAULT = {
    'price_min':10000, 'price_max':15000, 'area_min':None, 'area_max':None,
    'room_type':'ANY', 'sort':'CREATED_AT', 'amenities':[], 'facilities':[],
    'zone_ids':[], 'project_id':None, 'location_text':'',
}

def clean(raw:dict | None) -> dict:
    out = deepcopy(DEFAULT)
    if isinstance(raw,dict):
        for k in out:
            if k in raw: out[k] = raw[k]
    for k in ('price_min','price_max','area_min','area_max','project_id'):
        try: out[k] = int(out[k]) if k in ('price_min','price_max','project_id') and out[k] is not None else float(out[k]) if out[k] is not None else None
        except (TypeError,ValueError):out[k] = DEFAULT[k]
    out['price_min'] = max(0, int(out['price_min'] or 0))
    out['price_max'] = max(out['price_min'], int(out['price_max'] or out['price_min']))
    out['amenities'] = sorted(set(out['amenities'] or []) & AMENITIES.keys())
    out['facilities'] = sorted(set(out['facilities'] or []) & FACILITIES.keys())
    out['room_type'] = out['room_type'] if out['room_type'] in ROOM_TYPES else 'ANY'
    out['sort'] = out['sort'] if out['sort'] in SORTS else 'CREATED_AT'
    out['location_text'] = str(out['location_text'] or '')[:80]
    zones = out['zone_ids'] or []
    out['zone_ids'] = [str(x) for x in zones if str(x).isdigit()][:20]
    return out

def attributes(filters:dict) -> dict:
    f=clean(filters)
    result={'zoneIds':f['zone_ids'], 'postType':'FOR_RENT', 'propertyType':'CONDO'}
    if f['project_id']:result['projectId']=str(f['project_id'])
    result['price']={'min':f['price_min'],'max':f['price_max']}
    room={}
    if f['room_type']!='ANY':room['roomType']=f['room_type']
    if f['area_min'] is not None or f['area_max'] is not None:
        def as_graph_number(x):
            x=float(x)
            return int(x) if x.is_integer() else x
        room['roomArea']={'min':as_graph_number(f['area_min']) if f['area_min'] is not None else 0,
                          'max':as_graph_number(f['area_max']) if f['area_max'] is not None else 100000}
    if room:result['roomInformation']=room
    if f['amenities']:result['amenities']={k:True for k in f['amenities']}
    if f['facilities']:result['facilities']={k:True for k in f['facilities']}
    return result

def matches_local(item:dict, filters:dict)->bool:
    term=clean(filters)['location_text'].strip().casefold()
    if not term:return True
    p=item.get('project') or {}
    hay=' '.join(str(x or '') for x in (p.get('name'),p.get('nameEnglish'),p.get('address'),item.get('address'))).casefold()
    return term in hay


def concise(filters:dict)->str:
    f=clean(filters)
    words=[f"{f['price_min']:,}–{f['price_max']:,} ฿".replace(',',' ')]
    if f['room_type']!='ANY':words.append(ROOM_TYPES[f['room_type']])
    if f['area_min'] is not None or f['area_max'] is not None:
        words.append(f"{f['area_min'] or 0:g}–{f['area_max'] or '∞'} м²")
    if f['location_text']:words.append('📍'+f['location_text'])
    n=len(f['amenities'])+len(f['facilities'])
    if n:words.append(f'✓ {n} удобств')
    if f['zone_ids']:words.append(f'Зон: {len(f["zone_ids"])}')
    if f['project_id']:words.append('ЖК #'+str(f['project_id']))
    return ' · '.join(words)
