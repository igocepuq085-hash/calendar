from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import validate_production_settings
from app.routers import admin, calendar, health

validate_production_settings()

app = FastAPI(title="KIP Calendar Service")
app.include_router(health.router)
app.include_router(calendar.router)
app.include_router(admin.router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
