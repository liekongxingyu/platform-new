from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum

class VideoStatus(str, Enum):
    """设备在线状态枚举"""
    ONLINE = "online"
    OFFLINE = "offline"

class VideoBase(BaseModel):
    """
    基础视频设备模型，包含公共字段。
    参考 fence_schema.py 的设计模式。
    """
    name: str = Field(..., description="摄像头名称")
    ip_address: str = Field(..., description="设备IP地址")
    port: int = Field(80, description="服务端口")
    username: Optional[str] = Field(None, description="登录用户名")
    password: Optional[str] = Field(None, description="登录密码")
    
    stream_url: Optional[str] = Field(None, description="流地址 (RTSP/HLS/FLV)")
    rtsp_url: Optional[str] = Field(None, description="摄像头RTSP地址")
    
    latitude: Optional[float] = Field(None, description="纬度 (GCJ-02)")
    longitude: Optional[float] = Field(None, description="经度 (GCJ-02)")
    
    remark: Optional[str] = Field(None, description="备注信息")

class VideoCreate(VideoBase):
    """用于创建视频设备的模型"""
    status: VideoStatus = Field(default=VideoStatus.OFFLINE)
    pass

class VideoUpdate(BaseModel):
    """
    用于更新视频设备的模型。
    所有字段均为可选，参考 FenceUpdate。
    """
    name: Optional[str] = None
    ip_address: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    stream_url: Optional[str] = None
    rtsp_url: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    status: Optional[VideoStatus] = None
    remark: Optional[str] = None
    is_active: Optional[int] = None

class VideoOut(VideoBase):
    """
    用于 API 返回的序列化模型。
    包含数据库生成的 ID 和状态。
    """
    id: int

    # 兼容历史数据: 某些旧记录可能缺失网络字段，避免响应校验导致 500
    ip_address: Optional[str] = None
    port: Optional[int] = None
    rtsp_url: Optional[str] = None
    
    # --- 修改部分开始 ---
    # 原代码: status: VideoStatus
    # 修改为: 允许为空，并设置默认值为 None
    status: Optional[VideoStatus] = None
    
    # 原代码: is_active: int
    # 修改为: 允许为空，并设置默认值为 None
    is_active: Optional[int] = None
    # --- 修改部分结束 ---

    class Config:
        # 允许从 SQLAlchemy ORM 对象直接转换，参考 fence_schema.py
        from_attributes = True

class CameraCreateRequest(BaseModel):
    """
    用于从前端动态添加摄像头的请求模型
    """
    name: str = Field(..., description="摄像头名称")
    rtsp_url: str = Field(..., description="摄像头的RTSP地址")
    ip_address: Optional[str] = Field(None, description="设备IP地址 (可选)")
    port: Optional[int] = Field(None, description="服务端口 (可选)")
    username: Optional[str] = Field(None, description="登录用户名 (可选)")
    password: Optional[str] = Field(None, description="登录密码 (可选)")
    latitude: Optional[float] = Field(None, description="纬度 (可选)")
    longitude: Optional[float] = Field(None, description="经度 (可选)")
    remark: Optional[str] = Field(None, description="备注信息 (可选)")

class PTZDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"

class PTZControlRequest(BaseModel):
    """
    前端控制云台的请求模型
    direction: 上下左右
    speed: 可选速度 [0.1, 1.0]
    duration: 持续时间秒 (默认 0.5s)
    """
    direction: PTZDirection
    speed: Optional[float] = Field(0.5, ge=0.1, le=1.0)
    duration: Optional[float] = Field(0.5, ge=0.1, le=5.0)


class PresetCreateRequest(BaseModel):
    name: Optional[str] = Field(None, description="预置点名称")
    token: Optional[str] = Field(None, description="预置点 Token，可选")


class PresetGotoRequest(BaseModel):
    speed: Optional[float] = Field(0.5, ge=0.1, le=1.0)


class PTZPresetItem(BaseModel):
    token: str
    name: str


class PresetBulkDeleteRequest(BaseModel):
    preset_tokens: list[str] = Field(..., min_length=1, max_length=2000, description="待删除预置点 Token 列表")


class PresetBulkDeleteResponse(BaseModel):
    total: int
    deleted: int
    failed: int
    deleted_tokens: list[str]
    failed_tokens: list[str]


class CruiseStartRequest(BaseModel):
    preset_tokens: list[str] = Field(..., min_length=2, description="巡航预置点 Token 列表")
    dwell_seconds: Optional[float] = Field(8.0, ge=1.0, le=120.0)
    rounds: Optional[int] = Field(None, ge=1, le=1000, description="巡航轮次，None 表示无限循环")