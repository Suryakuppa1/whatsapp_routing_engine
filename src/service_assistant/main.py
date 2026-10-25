import logging

from service_assistant.api.app import create_app

logging.basicConfig(level=logging.INFO, format="%(message)s")
app = create_app()
