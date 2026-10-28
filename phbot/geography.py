"""Editorial expat area guide and conservative coordinate-based neighbourhoods.

These are *approximate search neighbourhoods*, NOT cadastral/administrative polygons.
Ratings are subjective long-stay convenience indicators, NOT official or measured scores.
Never silently assign an area when coordinates are missing/outside the city envelope.
Sources and tradeoffs are documented in docs/areas.md.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import radians, sin, cos, asin, sqrt

@dataclass(frozen=True)
class City:
    id: str
    title: str
    slug: str
    bounds: tuple[float, float, float, float]  # latmin,latmax,lngmin,lngmax

CITIES = {
    'bangkok': City('bangkok', 'Бангкок', 'bangkok', (13.48, 14.06, 100.30, 100.98)),
    'pattaya': City('pattaya', 'Паттайя', 'pattaya', (12.73, 13.05, 100.78, 101.06)),
    'phuket': City('phuket', 'Пхукет', 'phuket', (7.70, 8.22, 98.23, 98.54)),
    'chiangmai': City('chiangmai', 'Чиангмай', 'chiang-mai', (18.65, 19.05, 98.82, 99.14)),
    'samui': City('samui', 'Самуи', 'koh-samui', (9.41, 9.66, 99.87, 100.13)),
}

@dataclass(frozen=True)
class District:
    id: str
    city: str
    name: str
    lat: float
    lng: float
    radius_km: float
    rating: float
    reason: str

# Radius covers a meaningful neighbourhood catchment, without fabricating an exact boundary.
AREAS = [
    District('bkk-asok','bangkok','Асок / Нана',13.738,100.558,2.0,8.1,'транспорт и сервисы, но шумно'),
    District('bkk-phromphong','bangkok','Промпонг',13.731,100.569,1.9,8.8,'удобства, парки и магазины; высокая цена'),
    District('bkk-thonglor','bangkok','Тонглор',13.729,100.586,2.0,8.6,'кафе, рестораны и активная городская жизнь'),
    District('bkk-ekkamai','bangkok','Эккамай',13.720,100.586,1.8,8.2,'жильё и инфраструктура, но пробки'),
    District('bkk-phrakhanong','bangkok','Пхра Кханонг',13.713,100.595,2.0,8.0,'удачная цена за близость к центру'),
    District('bkk-onnut','bangkok','Он Нут',13.706,100.602,2.5,8.2,'цена, BTS, удобно для длительного проживания'),
    District('bkk-bangchak','bangkok','Банг Чак / Пуннавити',13.691,100.614,2.7,7.6,'доступнее, но дальше от центра'),
    District('bkk-bangna','bangkok','Банг На',13.671,100.605,4.2,6.6,'простор и цены, зависимость от маршрутов'),
    District('bkk-rama9','bangkok','Рама 9',13.755,100.566,2.4,8.0,'новые кондо, MRT, городская среда'),
    District('bkk-ratchada','bangkok','Ратчада',13.778,100.573,3.3,7.4,'цены и MRT; шум улиц'),
    District('bkk-ari','bangkok','Ари',13.779,100.544,2.4,8.7,'комфортные улицы, кафе, BTS'),
    District('bkk-phayathai','bangkok','Пхая Тхай',13.757,100.534,2.3,7.9,'транспортный узел и городской ритм'),
    District('bkk-siam','bangkok','Сиам / Ратчапрасонг',13.745,100.533,1.9,7.4,'самый центр, но торговый шум'),
    District('bkk-silom','bangkok','Силом',13.728,100.532,2.3,8.0,'центральность и MRT/BTS, шумный трафик'),
    District('bkk-sathorn','bangkok','Сатхон',13.716,100.526,3.0,8.1,'офисный центр и престиж, пробки'),
    District('bkk-lumpini','bangkok','Лумпини',13.729,100.545,1.9,8.9,'парк, близость к центру, дорого'),
    District('bkk-ladprao','bangkok','Латпрао',13.810,100.575,3.8,7.3,'средние цены, рынки и транспорт'),
    District('bkk-chatuchak','bangkok','Чатучак / Мо Чит',13.803,100.551,3.0,7.2,'парки и линии транспорта'),
    District('bkk-riverside','bangkok','Риверсайд / Чароен Крунг',13.716,100.504,3.0,7.5,'атмосфера реки, переменный доступ к метро'),
    District('bkk-thonburi','bangkok','Тхонбури',13.729,100.478,5.0,6.4,'доступная аренда, дольше дорога на восток'),
    District('bkk-pinklao','bangkok','Пинклао',13.775,100.476,3.5,6.5,'местный быт, слабее связь с BTS'),
    District('bkk-ramkhamhaeng','bangkok','Рамкхамхенг',13.755,100.612,4.2,6.2,'доступно, но труднее транспорт'),
    District('pt-central','pattaya','Центральная Паттайя',12.933,100.884,2.9,6.5,'много удобств, оживлённо и шумно'),
    District('pt-north','pattaya','Северная Паттайя',12.949,100.886,2.4,7.1,'магазины и удобства'),
    District('pt-wongamat','pattaya','Вонгамат',12.951,100.872,2.1,8.4,'пляж и кондо, цены выше'),
    District('pt-naklua','pattaya','Наклуа',12.963,100.891,2.9,7.2,'спокойнее, местный колорит'),
    District('pt-pratumnak','pattaya','Пратамнак',12.917,100.866,2.2,8.6,'спокойно, пляжи, меньше транзита'),
    District('pt-jomtien','pattaya','Джомтьен',12.890,100.875,4.3,8.3,'пляж, русскоязычная инфраструктура, аренда'),
    District('pt-na-jomtien','pattaya','На Джомтьен',12.836,100.904,5.0,7.0,'тише, но больше зависимость от транспорта'),
    District('pt-east','pattaya','Восточная Паттайя',12.930,100.945,5.5,5.4,'дешевле, но без байка неудобно'),
    District('ph-rawai','phuket','Раваи',7.781,98.330,3.7,8.1,'экспатское сообщество, далеко от купабельного пляжа'),
    District('ph-naiharn','phuket','Най Харн',7.778,98.307,3.0,8.9,'пляж и спокойствие, сезонная загруженность'),
    District('ph-chalong','phuket','Чалонг',7.848,98.338,5.0,7.5,'практичный юг, далековато от пляжей'),
    District('ph-kata','phuket','Ката',7.820,98.299,2.5,8.0,'пляж, туризм и сезонные цены'),
    District('ph-karon','phuket','Карон',7.849,98.294,3.1,7.7,'пляж и сервисы, сезонные пробки'),
    District('ph-patong','phuket','Патонг',7.897,98.295,4.0,5.8,'ночная жизнь, шум и туристические цены'),
    District('ph-kamala','phuket','Камала',7.952,98.281,3.5,8.0,'спокойнее Патонга, пляж, дороже'),
    District('ph-bangtao','phuket','Банг Тао / Лагуна',8.009,98.302,6.2,8.5,'пляж, инфраструктура; дорогая аренда'),
    District('ph-surin','phuket','Сурин',7.976,98.280,2.7,7.9,'пляж, премиум, мало доступных кондо'),
    District('ph-phukettown','phuket','Пхукет Таун',7.884,98.390,5.0,6.7,'городской быт, нет пляжа рядом'),
    District('ph-kathu','phuket','Катху',7.922,98.334,4.3,7.1,'доступно и практично, до пляжа ехать'),
    District('ph-wichit','phuket','Вичит',7.856,98.378,4.0,6.9,'современные ЖК, автомобильный быт'),
    District('ph-naiyang','phuket','Най Янг / Сакху',8.091,98.303,4.2,6.6,'море и аэропорт, далеко от центра острова'),
    District('cm-nimman','chiangmai','Нимман',18.799,98.969,2.4,8.5,'кофейни, коворкинги и аэропорт'),
    District('cm-oldcity','chiangmai','Старый город',18.790,98.985,2.7,8.0,'атмосфера, доступность пешком'),
    District('cm-santitham','chiangmai','Санти Тхам',18.806,98.983,2.2,8.1,'доступная аренда и местный быт'),
    District('cm-riverside','chiangmai','Риверсайд',18.790,99.010,3.3,7.0,'спокойнее, больше нужны поездки'),
    District('cm-hangdong','chiangmai','Ханг Донг',18.748,98.950,5.2,6.7,'просторно, без транспорта неудобно'),
    District('sm-chaweng','samui','Чавенг',9.529,100.058,4.4,6.4,'развлечения, туристический шум'),
    District('sm-lamai','samui','Ламай',9.465,100.046,4.2,7.6,'пляжи и размеренный образ жизни'),
    District('sm-bophut','samui','Бопхут / Фишерман',9.562,100.031,4.3,8.0,'кафе, пешие прогулки, дороже'),
    District('sm-maenam','samui','Маенам',9.577,99.994,4.2,7.8,'долгая аренда, спокойствие'),
    District('sm-bangrak','samui','Банг Рак',9.562,100.061,3.0,7.3,'магазины и порт, удобно'),
]
AREAS_BY_ID={a.id:a for a in AREAS}

def coords(x):
    point=x.get('location') or {}
    try:
        lat=float(point.get('lat')); lng=float(point.get('lng'))
        if -90 <= lat <= 90 and -180 <= lng <= 180:return lat,lng
    except (ValueError,TypeError):pass
    return None

def city_of(x):
    loc=coords(x)
    if not loc:return None
    lat,lng=loc
    for c in CITIES.values():
        a,b,d,e=c.bounds
        if a<=lat<=b and d<=lng<=e:return c.id
    return None

def km_distance(lat,lng,x,y):
    # Great-circle distance, in km.
    dlat=radians(x-lat);dlng=radians(y-lng)
    h=sin(dlat/2)**2+cos(radians(lat))*cos(radians(x))*sin(dlng/2)**2
    return 12742*asin(min(1.,sqrt(h)))

def district_of(x):
    loc=coords(x);city=city_of(x)
    if not loc or not city:return None
    lat,lng=loc
    options=[]
    for area in AREAS:
        if area.city!=city:continue
        distance=km_distance(lat,lng,area.lat,area.lng)
        if distance<=area.radius_km:options.append((distance/area.radius_km, distance,area))
    return min(options,key=lambda t:(t[0],t[1]))[2] if options else None

def district_label(x):
    area=district_of(x)
    if area:return f'{area.name} · {area.rating:g}/10'
    city=CITIES.get(city_of(x))
    return f'{city.title} · район не определён' if city else None
