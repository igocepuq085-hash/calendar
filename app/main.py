from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import admin, calendar, health

app = FastAPI(title="KIP Calendar Service")
app.include_router(health.router)
app.include_router(calendar.router)
app.include_router(admin.router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

