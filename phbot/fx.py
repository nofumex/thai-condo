"""Optional official CBR THB/RUB rate; failure is shown as unavailable, not invented."""
import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime,timezone
from decimal import Decimal
import httpx
log=logging.getLogger(__name__)
class FX:
    def __init__(self):self.rate=None;self.as_of=None
    async def refresh(self):
        try:
            async with httpx.AsyncClient(timeout=12) as c:
                r=await c.get('https://www.cbr.ru/scripts/XML_daily.asp');r.raise_for_status()
            decoded=r.content.decode('windows-1251')
            # XML_daily.asp declares windows-1251; parse decoded Unicode.
            root=ET.fromstring(decoded[decoded.find('<ValCurs'):])
            for row in root.findall('Valute'):
                if row.findtext('CharCode')=='THB':
                    value=Decimal(row.findtext('Value').replace(',','.'))
                    nominal=Decimal(row.findtext('Nominal'))
                    self.rate=float(value/nominal);self.as_of=root.get('Date')
                    return
            log.warning('THB not present in CBR')
        except Exception as exc:log.warning('CBR conversion unavailable: %s',exc)
    async def loop(self):
        while True:
            await self.refresh()
            await asyncio.sleep(8*3600)
    def rub(self,thb):
        return round(float(thb)*self.rate) if thb is not None and self.rate else None
