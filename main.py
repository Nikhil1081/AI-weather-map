from firebase_functions import https_fn
from app.main import app as fastapi_app

# Set __name__ so the firebase-functions wrapper can extract the function name correctly
fastapi_app.__name__ = "weather_api"

# Expose the FastAPI app as a Firebase Cloud Function
weather_api = https_fn.on_request()(fastapi_app)
