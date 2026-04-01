export enum MenuKey {
  DASHBOARD = 'dashboard',
  VIDEO = 'video',
  VIDEO_PLAYBACK = 'video_playback',
  TRACK = 'track',
  FENCE = 'fence',
  GROUP_CALL = 'group_call',
  ALARM = 'alarm',
  DEVICE = 'device',
  SETTINGS = 'settings',
}

export interface HelmetDevice {
  id: string;
  name: string;
  department: string;
  status: 'online' | 'offline';
  battery: number;
  signal: number; // 0-100
  lastActive: string;
}

export interface Fence {
  id: string;
  name: string;
  type: 'Circle' | 'Polygon';
  behavior: 'No Entry' | 'No Exit';
  radius?: number; // for Circle
  alarmCount: number;
  startTime: string;
  endTime: string;
  address?: string;
}

export interface AlarmRecord {
  id: string;
  deviceId: string;
  type: string; // SOS, Fence, Low Battery
  timestamp: string;
  status: 'resolved' | 'pending';
}