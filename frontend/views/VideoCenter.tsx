import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  Search,
  Plus,
  Trash2,
  MonitorPlay,
  Maximize2,
  X,
  Camera,
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  Grid3x3,
  Grid2x2,
  LayoutGrid,
  Loader,
  Settings,
  Edit2,
  // --- ✅ 新增图标（已合并，无重复）---
  Shield,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";

import VideoPlayer from "../src/components/VideoPlayer";
import PTZControlPanel from "../src/components/PTZControlPanel";
import {
  getAllVideos,
  createVideo,
  deleteVideo,
  getVideoStreamUrl,
  addCameraViaRTSP,
  updateVideo,
  ptzControl,
  Video,
  VideoCreate,
  VideoUpdate,
  // --- ✅ 新增 API（已合并，无重复）---
  startAIMonitoring,
  stopAIMonitoring,
  getAIRules,
  AIRule,
  StreamUrl,
} from "../src/api/videoApi";
import { API_BASE_URL } from "../src/api/config";

const getAlarmWebSocketUrl = () => {
  try {
    const apiUrl = new URL(API_BASE_URL);
    const wsProtocol = apiUrl.protocol === "https:" ? "wss:" : "ws:";
    return `${wsProtocol}//${apiUrl.host}/ws/alarm`;
  } catch {
    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${wsProtocol}//${window.location.hostname}:9000/ws/alarm`;
  }
};

const formatWorkDuration = (seconds?: number) => {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) {
    return "--";
  }

  const totalSeconds = Math.floor(seconds);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const secs = totalSeconds % 60;

  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
};

type VideoWithWorkDuration = Video & {
  total_work_seconds?: number;
  total_work_duration_seconds?: number;
  uptime_seconds?: number;
  runtime_seconds?: number;
};

const WORK_DURATION_STORAGE_KEY = "video_center_work_duration_by_device";

const getVideoWorkDurationSeconds = (video?: Video | null) => {
  if (!video) return undefined;

  const source = video as VideoWithWorkDuration;
  const candidates = [
    source.total_work_seconds,
    source.total_work_duration_seconds,
    source.uptime_seconds,
    source.runtime_seconds,
  ];

  for (const val of candidates) {
    if (typeof val === "number" && Number.isFinite(val) && val >= 0) {
      return Math.floor(val);
    }
  }

  return undefined;
};

const loadWorkDurationMap = (): Record<number, number> => {
  if (typeof window === "undefined") return {};

  try {
    const raw = window.localStorage.getItem(WORK_DURATION_STORAGE_KEY);
    if (!raw) return {};

    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (!parsed || typeof parsed !== "object") return {};

    const result: Record<number, number> = {};
    for (const [idStr, val] of Object.entries(parsed)) {
      const id = Number(idStr);
      const seconds = Number(val);
      if (Number.isInteger(id) && Number.isFinite(seconds) && seconds >= 0) {
        result[id] = Math.floor(seconds);
      }
    }
    return result;
  } catch {
    return {};
  }
};

const VIDEO_CENTER_STYLE_ID = "video-center-cyber-style";
if (typeof document !== "undefined" && !document.getElementById(VIDEO_CENTER_STYLE_ID)) {
  const styleEl = document.createElement("style");
  styleEl.id = VIDEO_CENTER_STYLE_ID;
  styleEl.textContent = `
    @keyframes vc-pulse {
      0%, 100% { opacity: 0.55; box-shadow: 0 0 6px rgba(96, 165, 250, 0.55); }
      50% { opacity: 1; box-shadow: 0 0 16px rgba(96, 165, 250, 0.95); }
    }
    @keyframes vc-scan {
      0% { transform: translateY(-140%); }
      100% { transform: translateY(220%); }
    }
    .vc-scrollbar::-webkit-scrollbar {
      width: 8px;
      height: 8px;
    }
    .vc-scrollbar::-webkit-scrollbar-track {
      background: rgba(15, 23, 42, 0.3);
    }
    .vc-scrollbar::-webkit-scrollbar-thumb {
      background: linear-gradient(180deg, #38bdf8, #2563eb);
      border-radius: 999px;
    }
  `;
  document.head.appendChild(styleEl);
}

function CyberPanel({
  title,
  icon,
  actions,
  children,
  className = "",
}: {
  title: string;
  icon?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`relative rounded-md border border-blue-400/30 bg-slate-900/65 backdrop-blur-md shadow-[inset_0_0_30px_rgba(59,130,246,0.12),0_8px_28px_rgba(2,6,23,0.6)] overflow-hidden ${className}`}
    >
      <div className="pointer-events-none absolute inset-0 opacity-40" style={{
        background: "linear-gradient(180deg, rgba(148,163,184,0) 0%, rgba(14,116,144,0.14) 45%, rgba(148,163,184,0) 100%)",
        animation: "vc-scan 6s linear infinite",
      }} />

      <div className="absolute -top-px -left-px h-3 w-3 border-l-2 border-t-2 border-cyan-300" />
      <div className="absolute -top-px -right-px h-3 w-3 border-r-2 border-t-2 border-cyan-300" />
      <div className="absolute -bottom-px -left-px h-3 w-3 border-l-2 border-b-2 border-cyan-300" />
      <div className="absolute -bottom-px -right-px h-3 w-3 border-r-2 border-b-2 border-cyan-300" />

      <div className="relative z-10 flex items-center justify-between border-b border-blue-400/20 bg-gradient-to-r from-blue-500/20 via-blue-300/5 to-transparent px-4 py-2.5">
        <div className="flex items-center gap-2 text-sky-100 font-semibold tracking-[0.12em] text-sm">
          <span className="h-2 w-2 rounded-full bg-cyan-300" style={{ animation: "vc-pulse 2.2s ease-in-out infinite" }} />
          {icon}
          <span>{title}</span>
        </div>
        {actions}
      </div>

      <div className="relative z-10">{children}</div>
    </div>
  );
}

export default function VideoCenter() {
  type AlarmBox = {
    type: string;
    msg: string;
    score: number;
    coords: [number, number, number, number];
    track_id: number;
  };

  // --- 状态管理 ---
  const [activeAlgos, setActiveAlgos] = useState<string[]>([]); 
  const [algos, setAlgos] = useState<Array<{ id: string; name: string }>>([
    { id: "helmet", name: "安全帽类" },
    { id: "signage", name: "现场标识类" },
    { id: "supervisor_count", name: "现场监督人数统计" },
    { id: "ladder_angle", name: "梯子角度类" },
    { id: "hole_curb", name: "孔口挡坎违规类" },
    { id: "unauthorized_person", name: "围栏入侵管理类" },
  ]);
  const [devices, setDevices] = useState<Video[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState("");
  const [maximizedVideo, setMaximizedVideo] = useState<Video | null>(null);
  const [streamUrl, setStreamUrl] = useState<string | null>(null);
  const [streamInfo, setStreamInfo] = useState<StreamUrl | null>(null);
  
  // --- ✅ 新增 AI 监控状态 ---
  const [isAIEnabled, setIsAIEnabled] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);

  // --- ✅ 新增：警报弹窗状态 ---
  const [alarmAlert, setAlarmAlert] = useState<{
    type: string;
    msg: string;
    score: number;
    timestamp: number;
  } | null>(null);
  const [alarmBoxes, setAlarmBoxes] = useState<AlarmBox[]>([]);

  // --- 分页与网格状态 ---
  const [currentPage, setCurrentPage] = useState(1);
  const [itemsPerPage, setItemsPerPage] = useState(9);
  const [gridInputValue, setGridInputValue] = useState("9");
  const [previewStreams, setPreviewStreams] = useState<Record<number, StreamUrl>>({});
  const [previewLoading, setPreviewLoading] = useState<Record<number, boolean>>({});
  const [previewErrors, setPreviewErrors] = useState<Record<number, string>>({});
  const [workDurationByDevice, setWorkDurationByDevice] = useState<Record<number, number>>(loadWorkDurationMap);

  // --- 弹窗与表单状态 ---
  const [showAddModal, setShowAddModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [selectedDevice, setSelectedDevice] = useState<Video | null>(null);
  const [editingDevice, setEditingDevice] = useState<Video | null>(null);
  const alarmWsRef = useRef<WebSocket | null>(null);
  const alarmReconnectTimerRef = useRef<number | null>(null);
  const alarmCloseTimerRef = useRef<number | null>(null);
  const alarmBoxesClearTimerRef = useRef<number | null>(null);
  const aiCanvasRef = useRef<HTMLCanvasElement | null>(null);

  const [newDeviceForm, setNewDeviceForm] = useState<VideoCreate>({
    name: "",
    ip_address: "",
    port: 80,
    username: "",
    password: "",
    stream_url: "",
    stream_protocol: "flv",
    platform_type: "onvif",
    access_source: "local",
    ptz_source: "onvif",
    device_serial: "",
    channel_no: 1,
    status: "offline",
    remark: "",
  });

  const [editDeviceForm, setEditDeviceForm] = useState<VideoUpdate>({
    name: "",
    ip_address: "",
    port: 80,
    username: "",
    password: "",
    stream_url: "",
    stream_protocol: "flv",
    platform_type: "onvif",
    access_source: "local",
    ptz_source: "onvif",
    device_serial: "",
    channel_no: 1,
    status: "offline",
    remark: "",
  });

  const handlePtzSuccess = useCallback((msg: string) => {
    console.log(msg);
  }, []);

  const handlePtzError = useCallback((err: string) => {
    console.error(err);
  }, []);

  const currentWorkDurationSeconds = maximizedVideo
    ? workDurationByDevice[maximizedVideo.id] ?? getVideoWorkDurationSeconds(maximizedVideo)
    : undefined;

  useEffect(() => {
    if (!devices.length) return;

    setWorkDurationByDevice((prev) => {
      const next = { ...prev };
      let changed = false;

      for (const device of devices) {
        const backendSeconds = getVideoWorkDurationSeconds(device);
        if (typeof backendSeconds === "number") {
          const localSeconds = next[device.id];
          if (typeof localSeconds !== "number" || backendSeconds > localSeconds) {
            next[device.id] = backendSeconds;
            changed = true;
          }
        } else if (typeof next[device.id] !== "number") {
          next[device.id] = 0;
          changed = true;
        }
      }

      return changed ? next : prev;
    });
  }, [devices]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setWorkDurationByDevice((prev) => {
        if (!devices.length) return prev;

        const next = { ...prev };
        let changed = false;

        for (const device of devices) {
          if (typeof next[device.id] !== "number") {
            next[device.id] = getVideoWorkDurationSeconds(device) ?? 0;
            changed = true;
          }

          const isWorking =
            device.status === "online" ||
            (!!maximizedVideo && maximizedVideo.id === device.id && !!streamUrl);

          if (isWorking) {
            next[device.id] += 1;
            changed = true;
          }
        }

        return changed ? next : prev;
      });
    }, 1000);

    return () => window.clearInterval(timer);
  }, [devices, maximizedVideo, streamUrl]);

  useEffect(() => {
    try {
      window.localStorage.setItem(WORK_DURATION_STORAGE_KEY, JSON.stringify(workDurationByDevice));
    } catch {
      // ignore localStorage write failures
    }
  }, [workDurationByDevice]);

  // --- ✅ 新增：切换摄像头时重置 AI 状态 ---
  useEffect(() => {
    setIsAIEnabled(false);
  }, [maximizedVideo]);

  // --- ✅ 改进：AI 开关处理逻辑 ---

  // 1. 处理单个功能的开启/关闭
  const handleSingleAI = async (type: string) => {
  if (!maximizedVideo) return;
  setAiLoading(true);

  try {
    const deviceId = String(maximizedVideo.id);
    const rtsp = (maximizedVideo.rtsp_url || maximizedVideo.stream_url || "").trim();
    const isEzvizCloud =
      String(maximizedVideo.platform_type || "").toLowerCase() === "ezviz" ||
      String(maximizedVideo.access_source || "").toLowerCase() === "cloud" ||
      !!maximizedVideo.device_serial;

    if ((!rtsp || !rtsp.toLowerCase().startsWith("rtsp://")) && !isEzvizCloud) {
      alert("当前设备缺少有效 RTSP 地址（本地设备必填）");
      return;
    }

    const nextAlgos = activeAlgos.includes(type)
      ? activeAlgos.filter(t => t !== type)
      : [...activeAlgos, type];

    // ✅ 关键：不要循环 start 多次；后端按 device_id 只允许一个监控线程
    await stopAIMonitoring(deviceId);
    if (nextAlgos.length > 0) {
      await startAIMonitoring(deviceId, isEzvizCloud ? "" : rtsp, nextAlgos.join(","));
    }

    setActiveAlgos(nextAlgos);
    setIsAIEnabled(nextAlgos.length > 0);
  } catch (error) {
    console.error(`${type} 操作失败:`, error);
    alert("AI 服务同步失败");
  } finally {
    setAiLoading(false);
  }
};


  // 2. 处理一键全开启/全关闭
  const handleToggleAll = async (enable: boolean) => {
  if (!maximizedVideo) return;
  setAiLoading(true);

  try {
    const deviceId = String(maximizedVideo.id);
    const rtsp = (maximizedVideo.rtsp_url || maximizedVideo.stream_url || "").trim();
    const isEzvizCloud =
      String(maximizedVideo.platform_type || "").toLowerCase() === "ezviz" ||
      String(maximizedVideo.access_source || "").toLowerCase() === "cloud" ||
      !!maximizedVideo.device_serial;

    if ((!rtsp || !rtsp.toLowerCase().startsWith("rtsp://")) && !isEzvizCloud) {
      alert("当前设备缺少有效 RTSP 地址（本地设备必填）");
      return;
    }

    await stopAIMonitoring(deviceId);

    if (enable) {
      const all = algos.map(a => a.id);
      await startAIMonitoring(deviceId, isEzvizCloud ? "" : rtsp, all.join(","));
      setActiveAlgos(all);
      setIsAIEnabled(true);
    } else {
      setActiveAlgos([]);
      setIsAIEnabled(false);
    }
  } catch (error) {
    const msg = (error as any)?.message || "批量操作失败";
    alert(msg);
  } finally {
    setAiLoading(false);
  }
};


  // --- 初始化加载 ---
  useEffect(() => {
    fetchDevices();
    fetchAIRules();
  }, []);

  // ✅ 新增：播放警报音效
  const playAlarmSound = () => {
    // 创建简单的蜂鸣音（使用Web Audio API）
    try {
      const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();
      const now = audioContext.currentTime;
      
      // 创建4个频率的警报音
      for (let i = 0; i < 4; i++) {
        const osc = audioContext.createOscillator();
        const gain = audioContext.createGain();
        
        osc.connect(gain);
        gain.connect(audioContext.destination);
        
        // 快速升降的频率
        osc.frequency.setValueAtTime(800 + i * 200, now + i * 0.15);
        osc.frequency.setValueAtTime(400 + i * 100, now + i * 0.15 + 0.1);
        
        gain.gain.setValueAtTime(0.3, now + i * 0.15);
        gain.gain.setValueAtTime(0, now + i * 0.15 + 0.12);
        
        osc.start(now + i * 0.15);
        osc.stop(now + i * 0.15 + 0.12);
      }
    } catch (err) {
      console.warn("音频上下文创建失败:", err);
    }
  };

  const fetchAIRules = async () => {
    try {
      const rules: AIRule[] = await getAIRules();
      if (!rules.length) return;

      const mapped = rules.map((rule) => ({
        id: rule.key,
        name: rule.desc || rule.key,
      }));

      setAlgos(mapped);
    } catch (e) {
      console.warn("AI 规则加载失败，使用本地兜底列表", e);
    }
  };

  const normalizeSingleAlarmBox = (raw: any, fallback: any): AlarmBox | null => {
    if (!raw || typeof raw !== "object") return null;

    let coordsSource =
      raw.coords ||
      raw.bbox ||
      raw.xyxy ||
      raw.box ||
      (raw.rect && [raw.rect.left, raw.rect.top, raw.rect.right, raw.rect.bottom]);

    if (!Array.isArray(coordsSource) || coordsSource.length < 4) {
      const x1 = Number(raw.x1 ?? raw.left ?? raw.x ?? 0);
      const y1 = Number(raw.y1 ?? raw.top ?? raw.y ?? 0);
      const x2 = Number(raw.x2 ?? raw.right ?? (Number(raw.w) ? x1 + Number(raw.w) : 0));
      const y2 = Number(raw.y2 ?? raw.bottom ?? (Number(raw.h) ? y1 + Number(raw.h) : 0));
      coordsSource = [x1, y1, x2, y2];
    }

    const numericCoords = coordsSource.map((v: any) => Number(v)).filter(Number.isFinite);
    if (numericCoords.length < 4) return null;

    let x1 = numericCoords[0];
    let y1 = numericCoords[1];
    let x2 = numericCoords[2];
    let y2 = numericCoords[3];

    if (x2 < x1) [x1, x2] = [x2, x1];
    if (y2 < y1) [y1, y2] = [y2, y1];

    return {
      type: raw.type || fallback?.type || "未知警报",
      msg: raw.msg || fallback?.msg || "检测到异常",
      score: Number.isFinite(Number(raw.score)) ? Number(raw.score) : Number(fallback?.score) || 0,
      coords: [x1, y1, x2, y2],
      track_id: Number(raw.track_id ?? fallback?.track_id ?? 0),
    };
  };

  const normalizeAlarmBoxes = (data: any): AlarmBox[] => {
    if (!data || typeof data !== "object") return [];

    const candidates = [
      data.boxes,
      data.data?.boxes,
      data.payload?.boxes,
      data.detail?.boxes,
      data.result?.boxes,
      data.event?.boxes,
    ];

    for (const candidate of candidates) {
      if (!Array.isArray(candidate) || candidate.length === 0) continue;
      const normalized = candidate
        .map((box: any) => normalizeSingleAlarmBox(box, data))
        .filter((box: AlarmBox | null): box is AlarmBox => Boolean(box));
      if (normalized.length > 0) return normalized;
    }

    const flatBox = normalizeSingleAlarmBox(data, data);
    if (flatBox) return [flatBox];

    return [];
  };

  const parseAlarmPayload = (raw: any): { boxes: AlarmBox[]; alarmLike: any } => {
    const alarmLike = (raw?.data && typeof raw.data === "object" ? raw.data : raw) || {};
    const boxes = normalizeAlarmBoxes(raw);
    return { boxes, alarmLike };
  };

  useEffect(() => {
    if (!isAIEnabled) {
      if (alarmReconnectTimerRef.current) {
        window.clearTimeout(alarmReconnectTimerRef.current);
        alarmReconnectTimerRef.current = null;
      }
      if (alarmCloseTimerRef.current) {
        window.clearTimeout(alarmCloseTimerRef.current);
        alarmCloseTimerRef.current = null;
      }
      if (alarmBoxesClearTimerRef.current) {
        window.clearTimeout(alarmBoxesClearTimerRef.current);
        alarmBoxesClearTimerRef.current = null;
      }
      if (alarmWsRef.current) {
        alarmWsRef.current.close();
        alarmWsRef.current = null;
      }
      return;
    }

    const wsUrl = getAlarmWebSocketUrl();
    let disposed = false;

    const connect = () => {
      if (disposed) return;

      try {
        if (alarmWsRef.current) {
          alarmWsRef.current.close();
          alarmWsRef.current = null;
        }

        const ws = new WebSocket(wsUrl);
        alarmWsRef.current = ws;

        ws.onopen = () => {
          console.log("AI报警WebSocket已连接:", wsUrl);
        };

        ws.onmessage = (event) => {
          let data: any;
          try {
            data = typeof event.data === "string" ? JSON.parse(event.data) : event.data;
          } catch {
            return;
          }

          const { boxes, alarmLike } = parseAlarmPayload(data);
          const isAlarm = Boolean(
            boxes.length ||
              alarmLike?.alarm ||
              alarmLike?.is_alarm ||
              alarmLike?.alert ||
              alarmLike?.msg ||
              alarmLike?.type
          );
          if (!isAlarm) return;

          if (boxes.length) {
            setAlarmBoxes(boxes);

            if (alarmBoxesClearTimerRef.current) {
              window.clearTimeout(alarmBoxesClearTimerRef.current);
            }
            alarmBoxesClearTimerRef.current = window.setTimeout(() => {
              setAlarmBoxes([]);
            }, 4200);
          }

          const firstBox = boxes[0];
          setAlarmAlert({
            type: firstBox?.type || alarmLike?.type || "未知警报",
            msg: firstBox?.msg || alarmLike?.msg || "检测到异常",
            score: Number(firstBox?.score ?? alarmLike?.score ?? 0) || 0,
            timestamp: Date.now(),
          });

          playAlarmSound();

          if (alarmCloseTimerRef.current) {
            window.clearTimeout(alarmCloseTimerRef.current);
          }
          alarmCloseTimerRef.current = window.setTimeout(() => {
            setAlarmAlert(null);
          }, 3000);
        };

        ws.onerror = (err) => {
          console.error("AI WebSocket错误:", err);
        };

        ws.onclose = () => {
          console.log("AI报警连接关闭，准备重连");
          if (disposed) return;
          if (alarmReconnectTimerRef.current) {
            window.clearTimeout(alarmReconnectTimerRef.current);
          }
          alarmReconnectTimerRef.current = window.setTimeout(connect, 2000);
        };
      } catch (err) {
        console.error("AI WebSocket连接初始化失败:", err);
      }
    };

    connect();

    return () => {
      disposed = true;

      if (alarmReconnectTimerRef.current) {
        window.clearTimeout(alarmReconnectTimerRef.current);
        alarmReconnectTimerRef.current = null;
      }
      if (alarmCloseTimerRef.current) {
        window.clearTimeout(alarmCloseTimerRef.current);
        alarmCloseTimerRef.current = null;
      }
      if (alarmBoxesClearTimerRef.current) {
        window.clearTimeout(alarmBoxesClearTimerRef.current);
        alarmBoxesClearTimerRef.current = null;
      }

      if (alarmWsRef.current) {
        alarmWsRef.current.close();
        alarmWsRef.current = null;
      }
    };
  }, [isAIEnabled, maximizedVideo?.id]);

  const formatLocalDateTimeForApi = (date: Date) => {
    const pad = (v: number) => String(v).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  };

  const fetchDevices = async () => {
    try {
      setLoading(true);
      const data = await getAllVideos();
      setDevices(data);
      setError(null);
    } catch (e: any) {
      setError("无法加载设备。请确认后端服务已启动。");
    } finally {
      setLoading(false);
    }
  };

  // --- 逻辑处理 ---
  const handleSearch = (val: string) => {
    setSearchTerm(val);
    setCurrentPage(1);
  };

  const filteredDevices = devices.filter(
    (h) =>
      h.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      String(h.id).includes(searchTerm)
  );

  const totalPages = Math.ceil(filteredDevices.length / itemsPerPage) || 1;
  const currentVideos = filteredDevices.slice(
    (currentPage - 1) * itemsPerPage,
    currentPage * itemsPerPage
  );

  const handleShowStream = async (device: Video) => {
    try {
      // 全屏播放时总是拉取最新地址，避免设备配置切换后复用旧缓存。
      const info = await getVideoStreamUrl(device.id);
      setPreviewStreams((prev) => ({ ...prev, [device.id]: info }));
      setStreamInfo(info);
      setStreamUrl(info.url);
      setMaximizedVideo(device);
    } catch (err: any) {
      alert(`获取视频流失败: ${err.message}`);
    }
  };

  const loadPreviewStream = useCallback(
    async (device: Video) => {
      if (maximizedVideo?.id === device.id) {
        return;
      }
      if (!device || previewStreams[device.id] || previewLoading[device.id]) {
        return;
      }
      setPreviewLoading((prev) => ({ ...prev, [device.id]: true }));
      try {
        const data = await getVideoStreamUrl(device.id);
        setPreviewStreams((prev) => ({ ...prev, [device.id]: data }));
        setPreviewErrors((prev) => ({ ...prev, [device.id]: "" }));
      } catch (err: any) {
        setPreviewErrors((prev) => ({
          ...prev,
          [device.id]: err?.message || "加载失败",
        }));
      } finally {
        setPreviewLoading((prev) => ({ ...prev, [device.id]: false }));
      }
    },
    [maximizedVideo?.id, previewLoading, previewStreams]
  );

  useEffect(() => {
    if (maximizedVideo) {
      return;
    }
    currentVideos.forEach((device) => {
      if (device) {
        loadPreviewStream(device);
      }
    });
  }, [currentVideos, loadPreviewStream, maximizedVideo]);

  const handleGridInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setGridInputValue(value);

    if (value === "") return;

    const num = parseInt(value, 10);
    if (!isNaN(num) && num >= 1 && num <= 100) {
      setItemsPerPage(num);
      setCurrentPage(1);
    }
  };

  const handleVideoDoubleClick = async (device: Video) => {
    await handleShowStream(device);
  };

  const handleAddDevice = async () => {
    const isEzviz = (newDeviceForm.platform_type || "onvif") === "ezviz";
    if (!newDeviceForm.name) {
      alert("请填写必填字段：设备名称");
      return;
    }
    if (!isEzviz && !newDeviceForm.stream_url) {
      alert("本地设备请填写流地址");
      return;
    }
    if (isEzviz && !newDeviceForm.device_serial) {
      alert("萤石设备请填写设备序列号");
      return;
    }

    const commonPayload = {
      name: newDeviceForm.name,
      ip_address: newDeviceForm.ip_address || undefined,
      port: newDeviceForm.port,
      username: newDeviceForm.username,
      password: newDeviceForm.password,
      remark: newDeviceForm.remark,
      stream_protocol: newDeviceForm.stream_protocol,
      platform_type: newDeviceForm.platform_type,
      access_source: isEzviz ? "cloud" : "local",
      ptz_source: isEzviz ? "ezviz" : "onvif",
      device_serial: newDeviceForm.device_serial || undefined,
      channel_no: newDeviceForm.channel_no || 1,
    };

    try {
      const newDevice = isEzviz
        ? await createVideo({
            ...commonPayload,
            stream_url: "",
            rtsp_url: undefined,
            status: "online",
          })
        : await addCameraViaRTSP({
            ...commonPayload,
            rtsp_url: newDeviceForm.stream_url || "",
          });
      setDevices([newDevice, ...devices]);
      setShowAddModal(false);
      setNewDeviceForm({
        name: "",
        ip_address: "",
        port: 80,
        username: "",
        password: "",
        stream_url: "",
        stream_protocol: "flv",
        platform_type: "onvif",
        access_source: "local",
        ptz_source: "onvif",
        device_serial: "",
        channel_no: 1,
        status: "offline",
        remark: "",
      });
    } catch (err: any) {
      console.error("添加失败详情:", err);
      const errorMsg = err.message || JSON.stringify(err);
      alert(`添加失败: ${errorMsg}`);
    }
  };

  const handleEditClick = (device: Video, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingDevice(device);
    setEditDeviceForm({
      name: device.name,
      ip_address: device.ip_address || "",
      port: device.port || 80,
      username: device.username || "",
      password: device.password || "",
      stream_url: device.rtsp_url || "",
      stream_protocol: device.stream_protocol || "flv",
      platform_type: device.platform_type || "onvif",
      access_source: device.access_source || "local",
      ptz_source: device.ptz_source || "onvif",
      device_serial: device.device_serial || "",
      channel_no: device.channel_no || 1,
      status: device.status,
      remark: device.remark || "",
    });
    setShowEditModal(true);
  };

  const handleUpdateDevice = async () => {
    if (!editingDevice) return;
    const isEzviz = (editDeviceForm.platform_type || "onvif") === "ezviz";
    if (!editDeviceForm.name) {
      alert("请填写必填字段：设备名称");
      return;
    }
    if (!isEzviz && !editDeviceForm.stream_url) {
      alert("本地设备请填写流地址");
      return;
    }
    if (isEzviz && !editDeviceForm.device_serial) {
      alert("萤石设备请填写设备序列号");
      return;
    }

    try {
      const updatedDevice = await updateVideo(editingDevice.id, {
        ...editDeviceForm,
        access_source: isEzviz ? "cloud" : "local",
        ptz_source: isEzviz ? "ezviz" : "onvif",
        rtsp_url: isEzviz ? undefined : editDeviceForm.stream_url,
        stream_url: undefined,
      });
      setDevices(
        devices.map((d) => (d.id === editingDevice.id ? updatedDevice : d))
      );
      setPreviewStreams((prev) => {
        const next = { ...prev };
        delete next[editingDevice.id];
        return next;
      });
      setPreviewErrors((prev) => {
        const next = { ...prev };
        delete next[editingDevice.id];
        return next;
      });
      setShowEditModal(false);
      setEditingDevice(null);
    } catch (err: any) {
      alert(`更新失败: ${err.message}`);
    }
  };

  const handleDelete = async (id: number, e: React.MouseEvent) => {
    e.stopPropagation();
    if (confirm(`确定删除设备 ID: ${id} 吗？`)) {
      try {
        await deleteVideo(id);
        setDevices((prev) => prev.filter((d) => d.id !== id));
      } catch (err: any) {
        alert(`删除失败: ${err.message}`);
      }
    }
  };

  const cols = Math.ceil(Math.sqrt(itemsPerPage));

    const drawBoxes = useCallback((boxes: AlarmBox[]) => {
      const canvas = aiCanvasRef.current;
      if (!canvas) return;

      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      const container = canvas.parentElement;
      const videoEl = container?.querySelector("video") as HTMLVideoElement | null;

      canvas.width = canvas.offsetWidth;
      canvas.height = canvas.offsetHeight;
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const rawVideoW = videoEl?.videoWidth || 0;
      const rawVideoH = videoEl?.videoHeight || 0;

      // 视频元数据未就绪时，依据告警坐标范围做自适应缩放，避免框被画到可视区外
      const maxCoordX = boxes.reduce((m, b) => Math.max(m, b.coords[0], b.coords[2]), 0);
      const maxCoordY = boxes.reduce((m, b) => Math.max(m, b.coords[1], b.coords[3]), 0);
      const inferredSourceW = Math.max(canvas.width, maxCoordX + 1);
      const inferredSourceH = Math.max(canvas.height, maxCoordY + 1);

      let renderX = 0;
      let renderY = 0;
      let renderW = canvas.width;
      let renderH = canvas.height;

      if (rawVideoW > 0 && rawVideoH > 0) {
        const videoAspect = rawVideoW / rawVideoH;
        const canvasAspect = canvas.width / canvas.height;

        if (videoAspect > canvasAspect) {
          renderW = canvas.width;
          renderH = canvas.width / videoAspect;
          renderY = (canvas.height - renderH) / 2;
        } else {
          renderH = canvas.height;
          renderW = canvas.height * videoAspect;
          renderX = (canvas.width - renderW) / 2;
        }
      }

      boxes.forEach((box) => {
        const coords = Array.isArray(box?.coords) ? box.coords : null;
        if (!coords || coords.length < 4) return;

        let [x1, y1, x2, y2] = coords.map((v: any) => Number(v));
        if (![x1, y1, x2, y2].every(Number.isFinite)) return;

        const isNormalized = x2 <= 1.5 && y2 <= 1.5 && x1 >= 0 && y1 >= 0;
        if (isNormalized) {
          x1 *= rawVideoW || 1;
          y1 *= rawVideoH || 1;
          x2 *= rawVideoW || 1;
          y2 *= rawVideoH || 1;
        }

        const sourceW = rawVideoW || inferredSourceW;
        const sourceH = rawVideoH || inferredSourceH;
        const scaleX = renderW / sourceW;
        const scaleY = renderH / sourceH;

        const drawX = renderX + x1 * scaleX;
        const drawY = renderY + y1 * scaleY;
        const drawW = Math.max(2, (x2 - x1) * scaleX);
        const drawH = Math.max(2, (y2 - y1) * scaleY);

        const id = Number(box.track_id || 0);
        const color = `hsl(${(id * 50) % 360}, 80%, 50%)`;
        const label = `${box.msg || box.type || "报警"} #${id}`;

        ctx.strokeStyle = color;
        ctx.lineWidth = 3;
        ctx.strokeRect(drawX, drawY, drawW, drawH);

        ctx.font = "14px Arial";
        const textWidth = ctx.measureText(label).width;
        const tagW = Math.min(Math.max(textWidth + 12, 90), 280);
        const tagH = 22;
        const tagX = Math.max(0, Math.min(drawX, canvas.width - tagW));
        const tagY = Math.max(0, drawY - tagH - 2);

        ctx.fillStyle = color;
        ctx.fillRect(tagX, tagY, tagW, tagH);
        ctx.fillStyle = "white";
        ctx.fillText(label, tagX + 6, tagY + 15);
      });
    }, []);

  useEffect(() => {
    if (!maximizedVideo || !streamUrl || !alarmBoxes.length) {
      const canvas = aiCanvasRef.current;
      const ctx = canvas?.getContext("2d");
      if (canvas && ctx) {
        canvas.width = canvas.offsetWidth;
        canvas.height = canvas.offsetHeight;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      }
      return;
    }

    const redraw = () => drawBoxes(alarmBoxes);
    redraw();

    // 初始几秒重复重绘，覆盖视频元数据异步加载导致的坐标偏移
    const warmupTimer = window.setInterval(redraw, 400);
    const stopWarmupTimer = window.setTimeout(() => {
      window.clearInterval(warmupTimer);
    }, 3000);

    const resizeHandler = () => redraw();
    window.addEventListener("resize", resizeHandler);

    return () => {
      window.removeEventListener("resize", resizeHandler);
      window.clearInterval(warmupTimer);
      window.clearTimeout(stopWarmupTimer);
    };
  }, [alarmBoxes, drawBoxes, maximizedVideo, streamUrl]);

  if (loading)
    return (
      <div className="h-full flex items-center justify-center text-blue-500">
        <Loader className="animate-spin" size={48} />
      </div>
    );

  return (
    <div className="h-full flex gap-4 p-4 text-slate-100 bg-[radial-gradient(circle_at_12%_8%,rgba(56,189,248,0.20),transparent_32%),radial-gradient(circle_at_86%_2%,rgba(59,130,246,0.22),transparent_30%),linear-gradient(135deg,#020617,#0b1f3f_45%,#102a5e)]">
      {/* ✅ 全局警报弹窗 - 显示在最前面 */}
      {alarmAlert && (
        <div className="fixed inset-0 z-[999] flex items-center justify-center pointer-events-auto">
          <div className="relative max-w-md">
            {/* 外层脉冲光环 */}
            <div className="absolute inset-0 bg-gradient-to-r from-red-500/40 via-orange-500/40 to-red-500/40 rounded-2xl blur-2xl animate-pulse" />
            
            {/* 主弹窗容器 */}
            <div className="relative bg-gradient-to-br from-slate-950 via-red-950/40 to-slate-950 border-2 border-red-500/60 rounded-2xl shadow-2xl p-8 backdrop-blur-xl">
              {/* 警报图标动画 */}
              <div className="flex justify-center mb-4">
                <div className="relative">
                  <div className="absolute inset-0 bg-red-500/20 rounded-full animate-ping" style={{ animationDuration: '1s' }} />
                  <div className="relative flex items-center justify-center h-16 w-16 bg-gradient-to-br from-red-500 to-orange-600 rounded-full shadow-lg">
                    <AlertCircle size={32} className="text-white animate-pulse" />
                  </div>
                </div>
              </div>

              {/* 警报文本 */}
              <div className="text-center mb-6">
                <h3 className="text-2xl font-black text-red-400 mb-2">🚨 警报！</h3>
                <p className="text-lg font-bold text-slate-100 mb-1">{alarmAlert.msg}</p>
                <div className="flex items-center justify-center gap-3 text-sm">
                  <span className="px-3 py-1 bg-red-500/30 border border-red-400/60 rounded-full text-red-200 font-semibold">
                    {alarmAlert.type}
                  </span>
                  <span className="px-3 py-1 bg-orange-500/30 border border-orange-400/60 rounded-full text-orange-200 font-semibold">
                    置信度: {(alarmAlert.score * 100).toFixed(0)}%
                  </span>
                </div>
              </div>

              {/* 闪烁的警告条 */}
              <div className="mb-4 h-1 bg-gradient-to-r from-red-500 via-orange-500 to-red-500 rounded-full animate-pulse" />

              {/* 关闭按钮 */}
              <div className="flex justify-center gap-3">
                <button
                  onClick={() => setAlarmAlert(null)}
                  className="px-6 py-2 bg-red-600 hover:bg-red-700 text-white font-bold rounded-lg transition-all hover:scale-105 shadow-lg"
                >
                  确认
                </button>
              </div>

              {/* 时间戳 */}
              <div className="mt-3 text-xs text-slate-400 text-center font-mono">
                {new Date(alarmAlert.timestamp).toLocaleTimeString('zh-CN')}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 左侧列表 */}
      <CyberPanel
        title="设备管理"
        icon={<MonitorPlay size={16} className="text-cyan-300" />}
        className="w-80 flex flex-col"
        actions={
          <button
            onClick={() => setShowAddModal(true)}
            className="bg-cyan-500/90 hover:bg-cyan-400 text-slate-950 px-2 py-1 rounded text-xs flex items-center gap-1 font-semibold"
          >
            <Plus size={14} />
            新增
          </button>
        }
      >
        <div className="p-3 flex flex-col gap-3">
          <input
            type="text"
            placeholder="搜索设备..."
            className="bg-slate-950/65 border border-blue-300/35 rounded px-3 py-2 text-sm outline-none focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 text-slate-100 placeholder-slate-500"
            value={searchTerm}
            onChange={(e) => handleSearch(e.target.value)}
          />
          <div className="flex-1 overflow-y-auto space-y-2 max-h-[calc(100vh-15rem)] vc-scrollbar pr-1">
          {filteredDevices.map((device) => (
            <div
              key={device.id}
              onClick={() => setSelectedDevice(device)}
              className={`p-3 rounded border cursor-pointer transition-all flex justify-between items-center ${
                selectedDevice?.id === device.id
                  ? "border-cyan-300/90 bg-cyan-400/15 shadow-[0_0_14px_rgba(56,189,248,.35)]"
                  : "border-blue-300/20 bg-slate-900/35 hover:border-cyan-300/45 hover:bg-sky-500/10"
              }`}
            >
              <div className="overflow-hidden">
                <p className="text-sm font-medium truncate text-slate-100">
                  {device.name}
                </p>
                <div className="flex items-center gap-2 text-[10px] text-slate-400 font-mono">
                  <span>
                    {device.ip_address}:{device.port}
                  </span>
                  {device.remark && (
                    <span className="bg-slate-800/90 px-1 rounded truncate max-w-[80px] border border-slate-700">
                      {device.remark}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <button
                  onClick={(e) => handleEditClick(device, e)}
                  className="text-slate-500 hover:text-cyan-300 transition-colors"
                >
                  <Edit2 size={14} />
                </button>
                <button
                  onClick={(e) => handleDelete(device.id, e)}
                  className="text-slate-500 hover:text-rose-400 transition-colors"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
          </div>
        </div>
      </CyberPanel>

      {/* 右侧网格 */}
      <div className="flex-1 flex flex-col gap-4">
        <div
          style={{
            display: "grid",
            gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
            gap: "1rem",
          }}
          className="flex-1"
        >
          {Array.from({ length: itemsPerPage }).map((_, i) => {
            const device = currentVideos[i];
            return (
              <div
                key={`${device?.id ?? "slot"}-${i}`}
                className="relative group overflow-hidden rounded-md border border-blue-300/20 bg-slate-900/55 shadow-[inset_0_0_18px_rgba(14,165,233,0.08),0_6px_14px_rgba(2,6,23,.5)] hover:border-cyan-300/45 transition-colors"
              >
                {device ? (
                  <>
                    <div
                      className="relative w-full pt-[56.25%] bg-black"
                      onDoubleClick={() => handleVideoDoubleClick(device)}
                    >
                      <div className="absolute inset-0">
                        {maximizedVideo?.id === device.id ? (
                          <div className="h-full w-full flex items-center justify-center text-cyan-200 text-xs bg-slate-950/70">
                            当前设备正在全屏播放，已暂停预览拉流
                          </div>
                        ) : previewStreams[device.id] ? (
                          <VideoPlayer
                            key={previewStreams[device.id].url}
                            src={previewStreams[device.id].url}
                            playType={previewStreams[device.id].play_type}
                            accessToken={previewStreams[device.id].access_token}
                          />
                        ) : previewLoading[device.id] ? (
                          <div className="h-full w-full flex items-center justify-center text-slate-300 text-sm">
                            正在加载预览...
                          </div>
                        ) : previewErrors[device.id] ? (
                          <div className="h-full w-full flex flex-col items-center justify-center gap-2 text-xs text-rose-300">
                            <span>{previewErrors[device.id]}</span>
                            <button
                              className="px-3 py-1 bg-rose-500 text-white rounded"
                              onClick={(e) => {
                                e.stopPropagation();
                                loadPreviewStream(device);
                              }}
                            >
                              重试
                            </button>
                          </div>
                        ) : (
                          <button
                            className="h-full w-full flex items-center justify-center text-slate-300 text-sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              loadPreviewStream(device);
                            }}
                          >
                            点击加载预览
                          </button>
                        )}
                      </div>
                    </div>
                    {/* 状态指示器 */}
                    <div className="absolute top-2 left-2 flex items-center gap-2 z-10">
                      <span
                        className={`w-2 h-2 rounded-full ${
                          device.status === "online"
                            ? "bg-green-500 animate-pulse"
                            : "bg-slate-500"
                        }`}
                      />
                      <span className="text-xs bg-slate-900/75 backdrop-blur px-2 py-0.5 rounded text-slate-100 border border-cyan-300/20 shadow-sm">
                        {device.name}
                      </span>
                      <span className="text-[10px] bg-slate-900/80 px-1.5 py-0.5 rounded border border-blue-300/20 text-cyan-200 uppercase">
                        {device.platform_type || "onvif"}
                      </span>
                      <span className="text-[10px] bg-slate-900/80 px-1.5 py-0.5 rounded border border-blue-300/20 text-sky-200 uppercase">
                        {device.stream_protocol || "flv"}
                      </span>
                    </div>
                    {/* 悬浮操作栏 */}
                    <div className="absolute bottom-2 right-2 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity z-10">
                      <button
                        onClick={() => handleShowStream(device)}
                        className="p-1.5 bg-cyan-500 hover:bg-cyan-400 rounded text-slate-900 shadow-lg transition-all"
                        title="全屏播放"
                      >
                        <Maximize2 size={14} />
                      </button>
                    </div>
                  </>
                ) : (
                  <div className="h-full flex items-center justify-center text-slate-700">
                    <Plus size={32} opacity={0.2} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {/* 分页控制 */}
        <div className="h-12 bg-slate-900/65 border border-blue-300/25 rounded-md flex items-center justify-between px-4 shadow-[inset_0_0_18px_rgba(56,189,248,.08)]">
          <div className="text-xs text-slate-300">
            共 {filteredDevices.length} 个设备
          </div>
          <div className="flex gap-3 items-center">
            <label className="text-xs text-slate-300 font-medium">布局：</label>
            <input
              type="number"
              min="1"
              max="100"
              value={gridInputValue}
              onChange={handleGridInputChange}
              className="w-16 px-2 py-1 text-xs border border-blue-300/30 rounded bg-slate-950/65 text-slate-100 outline-none focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30"
              placeholder="1-100"
            />
            <span className="text-xs text-slate-400">屏</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              disabled={currentPage === 1}
              onClick={() => setCurrentPage((p) => p - 1)}
              className="p-1 disabled:opacity-30 hover:bg-sky-500/15 rounded transition-colors text-slate-300"
            >
              <ChevronLeft />
            </button>
            <span className="text-xs font-mono w-10 text-center text-cyan-200">
              {currentPage} / {totalPages}
            </span>
            <button
              disabled={currentPage === totalPages}
              onClick={() => setCurrentPage((p) => p + 1)}
              className="p-1 disabled:opacity-30 hover:bg-sky-500/15 rounded transition-colors text-slate-300"
            >
              <ChevronRight />
            </button>
          </div>
        </div>
      </div>

      {/* 添加设备弹窗 */}
      {showAddModal && (
        <div className="fixed inset-0 z-[100] bg-black/40 flex items-center justify-center p-4 backdrop-blur-sm">
          <div className="bg-slate-900 border border-cyan-300/30 rounded-lg w-[500px] p-6 shadow-2xl text-slate-100">
            <div className="flex justify-between items-center mb-6">
              <h3 className="text-lg font-bold flex items-center gap-2 text-slate-100">
                <Settings size={18} className="text-cyan-300" /> 添加监控设备
              </h3>
              <button
                onClick={() => setShowAddModal(false)}
                className="text-slate-400 hover:text-slate-200 transition-colors"
              >
                <X size={20} />
              </button>
            </div>
            {/* 表单内容 */}
            <div className="grid grid-cols-2 gap-4">
              <div className="col-span-2">
                <label className="text-xs font-semibold text-slate-300 block mb-1">
                  设备名称 <span className="text-red-500">*</span>
                </label>
                <input
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={newDeviceForm.name}
                  onChange={(e) =>
                    setNewDeviceForm({ ...newDeviceForm, name: e.target.value })
                  }
                  placeholder="例如：北门入口摄像头"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-300 block mb-1">IP 地址</label>
                <input
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={newDeviceForm.ip_address}
                  onChange={(e) =>
                    setNewDeviceForm({ ...newDeviceForm, ip_address: e.target.value })
                  }
                  placeholder="192.168.1.100"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-300 block mb-1">端口</label>
                <input
                  type="number"
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={newDeviceForm.port}
                  onChange={(e) =>
                    setNewDeviceForm({ ...newDeviceForm, port: parseInt(e.target.value) || 80 })
                  }
                  placeholder="80"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-300 block mb-1">用户名</label>
                <input
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={newDeviceForm.username || ""}
                  onChange={(e) =>
                    setNewDeviceForm({ ...newDeviceForm, username: e.target.value })
                  }
                  placeholder="请输入登录账号"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-300 block mb-1">密码</label>
                <input
                  type="password"
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={newDeviceForm.password || ""}
                  onChange={(e) =>
                    setNewDeviceForm({ ...newDeviceForm, password: e.target.value })
                  }
                  placeholder="******"
                />
              </div>
              <div className="col-span-2">
                <label className="text-xs font-semibold text-slate-300 block mb-1">平台类型</label>
                <select
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={newDeviceForm.platform_type || "onvif"}
                  onChange={(e) => {
                    const platform = e.target.value as "onvif" | "ezviz";
                    setNewDeviceForm({
                      ...newDeviceForm,
                      platform_type: platform,
                      access_source: platform === "ezviz" ? "cloud" : "local",
                      ptz_source: platform === "ezviz" ? "ezviz" : "onvif",
                      stream_protocol: platform === "ezviz" ? "ezopen" : (newDeviceForm.stream_protocol || "flv"),
                    });
                  }}
                >
                  <option value="onvif">本地 ONVIF/RTSP</option>
                  <option value="ezviz">萤石云设备</option>
                </select>
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-300 block mb-1">设备序列号</label>
                <input
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={newDeviceForm.device_serial || ""}
                  onChange={(e) =>
                    setNewDeviceForm({ ...newDeviceForm, device_serial: e.target.value })
                  }
                  placeholder="例如：GM7974925"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-300 block mb-1">通道号</label>
                <input
                  type="number"
                  min={1}
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={newDeviceForm.channel_no || 1}
                  onChange={(e) =>
                    setNewDeviceForm({ ...newDeviceForm, channel_no: parseInt(e.target.value, 10) || 1 })
                  }
                  placeholder="1"
                />
              </div>
              <div className="col-span-2">
                <label className="text-xs font-semibold text-slate-300 block mb-1">播放协议偏好</label>
                <select
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={newDeviceForm.stream_protocol || "flv"}
                  onChange={(e) =>
                    setNewDeviceForm({
                      ...newDeviceForm,
                      stream_protocol: e.target.value as "ezopen" | "hls" | "rtmp" | "flv",
                    })
                  }
                >
                  <option value="ezopen">EZOPEN</option>
                  <option value="flv">FLV</option>
                  <option value="hls">HLS</option>
                  <option value="rtmp">RTMP</option>
                </select>
              </div>
              <div className="col-span-2">
                <label className="text-xs font-semibold text-gray-700 block mb-1">
                  流地址（RTSP/HLS）
                </label>
                <input
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={newDeviceForm.stream_url || ""}
                  onChange={(e) =>
                    setNewDeviceForm({ ...newDeviceForm, stream_url: e.target.value })
                  }
                  placeholder={newDeviceForm.platform_type === "ezviz" ? "萤石设备可留空" : "示例：rtsp://账号:密码@192.168.1.100:554/..."}
                />
              </div>
              <div className="col-span-2">
                <label className="text-xs font-semibold text-slate-300 block mb-1">备注</label>
                <input
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={newDeviceForm.remark || ""}
                  onChange={(e) =>
                    setNewDeviceForm({ ...newDeviceForm, remark: e.target.value })
                  }
                  placeholder="位置描述或其他信息"
                />
              </div>
            </div>
            <div className="flex gap-3 mt-8">
              <button
                onClick={handleAddDevice}
                className="flex-1 bg-cyan-500 hover:bg-cyan-400 py-2 rounded text-sm font-bold text-slate-900 transition-colors shadow-md"
              >
                保存配置
              </button>
              <button
                onClick={() => setShowAddModal(false)}
                className="flex-1 bg-slate-700 hover:bg-slate-600 py-2 rounded text-sm text-slate-100 transition-colors"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 编辑设备弹窗 */}
      {showEditModal && (
        <div className="fixed inset-0 z-[100] bg-black/40 flex items-center justify-center p-4 backdrop-blur-sm">
          <div className="bg-slate-900 border border-cyan-300/30 rounded-lg w-[500px] p-6 shadow-2xl text-slate-100">
            <div className="flex justify-between items-center mb-6">
              <h3 className="text-lg font-bold flex items-center gap-2 text-slate-100">
                <Settings size={18} className="text-cyan-300" /> 编辑监控设备
              </h3>
              <button
                onClick={() => setShowEditModal(false)}
                className="text-slate-400 hover:text-slate-200 transition-colors"
              >
                <X size={20} />
              </button>
            </div>
            {/* 表单内容 */}
            <div className="grid grid-cols-2 gap-4">
              <div className="col-span-2">
                <label className="text-xs font-semibold text-slate-300 block mb-1">
                  设备名称 <span className="text-red-500">*</span>
                </label>
                <input
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={editDeviceForm.name}
                  onChange={(e) =>
                    setEditDeviceForm({ ...editDeviceForm, name: e.target.value })
                  }
                  placeholder="例如：北门入口摄像头"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-300 block mb-1">IP 地址</label>
                <input
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={editDeviceForm.ip_address}
                  onChange={(e) =>
                    setEditDeviceForm({ ...editDeviceForm, ip_address: e.target.value })
                  }
                  placeholder="192.168.1.100"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-300 block mb-1">端口</label>
                <input
                  type="number"
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={editDeviceForm.port}
                  onChange={(e) =>
                    setEditDeviceForm({ ...editDeviceForm, port: parseInt(e.target.value) || 80 })
                  }
                  placeholder="80"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-300 block mb-1">用户名</label>
                <input
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={editDeviceForm.username || ""}
                  onChange={(e) =>
                    setEditDeviceForm({ ...editDeviceForm, username: e.target.value })
                  }
                  placeholder="请输入登录账号"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-300 block mb-1">密码</label>
                <input
                  type="password"
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={editDeviceForm.password || ""}
                  onChange={(e) =>
                    setEditDeviceForm({ ...editDeviceForm, password: e.target.value })
                  }
                  placeholder="******"
                />
              </div>
              <div className="col-span-2">
                <label className="text-xs font-semibold text-slate-300 block mb-1">平台类型</label>
                <select
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={editDeviceForm.platform_type || "onvif"}
                  onChange={(e) => {
                    const platform = e.target.value as "onvif" | "ezviz";
                    setEditDeviceForm({
                      ...editDeviceForm,
                      platform_type: platform,
                      access_source: platform === "ezviz" ? "cloud" : "local",
                      ptz_source: platform === "ezviz" ? "ezviz" : "onvif",
                      stream_protocol: platform === "ezviz" ? "ezopen" : (editDeviceForm.stream_protocol || "flv"),
                    });
                  }}
                >
                  <option value="onvif">本地 ONVIF/RTSP</option>
                  <option value="ezviz">萤石云设备</option>
                </select>
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-300 block mb-1">设备序列号</label>
                <input
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={editDeviceForm.device_serial || ""}
                  onChange={(e) =>
                    setEditDeviceForm({ ...editDeviceForm, device_serial: e.target.value })
                  }
                  placeholder="例如：GM7974925"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-300 block mb-1">通道号</label>
                <input
                  type="number"
                  min={1}
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={editDeviceForm.channel_no || 1}
                  onChange={(e) =>
                    setEditDeviceForm({ ...editDeviceForm, channel_no: parseInt(e.target.value, 10) || 1 })
                  }
                  placeholder="1"
                />
              </div>
              <div className="col-span-2">
                <label className="text-xs font-semibold text-slate-300 block mb-1">播放协议偏好</label>
                <select
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={editDeviceForm.stream_protocol || "flv"}
                  onChange={(e) =>
                    setEditDeviceForm({
                      ...editDeviceForm,
                      stream_protocol: e.target.value as "ezopen" | "hls" | "rtmp" | "flv",
                    })
                  }
                >
                  <option value="ezopen">EZOPEN</option>
                  <option value="flv">FLV</option>
                  <option value="hls">HLS</option>
                  <option value="rtmp">RTMP</option>
                </select>
              </div>
              <div className="col-span-2">
                <label className="text-xs font-semibold text-gray-700 block mb-1">
                  流地址（RTSP/HLS）
                </label>
                <input
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={editDeviceForm.stream_url || ""}
                  onChange={(e) =>
                    setEditDeviceForm({ ...editDeviceForm, stream_url: e.target.value })
                  }
                  placeholder={editDeviceForm.platform_type === "ezviz" ? "萤石设备可留空" : "示例：rtsp://账号:密码@192.168.1.100:554/..."}
                />
              </div>
              <div className="col-span-2">
                <label className="text-xs font-semibold text-slate-300 block mb-1">备注</label>
                <input
                  className="w-full bg-slate-950/60 border border-blue-300/30 rounded p-2 text-sm focus:border-cyan-300 focus:ring-2 focus:ring-cyan-400/30 outline-none text-slate-100"
                  value={editDeviceForm.remark || ""}
                  onChange={(e) =>
                    setEditDeviceForm({ ...editDeviceForm, remark: e.target.value })
                  }
                  placeholder="位置描述或其他信息"
                />
              </div>
            </div>
            <div className="flex gap-3 mt-8">
              <button
                onClick={handleUpdateDevice}
                className="flex-1 bg-cyan-500 hover:bg-cyan-400 py-2 rounded text-sm font-bold text-slate-900 transition-colors shadow-md"
              >
                更新配置
              </button>
              <button
                onClick={() => setShowEditModal(false)}
                className="flex-1 bg-slate-700 hover:bg-slate-600 py-2 rounded text-sm text-slate-100 transition-colors"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 播放弹窗 (包含 AI 侧边栏) */}
      {maximizedVideo && (
        <div className="fixed inset-0 z-[200] bg-[radial-gradient(circle_at_15%_8%,rgba(34,211,238,.15),transparent_35%),linear-gradient(140deg,#020617,#0b1f3f_50%,#102a5e)] flex flex-col p-4 gap-4">
          {/* Header */}
          <div className="flex justify-between items-center">
            <h2 className="text-xl font-bold flex items-center gap-3 text-slate-100">
              {maximizedVideo.name}
              <span className="text-sm font-mono font-normal text-slate-300 bg-slate-900/75 px-2 rounded border border-blue-300/20">
                {maximizedVideo.ip_address}
              </span>
            </h2>
            <button
              onClick={() => {
                setMaximizedVideo(null);
                setStreamUrl(null);
                setStreamInfo(null);
              }}
              className="p-2 text-slate-400 hover:bg-rose-500/20 hover:text-rose-300 rounded-full transition-colors"
            >
              <X size={24} />
            </button>
          </div>

          {/* Main Content */}
          <div className="flex-1 flex gap-4 min-h-0">
            {/* Video Section */}
            <div className="flex-1 flex flex-col bg-slate-900/65 rounded-lg border border-blue-300/25 overflow-hidden">
              {streamUrl ? (
                <>
                  <div className="p-3 bg-blue-500/12 border-b border-blue-300/25 text-sm text-cyan-100">
                    <div className="flex items-center gap-2 font-semibold">
                      <MonitorPlay size={18} /> 流信息
                    </div>
                    <div className="mt-2 text-xs text-cyan-200/90">
                      协议: {streamInfo?.play_type || "unknown"} | 平台: {streamInfo?.platform || "unknown"}
                    </div>
                    <code className="mt-2 block text-xs bg-slate-950/70 p-2 rounded border border-blue-300/25 break-all text-slate-200 max-h-20 overflow-auto vc-scrollbar">
                      {streamUrl}
                    </code>
                  </div>
                  <div className="flex-1 flex items-center justify-center bg-black relative min-h-0">
                    <div className="relative w-full h-full">
                      <VideoPlayer src={streamUrl} playType={streamInfo?.play_type} accessToken={streamInfo?.access_token} />
                      <canvas
                      id="aiCanvas"
                      ref={aiCanvasRef}
                      className="absolute top-0 left-0 w-full h-full pointer-events-none"
                    />
                      <div className="absolute top-24 left-16 z-20 pointer-events-none flex flex-col gap-0.5 max-w-[45vw] text-black text-lg font-bold leading-7 [text-shadow:0_1px_2px_rgba(0,0,0,0.9)]">
                        <div className="truncate">{maximizedVideo.name || ""}</div>
                        <div className="truncate">{maximizedVideo.remark?.trim() || ""}</div>
                        <div className="truncate">累计工作时长：{formatWorkDuration(currentWorkDurationSeconds)}</div>
                      </div>
                    </div>
                  </div>
                </>
              ) : (
                <div className="flex items-center justify-center flex-1">
                  <Loader className="animate-spin text-cyan-300" size={48} />
                </div>
              )}
            </div>

            {/* Right Sidebar: AI Control + PTZ */}
            {streamUrl && (
              <div className="w-80 flex flex-col gap-3 h-full">
                
                {/* ✅ 新增：AI 智脑控制中心 (下拉菜单版) */}
                <div className="bg-slate-900/75 rounded-lg border border-blue-300/25 p-4 shadow-lg shrink-0">
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="font-bold text-slate-100 flex items-center gap-2">
                      <Shield size={18} className="text-cyan-300" />
                      AI 智脑控制
                    </h3>
                    <div className="flex gap-2">
                      <button 
                        onClick={() => handleToggleAll(true)}
                        className="text-[10px] font-bold text-cyan-200 bg-cyan-500/20 border border-cyan-300/30 px-2 py-1 rounded hover:bg-cyan-500/30 transition-colors"
                      >全开启</button>
                      <button 
                        onClick={() => handleToggleAll(false)}
                        className="text-[10px] font-bold text-rose-200 bg-rose-500/20 border border-rose-300/30 px-2 py-1 rounded hover:bg-rose-500/30 transition-colors"
                      >全关闭</button>
                    </div>
                  </div>

                  {/* 下拉选择菜单 */}
                  <div className="relative mb-4">
                    <select 
                      className="w-full p-2.5 bg-slate-950/65 border border-blue-300/25 rounded-md text-sm text-slate-200 outline-none focus:ring-2 focus:ring-cyan-400/40 appearance-none cursor-pointer"
                      value=""
                      onChange={(e) => handleSingleAI(e.target.value)}
                      disabled={aiLoading}
                    >
                      <option value="" disabled>＋ 添加/切换算法功能</option>
                      {algos.map(algo => (
                        <option key={algo.id} value={algo.id}>
                          {activeAlgos.includes(algo.id) ? "✅ 已开启：" : "⭕ 未开启："} {algo.name}
                        </option>
                      ))}
                    </select>
                    {/* 自定义下拉箭头图标 */}
                    <div className="absolute right-3 top-3.5 pointer-events-none text-slate-500">
                      <Settings size={14} />
                    </div>
                  </div>

                  {/* 已开启功能状态标签 */}
                  <div className="flex flex-wrap gap-2 min-h-[24px]">
                    {activeAlgos.length === 0 ? (
                      <span className="text-[11px] text-slate-400 italic flex items-center gap-1">
                        <AlertCircle size={12} /> 暂无监测任务运行
                      </span>
                    ) : (
                      activeAlgos.map(id => {
                        const algo = algos.find(a => a.id === id);
                        return (
                          <div key={id} className="flex items-center gap-1.5 bg-emerald-500/15 border border-emerald-300/35 text-emerald-200 px-2 py-1 rounded text-[11px] animate-pulse">
                            <ShieldCheck size={12} />
                            {algo?.name}
                          </div>
                        );
                      })
                    )}
                  </div>

                  {/* 加载状态提示 */}
                  {aiLoading && (
                    <div className="mt-3 pt-3 border-t border-blue-300/20 flex items-center justify-center gap-2 text-[11px] text-cyan-300">
                      <Loader size={12} className="animate-spin" /> 正在同步云端算法状态...
                    </div>
                  )}
                </div>

                {/* PTZ Control Panel */}
                <div className="bg-slate-900/75 rounded-lg border border-blue-300/25 overflow-y-auto shadow-lg flex-1 vc-scrollbar">
                  <PTZControlPanel
                    video={maximizedVideo}
                    onSuccess={handlePtzSuccess}
                    onError={handlePtzError}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}