from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parent.parent
load_dotenv(ROOT/'.env')
@dataclass(frozen=True)
class Config:
    token:str
    admin_id:int
    db_path:str
    @classmethod
    def from_env(cls):
        token=os.getenv('TG_BOT_TOKEN','').strip()
        admin=os.getenv('ADMIN_ID','').strip()
        if not token or not admin.isdigit():
            raise SystemExit('Заполни TG_BOT_TOKEN и ADMIN_ID в .env')
        return cls(token,int(admin),str(ROOT/'data'/'propertyhub.sqlite3'))
