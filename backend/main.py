import os
import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from api.routes import router

app = FastAPI(
    title="AI QA Agent",
    description="Automated website quality analysis powered by AI",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

# Serve frontend — try multiple paths (local dev vs Docker)
_candidates = [
    os.path.join(os.path.dirname(__file__), "..", "frontend"),  # local: backend/../frontend
    "/frontend",                                                  # Docker: copied to /frontend
    os.path.join(os.path.dirname(__file__), "frontend"),        # flat layout fallback
]

for _path in _candidates:
    _path = os.path.abspath(_path)
    if os.path.isdir(_path) and os.path.exists(os.path.join(_path, "index.html")):
        try:
            app.mount("/", StaticFiles(directory=_path, html=True), name="frontend")
        except Exception:
            pass
        break


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
