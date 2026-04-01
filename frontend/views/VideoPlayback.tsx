import React, { useEffect, useMemo, useState } from 'react';
import { Video, Clock3, Siren, RefreshCw, Loader2 } from 'lucide-react';
import {
  getAllVideos,
  getAlarmPlaybackVideos,
  getSavedPlaybackVideos,
  getTempPlaybackVideos,
  triggerTempPlaybackCache,
  SavedPlaybackVideo,
} from '../src/api/videoApi';
import { API_BASE_URL } from '../src/api/config';

const toVideoUrl = (webPath: string) => {
  if (!webPath) return '';
  if (webPath.startsWith('http://') || webPath.startsWith('https://')) {
    return webPath;
  }
  return `${API_BASE_URL}${webPath.startsWith('/') ? '' : '/'}${webPath}`;
};

const formatSize = (size: number) => {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  if (size < 1024 * 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  return `${(size / (1024 * 1024 * 1024)).toFixed(2)} GB`;
};

type SourceType = 'temp' | 'playback' | 'alarm';

export default function VideoPlayback() {
  const [devices, setDevices] = useState<Array<{ id: number; name: string; platform_type?: string; access_source?: string }>>([]);
  const [selectedVideoId, setSelectedVideoId] = useState<number | null>(null);

  const [tempVideos, setTempVideos] = useState<SavedPlaybackVideo[]>([]);
  const [playbackVideos, setPlaybackVideos] = useState<SavedPlaybackVideo[]>([]);
  const [alarmVideos, setAlarmVideos] = useState<SavedPlaybackVideo[]>([]);

  const [activeSource, setActiveSource] = useState<SourceType>('temp');
  const [activeVideoPath, setActiveVideoPath] = useState<string>('');

  const [loadingDeviceList, setLoadingDeviceList] = useState(false);
  const [loadingLists, setLoadingLists] = useState(false);
  const [savingTemp, setSavingTemp] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string>('');
  const [errorMsg, setErrorMsg] = useState<string>('');

  const activeList = useMemo(() => {
    if (activeSource === 'temp') return tempVideos;
    if (activeSource === 'alarm') return alarmVideos;
    return playbackVideos;
  }, [activeSource, tempVideos, alarmVideos, playbackVideos]);

  const selectedDevice = useMemo(
    () => devices.find((d) => d.id === selectedVideoId) || null,
    [devices, selectedVideoId]
  );

  const refreshLists = async (videoId: number) => {
    setLoadingLists(true);
    setErrorMsg('');
    try {
      const [temp, playback, alarm] = await Promise.all([
        getTempPlaybackVideos(videoId, 30),
        getSavedPlaybackVideos(videoId, 120),
        getAlarmPlaybackVideos(videoId, 120),
      ]);
      setTempVideos(temp);
      setPlaybackVideos(playback);
      setAlarmVideos(alarm);

      const preferred = temp[0]?.web_path || playback[0]?.web_path || alarm[0]?.web_path || '';
      setActiveVideoPath((prev) => prev || preferred);
    } catch (err: any) {
      setErrorMsg(err?.message || '加载回放列表失败');
    } finally {
      setLoadingLists(false);
    }
  };

  useEffect(() => {
    const loadDevices = async () => {
      setLoadingDeviceList(true);
      setErrorMsg('');
      try {
        const data = await getAllVideos();
        const onlineDevices = data.map((item) => ({
          id: item.id,
          name: item.name,
          platform_type: item.platform_type,
          access_source: item.access_source,
        }));
        setDevices(onlineDevices);
        if (onlineDevices.length > 0) {
          setSelectedVideoId(onlineDevices[0].id);
        }
      } catch (err: any) {
        setErrorMsg(err?.message || '加载设备列表失败');
      } finally {
        setLoadingDeviceList(false);
      }
    };
    loadDevices();
  }, []);

  useEffect(() => {
    if (!selectedVideoId) return;
    setActiveVideoPath('');
    setStatusMsg('');
    setErrorMsg('');
    refreshLists(selectedVideoId);
  }, [selectedVideoId]);

  const handleTriggerTempCache = async () => {
    if (!selectedVideoId) return;

    setSavingTemp(true);
    setStatusMsg('正在生成临时缓存...');
    setErrorMsg('');
    try {
      const resp = await triggerTempPlaybackCache(selectedVideoId);
      setStatusMsg(`已生成临时缓存: ${resp.cache_window_start} ~ ${resp.cache_window_end}`);
    } catch (err: any) {
      setErrorMsg(err?.message || '生成临时缓存失败');
    } finally {
      setSavingTemp(false);
    }

    await refreshLists(selectedVideoId);
  };

  const onSelectVideo = (path: string, source: SourceType) => {
    setActiveSource(source);
    setActiveVideoPath(path);
  };

  return (
    <div className="h-full w-full p-4 md:p-6 text-white bg-gradient-to-br from-slate-900 via-blue-950 to-slate-950 overflow-hidden">
      <div className="h-full grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4">
        <aside className="rounded-2xl border border-cyan-400/20 bg-slate-900/70 backdrop-blur p-4 overflow-auto">
          <h2 className="text-lg font-semibold tracking-wide mb-3 flex items-center gap-2">
            <Video size={18} />
            视频回放
          </h2>

          {loadingDeviceList ? (
            <div className="text-sm text-slate-300 flex items-center gap-2">
              <Loader2 size={14} className="animate-spin" />
              正在加载设备...
            </div>
          ) : devices.length === 0 ? (
            <div className="text-sm text-slate-300">暂无设备</div>
          ) : (
            <div className="space-y-2">
              {devices.map((device) => (
                <button
                  key={device.id}
                  onClick={() => setSelectedVideoId(device.id)}
                  className={`w-full text-left px-3 py-2 rounded-lg border transition ${
                    selectedVideoId === device.id
                      ? 'border-cyan-400 bg-cyan-500/20 text-cyan-200'
                      : 'border-slate-700 bg-slate-800/60 hover:bg-slate-700/70'
                  }`}
                >
                  <div className="text-sm font-medium">{device.name}</div>
                  <div className="text-xs text-slate-400">ID: {device.id}</div>
                </button>
              ))}
            </div>
          )}

          <button
            disabled={!selectedVideoId || loadingLists || savingTemp}
            onClick={() => selectedVideoId && refreshLists(selectedVideoId)}
            className="mt-4 w-full px-3 py-2 rounded-lg border border-slate-600 hover:border-cyan-400 disabled:opacity-60 flex items-center justify-center gap-2"
          >
            <RefreshCw size={14} className={loadingLists ? 'animate-spin' : ''} />
            刷新列表
          </button>

          <button
            disabled={!selectedVideoId || loadingLists || savingTemp}
            onClick={handleTriggerTempCache}
            className="mt-2 w-full px-3 py-2 rounded-lg border border-slate-600 hover:border-cyan-400 disabled:opacity-60 flex items-center justify-center gap-2"
          >
            <Clock3 size={14} />
            生成临时缓存
          </button>
        </aside>

        <section className="rounded-2xl border border-cyan-400/20 bg-slate-900/70 backdrop-blur p-4 md:p-5 overflow-auto">
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <button
              onClick={() => {
                setActiveSource('temp');
                setActiveVideoPath(tempVideos[0]?.web_path || '');
              }}
              className={`px-3 py-1.5 rounded-lg text-sm border ${
                activeSource === 'temp' ? 'border-cyan-400 bg-cyan-500/20' : 'border-slate-700'
              }`}
            >
              <Clock3 size={14} className="inline-block mr-1" />
              临时缓存
            </button>
            <button
              onClick={() => {
                setActiveSource('playback');
                setActiveVideoPath(playbackVideos[0]?.web_path || '');
              }}
              className={`px-3 py-1.5 rounded-lg text-sm border ${
                activeSource === 'playback' ? 'border-cyan-400 bg-cyan-500/20' : 'border-slate-700'
              }`}
            >
              常态回放
            </button>
            <button
              onClick={() => {
                setActiveSource('alarm');
                setActiveVideoPath(alarmVideos[0]?.web_path || '');
              }}
              className={`px-3 py-1.5 rounded-lg text-sm border ${
                activeSource === 'alarm' ? 'border-cyan-400 bg-cyan-500/20' : 'border-slate-700'
              }`}
            >
              <Siren size={14} className="inline-block mr-1" />
              报警视频
            </button>
            {savingTemp && (
              <span className="text-xs text-cyan-300 flex items-center gap-1">
                <Loader2 size={12} className="animate-spin" />
                正在生成临时缓存...
              </span>
            )}
          </div>

          {statusMsg && <div className="mb-3 text-xs text-emerald-300">{statusMsg}</div>}
          {errorMsg && <div className="mb-3 text-xs text-rose-300">{errorMsg}</div>}

          <div className="grid grid-cols-1 xl:grid-cols-[1fr_360px] gap-4">
            <div className="rounded-xl border border-slate-700 bg-black/40 p-3 min-h-[340px]">
              {activeVideoPath ? (
                <video
                  key={activeVideoPath}
                  controls
                  className="w-full h-[300px] md:h-[420px] bg-black rounded"
                  src={toVideoUrl(activeVideoPath)}
                />
              ) : (
                <div className="h-[300px] md:h-[420px] flex items-center justify-center text-slate-400 text-sm">
                  {loadingLists ? '正在加载视频列表...' : '请选择一个回放视频'}
                </div>
              )}
            </div>

            <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-3 h-[420px] overflow-auto">
              <div className="text-sm text-slate-300 mb-2">当前列表 ({activeList.length})</div>
              <div className="space-y-2">
                {activeList.map((item) => (
                  <button
                    key={`${item.name}_${item.updated_at}`}
                    onClick={() => onSelectVideo(item.web_path, activeSource)}
                    className={`w-full text-left rounded-lg border p-2 transition ${
                      activeVideoPath === item.web_path
                        ? 'border-cyan-400 bg-cyan-500/20'
                        : 'border-slate-700 hover:bg-slate-800/60'
                    }`}
                  >
                    <div className="text-xs text-slate-100 truncate">{item.name}</div>
                    <div className="text-[11px] text-slate-400 mt-1">
                      {item.updated_at} | {formatSize(item.size_bytes)}
                    </div>
                  </button>
                ))}

                {activeList.length === 0 && !loadingLists && (
                  <div className="text-xs text-slate-400">该分类暂无可播放视频</div>
                )}
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
