from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import List
from app.core.database import get_db
# 统一使用 video_schema 以匹配模块结构
from app.schemas.video_schema import (
    VideoCreate,
    VideoOut,
    VideoUpdate,
    CameraCreateRequest,
    PTZControlRequest,
    PresetCreateRequest,
    PresetGotoRequest,
    PTZPresetItem,
    CruiseStartRequest,
    PresetBulkDeleteRequest,
    PresetBulkDeleteResponse,
    StreamUrlResponse,
)
from app.models.video import VideoDevice
from app.services.video_service import VideoService
import cv2
import time
import threading
# --- 在现有的 import 语句下面添加 ---
from app.services.ai_manager import ai_manager
from pydantic import BaseModel
from app.services.ai_features.registry import list_rules

router = APIRouter(prefix="/video", tags=["Video Surveillance"])
service = VideoService()


def _ensure_zoom_direction(direction: str):
    if direction not in {"zoom_in", "zoom_out"}:
        raise HTTPException(status_code=400, detail="变焦方向仅支持 zoom_in 或 zoom_out")


# --- 放在 router 定义之前或之后都可以，只要在下面的接口用到它之前 ---
class AIMonitorRequest(BaseModel):
    device_id: str
    rtsp_url: str | None = None
    algo_type: str = "helmet"


class PlaybackSaveRequest(BaseModel):
    start_time: str
    end_time: str


class TempCacheTriggerRequest(BaseModel):
    force: bool = True

@router.post("/ai/start")
async def start_ai(req: AIMonitorRequest, db: Session = Depends(get_db)):
    """开启 AI 监控"""
    device_id = str(req.device_id or "").strip()
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id 不能为空")

    db_video = None
    rtsp_url = (req.rtsp_url or "").strip()
    if device_id.isdigit():
        db_video = db.query(VideoDevice).filter(VideoDevice.id == int(device_id)).first()
        if not rtsp_url and db_video:
            candidate = str(getattr(db_video, "rtsp_url", "") or getattr(db_video, "stream_url", "") or "").strip()
            if candidate.lower().startswith("rtsp://"):
                rtsp_url = candidate

    has_valid_rtsp = rtsp_url.lower().startswith("rtsp://")
    is_ezviz_cloud = bool(db_video and getattr(db_video, "device_serial", None))
    if (not has_valid_rtsp) and (not is_ezviz_cloud):
        raise HTTPException(status_code=400, detail="缺少有效 RTSP，且当前设备非萤石云设备，无法启动AI检测")

    algo_type = str(req.algo_type or "").strip() or "helmet"
    success = ai_manager.start_monitoring(device_id, rtsp_url if has_valid_rtsp else "", algo_type)
    if success:
        return {"code": 200, "message": f"AI监控已启动: {algo_type}"}
    else:
        raise HTTPException(status_code=400, detail="启动失败或已在运行")

@router.get("/ai/rules")
def get_ai_rules():
    """获取限定的 AI 规则列表，只保留客户要求的 6 类"""
    rules = list_rules()
    
    # 客户要求的 6 类算法 Key (对应 backend 注册的真实 Key)
    # 1. 安全帽 -> helmet
    # 2. 现场标识 -> signage (从 site_signage.py 确认)
    # 3. 监管人数 -> supervisor_count
    # 4. 梯子角度 -> ladder_angle
    # 5. 孔口挡坎 -> hole_curb
    # 6. 入侵管理 -> unauthorized_person (对应围栏入侵)
    
    allowed_keys = [
        "helmet",
        "signage",
        "supervisor_count",
        "ladder_angle",
        "hole_curb",
        "unauthorized_person"
    ]
    
    # 统一转换显示名称（覆盖后端定义，匹配前端要求）
    display_names = {
        "helmet": "安全帽类",
        "signage": "现场标识类",
        "supervisor_count": "现场监督人数统计",
        "ladder_angle": "梯子角度类",
        "hole_curb": "孔口挡坎违规类",
        "unauthorized_person": "围栏入侵管理类"
    }
    
    result = []
    for key in allowed_keys:
        if key in rules:
            result.append({
                "key": key,
                "desc": display_names.get(key, rules[key].desc)
            })
            
    return {
        "code": 0,
        "data": result
    }

@router.post("/add_camera", response_model=VideoOut)
def add_camera_dynamically(camera: CameraCreateRequest, db: Session = Depends(get_db)):
    """
    Dynamically adds a new camera by commanding the media server
    and then creating a record in the database.
    """
    try:
        return service.add_camera_to_media_server(db, camera)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/", response_model=List[VideoOut])
def read_videos(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """获取所有视频设备列表"""
    return service.get_videos(db, skip=skip, limit=limit)

@router.post("/", response_model=VideoOut)
def create_video(video: VideoCreate, db: Session = Depends(get_db)):
    """手动创建/添加视频设备"""
    try:
        return service.create_video(db, video)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # 添加通用异常捕获，防止任何未预料的错误（如数据库连接失败、模型字段不匹配等）导致服务器崩溃并返回HTML
        # 在生产环境中，应该使用更精细的日志记录
        print(f"An unexpected error occurred: {e}") # 临时用于调试
        raise HTTPException(status_code=500, detail="An internal server error occurred while creating the video.")

@router.put("/{video_id}", response_model=VideoOut)
def update_video(video_id: int, video: VideoUpdate, db: Session = Depends(get_db)):
    """更新视频设备信息"""
    updated_video = service.update_video(db, video_id, video)
    if not updated_video:
        raise HTTPException(status_code=404, detail="Video device not found")
    return updated_video

@router.delete("/{video_id}")
def delete_video(video_id: int, db: Session = Depends(get_db)):
    """删除视频设备"""
    success = service.delete_video(db, video_id)
    if not success:
        raise HTTPException(status_code=404, detail="Video device not found")
    return {"status": "success"}

@router.post("/sync")
def sync_devices(db: Session = Depends(get_db)):
    """从海康威视等平台同步设备列表"""
    service.sync_hikvision_devices(db)
    return {"message": "Sync started"}


@router.get("/ezviz/health")
def get_ezviz_health():
    """萤石云配置与 token 健康检查"""
    return service.get_ezviz_health()

@router.get("/stream/{video_id}", response_model=StreamUrlResponse)
def get_video_stream(video_id: int, db: Session = Depends(get_db)):
    """获取指定设备的流媒体地址"""
    try:
        info = service.get_stream_info(db, video_id)
        if not info or not info.get("url"):
            raise HTTPException(status_code=404, detail="Stream URL not found or device offline")
        return info
    except HTTPException:
        raise
    except ValueError as e:
        # 透传 service 层语义码前缀，前端可直接展示/分流处理。
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"UPSTREAM_ERROR: 获取播放地址失败: {e}")


@router.post("/{video_id}/playback/save")
def save_playback_clip(video_id: int, body: PlaybackSaveRequest):
    """保存指定时间段的回放视频"""
    try:
        return service.save_playback_clip(video_id, body.start_time, body.end_time, output_type="playback", filename_prefix="playback")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"回放保存失败: {e}")


@router.get("/{video_id}/recordings")
def list_recording_segments(video_id: int, limit: int = 72):
    """获取设备录像分段列表（默认最近72段）"""
    try:
        return service.list_recording_segments(video_id, limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取录像分段失败: {e}")


@router.post("/{video_id}/playback/temp-cache")
def save_temp_cache_clip(video_id: int, body: TempCacheTriggerRequest):
    """触发从上一个归档时间节点到当前时刻的临时回放缓存"""
    try:
        return service.save_temp_cache_until_now(video_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"临时缓存生成失败: {e}")


@router.get("/{video_id}/playback/videos")
def list_saved_playback_videos(video_id: int, limit: int = 120):
    """获取已保存的常态回放视频列表"""
    try:
        return service.list_saved_playback_videos(video_id, limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取常态回放列表失败: {e}")


@router.get("/{video_id}/playback/temp/videos")
def list_temp_cache_videos(video_id: int, limit: int = 30):
    """获取临时缓存回放视频列表"""
    try:
        return service.list_temp_cache_videos(video_id, limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取临时回放列表失败: {e}")


@router.get("/{video_id}/alarm/videos")
def list_saved_alarm_videos(video_id: int, limit: int = 120):
    """获取报警回放视频列表"""
    try:
        return service.list_saved_alarm_videos(video_id, limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取报警回放列表失败: {e}")


@router.post("/time/sync/{video_id}")
def sync_camera_time(video_id: int, force: bool = True, db: Session = Depends(get_db)):
    """手动触发摄像头时间同步（默认强制同步）"""
    result = service.sync_camera_time_if_needed(db, video_id, force=force)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message", "摄像头校时失败"))
    return result


def _mjpeg_frame_generator(rtsp_url: str):
    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        # 延时重试几次，避免瞬时失败
        for _ in range(5):
            time.sleep(0.3)
            cap.open(rtsp_url)
            if cap.isOpened():
                break
    if not cap.isOpened():
        # 生成一个空白帧作为错误提示
        img = (255 * (1 - 0)).astype('uint8') if False else None
        # 无法打开时直接结束生成器
        return

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.05)
                continue
            # 可按需缩放，减少带宽/CPU
            # frame = cv2.resize(frame, (960, 540))
            ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if not ret:
                continue
            jpg_bytes = buffer.tobytes()
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + jpg_bytes + b"\r\n")
            time.sleep(0.03)  # ~30fps 限速，防止过载
    finally:
        cap.release()


@router.get("/mjpeg/{video_id}")
def get_video_mjpeg(video_id: int, db: Session = Depends(get_db)):
    """
    提供简易 MJPEG 实时预览流（multipart/x-mixed-replace）。
    适合快速演示，但占用 CPU，生产建议接入 MediaMTX/ZLMediaKit 或 HLS/WebRTC。
    """
    url = service.get_stream_url(db, video_id)
    if not url:
        raise HTTPException(status_code=404, detail="Stream URL not found or device offline")
    return StreamingResponse(_mjpeg_frame_generator(url), media_type="multipart/x-mixed-replace; boundary=frame")

@router.post("/ptz/{video_id}")
def ptz_control(video_id: int, body: PTZControlRequest, db: Session = Depends(get_db)):
    """云台控制接口，前端发送方向和速度，然后通过 ONVIF 控制摄像头"""
    try:
        # 添加日志
        import logging
        logger_temp = logging.getLogger("ptz_control")
        logger_temp.info(f"收到PTZ请求 - video_id: {video_id}, direction: {body.direction}, direction.value: {body.direction.value}, speed: {body.speed}, duration: {body.duration}")
        
        service.ptz_move(db, video_id, body.direction.value, body.speed or 0.5, body.duration or 0.5)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PTZ 控制失败: {e}")

@router.post("/ptz/{video_id}/start")
def ptz_start(video_id: int, body: PTZControlRequest, db: Session = Depends(get_db)):
    """云台持续移动（按下开始），前端按键按下时调用"""
    try:
        service.ptz_start_move(db, video_id, body.direction.value, body.speed or 0.5)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PTZ 启动失败: {e}")


@router.post("/ptz/{video_id}/stop")
def ptz_stop(video_id: int, db: Session = Depends(get_db)):
    """云台停止移动（松开停止），前端按键松开时调用"""
    try:
        service.ptz_stop_move(db, video_id)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PTZ 停止失败: {e}")


@router.post("/zoom/{video_id}")
def zoom_control(video_id: int, body: PTZControlRequest, db: Session = Depends(get_db)):
    """变焦单次控制接口"""
    try:
        direction = body.direction.value
        _ensure_zoom_direction(direction)
        service.zoom_move(db, video_id, direction, body.speed or 0.5, body.duration or 0.5)
        return {"status": "ok"}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"变焦控制失败: {e}")


@router.post("/zoom/{video_id}/start")
def zoom_start(video_id: int, body: PTZControlRequest, db: Session = Depends(get_db)):
    """变焦持续控制开始（按下开始）"""
    try:
        direction = body.direction.value
        _ensure_zoom_direction(direction)
        service.zoom_start_move(db, video_id, direction, body.speed or 0.5)
        return {"status": "ok"}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"变焦启动失败: {e}")


@router.post("/zoom/{video_id}/stop")
def zoom_stop(video_id: int, db: Session = Depends(get_db)):
    """变焦持续控制停止（松开停止）"""
    try:
        service.zoom_stop_move(db, video_id)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"变焦停止失败: {e}")


@router.get("/ptz/{video_id}/presets", response_model=list[PTZPresetItem])
def get_presets(video_id: int, db: Session = Depends(get_db)):
    """获取摄像头预置点列表"""
    try:
        return service.list_presets(db, video_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取预置点失败: {e}")


@router.post("/ptz/{video_id}/presets", response_model=PTZPresetItem)
def create_preset(video_id: int, body: PresetCreateRequest, db: Session = Depends(get_db)):
    """保存当前云台位置为预置点"""
    try:
        return service.set_preset(db, video_id, body.name, body.token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建预置点失败: {e}")


@router.post("/ptz/{video_id}/presets/{preset_token}/goto")
def goto_preset(video_id: int, preset_token: str, body: PresetGotoRequest, db: Session = Depends(get_db)):
    """跳转到指定预置点"""
    try:
        return service.goto_preset(db, video_id, preset_token, body.speed or 0.5)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"预置点跳转失败: {e}")


@router.delete("/ptz/{video_id}/presets/{preset_token}")
def delete_preset(video_id: int, preset_token: str, db: Session = Depends(get_db)):
    """删除预置点"""
    try:
        return service.remove_preset(db, video_id, preset_token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除预置点失败: {e}")


@router.post("/ptz/{video_id}/presets/bulk-delete", response_model=PresetBulkDeleteResponse)
def bulk_delete_presets(video_id: int, body: PresetBulkDeleteRequest, db: Session = Depends(get_db)):
    """批量删除预置点（减少前端逐条 DELETE 导致的 CORS 预检刷屏）"""
    try:
        return service.remove_presets_bulk(db, video_id, body.preset_tokens)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量删除预置点失败: {e}")


@router.post("/ptz/{video_id}/cruise/start")
def start_cruise(video_id: int, body: CruiseStartRequest, db: Session = Depends(get_db)):
    """启动常规巡航（按预置点列表轮巡）"""
    try:
        return service.start_cruise(db, video_id, body.preset_tokens, body.dwell_seconds or 8.0, body.rounds)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动巡航失败: {e}")


@router.post("/ptz/{video_id}/cruise/stop")
def stop_cruise(video_id: int):
    """停止常规巡航"""
    try:
        return service.stop_cruise(video_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"停止巡航失败: {e}")


@router.get("/ptz/{video_id}/cruise/status")
def cruise_status(video_id: int):
    """获取巡航状态"""
    try:
        return service.get_cruise_status(video_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取巡航状态失败: {e}")


@router.post("/ai/stop")
async def stop_ai(device_id: str):
    """停止 AI 监控"""
    success = ai_manager.stop_monitoring(device_id)
    if success:
        return {"code": 200, "message": "AI监控已停止"}
    else:
        # 幂等语义：未运行也返回成功，便于前端先 stop 再 start。
        return {"code": 200, "message": "AI监控未运行，已跳过停止"}