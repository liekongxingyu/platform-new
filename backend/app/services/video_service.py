import json
from sqlalchemy.orm import Session
from urllib.parse import urlparse
from app.models.video import VideoDevice
from app.models.device import Device
from app.models.alarm_records import AlarmRecord
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
from typing import Optional, List, Dict, Any, Set

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
EZVIZ_TOKEN_CACHE: Dict[str, Any] = {"access_token": None, "expire_at": 0.0}
EZVIZ_TOKEN_LOCK = threading.Lock()
EZVIZ_PTZ_LAST_DIRECTION: Dict[int, int] = {}
EZVIZ_PTZ_LAST_STOP_AT: Dict[int, float] = {}

EZVIZ_BASE_URL = os.getenv("EZVIZ_BASE_URL", "https://open.ys7.com").rstrip("/")
EZVIZ_APP_KEY = os.getenv("EZVIZ_APP_KEY", "")
EZVIZ_APP_SECRET = os.getenv("EZVIZ_APP_SECRET", "")
DEFAULT_STREAM_PROTOCOL = os.getenv("VIDEO_DEFAULT_STREAM_PROTOCOL", "ezopen")
DEFAULT_WEEKLY_QUOTA_GB = float(os.getenv("VIDEO_DEFAULT_WEEKLY_QUOTA_GB", "2"))
DEFAULT_WEEKLY_QUOTA_BYTES = int(DEFAULT_WEEKLY_QUOTA_GB * 1024 * 1024 * 1024)
TRAFFIC_ALERT_THRESHOLD_RATIO = float(os.getenv("VIDEO_TRAFFIC_ALERT_THRESHOLD_RATIO", "0.2"))

STREAM_PROTOCOL_MAP = {
    "ezopen": 1,
    "hls": 2,
    "rtmp": 3,
    "flv": 4,
}

EZVIZ_DIRECTION_MAP = {
    "up": 0,
    "down": 1,
    "left": 2,
    "right": 3,
    "zoom_in": 8,
    "zoom_out": 9,
}

TOKEN_ERROR_CODES = {"10002", "10029", "10030", "10031", "20002"}
# 近实时回放依赖短分段；常态回放由独立归档逻辑完成，不与分段时长绑定。
RECORD_SEGMENT_SECONDS = int(os.getenv("VIDEO_RECORD_SEGMENT_SECONDS", "30"))
RECORD_SEGMENT_SAFE_MARGIN_SECONDS = int(os.getenv("VIDEO_RECORD_SEGMENT_SAFE_MARGIN_SECONDS", "8"))
PLAYBACK_ARCHIVE_WINDOW_HOURS = max(1, int(os.getenv("PLAYBACK_ARCHIVE_WINDOW_HOURS", "3")))
PLAYBACK_ARCHIVE_LOOKBACK_HOURS = max(PLAYBACK_ARCHIVE_WINDOW_HOURS, int(os.getenv("PLAYBACK_ARCHIVE_LOOKBACK_HOURS", "24")))
PERIODIC_ARCHIVE_LAST_RUN_AT: Dict[int, float] = {}
EZVIZ_PRESET_UNSUPPORTED_DEVICES: Set[int] = set()

class VideoService:
    def _normalize_flag(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _format_bytes(self, value: int) -> str:
        size = max(0, int(value or 0))
        units = ["B", "KB", "MB", "GB", "TB"]
        display = float(size)
        unit_index = 0
        while display >= 1024 and unit_index < len(units) - 1:
            display /= 1024
            unit_index += 1
        if unit_index == 0:
            return f"{int(display)}{units[unit_index]}"
        return f"{display:.2f}{units[unit_index]}"

    def _get_week_cycle_bounds(self, reference: Optional[datetime] = None) -> tuple[datetime, datetime]:
        now = reference or datetime.now()
        week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return week_start, now

    def _get_weekly_quota_bytes(self, db_video: VideoDevice) -> int:
        quota = getattr(db_video, "weekly_quota_bytes", None)
        if isinstance(quota, int) and quota > 0:
            return quota
        if isinstance(quota, float) and quota > 0:
            return int(quota)
        return DEFAULT_WEEKLY_QUOTA_BYTES

    def _get_ezviz_device_traffic_bytes(self, db_video: VideoDevice, cycle_start: datetime, cycle_end: datetime) -> Optional[int]:
        """
        从萤石云查询设备在指定时间范围内的实际流量消耗（单位：字节）。
        查询失败时返回 None，外层可降级到本地分段统计。
        """
        device_serial = str(getattr(db_video, "device_serial", "") or "").strip()
        if not device_serial:
            return None

        channel_no = int(getattr(db_video, "channel_no", None) or 1)
        start_str = cycle_start.strftime("%Y-%m-%d %H:%M:%S")
        end_str = cycle_end.strftime("%Y-%m-%d %H:%M:%S")

        # 尝试多种可能的萤石云流量查询端点和参数格式
        paths_and_payloads = [
            # 方案 1: 设备流量查询 - 完整参数
            (
                "/api/lapp/device/traffic",
                {
                    "deviceSerial": device_serial,
                    "channelNo": channel_no,
                    "startTime": start_str,
                    "endTime": end_str,
                }
            ),
            # 方案 2: 设备流量查询 v2
            (
                "/api/lapp/v2/device/traffic",
                {
                    "deviceSerial": device_serial,
                    "channelNo": channel_no,
                    "startTime": start_str,
                    "endTime": end_str,
                }
            ),
            # 方案 3: 设备流量统计 - 时间戳格式
            (
                "/api/lapp/device/traffic/get",
                {
                    "deviceSerial": device_serial,
                    "channelNo": channel_no,
                    "beginTime": int(cycle_start.timestamp() * 1000),
                    "endTime": int(cycle_end.timestamp() * 1000),
                }
            ),
            # 方案 4: 设备用量查询
            (
                "/api/lapp/device/usage",
                {
                    "deviceSerial": device_serial,
                    "channelNo": channel_no,
                    "startTime": start_str,
                    "endTime": end_str,
                }
            ),
        ]

        for path, payload in paths_and_payloads:
            try:
                body = self._call_ezviz_api(path, payload, retry_on_token_error=True)
                data = body.get("data") or {}

                # 尝试各种可能的字段名
                traffic_bytes = (
                    data.get("traffic")
                    or data.get("flowUsed")
                    or data.get("used")
                    or data.get("consumed")
                    or data.get("bytes")
                    or data.get("trafficBytes")
                )

                if traffic_bytes is not None:
                    traffic_int = int(traffic_bytes)
                    logger.info(
                        f"EZVIZ traffic query success: device_serial={device_serial}, "
                        f"channel={channel_no}, bytes={traffic_int}, period={cycle_start}~{cycle_end}"
                    )
                    return max(0, traffic_int)
            except Exception as e:
                logger.debug(
                    f"EZVIZ traffic query failed on {path} for {device_serial}#{channel_no}: {e}"
                )
                continue

        # 所有端点都失败或返回 None
        logger.warning(
            f"EZVIZ traffic query exhausted all endpoints for {device_serial}#{channel_no}, "
            f"will fallback to local recording segments"
        )
        return None

    def _collect_weekly_recording_usage_bytes(self, video_id: int, cycle_start: datetime, cycle_end: datetime) -> int:
        """统计本地录像分段字节数，作为流量消耗的降级方案。"""
        device_root = os.path.join(self._get_record_root(), str(video_id))
        if not os.path.isdir(device_root):
            return 0

        total = 0
        for file_path in glob.glob(os.path.join(device_root, "*.mp4")):
            try:
                seg_start = self._parse_segment_start(file_path)
                if seg_start is None:
                    seg_start = datetime.fromtimestamp(os.path.getmtime(file_path))
                if seg_start < cycle_start or seg_start >= cycle_end:
                    continue
                total += int(os.path.getsize(file_path))
            except Exception:
                continue
        return total

    def _build_device_status_summary(self, db: Session, db_video: VideoDevice) -> dict:
        raw_status = str(getattr(db_video, "status", "offline") or "offline").strip().lower()
        sleeping = self._normalize_flag(getattr(db_video, "sleeping", False))
        if sleeping:
            main_status = "sleeping"
        elif raw_status not in {"online", "offline", "sleeping"}:
            main_status = "offline"
        else:
            main_status = raw_status

        privacy_enabled = self._normalize_flag(getattr(db_video, "privacy_enabled", False))
        storage_abnormal = self._normalize_flag(getattr(db_video, "storage_abnormal", False))
        low_battery = self._normalize_flag(getattr(db_video, "low_battery", False))
        weak_signal = self._normalize_flag(getattr(db_video, "weak_signal", False))

        alarm_active = bool(
            db.query(AlarmRecord)
            .filter(
                AlarmRecord.device_id == str(db_video.id),
                AlarmRecord.status == "pending",
            )
            .first()
        )

        status_tags: list[str] = []
        if privacy_enabled:
            status_tags.append("privacy_enabled")
        if storage_abnormal:
            status_tags.append("storage_abnormal")
        if low_battery:
            status_tags.append("low_battery")
        if weak_signal:
            status_tags.append("weak_signal")
        if alarm_active:
            status_tags.append("alarm_active")

        is_fault = bool(storage_abnormal or low_battery or weak_signal or alarm_active or main_status == "offline")
        status_text_map = {
            "online": "在线",
            "offline": "离线",
            "sleeping": "待机/休眠",
        }
        status_text_parts = [status_text_map.get(main_status, "离线")]
        tag_text_map = {
            "privacy_enabled": "隐私开启",
            "storage_abnormal": "存储异常",
            "low_battery": "低电量",
            "weak_signal": "信号弱",
            "alarm_active": "异常告警中",
        }
        status_text_parts.extend(tag_text_map[tag] for tag in status_tags if tag in tag_text_map)

        return {
            "main_status": main_status,
            "privacy_enabled": privacy_enabled,
            "storage_abnormal": storage_abnormal,
            "low_battery": low_battery,
            "weak_signal": weak_signal,
            "sleeping": sleeping,
            "alarm_active": alarm_active,
            "status_tags": status_tags,
            "is_fault": is_fault,
            "status_text": "｜".join(status_text_parts),
        }

    def _sync_single_monitoring_alarm(
        self,
        db: Session,
        db_video: VideoDevice,
        alarm_type: str,
        severity: str,
        description: str,
        active: bool,
    ) -> bool:
        """将监控状态同步为报警记录：active=True 生成待处理报警，False 自动恢复。"""
        device_id = str(db_video.id)
        pending_alarm = (
            db.query(AlarmRecord)
            .filter(
                AlarmRecord.device_id == device_id,
                AlarmRecord.alarm_type == alarm_type,
                AlarmRecord.status == "pending",
            )
            .first()
        )

        if active:
            if pending_alarm:
                return False
            db.add(
                AlarmRecord(
                    device_id=device_id,
                    alarm_type=alarm_type,
                    severity=severity,
                    description=description,
                    location=db_video.name or f"视频设备-{device_id}",
                    status="pending",
                )
            )
            return True

        if pending_alarm:
            pending_alarm.status = "resolved"
            pending_alarm.handled_at = datetime.utcnow() + timedelta(hours=8)
            return True

        return False

    def _sync_monitoring_alarms(
        self,
        db: Session,
        db_video: VideoDevice,
        status_summary: dict,
        weekly_quota_bytes: int,
        weekly_used_bytes: int,
        weekly_remaining_bytes: int,
    ) -> bool:
        """根据设备状态与流量阈值同步报警记录。"""
        changed = False

        alarm_specs = [
            (
                "VIDEO_DEVICE_OFFLINE",
                "high",
                f"视频设备 {db_video.name} 离线",
                status_summary.get("main_status") == "offline",
            ),
            (
                "VIDEO_DEVICE_SLEEPING",
                "low",
                f"视频设备 {db_video.name} 处于待机/休眠",
                bool(status_summary.get("sleeping")),
            ),
            (
                "VIDEO_DEVICE_PRIVACY_ENABLED",
                "low",
                f"视频设备 {db_video.name} 开启隐私模式",
                bool(status_summary.get("privacy_enabled")),
            ),
            (
                "VIDEO_DEVICE_STORAGE_ABNORMAL",
                "high",
                f"视频设备 {db_video.name} 存储异常",
                bool(status_summary.get("storage_abnormal")),
            ),
            (
                "VIDEO_DEVICE_LOW_BATTERY",
                "medium",
                f"视频设备 {db_video.name} 低电量",
                bool(status_summary.get("low_battery")),
            ),
            (
                "VIDEO_DEVICE_WEAK_SIGNAL",
                "medium",
                f"视频设备 {db_video.name} 信号弱",
                bool(status_summary.get("weak_signal")),
            ),
        ]

        for alarm_type, severity, description, active in alarm_specs:
            changed = self._sync_single_monitoring_alarm(
                db=db,
                db_video=db_video,
                alarm_type=alarm_type,
                severity=severity,
                description=description,
                active=active,
            ) or changed

        quota = max(0, int(weekly_quota_bytes or 0))
        remaining = max(0, int(weekly_remaining_bytes or 0))
        ratio = (remaining / quota) if quota > 0 else 0.0
        traffic_low_active = quota > 0 and ratio <= TRAFFIC_ALERT_THRESHOLD_RATIO
        traffic_desc = (
            f"视频设备 {db_video.name} 流量低于阈值20%，"
            f"剩余 {self._format_bytes(remaining)} / 周额度 {self._format_bytes(quota)}，"
            f"本周已用 {self._format_bytes(weekly_used_bytes)}"
        )
        changed = self._sync_single_monitoring_alarm(
            db=db,
            db_video=db_video,
            alarm_type="VIDEO_TRAFFIC_LOW",
            severity="medium",
            description=traffic_desc,
            active=traffic_low_active,
        ) or changed

        return changed

    def get_monitoring_summary(self, db: Session, video_id: Optional[int] = None):
        query = db.query(VideoDevice)
        if video_id is not None:
            query = query.filter(VideoDevice.id == video_id)

        now = datetime.now()
        cycle_start, cycle_end = self._get_week_cycle_bounds(now)
        videos = query.all()
        summaries = []
        has_alarm_changes = False

        for db_video in videos:
            weekly_quota_bytes = self._get_weekly_quota_bytes(db_video)
            
            # ✅ 优先查询萤石云真实流量消耗，失败则降级到本地分段统计
            is_ezviz_cloud = self._is_ezviz_access(db_video)
            if is_ezviz_cloud:
                ezviz_traffic = self._get_ezviz_device_traffic_bytes(db_video, cycle_start, cycle_end)
                if ezviz_traffic is not None:
                    weekly_used_bytes = ezviz_traffic
                else:
                    # 萤石云查询失败，降级到本地分段统计
                    weekly_used_bytes = self._collect_weekly_recording_usage_bytes(db_video.id, cycle_start, cycle_end)
            else:
                # 本地 ONVIF 设备，使用本地分段统计
                weekly_used_bytes = self._collect_weekly_recording_usage_bytes(db_video.id, cycle_start, cycle_end)
            
            weekly_remaining_bytes = max(0, weekly_quota_bytes - weekly_used_bytes)

            status_summary = self._build_device_status_summary(db, db_video)
            has_alarm_changes = self._sync_monitoring_alarms(
                db=db,
                db_video=db_video,
                status_summary=status_summary,
                weekly_quota_bytes=weekly_quota_bytes,
                weekly_used_bytes=weekly_used_bytes,
                weekly_remaining_bytes=weekly_remaining_bytes,
            ) or has_alarm_changes
            status_summary = self._build_device_status_summary(db, db_video)

            summaries.append({
                "device_id": db_video.id,
                "device_name": db_video.name,
                "device_serial": getattr(db_video, "device_serial", None),
                "weekly_quota_bytes": weekly_quota_bytes,
                "weekly_used_bytes": weekly_used_bytes,
                "weekly_remaining_bytes": weekly_remaining_bytes,
                "weekly_quota_text": self._format_bytes(weekly_quota_bytes),
                "weekly_used_text": self._format_bytes(weekly_used_bytes),
                "weekly_remaining_text": self._format_bytes(weekly_remaining_bytes),
                "cycle_start_time": cycle_start,
                "cycle_end_time": cycle_end,
                "last_calculated_at": now,
                **status_summary,
            })

        if has_alarm_changes:
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                logger.error(f"Failed to persist monitoring alarms: {e}")

        if video_id is not None:
            return summaries[0] if summaries else None
        return summaries

    def _get_ezviz_config(self) -> tuple[str, str, str]:
        # 运行时读取环境变量，避免导入时机导致配置值为空。
        base_url = (os.getenv("EZVIZ_BASE_URL") or EZVIZ_BASE_URL or "https://open.ys7.com").rstrip("/")
        app_key = os.getenv("EZVIZ_APP_KEY") or EZVIZ_APP_KEY or ""
        app_secret = os.getenv("EZVIZ_APP_SECRET") or EZVIZ_APP_SECRET or ""
        return base_url, app_key, app_secret

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

        if self._is_ezviz_ptz(db_video):
            return self._ezviz_ptz_stop(db_video)

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

        if self._is_ezviz_ptz(db_video):
            return self._ezviz_ptz_start(db_video, direction, speed)

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

    def _normalize_stream_protocol(self, protocol: Optional[str]) -> str:
        normalized = (protocol or DEFAULT_STREAM_PROTOCOL or "ezopen").strip().lower()
        return normalized if normalized in STREAM_PROTOCOL_MAP else "ezopen"

    def _resolve_access_source(self, db_video: VideoDevice) -> str:
        explicit_source = (getattr(db_video, "access_source", None) or "").strip().lower()
        if explicit_source in {"cloud", "local"}:
            return explicit_source

        platform = (getattr(db_video, "platform_type", None) or "").strip().lower()
        if platform == "ezviz":
            return "cloud"
        return "local"

    def _resolve_ptz_source(self, db_video: VideoDevice) -> str:
        explicit_source = (getattr(db_video, "ptz_source", None) or "").strip().lower()
        if explicit_source in {"ezviz", "onvif"}:
            return explicit_source

        platform = (getattr(db_video, "platform_type", None) or "").strip().lower()
        if platform == "ezviz":
            return "ezviz"
        return "onvif"

    def _is_ezviz_access(self, db_video: VideoDevice) -> bool:
        return self._resolve_access_source(db_video) == "cloud" and bool(getattr(db_video, "device_serial", None))

    def _is_ezviz_ptz(self, db_video: VideoDevice) -> bool:
        return self._resolve_ptz_source(db_video) == "ezviz" and bool(getattr(db_video, "device_serial", None))

    def _map_error_code(self, raw_code: Any, raw_message: str) -> tuple[str, str]:
        code_str = str(raw_code or "")
        msg = raw_message or "调用失败"
        msg_lower = msg.lower()

        if code_str in TOKEN_ERROR_CODES or "token" in msg_lower:
            return "TOKEN_EXPIRED", "平台凭证失效，请稍后重试"
        if "offline" in msg_lower or "设备不在线" in msg or "设备离线" in msg:
            return "DEVICE_OFFLINE", "设备离线或不可达"
        if "ptz" in msg_lower and ("not" in msg_lower or "不支持" in msg):
            return "PTZ_NOT_SUPPORTED", "设备不支持云台控制"
        if code_str == "60019" or "加密" in msg:
            return "VIDEO_ENCRYPTED", "视频加密已开启，当前协议不可用"
        return "UPSTREAM_ERROR", msg

    def _ensure_ezviz_credentials(self):
        _, app_key, app_secret = self._get_ezviz_config()
        if not app_key or not app_secret:
            raise ValueError("UPSTREAM_ERROR: 未配置萤石 AppKey/AppSecret")

    def _request_ezviz_token(self) -> str:
        self._ensure_ezviz_credentials()
        base_url, app_key, app_secret = self._get_ezviz_config()
        url = f"{base_url}/api/lapp/token/get"
        payload = {"appKey": app_key, "appSecret": app_secret}
        resp = requests.post(url, data=payload, timeout=8)
        resp.raise_for_status()
        body = resp.json() if resp.content else {}

        code = str(body.get("code", ""))
        if code != "200":
            semantic_code, semantic_msg = self._map_error_code(code, str(body.get("msg", "获取 token 失败")))
            raise ValueError(f"{semantic_code}: {semantic_msg}")

        data = body.get("data") or {}
        token = data.get("accessToken")
        expire_time = int(data.get("expireTime") or 0)
        if not token:
            raise ValueError("UPSTREAM_ERROR: 获取 token 失败")

        if expire_time <= 0:
            expire_at = time.time() + 6 * 24 * 3600
        elif expire_time > 10_000_000_000:
            expire_at = expire_time / 1000.0
        else:
            expire_at = float(expire_time)

        EZVIZ_TOKEN_CACHE["access_token"] = token
        EZVIZ_TOKEN_CACHE["expire_at"] = expire_at
        return token

    def _get_ezviz_token(self, force_refresh: bool = False) -> str:
        with EZVIZ_TOKEN_LOCK:
            token = EZVIZ_TOKEN_CACHE.get("access_token")
            expire_at = float(EZVIZ_TOKEN_CACHE.get("expire_at") or 0.0)
            now = time.time()
            if (not force_refresh) and token and expire_at - now > 120:
                return str(token)
            return self._request_ezviz_token()

    def get_ezviz_health(self) -> dict:
        _, app_key, app_secret = self._get_ezviz_config()
        try:
            token = self._get_ezviz_token(force_refresh=False)
            expire_at = float(EZVIZ_TOKEN_CACHE.get("expire_at") or 0.0)
            return {
                "status": "ok",
                "configured": bool(app_key and app_secret),
                "token_ready": bool(token),
                "token_expire_at": int(expire_at),
            }
        except Exception as e:
            return {
                "status": "error",
                "configured": bool(app_key and app_secret),
                "token_ready": False,
                "message": str(e),
            }

    def _call_ezviz_api(self, path: str, payload: dict, retry_on_token_error: bool = True) -> dict:
        token = self._get_ezviz_token(force_refresh=False)
        base_url, _, _ = self._get_ezviz_config()
        url = f"{base_url}{path}"
        request_payload = dict(payload)
        request_payload["accessToken"] = token

        resp = requests.post(url, data=request_payload, timeout=8)
        resp.raise_for_status()
        body = resp.json() if resp.content else {}
        code = str(body.get("code", ""))
        if code == "200":
            return body

        if retry_on_token_error and code in TOKEN_ERROR_CODES:
            token = self._get_ezviz_token(force_refresh=True)
            retry_payload = dict(payload)
            retry_payload["accessToken"] = token
            retry_resp = requests.post(url, data=retry_payload, timeout=8)
            retry_resp.raise_for_status()
            retry_body = retry_resp.json() if retry_resp.content else {}
            retry_code = str(retry_body.get("code", ""))
            if retry_code == "200":
                return retry_body
            semantic_code, semantic_msg = self._map_error_code(retry_code, str(retry_body.get("msg", "调用失败")))
            raise ValueError(f"{semantic_code}: {semantic_msg}")

        semantic_code, semantic_msg = self._map_error_code(code, str(body.get("msg", "调用失败")))
        raise ValueError(f"{semantic_code}: {semantic_msg}")

    def _get_stream_info_local(self, db_video: VideoDevice) -> dict:
        # 拉流前执行按需校时：超过阈值才改，且有冷却时间避免频繁写设备。
        sync_result = self._sync_camera_time_for_video(db_video, force=False)
        if sync_result.get("status") == "error":
            logger.warning(f"Auto time sync failed for video_id={db_video.id}: {sync_result.get('message')}")

        # 懒启动推流：当前端请求播放地址时，如推流进程不存在则自动拉起。
        stream_name = str(db_video.id)
        entry = FFMPEG_PROCESSES.get(stream_name)
        is_running = False
        if entry is not None:
            try:
                is_running = entry.poll() is None
            except Exception:
                is_running = False

        if not is_running:
            rtsp_url = self._get_rtsp_url_for_device(db_video)
            if rtsp_url:
                self.start_ffmpeg_stream(rtsp_url, stream_name)

        # 拉流阶段顺带做一次录像自愈，确保“设备在线时持续落盘”。
        record_entry = RECORDING_PROCESSES.get(db_video.id)
        record_running = False
        if isinstance(record_entry, dict):
            record_proc = record_entry.get("process")
            if record_proc is not None:
                try:
                    record_running = record_proc.poll() is None
                except Exception:
                    record_running = False
        elif record_entry is not None:
            try:
                record_running = record_entry.poll() is None
            except Exception:
                record_running = False

        if not record_running:
            rtsp_url = self._get_rtsp_url_for_device(db_video)
            if rtsp_url:
                self.start_ffmpeg_recording(db_video.id, rtsp_url)

        url = db_video.stream_url or ""
        play_type = "flv"
        lowered = str(url).lower()
        if lowered.startswith("rtsp://"):
            play_type = "rtsp"
        elif lowered.endswith(".m3u8"):
            play_type = "hls"
        elif lowered.startswith("http") and ".flv" in lowered:
            play_type = "flv"

        return {
            "url": url,
            "play_type": play_type,
            "platform": "onvif",
            "device_serial": None,
            "channel_no": None,
            "access_token": None,
        }

    def _get_stream_info_ezviz(self, db_video: VideoDevice) -> dict:
        protocol_name = self._normalize_stream_protocol(getattr(db_video, "stream_protocol", None))
        channel_no = int(getattr(db_video, "channel_no", None) or 1)
        device_serial = str(getattr(db_video, "device_serial", "") or "").strip()
        if not device_serial:
            raise ValueError("UPSTREAM_ERROR: 云设备缺少 device_serial")

        preferred_code = STREAM_PROTOCOL_MAP[protocol_name]
        protocol_candidates = [preferred_code] + [c for c in [1, 2, 3, 4] if c != preferred_code]

        url = ""
        last_error: Optional[Exception] = None
        for protocol_code in protocol_candidates:
            payload = {
                "deviceSerial": device_serial,
                "channelNo": channel_no,
                "protocol": protocol_code,
                "expireTime": 3600,
            }

            body = None
            paths = ["/api/lapp/live/address/get", "/api/lapp/v2/live/address/get"]
            for path in paths:
                try:
                    body = self._call_ezviz_api(path, payload)
                    break
                except Exception as e:
                    last_error = e

            if body is None:
                continue

            data = body.get("data") or {}
            url = (
                data.get("url")
                or data.get("liveAddress")
                or data.get("hls")
                or data.get("rtmp")
                or data.get("ezopen")
                or ""
            )
            if url:
                break

        if not url:
            raise last_error or ValueError("UPSTREAM_ERROR: 平台未返回可用播放地址")

        lower_url = str(url).lower()
        resolved_play_type = protocol_name
        if lower_url.startswith("ezopen://"):
            resolved_play_type = "ezopen"
        elif ".m3u8" in lower_url:
            resolved_play_type = "hls"
        elif lower_url.startswith("rtmp"):
            resolved_play_type = "rtmp"
        elif ".flv" in lower_url:
            resolved_play_type = "flv"

        # 云流场景也要持续落本地分段，供临时缓存/常态回放使用。
        record_entry = RECORDING_PROCESSES.get(db_video.id)
        record_running = False
        if isinstance(record_entry, dict):
            record_proc = record_entry.get("process")
            if record_proc is not None:
                try:
                    record_running = record_proc.poll() is None
                except Exception:
                    record_running = False
        elif record_entry is not None:
            try:
                record_running = record_entry.poll() is None
            except Exception:
                record_running = False

        if not record_running:
            record_source = self._get_record_source_for_device(db_video)
            if record_source:
                self.start_ffmpeg_recording(db_video.id, record_source)

        return {
            "url": url,
            "play_type": resolved_play_type,
            "platform": "ezviz",
            "device_serial": device_serial,
            "channel_no": channel_no,
            "access_token": self._get_ezviz_token(force_refresh=False),
        }

    def _ezviz_ptz_start(self, db_video: VideoDevice, direction: str, speed: float = 0.5):
        direction_code = EZVIZ_DIRECTION_MAP.get(direction)
        if direction_code is None:
            raise ValueError("PTZ_NOT_SUPPORTED: 不支持的 PTZ 方向")

        payload = {
            "deviceSerial": db_video.device_serial,
            "channelNo": int(getattr(db_video, "channel_no", None) or 1),
            "direction": direction_code,
            "speed": max(1, min(8, int(round(float(speed) * 8)))),
        }
        try:
            self._call_ezviz_api("/api/lapp/device/ptz/start", payload)
        except Exception as first_error:
            # 萤石云偶发网络抖动会导致 start 超时，短暂重试一次可提升稳定性。
            logger.warning(f"EZVIZ PTZ start retry for video_id={db_video.id}: {first_error}")
            time.sleep(0.15)
            self._call_ezviz_api("/api/lapp/device/ptz/start", payload)
        EZVIZ_PTZ_LAST_DIRECTION[db_video.id] = direction_code
        return {"status": "success"}

    def _ezviz_ptz_stop(self, db_video: VideoDevice):
        now = time.time()
        last_stop_at = EZVIZ_PTZ_LAST_STOP_AT.get(db_video.id, 0.0)
        if now - last_stop_at < 0.35:
            return {"status": "skipped", "message": "duplicate stop suppressed"}
        EZVIZ_PTZ_LAST_STOP_AT[db_video.id] = now

        payload = {
            "deviceSerial": db_video.device_serial,
            "channelNo": int(getattr(db_video, "channel_no", None) or 1),
        }
        last_direction = EZVIZ_PTZ_LAST_DIRECTION.get(db_video.id)
        if last_direction is not None:
            payload["direction"] = last_direction
        try:
            self._call_ezviz_api("/api/lapp/device/ptz/stop", payload)
            return {"status": "success"}
        except Exception as e:
            # Stop 属于幂等操作，平台超时场景下按降级成功处理，避免前端长按/松开持续报错。
            logger.warning(f"EZVIZ PTZ stop degraded for video_id={db_video.id}: {e}")
            return {"status": "degraded", "message": str(e)}

    def get_stream_info(self, db: Session, video_id: int):
        db_video = db.query(VideoDevice).filter(VideoDevice.id == video_id).first()
        if not db_video:
            return None

        if self._is_ezviz_access(db_video):
            return self._get_stream_info_ezviz(db_video)
        return self._get_stream_info_local(db_video)

    def _create_ptz_and_media(self, db: Session, video_id: int):
        db_video = db.query(VideoDevice).filter(VideoDevice.id == video_id).first()
        if not db_video:
            raise ValueError("Device not found")
        camera, ptz, media = self._get_onvif_service(db_video)
        token = self._get_profile_token(media)
        return db_video, camera, ptz, media, token

    def list_presets(self, db: Session, video_id: int):
        db_video = db.query(VideoDevice).filter(VideoDevice.id == video_id).first()
        if not db_video:
            raise ValueError("Device not found")

        if self._is_ezviz_ptz(db_video):
            if video_id in EZVIZ_PRESET_UNSUPPORTED_DEVICES:
                return []
            try:
                payload = {
                    "deviceSerial": db_video.device_serial,
                    "channelNo": int(getattr(db_video, "channel_no", None) or 1),
                }
                body = None
                for path in ["/api/lapp/device/preset/list", "/api/lapp/v2/device/preset/list"]:
                    try:
                        body = self._call_ezviz_api(path, payload)
                        break
                    except Exception:
                        body = None
                if body is None:
                    EZVIZ_PRESET_UNSUPPORTED_DEVICES.add(video_id)
                    logger.info(f"EZVIZ presets unsupported or unavailable for video_id={video_id}, skip preset polling")
                    return []
                data = body.get("data") or []
                result = []
                for item in data:
                    token = str(item.get("index") or item.get("presetIndex") or item.get("token") or "")
                    if not token:
                        continue
                    result.append({
                        "token": token,
                        "name": str(item.get("name") or item.get("presetName") or f"Preset-{token}"),
                    })
                return result
            except Exception as e:
                if "404" in str(e):
                    EZVIZ_PRESET_UNSUPPORTED_DEVICES.add(video_id)
                    logger.info(f"EZVIZ presets unsupported for video_id={video_id}, skip preset polling")
                else:
                    logger.warning(f"EZVIZ list presets failed for video_id={video_id}: {e}")
                return []

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
        db_video = db.query(VideoDevice).filter(VideoDevice.id == video_id).first()
        if not db_video:
            raise ValueError("Device not found")

        if self._is_ezviz_ptz(db_video):
            payload = {
                "deviceSerial": db_video.device_serial,
                "channelNo": int(getattr(db_video, "channel_no", None) or 1),
            }
            if name:
                payload["name"] = name
            if preset_token:
                payload["index"] = preset_token
            body = self._call_ezviz_api("/api/lapp/device/preset/add", payload)
            data = body.get("data") or {}
            token = str(data.get("index") or data.get("presetIndex") or preset_token or "")
            return {
                "token": token,
                "name": name or f"Preset-{token}",
            }

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
        db_video = db.query(VideoDevice).filter(VideoDevice.id == video_id).first()
        if not db_video:
            raise ValueError("Device not found")

        if self._is_ezviz_ptz(db_video):
            payload = {
                "deviceSerial": db_video.device_serial,
                "channelNo": int(getattr(db_video, "channel_no", None) or 1),
                "index": preset_token,
            }
            self._call_ezviz_api("/api/lapp/device/preset/move", payload)
            return {"status": "success"}

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
        db_video = db.query(VideoDevice).filter(VideoDevice.id == video_id).first()
        if not db_video:
            raise ValueError("Device not found")

        if self._is_ezviz_ptz(db_video):
            payload = {
                "deviceSerial": db_video.device_serial,
                "channelNo": int(getattr(db_video, "channel_no", None) or 1),
                "index": preset_token,
            }
            self._call_ezviz_api("/api/lapp/device/preset/clear", payload)
            return {"status": "success"}

        _, _, ptz, _, token = self._create_ptz_and_media(db, video_id)
        req = {'ProfileToken': token, 'PresetToken': preset_token}
        try:
            ptz.RemovePreset(req)
            return {"status": "success"}
        except Exception as e:
            raise ValueError(f"删除预置点失败: {e}")

    def remove_presets_bulk(self, db: Session, video_id: int, preset_tokens: list[str]):
        if not preset_tokens:
            raise ValueError("preset_tokens 不能为空")

        db_video = db.query(VideoDevice).filter(VideoDevice.id == video_id).first()
        if not db_video:
            raise ValueError("Device not found")

        if self._is_ezviz_ptz(db_video):
            unique_tokens: list[str] = list(dict.fromkeys([str(t) for t in preset_tokens if str(t).strip()]))
            deleted_tokens: list[str] = []
            failed_tokens: list[str] = []
            for preset_token in unique_tokens:
                try:
                    self.remove_preset(db, video_id, preset_token)
                    deleted_tokens.append(preset_token)
                except Exception as e:
                    logger.warning(f"Bulk remove preset failed video_id={video_id}, token={preset_token}: {e}")
                    failed_tokens.append(preset_token)
            return {
                "total": len(unique_tokens),
                "deleted": len(deleted_tokens),
                "failed": len(failed_tokens),
                "deleted_tokens": deleted_tokens,
                "failed_tokens": failed_tokens,
            }

        _, _, ptz, _, token = self._create_ptz_and_media(db, video_id)
        unique_tokens: list[str] = list(dict.fromkeys([str(t) for t in preset_tokens if str(t).strip()]))
        if not unique_tokens:
            raise ValueError("没有有效的预置点 token")

        deleted_tokens: list[str] = []
        failed_tokens: list[str] = []

        for preset_token in unique_tokens:
            req = {'ProfileToken': token, 'PresetToken': preset_token}
            try:
                ptz.RemovePreset(req)
                deleted_tokens.append(preset_token)
            except Exception as e:
                logger.warning(f"Bulk remove preset failed video_id={video_id}, token={preset_token}: {e}")
                failed_tokens.append(preset_token)

        return {
            "total": len(unique_tokens),
            "deleted": len(deleted_tokens),
            "failed": len(failed_tokens),
            "deleted_tokens": deleted_tokens,
            "failed_tokens": failed_tokens,
        }

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

        db_video = db.query(VideoDevice).filter(VideoDevice.id == video_id).first()
        if not db_video:
            raise ValueError("Device not found")

        available_items = self.list_presets(db, video_id)
        available = {item["token"] for item in available_items}
        if available:
            missing = [token for token in preset_tokens if token not in available]
            if missing:
                raise ValueError(f"以下预置点不存在: {', '.join(missing)}")
        elif not self._is_ezviz_ptz(db_video):
            raise ValueError("未查询到可用预置点，请先创建预置点")
        else:
            logger.warning(
                "EZVIZ preset list unavailable for video_id=%s, skip strict cruise validation",
                video_id,
            )

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
            stream_protocol=self._normalize_stream_protocol(camera_data.stream_protocol),
            platform_type=(camera_data.platform_type or "onvif"),
            access_source=(camera_data.access_source or "local"),
            ptz_source=(camera_data.ptz_source or "onvif"),
            device_serial=camera_data.device_serial,
            channel_no=camera_data.channel_no or 1,
            supports_ptz=1,
            supports_preset=1,
            supports_cruise=1,
            supports_zoom=1,
            supports_focus=0,
            weekly_quota_bytes=int(getattr(camera_data, "weekly_quota_bytes", None) or DEFAULT_WEEKLY_QUOTA_BYTES),
            sleeping=self._normalize_flag(getattr(camera_data, "sleeping", False)),
            privacy_enabled=self._normalize_flag(getattr(camera_data, "privacy_enabled", False)),
            storage_abnormal=self._normalize_flag(getattr(camera_data, "storage_abnormal", False)),
            low_battery=self._normalize_flag(getattr(camera_data, "low_battery", False)),
            weak_signal=self._normalize_flag(getattr(camera_data, "weak_signal", False)),
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
        payload = video_data.model_dump()
        payload["stream_protocol"] = self._normalize_stream_protocol(payload.get("stream_protocol"))
        payload["platform_type"] = payload.get("platform_type") or "onvif"
        payload["access_source"] = payload.get("access_source") or "local"
        payload["ptz_source"] = payload.get("ptz_source") or "onvif"
        payload["channel_no"] = payload.get("channel_no") or 1
        new_video = VideoDevice(**payload)
        db.add(new_video)
        db.commit()
        db.refresh(new_video)
        return new_video
    
    def get_videos(self, db: Session, skip: int = 0, limit: int = 100):
        return db.query(VideoDevice).offset(skip).limit(limit).all()

    def update_video(self, db: Session, video_id: int, video_data: VideoUpdate):
        db_video = db.query(VideoDevice).filter(VideoDevice.id == video_id).first()
        if not db_video: return None
        update_payload = video_data.model_dump(exclude_unset=True)
        if "stream_protocol" in update_payload:
            update_payload["stream_protocol"] = self._normalize_stream_protocol(update_payload.get("stream_protocol"))

        for key, value in update_payload.items():
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
            if video_id in EZVIZ_PTZ_LAST_DIRECTION:
                del EZVIZ_PTZ_LAST_DIRECTION[video_id]
            if video_id in EZVIZ_PTZ_LAST_STOP_AT:
                del EZVIZ_PTZ_LAST_STOP_AT[video_id]
            return True
        return False

    def get_stream_url(self, db: Session, video_id: int):
        stream_info = self.get_stream_info(db, video_id)
        if not stream_info:
            return None
        return stream_info.get("url")
        
    def ptz_move(self, db: Session, video_id: int, direction: str, speed: float = 0.5, duration: float = 0.5):
        try:
            self.ptz_start_move(db, video_id, direction, speed)
            time.sleep(duration)
            self.ptz_stop_move(db, video_id)
            return {"status": "success"}
        except Exception as e:
            raise ValueError(f"Move error: {e}")

    def zoom_start_move(self, db: Session, video_id: int, direction: str, speed: float = 0.5):
        if direction not in {"zoom_in", "zoom_out"}:
            raise ValueError("Invalid zoom direction. Use zoom_in or zoom_out")
        return self.ptz_start_move(db, video_id, direction, speed)

    def zoom_stop_move(self, db: Session, video_id: int):
        return self.ptz_stop_move(db, video_id)

    def zoom_move(self, db: Session, video_id: int, direction: str, speed: float = 0.5, duration: float = 0.5):
        if direction not in {"zoom_in", "zoom_out"}:
            raise ValueError("Invalid zoom direction. Use zoom_in or zoom_out")
        return self.ptz_move(db, video_id, direction, speed, duration)

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

    def _get_temp_playback_root(self) -> str:
        temp_root = os.path.join(self._get_playback_video_root(), "temp_cache")
        os.makedirs(temp_root, exist_ok=True)
        return temp_root

    def _get_rtsp_url_for_device(self, db_video: VideoDevice) -> Optional[str]:
        if getattr(db_video, "rtsp_url", None) and str(db_video.rtsp_url).lower().startswith("rtsp://"):
            return db_video.rtsp_url

        if db_video.stream_url and str(db_video.stream_url).lower().startswith("rtsp://"):
            return db_video.stream_url

        if db_video.ip_address and db_video.username and db_video.password:
            return f"rtsp://{db_video.username}:{db_video.password}@{db_video.ip_address}:554/Streaming/Channels/1"

        return None

    def _get_ezviz_recordable_url(self, db_video: VideoDevice) -> Optional[str]:
        """为云设备获取可被 FFmpeg 直接录制的地址（优先 flv/hls/rtmp）。"""
        device_serial = str(getattr(db_video, "device_serial", "") or "").strip()
        if not device_serial:
            return None

        channel_no = int(getattr(db_video, "channel_no", None) or 1)
        protocol_candidates = [4, 2, 3, 1]  # flv, hls, rtmp, ezopen
        paths = ["/api/lapp/live/address/get", "/api/lapp/v2/live/address/get"]

        for protocol_code in protocol_candidates:
            payload = {
                "deviceSerial": device_serial,
                "channelNo": channel_no,
                "protocol": protocol_code,
                "expireTime": 3600,
            }

            body = None
            for path in paths:
                try:
                    body = self._call_ezviz_api(path, payload)
                    break
                except Exception:
                    body = None

            if body is None:
                continue

            data = body.get("data") or {}
            url = (
                data.get("url")
                or data.get("liveAddress")
                or data.get("flv")
                or data.get("hls")
                or data.get("rtmp")
                or data.get("ezopen")
                or ""
            )
            lower_url = str(url).lower()
            if not url:
                continue
            if lower_url.startswith("ezopen://"):
                continue
            if lower_url.startswith("http://") or lower_url.startswith("https://") or lower_url.startswith("rtmp://") or lower_url.startswith("rtsp://"):
                return str(url)

        return None

    def _get_record_source_for_device(self, db_video: VideoDevice) -> Optional[str]:
        if self._is_ezviz_access(db_video):
            ezviz_url = self._get_ezviz_recordable_url(db_video)
            if ezviz_url:
                return ezviz_url

        rtsp_url = self._get_rtsp_url_for_device(db_video)
        if rtsp_url:
            return rtsp_url

        return None

    def start_ffmpeg_recording(self, video_id: int, source_url: str):
        if not source_url:
            logger.warning(f"录像启动失败，video_id={video_id} 缺少可录制地址")
            return None

        # 如果同一路录像进程正在运行且源地址未变，不要重启。
        existing = RECORDING_PROCESSES.get(video_id)
        if isinstance(existing, dict):
            existing_process = existing.get("process")
            existing_source = existing.get("source_url") or existing.get("rtsp_url")
            if existing_process and existing_process.poll() is None and existing_source == source_url:
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

        source_lower = str(source_url).lower()
        input_options: list[str] = []
        if source_lower.startswith("rtsp://"):
            input_options.extend(["-rtsp_transport", "tcp"])

        command = [
            ffmpeg_path,
            "-y",
            *input_options,
            "-use_wallclock_as_timestamps", "1",
            "-i", source_url,
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-c:v", "copy",
            "-c:a", "aac",
            "-f", "segment",
            "-segment_time", str(RECORD_SEGMENT_SECONDS),
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
                "source_url": source_url,
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

            # 录像按固定分段时长切片，至少等待一个完整分段周期再参与拼接，
            # 避免把仍在写入中的当前分段加入 concat。
            seg_start = self._parse_segment_start(file_path)
            if seg_start:
                if (datetime.now() - seg_start).total_seconds() < (RECORD_SEGMENT_SECONDS + RECORD_SEGMENT_SAFE_MARGIN_SECONDS):
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
            record_source = self._get_record_source_for_device(v)
            if record_source:
                self.start_ffmpeg_recording(v.id, record_source)

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

            seg_end = seg_start + timedelta(seconds=RECORD_SEGMENT_SECONDS)
            if seg_end <= start_dt or seg_start >= end_dt:
                continue
            if not self._is_segment_usable(seg_path):
                continue

            candidates.append((seg_path, seg_start, seg_end))

        return candidates

    def _floor_to_archive_slot(self, dt: datetime) -> datetime:
        floored_hour = dt.hour - (dt.hour % PLAYBACK_ARCHIVE_WINDOW_HOURS)
        return dt.replace(hour=floored_hour, minute=0, second=0, microsecond=0)

    def _auto_archive_periodic_playback(self, video_id: int):
        now_ts = time.time()
        last_run_at = PERIODIC_ARCHIVE_LAST_RUN_AT.get(video_id, 0.0)
        if now_ts - last_run_at < 60:
            return
        PERIODIC_ARCHIVE_LAST_RUN_AT[video_id] = now_ts

        now = datetime.now()
        current_slot_start = self._floor_to_archive_slot(now)
        lookback_start = current_slot_start - timedelta(hours=PLAYBACK_ARCHIVE_LOOKBACK_HOURS)

        record_root = self._get_record_root()
        device_root = os.path.join(record_root, str(video_id))
        if not os.path.isdir(device_root):
            return

        periodic_slots: set[datetime] = set()
        for seg_path in glob.glob(os.path.join(device_root, "*.mp4")):
            seg_start = self._parse_segment_start(seg_path)
            if not seg_start:
                continue
            slot_start = self._floor_to_archive_slot(seg_start)
            if slot_start < lookback_start or slot_start >= current_slot_start:
                continue
            periodic_slots.add(slot_start)

        if not periodic_slots:
            return

        output_root = self._get_playback_video_root()
        for slot_start in sorted(periodic_slots):
            slot_end = slot_start + timedelta(hours=PLAYBACK_ARCHIVE_WINDOW_HOURS)
            existing_pattern = os.path.join(
                output_root,
                f"periodic_{PLAYBACK_ARCHIVE_WINDOW_HOURS}h_{slot_start.strftime('%Y%m%d_%H%M%S')}_{video_id}_{slot_start.strftime('%Y%m%d_%H%M%S')}_{slot_end.strftime('%Y%m%d_%H%M%S')}.mp4",
            )
            if os.path.exists(existing_pattern):
                continue

            try:
                self.save_playback_clip(
                    video_id,
                    slot_start,
                    slot_end,
                    output_type="playback",
                    filename_prefix=f"periodic_{PLAYBACK_ARCHIVE_WINDOW_HOURS}h_{slot_start.strftime('%Y%m%d_%H%M%S')}",
                )
            except Exception as e:
                logger.debug(
                    "periodic archive skip video_id=%s slot=%s reason=%s",
                    video_id,
                    slot_start.strftime("%Y-%m-%d %H:%M:%S"),
                    str(e),
                )

    def list_recording_segments(self, video_id: int, limit: int = 72):
        self._auto_archive_periodic_playback(video_id)
        record_root = self._get_record_root()
        device_root = os.path.join(record_root, str(video_id))
        if not os.path.isdir(device_root):
            return []

        segments = []
        for seg_path in sorted(glob.glob(os.path.join(device_root, "*.mp4")), reverse=True):
            seg_start = self._parse_segment_start(seg_path)
            if not seg_start:
                continue
            if not self._is_segment_usable(seg_path):
                continue

            seg_end = seg_start + timedelta(seconds=RECORD_SEGMENT_SECONDS)
            segments.append({
                "name": os.path.basename(seg_path),
                "start_time": seg_start.strftime("%Y-%m-%d %H:%M:%S"),
                "end_time": seg_end.strftime("%Y-%m-%d %H:%M:%S"),
                "duration_seconds": RECORD_SEGMENT_SECONDS,
                "size_bytes": int(os.path.getsize(seg_path)),
                "web_path": self._to_static_web_path(seg_path),
            })

            if len(segments) >= max(1, min(limit, 720)):
                break

        return segments

    def save_playback_clip(self, video_id: int, start_time: datetime | str, end_time: datetime | str, output_type: str = "playback", filename_prefix: Optional[str] = None, alarm_time: Optional[datetime] = None, details: Optional[Dict] = None):
        start_dt = self._parse_datetime_input(start_time)
        end_dt = self._parse_datetime_input(end_time)
        if end_dt <= start_dt:
            raise ValueError("结束时间必须大于开始时间")

        segments = self._collect_segments_for_timerange(video_id, start_dt, end_dt)
        if not segments:
            raise ValueError("所选时间段没有可用录像分段")

        if output_type == "alarm":
            output_root = self._get_alarm_video_root()
        elif output_type == "temp":
            output_root = self._get_temp_playback_root()
        else:
            output_root = self._get_playback_video_root()
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

            # --- 报警视频标注增强 ---
            vf_filter = None
            if output_type == "alarm" and details and alarm_time:
                try:
                    boxes = details.get("boxes", [])
                    if boxes:
                        rel_alarm_time = (alarm_time - start_dt).total_seconds()
                        start_visible = max(0, rel_alarm_time - 5)
                        # 确保显示时长足够（前后 5 秒，一共 10 秒）
                        end_visible = rel_alarm_time + 5
                        
                        # Windows FFmpeg 特殊路径转义：要把 ':' 转义成 '\:'
                        font_path = "C\\:/Windows/Fonts/simhei.ttf"
                        filter_chains = []
                        for box in boxes:
                            coords = box.get("coords")
                            if not coords or len(coords) < 4: continue
                            x1, y1, x2, y2 = [int(v) for v in coords[:4]]
                            label = box.get("msg") or box.get("type", "报警")
                            score = box.get("score")
                            if score: label = f"{label} {score:.0%}"
                            
                            # 颜色映射 (ffmpeg 格式)
                            color = "orange"
                            if "未佩戴" in label or "head" in label.lower(): color = "red"
                            elif "helmet" in label.lower() or "合规" in label: color = "green"
                            
                            # 添加绘制框和文字的滤镜链 (注意转义)
                            safe_label = label.replace(":", "\\:").replace("'", "")
                            filter_chains.append(
                                f"drawbox=x={x1}:y={y1}:w={x2-x1}:h={y2-y1}:color={color}@0.8:t=4:enable='between(t,{start_visible:.1f},{end_visible:.1f})'"
                            )
                            # 如果字体文件不存在，drawtext 会报错导致整个 vf 失败，所以加 try/catch 或用默认字体
                            filter_chains.append(
                                f"drawtext=text='{safe_label}':x={x1}:y={y1-30 if y1 > 35 else y1+10}:fontsize=28:fontfile='{font_path}':fontcolor=white:box=1:boxcolor={color}@0.6:enable='between(t,{start_visible:.1f},{end_visible:.1f})'"
                            )
                        if filter_chains:
                            vf_filter = ",".join(filter_chains)
                            logger.info(f"Generated alarm vf_filter (len={len(vf_filter)}) for video_id={video_id}")
                except Exception as e:
                    logger.warning(f"Failed to build annotation filters: {e}")

            trim_cmd = [
                ffmpeg_path,
                "-y",
                "-ss", f"{clip_offset:.3f}",
                "-i", concat_output_path,
                "-t", f"{clip_duration:.3f}",
            ]
            
            if vf_filter:
                # 有滤镜必须重编码
                trim_cmd.extend([
                    "-vf", vf_filter,
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-c:a", "copy",
                ])
            else:
                trim_cmd.extend(["-c", "copy"])
                
            trim_cmd.append(final_output_path)
            
            trim_proc = subprocess.run(trim_cmd, capture_output=True, text=True)
            if trim_proc.returncode != 0:
                # 即使加了滤镜失败了，也尝试用 copy 兜底输出一个普通视频
                logger.warning(f"Trim with annotations failed, falling back to copy: {trim_proc.stderr}")
                trim_fallback_cmd = [
                    ffmpeg_path,
                    "-y",
                    "-ss", f"{clip_offset:.3f}",
                    "-i", concat_output_path,
                    "-t", f"{clip_duration:.3f}",
                    "-c", "copy",
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

    def save_temp_cache_until_now(self, video_id: int):
        now = datetime.now().replace(microsecond=0)
        current_slot_start = self._floor_to_archive_slot(now)

        if now <= current_slot_start:
            start_dt = current_slot_start - timedelta(hours=PLAYBACK_ARCHIVE_WINDOW_HOURS)
        else:
            start_dt = current_slot_start

        if now <= start_dt:
            raise ValueError("当前时间窗口无可缓存内容")

        # 若当前窗口并非从起点就开始有分段（例如服务刚恢复），允许从窗口内最早可用分段开始生成。
        available_segments = self._collect_segments_for_timerange(video_id, start_dt, now)
        if not available_segments:
            raise ValueError("当前时间窗口无可缓存内容")
        effective_start_dt = max(start_dt, available_segments[0][1])

        result = self.save_playback_clip(
            video_id,
            effective_start_dt,
            now,
            output_type="temp",
            filename_prefix=f"tempcache_{effective_start_dt.strftime('%Y%m%d_%H%M%S')}",
        )
        self._prune_temp_cache_videos(video_id, keep_latest=3)
        result["cache_window_start"] = effective_start_dt.strftime("%Y-%m-%d %H:%M:%S")
        result["cache_window_end"] = now.strftime("%Y-%m-%d %H:%M:%S")
        result["archive_window_hours"] = PLAYBACK_ARCHIVE_WINDOW_HOURS
        return result

    def _prune_temp_cache_videos(self, video_id: int, keep_latest: int = 3):
        """每个设备仅保留最近 keep_latest 个临时缓存视频，超过则删除最早的。"""
        keep_latest = max(1, int(keep_latest))
        temp_root = self._get_temp_playback_root()
        if not os.path.isdir(temp_root):
            return

        matched_files: list[tuple[float, str]] = []
        pattern = os.path.join(temp_root, "*.mp4")
        for file_path in glob.glob(pattern):
            file_name = os.path.basename(file_path)
            if f"_{video_id}_" not in file_name:
                continue
            try:
                mtime = os.path.getmtime(file_path)
                matched_files.append((mtime, file_path))
            except Exception:
                continue

        if len(matched_files) <= keep_latest:
            return

        matched_files.sort(key=lambda item: item[0], reverse=True)
        stale_files = matched_files[keep_latest:]

        for _, stale_path in stale_files:
            try:
                if os.path.exists(stale_path):
                    os.remove(stale_path)
            except Exception as e:
                logger.warning(f"Failed to prune temp cache file: {stale_path}, reason: {e}")

    def _list_saved_videos(self, root_dir: str, video_id: int, limit: int = 120) -> list[dict]:
        if not os.path.isdir(root_dir):
            return []

        clips: list[dict] = []
        for file_path in sorted(glob.glob(os.path.join(root_dir, "*.mp4")), reverse=True):
            file_name = os.path.basename(file_path)
            if f"_{video_id}_" not in file_name:
                continue

            try:
                stat = os.stat(file_path)
                clips.append(
                    {
                        "name": file_name,
                        "size_bytes": int(stat.st_size),
                        "updated_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                        "web_path": self._to_static_web_path(file_path),
                    }
                )
            except Exception:
                continue

            if len(clips) >= max(1, min(limit, 500)):
                break

        return clips

    def list_saved_playback_videos(self, video_id: int, limit: int = 120) -> list[dict]:
        return self._list_saved_videos(self._get_playback_video_root(), video_id, limit)

    def list_saved_alarm_videos(self, video_id: int, limit: int = 120) -> list[dict]:
        return self._list_saved_videos(self._get_alarm_video_root(), video_id, limit)

    def list_temp_cache_videos(self, video_id: int, limit: int = 30) -> list[dict]:
        return self._list_saved_videos(self._get_temp_playback_root(), video_id, limit)
