import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.routers import analysis, scanner, earnings, tracker, macro, telegram
from backend.routers import scheduler as scheduler_router
from backend.services.scheduler import scheduler, setup_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_scheduler()
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="StockPulse API", version="1.0.0", lifespan=lifespan)

_cors_env = os.getenv("CORS_ORIGINS", "")
_cors_origins = (
    [o.strip() for o in _cors_env.split(",") if o.strip()]
    if _cors_env
    else ["http://localhost:3000", "http://localhost:3001",
          "http://localhost:3002", "http://localhost:3003"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(analysis.router)
app.include_router(scanner.router)
app.include_router(earnings.router)
app.include_router(tracker.router)
app.include_router(scheduler_router.router)
app.include_router(macro.router)
app.include_router(telegram.router)


@app.get("/health")
def health():
    from backend.services.scheduler import scheduler as sched
    from backend.db import gex_store
    return {
        "status":          "ok",
        "scheduler":       sched.running,
        "polling_active":  sched.get_job("eps_poll") is not None,
        "spy_gamma_job":   sched.get_job("spy_gamma") is not None,
        "gex_store":       gex_store.backend_name(),
    }
