import asyncio
import logging
from phbot.config import Config
from phbot.bot import Application

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    try:asyncio.run(Application(Config.from_env()).run())
    except KeyboardInterrupt:pass
