import { API_BASE_URL } from './config';

// --- 类型定义 ---

// 对应后端的 VideoOut schema (API 返回的数据)
export interface Video {
  id: number;
  name: string;
  ip_address: string;
  port: number;
  username?: string; // 补全：用于编辑回显
  password?: string; // 补全：用于编辑回显
  stream_url?: string; // 后端可能返回 null
  rtsp_url?: string;
  status: 'online' | 'offline';
  is_active: number;
  remark?: string;
  latitude?: number;
  longitude?: number;
}

// 对应后端的 VideoCreate schema (创建时提交的数据)
export interface VideoCreate {
  name: string;
  ip_address: string;
  port?: number;      // 后端默认为 80
  username?: string;
  password?: string;
  stream_url?: string; // 改为可选，允许为空
  rtsp_url?: string;
  status?: 'online' | 'offline';
  remark?: string;
}

// 对应后端的 VideoUpdate schema (更新时提交的数据)
export interface VideoUpdate {
  name?: string;
  ip_address?: string;
  port?: number;
  username?: string;
  password?: string;
  stream_url?: string;
  rtsp_url?: string;
  status?: 'online' | 'offline';
  remark?: string;
  is_active?: number;
}

export interface StreamUrl {
  url: string;
}

export interface AIRule {
  key: string;
  desc: string;
}

export interface PlaybackSavePayload {
  start_time: string;
  end_time: string;
}

export interface PlaybackSaveResponse {
  status: string;
  video_id: number;
  start_time: string;
  end_time: string;
  duration_seconds: number;
  recording_path: string;
}

export type PTZDirection = 'up' | 'down' | 'left' | 'right' | 'zoom_in' | 'zoom_out';

export interface PTZPresetItem {
  token: string;
  name: string;
}

export interface CruiseStatus {
  running: boolean;
  preset_tokens?: string[];
  dwell_seconds?: number;
  rounds?: number | null;
}

// --- API 方法 ---

/** 获取所有视频设备列表 */
export async function getAllVideos(): Promise<Video[]> {
  const response = await fetch(`${API_BASE_URL}/video/`);
  if (!response.ok) throw new Error('Failed to fetch videos');
  return response.json();
}

/** 创建新的视频设备 */
export async function createVideo(videoData: VideoCreate): Promise<Video> {
  const response = await fetch(`${API_BASE_URL}/video/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(videoData),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to create video');
  }
  return response.json();
}

/** 更新视频设备信息 (补充缺失的方法) */
export async function updateVideo(id: number, videoData: VideoUpdate): Promise<Video> {
  const response = await fetch(`${API_BASE_URL}/video/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(videoData),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to update video');
  }
  return response.json();
}

/** 控制摄像头云台方向 */
export async function ptzControl(
  videoId: number,
  direction: PTZDirection,
  speed: number = 0.5,
  duration: number = 0.5
): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE_URL}/video/ptz/${videoId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ direction, speed, duration }),
  });
  if (!response.ok) {
    let msg = 'Failed to control PTZ';
    try {
      const err = await response.json();
      msg = err.detail || msg;
    } catch {}
    throw new Error(msg);
  }
  return response.json();
}

/** 删除指定的视频设备 */
export async function deleteVideo(videoId: number): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE_URL}/video/${videoId}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to delete video');
  }
  return response.json();
}

/** 获取指定设备的视频流地址 */
export async function getVideoStreamUrl(videoId: number): Promise<StreamUrl> {
  const response = await fetch(`${API_BASE_URL}/video/stream/${videoId}`);
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to get stream URL');
  }
  return response.json();
}

/** 同步设备列表 (补充缺失的方法) */
export async function syncDevices(): Promise<{ message: string }> {
  const response = await fetch(`${API_BASE_URL}/video/sync`, {
    method: 'POST',
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to sync devices');
  }
  return response.json();
}

/** 通过 RTSP 地址动态添加摄像头（由 Node Media Server 拉流转码） */
export async function addCameraViaRTSP(cameraData: {
  name: string;
  rtsp_url: string;
  ip_address?: string;
  port?: number;
  username?: string;
  password?: string;
  latitude?: number;
  longitude?: number;
  remark?: string;
}): Promise<Video> {
  const response = await fetch(`${API_BASE_URL}/video/add_camera`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cameraData),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to add camera via RTSP');
  }
  return response.json();
}
/** 持续云台移动-开始（按下时调用） */
export async function ptzStartControl(
  videoId: number,
  direction: PTZDirection,
  speed: number = 0.5,
): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE_URL}/video/ptz/${videoId}/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ direction, speed, duration: 1 }),
  });
  if (!response.ok) {
    let msg = 'Failed to start PTZ';
    try {
      const err = await response.json();
      msg = err.detail || msg;
    } catch {}
    throw new Error(msg);
  }
  return response.json();
}

/** 持续云台移动-停止（松开时调用） */
export async function ptzStopControl(videoId: number): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE_URL}/video/ptz/${videoId}/stop`, {
    method: 'POST',
  });
  if (!response.ok) {
    let msg = 'Failed to stop PTZ';
    try {
      const err = await response.json();
      msg = err.detail || msg;
    } catch {}
    throw new Error(msg);
  }
  return response.json();
}

export async function getPresets(videoId: number): Promise<PTZPresetItem[]> {
  const response = await fetch(`${API_BASE_URL}/video/ptz/${videoId}/presets`);
  if (!response.ok) {
    let msg = 'Failed to fetch presets';
    try {
      const err = await response.json();
      msg = err.detail || msg;
    } catch {}
    throw new Error(msg);
  }
  return response.json();
}

export async function createPreset(videoId: number, payload: { name?: string; token?: string }): Promise<PTZPresetItem> {
  const response = await fetch(`${API_BASE_URL}/video/ptz/${videoId}/presets`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    let msg = 'Failed to create preset';
    try {
      const err = await response.json();
      msg = err.detail || msg;
    } catch {}
    throw new Error(msg);
  }
  return response.json();
}

export async function gotoPreset(videoId: number, presetToken: string, speed: number = 0.5): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE_URL}/video/ptz/${videoId}/presets/${encodeURIComponent(presetToken)}/goto`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ speed }),
  });
  if (!response.ok) {
    let msg = 'Failed to goto preset';
    try {
      const err = await response.json();
      msg = err.detail || msg;
    } catch {}
    throw new Error(msg);
  }
  return response.json();
}

export async function deletePreset(videoId: number, presetToken: string): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE_URL}/video/ptz/${videoId}/presets/${encodeURIComponent(presetToken)}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    let msg = 'Failed to delete preset';
    try {
      const err = await response.json();
      msg = err.detail || msg;
    } catch {}
    throw new Error(msg);
  }
  return response.json();
}

export async function startCruise(videoId: number, payload: {
  preset_tokens: string[];
  dwell_seconds?: number;
  rounds?: number | null;
}): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE_URL}/video/ptz/${videoId}/cruise/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    let msg = 'Failed to start cruise';
    try {
      const err = await response.json();
      msg = err.detail || msg;
    } catch {}
    throw new Error(msg);
  }
  return response.json();
}

export async function stopCruise(videoId: number): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE_URL}/video/ptz/${videoId}/cruise/stop`, {
    method: 'POST',
  });
  if (!response.ok) {
    let msg = 'Failed to stop cruise';
    try {
      const err = await response.json();
      msg = err.detail || msg;
    } catch {}
    throw new Error(msg);
  }
  return response.json();
}

export async function getCruiseStatus(videoId: number): Promise<CruiseStatus> {
  const response = await fetch(`${API_BASE_URL}/video/ptz/${videoId}/cruise/status`);
  if (!response.ok) {
    let msg = 'Failed to fetch cruise status';
    try {
      const err = await response.json();
      msg = err.detail || msg;
    } catch {}
    throw new Error(msg);
  }
  return response.json();
}

/** 保存指定设备在自定义时间段的回放视频 */
export async function savePlaybackClip(
  videoId: number,
  payload: PlaybackSavePayload
): Promise<PlaybackSaveResponse> {
  const response = await fetch(`${API_BASE_URL}/video/${videoId}/playback/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    let msg = 'Failed to save playback clip';
    try {
      const err = await response.json();
      msg = err.detail || msg;
    } catch {}
    throw new Error(msg);
  }

  return response.json();
}

// --- 新增：AI 监控控制接口 ---

// 开启指定设备的 AI 监控
// --- 找到 frontend/src/api/videoApi.ts 文件，在末尾添加以下内容 ---

// 1. 开启 AI 监控
export const startAIMonitoring = async (deviceId: string, rtspUrl: string, algoType: string = "helmet") => {
  // 注意：这里假设你的后端运行在 localhost:8000，且 API_BASE_URL 已正确定义
  // 如果 videoApi.ts 顶部已经定义了 API_BASE_URL，请直接使用它
  // 如果没有，请手动替换为 'http://localhost:8000/api'
  const response = await fetch(`http://127.0.0.1:9000/video/ai/start`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ device_id: deviceId, rtsp_url: rtspUrl, algo_type: algoType }),
  });
  return response.json();
};

// 2. 停止 AI 监控
export const stopAIMonitoring = async (deviceId: string) => {
  const response = await fetch(`http://127.0.0.1:9000/video/ai/stop?device_id=${deviceId}`, {
    method: 'POST',
  });
  return response.json();
};

export const getAIRules = async (): Promise<AIRule[]> => {
  // Keep compatibility with existing project setup where backend may run on 9000.
  const urls = [`${API_BASE_URL}/video/ai/rules`, 'http://127.0.0.1:9000/video/ai/rules'];
  let result: any = null;
  let lastError = 'Failed to load AI rules';

  for (const url of urls) {
    try {
      const response = await fetch(url);
      if (!response.ok) {
        try {
          const err = await response.json();
          lastError = err.detail || err.message || lastError;
        } catch {}
        continue;
      }
      result = await response.json();
      break;
    } catch (e: any) {
      lastError = e?.message || lastError;
    }
  }

  if (!result) {
    throw new Error(lastError);
  }

  const list = Array.isArray(result?.data) ? result.data : [];

  return list
    .filter((item: any) => item?.key)
    .map((item: any) => ({
      key: String(item.key),
      desc: String(item.desc || item.key),
    }));
};