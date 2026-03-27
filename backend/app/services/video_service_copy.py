import json
from sqlalchemy.orm import Session
from urllib.parse import urlparse
from app.models.video import VideoDevice
from app.models.device import Device
from app.schemas.video_schema import VideoCreate, VideoUpdate, CameraCreateRequest
from app.utils.logger import get_logger
import requests
import os
import glob
import time
import threading
import subprocess
import signal
from datetime import datetime, timezone, timedelta
import logging
import sys
import hashlib
import base64
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any

RECORDING_PROCESSES = {}

# [日志压制]
def suppress_verbose_logging():
    for logger_name in ["zeep", "urllib3", "onvif", "wsdl", "requests"]:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.CRITICAL)
        logger.propagate = False

suppress_verbose_logging()

from app.core.database import SessionLocal

try:
    import onvif
    from onvif import ONVIFCamera
except Exception:
    onvif = None
    ONVIFCamera = None

logger = get_logger("VideoService")

# --- 配置部分 ---
NMS_HOST = "http://127.0.0.1:8001"
NMS_USER = "admin"
NMS_PASS = "123456" 
NMS_MEDIA_ROOT = os.path.abspath(os.getenv("NMS_MEDIA_ROOT", r"C:\media"))

# --- 全局缓存 ---
ONVIF_CLIENT_CACHE = {}

CAMERA_TIME_DRIFT_THRESHOLD_SECONDS = int(os.getenv("CAMERA_TIME_DRIFT_THRESHOLD_SECONDS", "120"))
CAMERA_TIME_SYNC_COOLDOWN_SECONDS = int(os.getenv("CAMERA_TIME_SYNC_COOLDOWN_SECONDS", "1800"))
CAMERA_TIMEZONE_TZ = os.getenv("CAMERA_TIMEZONE_TZ", "CST-8:00:00")
CAMERA_TIME_SYNC_CACHE: Dict[int, float] = {}

# [新增] 全局字典：用于存储正在运行的 FFmpeg 进程 {stream_name: process_object}
FFMPEG_PROCESSES = {}
CRUISE_TASKS = {}

class VideoService:
    # -------------------------------------------------------------------------
    # 核心 1: 获取连接
    # -------------------------------------------------------------------------
    def _get_onvif_service(self, db_video):
        global ONVIF_CLIENT_CACHE
        if not ONVIFCamera: raise ImportError("ONVIF library missing")

        if db_video.id in ONVIF_CLIENT_CACHE:
            try:
                cam = ONVIF_CLIENT_CACHE[db_video.id]
                return cam, cam.create_ptz_service(), cam.create_media_service()
            except Exception:
                if db_video.id in ONVIF_CLIENT_CACHE: del ONVIF_CLIENT_CACHE[db_video.id]

        logger.info(f"Connecting to {db_video.ip_address}...")
        
        try:
            base_dir = os.path.dirname(os.path.dirname(__file__))
            root_dir = os.path.dirname(base_dir)
            possible_paths = [
                os.path.join(root_dir, 'wsdl'),
                os.path.join(base_dir, 'wsdl'),
                os.path.join(os.getcwd(), 'wsdl')
            ]
            
            wsdl_path = None
            for p in possible_paths:
                if os.path.exists(p) and os.path.isdir(p):
                    wsdl_path = p
                    logger.info(f"Loaded local WSDL from: {p}")
                    break
            
            kwargs = {'no_cache': False}
            if wsdl_path:
                kwargs['wsdl_dir'] = wsdl_path

            camera = ONVIFCamera(
                db_video.ip_address, db_video.port or 80, 
                db_video.username, db_video.password, 
                **kwargs
            )
            
            ONVIF_CLIENT_CACHE[db_video.id] = camera
            return camera, camera.create_ptz_service(), camera.create_media_service()
            
        except Exception as e:
            logger.error(f"Connection Failed: {e}")
            raise ValueError(f"连接失败: {e}")

    def _extract_onvif_datetime(self, value: Any, default_tz=timezone.utc) -> Optional[datetime]:
        if not value:
            return None

        if isinstance(value, dict):
            date_part = value.get("Date")
            time_part = value.get("Time")
        else:
            date_part = getattr(value, "Date", None)
            time_part = getattr(value, "Time", None)

        if not date_part or not time_part:
            return None

        try:
            year = int(date_part.get("Year") if isinstance(date_part, dict) else getattr(date_part, "Year"))
            month = int(date_part.get("Month") if isinstance(date_part, dict) else getattr(date_part, "Month"))
            day = int(date_part.get("Day") if isinstance(date_part, dict) else getattr(date_part, "Day"))
            hour = int(time_part.get("Hour") if isinstance(time_part, dict) else getattr(time_part, "Hour"))
            minute = int(time_part.get("Minute") if isinstance(time_part, dict) else getattr(time_part, "Minute"))
            second = int(time_part.get("Second") if isinstance(time_part, dict) else getattr(time_part, "Second"))
            return datetime(year, month, day, hour, minute, second, tzinfo=default_tz)
        except Exception:
            return None

    def _sync_camera_time_for_video(self, db_video: VideoDevice, force: bool = False) -> dict:
        if not db_video:
            return {"status": "error", "message": "设备不存在"}

        if not db_video.ip_address or not db_video.username or not db_video.password:
            return {"status": "skipped", "message": "设备缺少 ONVIF 连接参数"}

        now_ts = time.time()
        last_sync_ts = CAMERA_TIME_SYNC_CACHE.get(db_video.id)
        if (not force) and last_sync_ts and (now_ts - last_sync_ts < CAMERA_TIME_SYNC_COOLDOWN_SECONDS):
            return {
                "status": "skipped",
                "message": "校时冷却中",
                "next_sync_in_seconds": int(CAMERA_TIME_SYNC_COOLDOWN_SECONDS - (now_ts - last_sync_ts)),
            }

        try:
            camera, _, _ = self._get_onvif_service(db_video)
            devicemgmt = camera.create_devicemgmt_service()
            date_time_info = devicemgmt.GetSystemDateAndTime()
            system_date_time = getattr(date_time_info, "SystemDateAndTime", date_time_info)

            utc_dt = getattr(system_date_time, "UTCDateTime", None)
            local_dt = getattr(system_date_time, "LocalDateTime", None)

            camera_time_utc = self._extract_onvif_datetime(utc_dt, timezone.utc)
            if not camera_time_utc:
                local_time = self._extract_onvif_datetime(local_dt, datetime.now().astimezone().tzinfo or timezone.utc)
                if local_time:
                    camera_time_utc = local_time.astimezone(timezone.utc)

            now_utc = datetime.now(timezone.utc)
            drift_seconds = None
            if camera_time_utc:
                drift_seconds = abs((now_utc - camera_time_utc).total_seconds())

            if (not force) and drift_seconds is not None and drift_seconds < CAMERA_TIME_DRIFT_THRESHOLD_SECONDS:
                CAMERA_TIME_SYNC_CACHE[db_video.id] = now_ts
                return {
                    "status": "skipped",
                    "message": "摄像头时间在允许误差内",
                    "drift_seconds": int(drift_seconds),
                }

            req = devicemgmt.create_type("SetSystemDateAndTime")
            req.DateTimeType = "Manual"
            req.DaylightSavings = False
            req.TimeZone = {"TZ": CAMERA_TIMEZONE_TZ}
            req.UTCDateTime = {
                "Time": {
                    "Hour": now_utc.hour,
                    "Minute": now_utc.minute,
                    "Second": now_utc.second,
                },
                "Date": {
                    "Year": now_utc.year,
                    "Month": now_utc.month,
                    "Day": now_utc.day,
                },
            }
            devicemgmt.SetSystemDateAndTime(req)

            CAMERA_TIME_SYNC_CACHE[db_video.id] = now_ts
            logger.info(
                "Camera time synced for video_id=%s drift=%s seconds",
                db_video.id,
                int(drift_seconds) if drift_seconds is not None else "unknown",
            )
            return {
                "status": "success",
                "message": "摄像头时间已同步",
                "drift_seconds_before_sync": int(drift_seconds) if drift_seconds is not None else None,
            }
        except Exception as e:
            logger.warning(f"Camera time sync skipped for video_id={db_video.id}: {e}")
            return {"status": "error", "message": f"摄像头校时失败: {e}"}

    def sync_camera_time_if_needed(self, db: Session, video_id: int, force: bool = False) -> dict:
        db_video = db.query(VideoDevice).filter(VideoDevice.id == video_id).first()
        if not db_video:
            return {"status": "error", "message": "设备不存在"}
        return self._sync_camera_time_for_video(db_video, force=force)

    # -------------------------------------------------------------------------
    # 辅助: 生成 WS-Security Header (模拟 ODM 认证)
    # -------------------------------------------------------------------------
    def _generate_wsse_header(self, username, password):
        nonce_raw = os.urandom(16)
        nonce_b64 = base64.b64encode(nonce_raw).decode('utf-8')
        created = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
        
        sha1 = hashlib.sha1()
        sha1.update(nonce_raw)
        sha1.update(created.encode('utf-8'))
        sha1.update(password.encode('utf-8'))
        digest = base64.b64encode(sha1.digest()).decode('utf-8')
        
        return f"""<s:Header>
    <Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
        <UsernameToken>
            <Username>{username}</Username>
            <Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</Password>
            <Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</Nonce>
            <Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">{created}</Created>
        </UsernameToken>
    </Security>
</s:Header>"""

    # -------------------------------------------------------------------------
    # 核心 2: 原始 SOAP 停止 (ODMFix)
    # -------------------------------------------------------------------------
    def _send_raw_soap_stop(self, camera, ptz_service, profile_token, username, password):
        ptz_url = None
        if hasattr(ptz_service, 'binding') and hasattr(ptz_service.binding, 'options'):
            ptz_url = ptz_service.binding.options.get('address')
        if not ptz_url:
            ptz_url = camera.xaddrs.get('http://www.onvif.org/ver20/ptz/wsdl')
        
        if not ptz_url:
            logger.error("No PTZ URL found")
            return False

        security_header = self._generate_wsse_header(username, password)

        payloads = [
            # 方案 0: Wireshark 抓包复刻
            f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  {security_header}
  <s:Body xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
    <Stop xmlns="http://www.onvif.org/ver20/ptz/wsdl">
      <ProfileToken>{profile_token}</ProfileToken>
      <PanTilt>true</PanTilt>
            <Zoom>true</Zoom>
    </Stop>
  </s:Body>
</s:Envelope>""",
            # 方案 A: 备用
            f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
  {security_header}
  <s:Body>
    <tptz:Stop>
      <tptz:ProfileToken>{profile_token}</tptz:ProfileToken>
      <tptz:PanTilt>true</tptz:PanTilt>
      <tptz:Zoom>true</tptz:Zoom>
    </tptz:Stop>
  </s:Body>
</s:Envelope>""",
            # 方案 B: 备用
            f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
  {security_header}
  <s:Body>
    <tptz:Stop>
      <tptz:ProfileToken>{profile_token}</tptz:ProfileToken>
      <tptz:PanTilt>1</tptz:PanTilt>
      <tptz:Zoom>1</tptz:Zoom>
    </tptz:Stop>
  </s:Body>
</s:Envelope>"""
        ]

        headers = {
            'Content-Type': 'application/soap+xml; charset=utf-8; action="http://www.onvif.org/ver20/ptz/wsdl/Stop"'
        }

        for i, payload in enumerate(payloads):
            try:
                response = requests.post(ptz_url, data=payload, headers=headers, timeout=2)
                if 200 <= response.status_code < 300:
                    logger.info(f"Raw SOAP Variant {i} (Capture Match) SUCCESS")
                    return True
                else:
                    logger.warning(f"Raw SOAP Variant {i} Failed: {response.status_code}")
            except Exception as e:
                logger.error(f"Raw SOAP Variant {i} Error: {e}")
        return False

    def ptz_stop_move(self, db: Session, video_id: int):
        db_video = db.query(VideoDevice).filter(VideoDevice.id == video_id).first()
        if not db_video: raise ValueError("Device not found")

        try:
            camera, ptz, media = self._get_onvif_service(db_video)
            token = self._get_profile_token(media)
            
            logger.info(f"STOPPING {db_video.name} using ODM Raw Mode...")

            if self._send_raw_soap_stop(camera, ptz, token, db_video.username, db_video.password):
                return {"status": "success", "message": "Stopped (ODM Mode)"}
            
            # 兜底
            try:
                space_uri = "http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace"
                stop_req = {
                    'ProfileToken': token, 
                    'Velocity': {'PanTilt': {'x': 0.0, 'y': 0.0, 'space': space_uri}}
                }
                ptz.ContinuousMove(stop_req)
                ptz.ContinuousMove(stop_req)
                logger.info("Stopped via ZeroVel Fallback")
                return {"status": "success", "message": "Stopped (ZeroVel)"}
            except Exception as e:
                logger.warning(f"ZeroVel Failed: {e}")

            if video_id in ONVIF_CLIENT_CACHE: del ONVIF_CLIENT_CACHE[video_id]
            raise ValueError("所有停止方法均失败")

        except Exception as e:
            if video_id in ONVIF_CLIENT_CACHE: del ONVIF_CLIENT_CACHE[video_id]
            logger.error(f"Stop Fatal Error: {e}")
            raise ValueError(f"停止失败: {e}")

    def ptz_start_move(self, db: Session, video_id: int, direction: str, speed: float = 0.5):
        db_video = db.query(VideoDevice).filter(VideoDevice.id == video_id).first()
        if not db_video: raise ValueError("Device not found")

        try:
            camera, ptz, media = self._get_onvif_service(db_video)
            token = self._get_profile_token(media)

            pan = speed if direction == 'right' else (-speed if direction == 'left' else 0.0)
            tilt = speed if direction == 'up' else (-speed if direction == 'down' else 0.0)
            zoom = speed if direction == 'zoom_in' else (-speed if direction == 'zoom_out' else 0.0)

            request = {
                'ProfileToken': token,
                'Velocity': {
                    'PanTilt': {'x': pan, 'y': tilt},
                    'Zoom': {'x': zoom}
                },
                'Timeout': 'PT5S' 
            }
            ptz.ContinuousMove(request)
            return {"status": "success"}
        except Exception as e:
            if video_id in ONVIF_CLIENT_CACHE: del ONVIF_CLIENT_CACHE[video_id]
            raise ValueError(f"Start failed: {e}")

    def _get_profile_token(self, media_service):
        profiles = media_service.GetProfiles()
        if not profiles: raise Exception("No profiles")
        return profiles[0].token

    def _get_direction_name(self, direction: str) -> str:
        return {
            'up': '上',
            'down': '下',
            'left': '左',
            'right': '右',
            'zoom_in': '放大',
            'zoom_out': '缩小',
        }.get(direction, direction)

    def _extract_ip_from_rtsp(self, rtsp_url: str) -> Optional[str]:
        try:
            parsed = urlparse(rtsp_url)
            return parsed.hostname
        except Exception:
            return None

    def _create_ptz_and_media(self, db: Session, video_id: int):
        db_video = db.query(VideoDevice).filter(VideoDevice.id == video_id).first()
        if not db_video:
            raise ValueError("Device not found")
        camera, ptz, media = self._get_onvif_service(db_video)
        token = self._get_profile_token(media)
        return db_video, camera, ptz, media, token

    def list_presets(self, db: Session, video_id: int):
        try:
            _, _, ptz, _, token = self._create_ptz_and_media(db, video_id)
            presets = ptz.GetPresets({'ProfileToken': token})
        except Exception as e:
            # 某些摄像头不支持预置点，或当前连接暂时不可用；此处降级为空列表，避免前端持续出现 400。
            logger.warning(f"GetPresets failed for video_id={video_id}: {e}")
            return []

        result = []
        for p in presets or []:
            result.append({
                "token": str(getattr(p, 'token', '') or ''),
                "name": str(getattr(p, 'Name', None) or getattr(p, 'name', None) or f"Preset-{getattr(p, 'token', '')}")
            })
        return result

    def set_preset(self, db: Session, video_id: int, name: Optional[str] = None, preset_token: Optional[str] = None):
        _, _, ptz, _, token = self._create_ptz_and_media(db, video_id)
        req = {'ProfileToken': token}
        if name:
            req['PresetName'] = name
        if preset_token:
            req['PresetToken'] = preset_token

        try:
            created_token = ptz.SetPreset(req)
            return {
                "token": str(created_token or preset_token or ''),
                "name": name or f"Preset-{created_token}"
            }
        except Exception as e:
            raise ValueError(f"创建预置点失败: {e}")

    def goto_preset(self, db: Session, video_id: int, preset_token: str, speed: float = 0.5):
        _, _, ptz, _, token = self._create_ptz_and_media(db, video_id)
        req = {
            'ProfileToken': token,
            'PresetToken': preset_token,
            'Speed': {
                'PanTilt': {'x': speed, 'y': speed},
                'Zoom': {'x': speed}
            }
        }
        try:
            ptz.GotoPreset(req)
            return {"status": "success"}
        except Exception as e:
            raise ValueError(f"调用预置点失败: {e}")

    def remove_preset(self, db: Session, video_id: int, preset_token: str):
        _, _, ptz, _, token = self._create_ptz_and_media(db, video_id)
        req = {'ProfileToken': token, 'PresetToken': preset_token}
        try:
            ptz.RemovePreset(req)
            return {"status": "success"}
        except Exception as e:
            raise ValueError(f"删除预置点失败: {e}")

    def _cruise_worker(self, db_factory, video_id: int, preset_tokens: list[str], dwell_seconds: float, rounds: Optional[int], stop_event: threading.Event):
        db = db_factory()
        completed_rounds = 0
        try:
            while not stop_event.is_set():
                for preset in preset_tokens:
                    if stop_event.is_set():
                        return
                    try:
                        self.goto_preset(db, video_id, preset)
                    except Exception as e:
                        logger.warning(f"巡航跳转失败 video_id={video_id}, preset={preset}: {e}")

                    if stop_event.wait(timeout=dwell_seconds):
                        return

                completed_rounds += 1
                if rounds is not None and completed_rounds >= rounds:
                    return
        finally:
            db.close()
            task = CRUISE_TASKS.get(video_id)
            if task and task.get("stop") is stop_event:
                CRUISE_TASKS.pop(video_id, None)

    def start_cruise(self, db: Session, video_id: int, preset_tokens: list[str], dwell_seconds: float = 8.0, rounds: Optional[int] = None):
        if len(preset_tokens) < 2:
            raise ValueError("巡航至少需要两个预置点")

        available = {item["token"] for item in self.list_presets(db, video_id)}
        missing = [token for token in preset_tokens if token not in available]
        if missing:
            raise ValueError(f"以下预置点不存在: {', '.join(missing)}")

        self.stop_cruise(video_id)

        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._cruise_worker,
            args=(SessionLocal, video_id, preset_tokens, dwell_seconds, rounds, stop_event),
            daemon=True
        )
        CRUISE_TASKS[video_id] = {
            "thread": thread,
            "stop": stop_event,
            "presets": list(preset_tokens),
            "dwell_seconds": dwell_seconds,
            "rounds": rounds,
        }
        thread.start()
        return {"status": "success"}

    def stop_cruise(self, video_id: int):
        task = CRUISE_TASKS.get(video_id)
        if not task:
            return {"status": "idle"}

        stop_event = task.get("stop")
        if stop_event:
            stop_event.set()
        CRUISE_TASKS.pop(video_id, None)
        return {"status": "success"}

    def get_cruise_status(self, video_id: int):
        task = CRUISE_TASKS.get(video_id)
        if not task:
            return {"running": False}

        thread = task.get("thread")
        running = bool(thread and thread.is_alive())
        return {
            "running": running,
            "preset_tokens": task.get("presets", []),
            "dwell_seconds": task.get("dwell_seconds", 8.0),
            "rounds": task.get("rounds"),
        }

    # -------------------------------------------------------------------------
    # 核心业务: 添加/删除/更新
    # -------------------------------------------------------------------------
    def add_camera_to_media_server(self, db: Session, camera_data: CameraCreateRequest):
        logger.info(f"Adding stream: {camera_data.name}")
        ip_address = camera_data.ip_address or self._extract_ip_from_rtsp(camera_data.rtsp_url) or ""
        port = camera_data.port or 80

        # 先落库拿到稳定ID，再用 ID 作为 stream_name，避免名称改动导致流路径漂移。
        new_video = VideoDevice(
            name=camera_data.name,
            ip_address=ip_address,
            port=port,
            username=camera_data.username,
            password=camera_data.password,
            stream_url="",
            rtsp_url=camera_data.rtsp_url,
            latitude=camera_data.latitude,
            longitude=camera_data.longitude,
            status="online",
            remark=camera_data.remark,
        )
        db.add(new_video)
        db.commit()
        db.refresh(new_video)

        # 新增或替换设备后先尝试同步摄像头时间，避免 OSD 时间持续漂移。
        sync_result = self._sync_camera_time_for_video(new_video, force=True)
        if sync_result.get("status") == "error":
            logger.warning(f"Initial camera time sync failed for video_id={new_video.id}: {sync_result.get('message')}")

        stream_name = str(new_video.id)

        # 启动推流并更新播放地址
        self.start_ffmpeg_stream(camera_data.rtsp_url, stream_name)
        flv_url = f"{NMS_HOST}/live/{stream_name}.flv"
        new_video.stream_url = flv_url
        db.commit()
        db.refresh(new_video)
        
        self.start_ffmpeg_recording(new_video.id, camera_data.rtsp_url)
        return new_video

    def sync_hikvision_devices(self, db: Session):
        # 当前项目以 RTSP/ONVIF 手动接入为主，保留同步接口避免路由调用时报错。
        logger.info("sync_hikvision_devices called - manual RTSP/ONVIF flow is used")
        return []

    def create_video(self, db: Session, video_data: VideoCreate):
        new_video = VideoDevice(**video_data.model_dump())
        db.add(new_video)
        db.commit()
        db.refresh(new_video)
        return new_video
    
    def get_videos(self, db: Session, skip: int = 0, limit: int = 100):
        return db.query(VideoDevice).offset(skip).limit(limit).all()

    def update_video(self, db: Session, video_id: int, video_data: VideoUpdate):
        db_video = db.query(VideoDevice).filter(VideoDevice.id == video_id).first()
        if not db_video: return None
        for key, value in video_data.model_dump(exclude_unset=True).items():
            setattr(db_video, key, value)

        if db_video.rtsp_url and (not db_video.stream_url or "/live/" not in str(db_video.stream_url)):
            db_video.stream_url = f"{NMS_HOST}/live/{video_id}.flv"

        db.commit()
        db.refresh(db_video)
        if video_id in ONVIF_CLIENT_CACHE: del ONVIF_CLIENT_CACHE[video_id]
        if video_id in CAMERA_TIME_SYNC_CACHE: del CAMERA_TIME_SYNC_CACHE[video_id]
        return db_video

    def delete_video(self, db: Session, video_id: int):
        db_video = db.query(VideoDevice).filter(VideoDevice.id == video_id).first()
        if db_video:
            stream_name = str(db_video.id)
            self.stop_ffmpeg_stream(stream_name)
            self.stop_ffmpeg_recording(video_id)

            db.delete(db_video)
            db.commit()
            if video_id in ONVIF_CLIENT_CACHE:
                del ONVIF_CLIENT_CACHE[video_id]
            return True
        return False

    def get_stream_url(self, db: Session, video_id: int):
        v = db.query(VideoDevice).filter(VideoDevice.id == video_id).first()
        if not v:
            return None

        # 拉流前执行按需校时：超过阈值才改，且有冷却时间避免频繁写设备。
        sync_result = self._sync_camera_time_for_video(v, force=False)
        if sync_result.get("status") == "error":
            logger.warning(f"Auto time sync failed for video_id={video_id}: {sync_result.get('message')}")

        # 懒启动推流：当前端请求播放地址时，如推流进程不存在则自动拉起。
        stream_name = str(v.id)
        entry = FFMPEG_PROCESSES.get(stream_name)
        is_running = False
        if entry is not None:
            try:
                is_running = entry.poll() is None
            except Exception:
                is_running = False

        if not is_running:
            rtsp_url = self._get_rtsp_url_for_device(v)
            if rtsp_url:
                self.start_ffmpeg_stream(rtsp_url, stream_name)

        return v.stream_url
        
    def ptz_move(self, db: Session, video_id: int, direction: str, speed: float = 0.5, duration: float = 0.5):
        try:
            self.ptz_start_move(db, video_id, direction, speed)
            time.sleep(duration)
            self.ptz_stop_move(db, video_id)
            return {"status": "success"}
        except Exception as e:
            raise ValueError(f"Move error: {e}")

    # -------------------------------------------------------------------------
    # [新功能] V4 极速推流 + 进程管理
    # -------------------------------------------------------------------------
    def start_ffmpeg_stream(self, rtsp_url: str, stream_name: str):
        """
        启动 FFmpeg 推流 (隐藏窗口 + 全局管理)
        """
        # 如果已经存在同名推流，先停止旧的
        self.stop_ffmpeg_stream(stream_name)

        ffmpeg_path = self._get_ffmpeg_path()
        rtmp_url = f"rtmp://127.0.0.1:19350/live/{stream_name}"
        
        # V4 完美配置
        command = [
            ffmpeg_path, "-y",
            "-f", "rtsp", "-rtsp_transport", "tcp",
            "-user_agent", "LIVE555 Streaming Media v2013.02.11",
            "-fflags", "nobuffer", "-flags", "low_delay",
            "-strict", "experimental",
            "-analyzeduration", "100000", "-probesize", "100000",
            "-i", rtsp_url,
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-b:v", "4000k", "-maxrate", "6000k", "-bufsize", "1000k",
            "-pix_fmt", "yuv420p", "-g", "15",
            "-c:a", "aac", "-b:a", "64k", "-ar", "16000",
            "-flvflags", "no_duration_filesize",
            "-f", "flv", rtmp_url
        ]

        logger.info(f"Starting FFmpeg Stream for {stream_name}...")
        
        try:
            # [修改关键点] 隐藏 CMD 窗口
            startupinfo = None
            creationflags = 0
            
            if os.name == 'nt':
                # Windows 下使用 CREATE_NO_WINDOW (0x08000000) 彻底隐藏
                creationflags = 0x08000000 

            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL,
                creationflags=creationflags
            )
            
            # [新增] 存入全局字典
            FFMPEG_PROCESSES[stream_name] = process
            logger.info(f"Stream {stream_name} started (PID: {process.pid})")
            
            return process
        except Exception as e:
            logger.error(f"FFmpeg start failed: {e}")
            return None

    def stop_ffmpeg_stream(self, stream_name: str):
        """
        [新增] 停止并清理 FFmpeg 进程
        """
        global FFMPEG_PROCESSES
        process = FFMPEG_PROCESSES.get(stream_name)

        if process:
            try:
                logger.info(f"Stopping FFmpeg for {stream_name} (PID: {process.pid})...")
                process.terminate() # 尝试温和关闭
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()  # 强制关闭
                logger.info(f"Stream {stream_name} stopped.")
            except Exception as e:
                logger.error(f"Error stopping stream {stream_name}: {e}")
            finally:
                # 无论如何从字典中移除
                if stream_name in FFMPEG_PROCESSES:
                    del FFMPEG_PROCESSES[stream_name]

    def _sanitize_stream_name(self, name: str) -> str:
        return name.replace(" ", "_").replace("/", "_").replace("\\", "_").lower()

    def _get_ffmpeg_path(self) -> str:
        return os.getenv(
            "FFMPEG_PATH", 
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "..", "ffmpeg-8.0.1-essentials_build", "bin", "ffmpeg.exe")
            )

    def _get_ffprobe_path(self) -> str:
        ffmpeg_path = self._get_ffmpeg_path()
        ffprobe_path = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe.exe")
        return ffprobe_path

    def _get_record_root(self) -> str:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        record_root = os.path.join(base_dir, "static", "recordings")
        os.makedirs(record_root, exist_ok=True)
        return record_root

    def _get_alarm_video_root(self) -> str:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        alarm_root = os.path.join(base_dir, "static", "alarm_videos")
        os.makedirs(alarm_root, exist_ok=True)
        return alarm_root

    def _get_playback_video_root(self) -> str:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        playback_root = os.path.join(base_dir, "static", "playback_videos")
        os.makedirs(playback_root, exist_ok=True)
        return playback_root

    def _get_rtsp_url_for_device(self, db_video: VideoDevice) -> Optional[str]:
        if getattr(db_video, "rtsp_url", None) and str(db_video.rtsp_url).lower().startswith("rtsp://"):
            return db_video.rtsp_url

        if db_video.stream_url and str(db_video.stream_url).lower().startswith("rtsp://"):
            return db_video.stream_url

        if db_video.ip_address and db_video.username and db_video.password:
            return f"rtsp://{db_video.username}:{db_video.password}@{db_video.ip_address}:554/Streaming/Channels/1"

        return None

    def start_ffmpeg_recording(self, video_id: int, rtsp_url: str):
        if not rtsp_url:
            logger.warning(f"录像启动失败，video_id={video_id} 缺少 RTSP 地址")
            return None

        # 如果同一路录像进程正在运行且源地址未变，不要重启。
        existing = RECORDING_PROCESSES.get(video_id)
        if isinstance(existing, dict):
            existing_process = existing.get("process")
            existing_rtsp = existing.get("rtsp_url")
            if existing_process and existing_process.poll() is None and existing_rtsp == rtsp_url:
                return existing_process
        elif existing is not None:
            try:
                if existing.poll() is None:
                    return existing
            except Exception:
                pass

        self.stop_ffmpeg_recording(video_id)

        ffmpeg_path = self._get_ffmpeg_path()
        record_root = self._get_record_root()
        device_root = os.path.join(record_root, str(video_id))
        os.makedirs(device_root, exist_ok=True)
        log_root = os.path.join(os.path.dirname(record_root), "logs")
        os.makedirs(log_root, exist_ok=True)
        log_path = os.path.join(log_root, f"recording_{video_id}.log")

        # 直接写到设备目录，避免日期子目录不存在导致 ffmpeg 无法落盘。
        segment_pattern = os.path.join(device_root, "%Y%m%d_%H%M%S.mp4")

        command = [
            ffmpeg_path,
            "-y",
            "-rtsp_transport", "tcp",
            "-use_wallclock_as_timestamps", "1",
            "-i", rtsp_url,
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-c:v", "copy",
            "-c:a", "aac",
            "-f", "segment",
            "-segment_time", "60",
            "-segment_atclocktime", "1",
            "-strftime", "1",
            "-reset_timestamps", "1",
            segment_pattern
        ]

        logger.info(f"Starting recording for video_id={video_id}")
        try:
            creationflags = 0x08000000 if os.name == "nt" else 0
            log_file = open(log_path, "a", encoding="utf-8")
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=log_file,
                creationflags=creationflags
            )
            RECORDING_PROCESSES[video_id] = {
                "process": process,
                "log_file": log_file,
                "rtsp_url": rtsp_url,
            }
            return process
        except Exception as e:
            logger.error(f"录像启动失败 video_id={video_id}: {e}")
            return None

    def stop_ffmpeg_recording(self, video_id: int):
        entry = RECORDING_PROCESSES.get(video_id)
        if not entry:
            return

        process = entry["process"] if isinstance(entry, dict) else entry
        log_file = entry.get("log_file") if isinstance(entry, dict) else None

        try:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        except Exception as e:
            logger.error(f"停止录像失败 video_id={video_id}: {e}")
        finally:
            RECORDING_PROCESSES.pop(video_id, None)
            if log_file:
                try:
                    log_file.close()
                except Exception:
                    pass

    def _parse_segment_start(self, file_path: str) -> Optional[datetime]:
        try:
            name = os.path.basename(file_path).replace(".mp4", "")
            return datetime.strptime(name, "%Y%m%d_%H%M%S")
        except Exception:
            return None

    def _is_segment_usable(self, file_path: str, min_age_seconds: int = 6) -> bool:
        """过滤未写完/损坏分段，避免 concat 阶段出现 moov atom not found。"""
        try:
            if not os.path.exists(file_path):
                return False

            # 录像按 60 秒分段，至少等待一个完整分段周期再参与拼接，
            # 避免把仍在写入中的当前分段加入 concat。
            seg_start = self._parse_segment_start(file_path)
            if seg_start:
                if (datetime.now() - seg_start).total_seconds() < 68:
                    return False

            stat = os.stat(file_path)
            if stat.st_size < 64 * 1024:
                return False

            age = time.time() - stat.st_mtime
            if age < min_age_seconds:
                return False

            ffprobe_path = self._get_ffprobe_path()
            if not os.path.exists(ffprobe_path):
                # 没有 ffprobe 时至少保证文件不是“正在写入”状态
                return True

            cmd = [
                ffprobe_path,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=noprint_wrappers=1:nokey=1",
                file_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if not (result.returncode == 0 and bool((result.stdout or "").strip())):
                return False

            # 二次校验：快速解码 1 秒视频，尽早剔除明显损坏分段
            ffmpeg_path = self._get_ffmpeg_path()
            decode_check_cmd = [
                ffmpeg_path,
                "-v", "error",
                "-t", "1",
                "-i", file_path,
                "-an",
                "-f", "null",
                "-",
            ]
            decode_result = subprocess.run(decode_check_cmd, capture_output=True, text=True)
            return decode_result.returncode == 0
        except Exception:
            return False

    def ensure_all_recordings(self, db: Session):
        videos = db.query(VideoDevice).all()
        for v in videos:
            entry = RECORDING_PROCESSES.get(v.id)
            if isinstance(entry, dict):
                proc = entry.get("process")
                if proc and proc.poll() is None:
                    continue
            elif entry is not None:
                try:
                    if entry.poll() is None:
                        continue
                except Exception:
                    pass
            rtsp_url = self._get_rtsp_url_for_device(v)
            if rtsp_url:
                self.start_ffmpeg_recording(v.id, rtsp_url)

    def _parse_datetime_input(self, value: datetime | str) -> datetime:
        if isinstance(value, datetime):
            return value

        if not isinstance(value, str):
            raise ValueError("时间参数格式不正确")

        raw = value.strip()
        if not raw:
            raise ValueError("时间参数不能为空")

        normalized = raw.replace(" ", "T")
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"

        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            raise ValueError("时间格式无效，支持 ISO 格式，如 2026-03-24T09:47:00")

        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt

    def _to_static_web_path(self, abs_file_path: str) -> str:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        static_root = os.path.join(base_dir, "static")
        rel_path = os.path.relpath(abs_file_path, static_root)
        return "/static/" + rel_path.replace("\\", "/")

    def _collect_segments_for_timerange(self, video_id: int, start_dt: datetime, end_dt: datetime) -> list[tuple[str, datetime, datetime]]:
        record_root = self._get_record_root()
        device_root = os.path.join(record_root, str(video_id))
        if not os.path.isdir(device_root):
            return []

        candidates: list[tuple[str, datetime, datetime]] = []
        for seg_path in sorted(glob.glob(os.path.join(device_root, "*.mp4"))):
            seg_start = self._parse_segment_start(seg_path)
            if not seg_start:
                continue

            seg_end = seg_start + timedelta(seconds=60)
            if seg_end <= start_dt or seg_start >= end_dt:
                continue
            if not self._is_segment_usable(seg_path):
                continue

            candidates.append((seg_path, seg_start, seg_end))

        return candidates

    def save_playback_clip(self, video_id: int, start_time: datetime | str, end_time: datetime | str, output_type: str = "playback", filename_prefix: Optional[str] = None):
        start_dt = self._parse_datetime_input(start_time)
        end_dt = self._parse_datetime_input(end_time)
        if end_dt <= start_dt:
            raise ValueError("结束时间必须大于开始时间")

        segments = self._collect_segments_for_timerange(video_id, start_dt, end_dt)
        if not segments:
            raise ValueError("所选时间段没有可用录像分段")

        output_root = self._get_alarm_video_root() if output_type == "alarm" else self._get_playback_video_root()
        os.makedirs(output_root, exist_ok=True)

        ffmpeg_path = self._get_ffmpeg_path()
        if not os.path.exists(ffmpeg_path):
            raise ValueError(f"未找到 ffmpeg: {ffmpeg_path}")

        first_seg_start = segments[0][1]
        concat_list_path = os.path.join(output_root, f"_concat_{video_id}_{uuid.uuid4().hex}.txt")
        concat_output_path = os.path.join(output_root, f"_concat_{video_id}_{uuid.uuid4().hex}.mp4")

        safe_prefix = (filename_prefix or "playback").replace(" ", "_")
        final_name = f"{safe_prefix}_{video_id}_{start_dt.strftime('%Y%m%d_%H%M%S')}_{end_dt.strftime('%Y%m%d_%H%M%S')}.mp4"
        final_output_path = os.path.join(output_root, final_name)

        try:
            with open(concat_list_path, "w", encoding="utf-8") as f:
                for seg_path, _, _ in segments:
                    safe_seg_path = seg_path.replace("\\", "/").replace("'", "\\'")
                    f.write(f"file '{safe_seg_path}'\n")

            concat_cmd = [
                ffmpeg_path,
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_path,
                "-c", "copy",
                concat_output_path,
            ]
            concat_proc = subprocess.run(concat_cmd, capture_output=True, text=True)
            if concat_proc.returncode != 0:
                concat_fallback_cmd = [
                    ffmpeg_path,
                    "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", concat_list_path,
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-c:a", "aac",
                    concat_output_path,
                ]
                concat_fallback_proc = subprocess.run(concat_fallback_cmd, capture_output=True, text=True)
                if concat_fallback_proc.returncode != 0:
                    logger.error(
                        "Concat failed video_id=%s start=%s end=%s copy_err=%s reencode_err=%s",
                        video_id,
                        start_dt,
                        end_dt,
                        (concat_proc.stderr or "").strip()[-1200:],
                        (concat_fallback_proc.stderr or "").strip()[-1200:],
                    )
                    raise ValueError("录像分段合并失败")

            clip_offset = max(0.0, (start_dt - first_seg_start).total_seconds())
            clip_duration = max(1.0, (end_dt - start_dt).total_seconds())

            trim_cmd = [
                ffmpeg_path,
                "-y",
                "-ss", f"{clip_offset:.3f}",
                "-i", concat_output_path,
                "-t", f"{clip_duration:.3f}",
                "-c", "copy",
                final_output_path,
            ]
            trim_proc = subprocess.run(trim_cmd, capture_output=True, text=True)
            if trim_proc.returncode != 0:
                trim_fallback_cmd = [
                    ffmpeg_path,
                    "-y",
                    "-ss", f"{clip_offset:.3f}",
                    "-i", concat_output_path,
                    "-t", f"{clip_duration:.3f}",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-c:a", "aac",
                    final_output_path,
                ]
                trim_fallback_proc = subprocess.run(trim_fallback_cmd, capture_output=True, text=True)
                if trim_fallback_proc.returncode != 0:
                    raise ValueError("录像裁剪失败")

            if not os.path.exists(final_output_path) or os.path.getsize(final_output_path) == 0:
                raise ValueError("生成的视频文件无效")

            return {
                "status": "success",
                "video_id": video_id,
                "start_time": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "end_time": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "duration_seconds": int((end_dt - start_dt).total_seconds()),
                "recording_path": self._to_static_web_path(final_output_path),
                "recording_full_path": final_output_path,
            }
        finally:
            for temp_file in [concat_list_path, concat_output_path]:
                try:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                except Exception:
                    pass
