"""光伏电站运维管理平台 后端服务入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8000
健康检查：GET /api/health
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import ROUTERS
from app.services.green_asset import LedgerError, service
from app.store import store

log = logging.getLogger("green_asset")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动即把存量绿证按核发月份回填（幂等：已回填月份沿用原算法并跳过）。"""
    try:
        result = service.backfill()
        log.info("绿证存量回填：%s", result["message"])
    except LedgerError as exc:
        log.warning("绿证存量回填未执行：%s", exc)
    yield


app = FastAPI(title="光伏电站运维管理平台", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in ROUTERS:
    app.include_router(module.router)


@app.get("/api/health")
def health() -> dict[str, object]:
    """健康检查：确认服务已经监听、示例数据已经就绪。"""
    return {"ok": True, "app": settings.app_name, "modules": len(store.module_names())}


@app.get("/api/overview")
def overview() -> dict[str, object]:
    """运营概览：把各业务模块的待处理量汇总成看板卡片。"""
    return store.overview()
