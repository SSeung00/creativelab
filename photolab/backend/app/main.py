from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from sqlmodel import Session, col, select

from app.auth import RemoteAuthMiddleware
from app.config import FRONTEND_DIST, FRONTEND_ORIGINS
from app.db import engine, init_db
from app.models import Job
from app.routers import ai, develop, export, library, remote, system


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    # Jobs that were running when the server stopped will never finish.
    with Session(engine) as s:
        for j in s.exec(select(Job).where(col(Job.status).in_(["queued", "running"]))):
            j.status = "error"
            j.message = "서버가 다시 시작되어 중단됐어요."
            s.add(j)
        s.commit()
    yield


app = FastAPI(title="PhotoLab API", version="0.1.0", lifespan=lifespan)

app.add_middleware(RemoteAuthMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Info", "X-Hist"],
)

for module in (system, library, develop, export, ai, remote):
    app.include_router(module.router, prefix="/api")


# ---------- built web app (frontend/dist), so one port serves PC and phone ----------

NO_CACHE = {"Cache-Control": "no-cache"}


@app.get("/{path:path}", include_in_schema=False)
def web_app(path: str) -> FileResponse:
    if path == "api" or path.startswith("api/"):
        raise HTTPException(404, "Not Found")
    index = FRONTEND_DIST / "index.html"
    if not index.exists():
        raise HTTPException(404, "웹 화면이 아직 빌드되지 않았어요. frontend에서 npm run build를 실행하세요.")
    file = (FRONTEND_DIST / path).resolve()
    if path and file.is_file() and file.is_relative_to(FRONTEND_DIST):
        immutable = path.startswith("assets/")
        headers = {"Cache-Control": "public, max-age=31536000, immutable"} if immutable else NO_CACHE
        return FileResponse(file, headers=headers)
    return FileResponse(index, headers=NO_CACHE)
