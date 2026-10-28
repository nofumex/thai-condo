"""PropertyHub-specific deterministic relative-value ranking, inspired by viet-aggregator.
Comparables use only the sampled database; confidence and sample size are displayed explicitly.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime,timezone
from math import exp,log1p
from statistics import median
from .propertyhub import price_of

def number(v):
    try:return float(v)
    except (ValueError,TypeError):return None

def age_days(x,now=None):
    now=now or datetime.now(timezone.utc)
    try:return max(0.,(now-datetime.fromisoformat(str(x.get('createdAt')).replace('Z','+00:00'))).total_seconds()/86400)
    except (TypeError,ValueError):return 365.

def comparables(rows:list[dict]):
    groups=defaultdict(list)
    for x in rows:
        price=number(price_of(x));size=number((x.get('roomInformation') or {}).get('roomArea'))
        district=str((x.get('project') or {}).get('address') or '').strip().lower()
        beds=(x.get('roomInformation') or {}).get('roomType')
        if price and size and size>0:
            groups[(district,beds)].append((price,price/size))
    return groups

def score(x:dict,groups:dict,now=None)->tuple[float,float]:
    """Relative price 38%, m² value 19%, freshness 13%, space 10%, disclosure 12%, context 8%."""
    price=number(price_of(x));area=number((x.get('roomInformation') or {}).get('roomArea'))
    project=x.get('project') or {}
    key=(str(project.get('address') or '').strip().lower(),(x.get('roomInformation') or {}).get('roomType'))
    same=groups.get(key) or []
    # Avoid falsely high valuations with fewer than five comparables.
    if len(same)>=5 and price and area and area>0:
        medrent=median(v[0] for v in same);medppm=median(v[1] for v in same)
        relative=max(-1,min(1,(medrent-price)/(medrent*.35)))
        sqm=max(-1,min(1,(medppm-price/area)/(medppm*.35)))
        value=38*(relative+1)/2+19*(sqm+1)/2
    else:value=28.5
    freshness=13*exp(-age_days(x,now)/21)
    space=10*max(0,min(1,((area or 15)-18)/35))
    info=sum(bool(v) for v in [project.get('name'),project.get('address'),price,area,
                        (x.get('roomInformation') or {}).get('numberOfBed'),x.get('coverPicture'),x.get('createdAt')])
    disclosure=12*info/7
    context=3*bool(x.get('coverPicture'))+3*bool(x.get('contactInformation'))+2*bool(x.get('userVerificationStatus'))
    confidence=round(min(1,.40*(info/7)+.40*min(1,log1p(len(same))/log1p(30))+.20*bool(price and area)),2)
    return round(max(0,min(100,value+freshness+space+disclosure+context)),1),confidence
