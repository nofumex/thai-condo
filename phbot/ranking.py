"""Transparent bounded rental fit score, recalculated against the current time.

Not a claim about investment return, safety, live availability, or building quality.
The unit's score falls continuously as publication age increases (even in Favorites).
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime,timezone
from math import exp,log1p,sqrt
from statistics import median
from .propertyhub import price_of
from .filters import deposit_terms
from .geography import district_of

def number(v):
    try:
        r=float(v)
        return r if r>=0 and r<1e10 else None
    except (ValueError,TypeError):return None

def age_days(x,now=None):
    now=now or datetime.now(timezone.utc)
    try:return max(0.,(now-datetime.fromisoformat(str(x.get('createdAt')).replace('Z','+00:00'))).total_seconds()/86400)
    except (TypeError,ValueError):return 365.

@dataclass(frozen=True)
class ComparableSummary:
    count:int
    median_rent:float
    median_sqm:float
    def __len__(self):return self.count

def comparables(rows:list[dict], summarize:bool=False):
    groups=defaultdict(list)
    for x in rows:
        rent=number(price_of(x));area=number((x.get('roomInformation') or {}).get('roomArea'))
        district=district_of(x)
        city_key=district.id if district else str((x.get('project') or {}).get('address') or '').strip().lower()
        room=(x.get('roomInformation') or {}).get('roomType')
        if rent and area and area>0:
            groups[(city_key,room)].append((rent,rent/area))
    if summarize:
        return {key:ComparableSummary(len(values),median(p for p,_ in values),
                       median(area_price for _,area_price in values))
                for key,values in groups.items()}
    return groups

def _clamp(v):return max(0.,min(1.,v))

def _relative(value,benchmark):
    # Symmetric, bounded on either side of the median.
    return _clamp(.5+(benchmark-value)/(max(1.,benchmark)*.8))

def score_parts(x:dict, groups:dict,now=None)->tuple[dict,float]:
    now=now or datetime.now(timezone.utc)
    price=number(price_of(x));room=x.get('roomInformation') or {}
    area=number(room.get('roomArea'));floor=number(room.get('onFloor'))
    beds=number(room.get('numberOfBed'))
    district=district_of(x)
    key=(district.id if district else str((x.get('project') or {}).get('address') or '').strip().lower(),room.get('roomType'))
    same=groups.get(key) or []
    # Price and price/m² are rewarded independently but the two components are
    # kept under 33% total to prevent tiny cheaply priced studios from dominating.
    if len(same)>=5 and price and area and area>0:
        if isinstance(same,ComparableSummary):
            median_rent,median_sqm=same.median_rent,same.median_sqm
        else:
            median_rent=median(v[0] for v in same)
            median_sqm=median(v[1] for v in same)
        relative_price=_relative(price,median_rent)
        relative_area=_relative(price/area,median_sqm)
    else:
        # Uncalibrated prices are mildly evaluated against broad 20k/600-THB baselines,
        # and low confidence is reported; never claim a "market discount".
        relative_price=_relative(price,20000) if price is not None else .5
        relative_area=_relative(price/area,600) if price and area else .5
    dep_amount,dep_months=deposit_terms(x)
    parts={
        'Цена':15*relative_price,
        'Бат за м²':18*relative_area,
        'Площадь':10*_clamp((sqrt(area or 0)-sqrt(15))/(sqrt(95)-sqrt(15))) if area else 5.,
        'Этаж':8*_clamp(log1p(floor or 0)/log1p(45)) if floor is not None else 4.,
        'Спальни':7*_clamp((beds or 0)/3) if beds is not None else 3.5,
        'Депозит':13*_clamp(1-(dep_months or 0)/4) if dep_months is not None else 6.5,
        'Свежесть':11*exp(-age_days(x,now)/21),
        'Район':11*(district.rating/10) if district else 5.5,
        'Верификация':7.0 if x.get('userVerificationStatus') is True else 2.0 if x.get('userVerificationStatus') is None else 0.,
    }
    completeness=[price is not None,area is not None,floor is not None,
                  beds is not None,dep_months is not None,district is not None,
                  x.get('createdAt') is not None,x.get('userVerificationStatus') is not None]
    confidence=.15+.55*sum(completeness)/len(completeness)+.30*min(1,len(same)/20)
    return parts,round(_clamp(confidence),2)

def score(x:dict,groups:dict,now=None)->tuple[float,float]:
    parts,confidence=score_parts(x,groups,now)
    return round(sum(parts.values()),3),confidence
