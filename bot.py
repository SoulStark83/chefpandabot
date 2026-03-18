# -*- coding: utf-8 -*-
import logging

from telegram.ext import ApplicationBuilder

from config.settings import BOT_TOKEN
from handlers.commands import register_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("chefpanda-admin")


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    register_handlers(app)
    logger.info("ChefPanda Admin Bot arrancado...")
    app.run_polling()


if __name__ == "__main__":
    main()
