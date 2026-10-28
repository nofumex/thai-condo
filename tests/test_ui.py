import sys
import types
import unittest
try:
    import aiogram.types
except ImportError:
    # Test pure markup logic in offline CI where aiogram cannot be installed.
    aiogram=types.ModuleType('aiogram')
    types_module=types.ModuleType('aiogram.types')
    class InlineKeyboardButton:
        def __init__(self,**kwargs):self.__dict__.update(kwargs)
    class InlineKeyboardMarkup:
        def __init__(self,inline_keyboard):self.inline_keyboard=inline_keyboard
    types_module.InlineKeyboardMarkup=InlineKeyboardMarkup
    types_module.InlineKeyboardButton=InlineKeyboardButton
    aiogram.types=types_module
    sys.modules['aiogram']=aiogram
    sys.modules['aiogram.types']=types_module
from phbot.render import main_kb,card_kb,card_text,filter_kb,details_text
from phbot.filters import clean,AMENITIES,FACILITIES
from test_core import listing

class UITests(unittest.TestCase):
    def test_main_seven_buttons(self):
        rows=main_kb(True).inline_keyboard
        self.assertEqual([len(x) for x in rows],[2,2,2,1])
        self.assertEqual(rows[0][0].text,'🆕 Новые')
        self.assertEqual(rows[0][1].text,'🔎 Поиск и фильтры')
        self.assertEqual(rows[-1][0].text,'🛠 Админка')
    def test_card_buttons(self):
        rows=card_kb(listing(123),0,100).inline_keyboard
        self.assertEqual([len(x) for x in rows],[3,2,2,1])
        self.assertEqual(rows[0][1].text,'1/100')
        self.assertIn('---123',rows[2][1].url)
    def test_filter_keyboard(self):
        row=filter_kb('search',clean({'amenities':['hasWasher']})).inline_keyboard
        self.assertIn('1',row[3][0].text)
    def test_html_safe_and_rent_terms(self):
        x=listing(123)
        x['project']['nameEnglish']='<b>bad</b>'
        self.assertIn('&lt;b&gt;bad&lt;/b&gt;',card_text(x))
        x['price']['forRent']['deposit']={'type':'MONTH','month':2}
        x['price']['forRent']['advancePayment']={'type':'MONTH','month':1}
        d=details_text(x,x)
        self.assertIn('36 000 ฿',d)

if __name__=='__main__':unittest.main()
