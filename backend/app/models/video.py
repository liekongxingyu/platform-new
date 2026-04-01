from sqlalchemy import Column, Integer, String, Float, Text
from app.core.database import Base

class VideoDevice(Base):
    """
    视频设备模型，用于存储监控摄像头的信息。
    """
    __tablename__ = "video_devices"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), index=True, comment="摄像头名称")
    
    # 网络连接信息
    ip_address = Column(String(50), comment="设备IP地址")
    port = Column(Integer, default=80, comment="服务端口")
    username = Column(String(50), comment="登录用户名")
    password = Column(String(100), comment="登录密码")
    
    # 流媒体信息
    stream_url = Column(Text, comment="原始流地址 (RTSP/HLS/FLV)")
    rtsp_url = Column(Text, nullable=True, comment="摄像头RTSP地址")
    stream_protocol = Column(String(20), nullable=True, comment="拉流协议偏好: ezopen/hls/rtmp/flv")

    # 平台与来源路由
    platform_type = Column(String(20), nullable=True, comment="设备平台类型: onvif/ezviz")
    access_source = Column(String(20), nullable=True, comment="视频访问来源: local/cloud")
    ptz_source = Column(String(20), nullable=True, comment="PTZ 控制来源: onvif/ezviz")

    # 云平台设备标识
    device_serial = Column(String(100), nullable=True, comment="萤石设备序列号")
    channel_no = Column(Integer, nullable=True, default=1, comment="萤石通道号")

    # 能力标记
    supports_ptz = Column(Integer, default=1, comment="是否支持云台")
    supports_preset = Column(Integer, default=1, comment="是否支持预置点")
    supports_cruise = Column(Integer, default=1, comment="是否支持巡航")
    supports_zoom = Column(Integer, default=1, comment="是否支持变焦")
    supports_focus = Column(Integer, default=0, comment="是否支持焦距")
    
    # 地理位置信息 (用于在地图上标记)
    latitude = Column(Float, nullable=True, comment="纬度 (GCJ-02)")
    longitude = Column(Float, nullable=True, comment="经度 (GCJ-02)")
    
    # 状态与备注
    status = Column(String(20), default="offline", comment="设备状态: online, offline")
    remark = Column(String(255), comment="备注信息")
    
    # 启用状态
    is_active = Column(Integer, default=1, comment="是否启用 1-启用 0-禁用")