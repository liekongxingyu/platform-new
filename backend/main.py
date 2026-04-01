import os
import logging
import threading
import asyncio
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

# 在模块导入阶段加载 .env，避免依赖 __main__ 分支导致配置失效
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

from app.core.database import engine, Base, SessionLocal, ensure_schema_compatibility
from app.controllers import (
    admin_controller,
    device_controller,
    video_controller,
    fence_controller,
    alarm_controller,
    call_controller,
    dashboard_controller,
    auth_controller,
    project_controller,
)
from app.utils.logger import get_logger
from app.core.ws_manager import alarm_clients, set_main_event_loop
from app.services.video_service import VideoService
from app.services.jt808_service import jt808_manager

# --- 日志配置 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s'
)
logger = get_logger("Main")

# --- 生命周期管理 (Lifespan) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 【启动阶段】
    set_main_event_loop(asyncio.get_running_loop())
    logger.info("Initializing system services...")
    
    # 1. 启动 JT808 TCP 服务线程
    logger.info("Starting JT808 TCP service on port 8989...")
    jt_thread = threading.Thread(target=jt808_manager.start_server, daemon=True)
    jt_thread.start()
    
    # 2. 视频录像状态自检 (增加异常保护)
    db = SessionLocal()
    try:
        logger.info("Checking video device recording status...")
        # 即使这里报错(比如摄像头连不上)，也不会弄挂主程序
        VideoService().ensure_all_recordings(db)
        logger.info("Video recordings initialized.")
    except Exception as e:
        logger.error(f"Video Recording Check Failed: {e}. (System will continue to run)")
    finally:
        db.close()

    yield
    
    # 【关闭阶段】
    set_main_event_loop(None)
    logger.info("Shutting down services...")
    jt808_manager.running = False

# --- App 初始化 ---
Base.metadata.create_all(bind=engine)
ensure_schema_compatibility()
app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态资源
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# 路由挂载
app.include_router(admin_controller.router)
app.include_router(device_controller.router)
app.include_router(video_controller.router)
app.include_router(fence_controller.router)
app.include_router(alarm_controller.router)
app.include_router(call_controller.router)
app.include_router(dashboard_controller.router)
app.include_router(auth_controller.router)
app.include_router(project_controller.router)

@app.get("/")
def root():
    return {"status": "running", "message": "Smart Helmet Platform API"}

# --- WebSocket ---
@app.websocket("/ws/alarm")
async def alarm_ws(websocket: WebSocket):
    await websocket.accept()
    alarm_clients.append(websocket)
    try:
        while True:
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        # 服务停止时 websocket 任务被取消，属于正常退出流程
        pass
    finally:
        if websocket in alarm_clients:
            alarm_clients.remove(websocket)

# --- 启动入口 ---
if __name__ == "__main__":
    import uvicorn
    
    host = os.getenv("BACKEND_HOST", "0.0.0.0")
    port = int(os.getenv("BACKEND_PORT", 9000))
    
    try:
        uvicorn.run(app, host=host, port=port)
    except KeyboardInterrupt:
        print("\nShutdown by user.")