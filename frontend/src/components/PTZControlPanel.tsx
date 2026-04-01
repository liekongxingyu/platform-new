import React, { useState, useCallback, useEffect, useRef } from 'react';
import {
  ChevronUp,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Loader,
  Plus,
  Move,
  ZoomIn,
  ZoomOut,
  RefreshCw,
  Trash2,
  Play,
  Square,
} from 'lucide-react';
import {
  ptzStartControl,
  ptzStopControl,
  zoomStartControl,
  zoomStopControl,
  Video,
  PTZDirection,
  PTZPresetItem,
  getPresets,
  createPreset,
  gotoPreset,
  deletePreset,
  deletePresetsBulk,
  startCruise,
  stopCruise,
  getCruiseStatus,
} from '../api/videoApi';

interface PTZControlPanelProps {
  video: Video;
  onError?: (msg: string) => void;
  onSuccess?: (msg: string) => void;
}

const PTZControlPanel: React.FC<PTZControlPanelProps> = ({
  video,
  onError,
  onSuccess
}) => {
  const [isControlling, setIsControlling] = useState(false);
  // 临时停用“变速控制”功能：保留原代码，后续需要时可恢复。
  // const [speed, setSpeed] = useState(0.3);
  const fixedControlSpeed = 0.5;
  const [presets, setPresets] = useState<PTZPresetItem[]>([]);
  const [newPresetName, setNewPresetName] = useState('');
  const [selectedPresetTokens, setSelectedPresetTokens] = useState<string[]>([]);
  const [dwellSeconds, setDwellSeconds] = useState(8);
  const [isCruising, setIsCruising] = useState(false);
  const [busy, setBusy] = useState(false);
  const activeControlTypeRef = useRef<'ptz' | 'zoom' | null>(null);
  const stopInFlightRef = useRef(false);
  const lastStopAtRef = useRef(0);

  const canPTZ = (video.supports_ptz ?? 1) === 1;
  const canPreset = (video.supports_preset ?? 1) === 1;
  const canCruise = (video.supports_cruise ?? 1) === 1;
  const canZoom = (video.supports_zoom ?? 1) === 1;

  const getDirectionName = (direction: string): string => {
    const names: Record<string, string> = {
      up: '上',
      down: '下',
      left: '左',
      right: '右',
      zoom_in: '放大',
      zoom_out: '缩小',
    };
    return names[direction] || direction;
  };

  const loadPresetsAndCruiseStatus = useCallback(async () => {
    try {
      const [presetList, cruiseStatus] = await Promise.all([
        getPresets(video.id),
        getCruiseStatus(video.id),
      ]);
      setPresets(presetList);
      setIsCruising(Boolean(cruiseStatus.running));
      if (Array.isArray(cruiseStatus.preset_tokens)) {
        setSelectedPresetTokens(cruiseStatus.preset_tokens);
      }
      if (typeof cruiseStatus.dwell_seconds === 'number') {
        setDwellSeconds(Math.max(1, Math.min(120, Math.round(cruiseStatus.dwell_seconds))));
      }
    } catch (err: any) {
      onError?.(`加载预置点失败: ${err.message || err}`);
    }
  }, [video.id, onError]);

  useEffect(() => {
    loadPresetsAndCruiseStatus();
  }, [loadPresetsAndCruiseStatus]);

  const startMove = useCallback(
    async (direction: PTZDirection) => {
      if (!canPTZ) {
        onError?.('当前设备不支持云台控制');
        return;
      }
      if ((direction === 'zoom_in' || direction === 'zoom_out') && !canZoom) {
        onError?.('当前设备不支持变焦控制');
        return;
      }
      if (isControlling) return;
      try {
        setIsControlling(true);
        const isZoomDirection = direction === 'zoom_in' || direction === 'zoom_out';
        if (isZoomDirection) {
          await zoomStartControl(video.id, direction, fixedControlSpeed);
          activeControlTypeRef.current = 'zoom';
        } else {
          await ptzStartControl(video.id, direction, fixedControlSpeed);
          activeControlTypeRef.current = 'ptz';
        }
        onSuccess?.(`摄像头向${getDirectionName(direction)}移动中...`);
      } catch (err: any) {
        activeControlTypeRef.current = null;
        onError?.(`云台控制失败: ${err.message || err}`);
        setIsControlling(false);
      }
    },
    [canPTZ, canZoom, isControlling, fixedControlSpeed, video.id, onSuccess, onError]
  );

  const stopMove = useCallback(async () => {
    const now = Date.now();
    if (now - lastStopAtRef.current < 180) {
      return;
    }
    lastStopAtRef.current = now;

    if (stopInFlightRef.current) {
      return;
    }

    const activeControlType = activeControlTypeRef.current;
    if (!activeControlType) {
      setIsControlling(false);
      return;
    }

    stopInFlightRef.current = true;
    try {
      if (activeControlType === 'zoom') {
        await zoomStopControl(video.id);
      } else {
        await ptzStopControl(video.id);
      }
    } catch (err: any) {
      onError?.(`云台停止失败: ${err.message || err}`);
    } finally {
      activeControlTypeRef.current = null;
      stopInFlightRef.current = false;
      setIsControlling(false);
    }
  }, [video.id, onError]);

  const bindPress = (direction: PTZDirection) => ({
    onMouseDown: () => startMove(direction),
    onMouseUp: stopMove,
    onMouseLeave: stopMove,
    onTouchStart: () => startMove(direction),
    onTouchEnd: stopMove,
    onTouchCancel: stopMove,
  });

  const handleCreatePreset = async () => {
    try {
      setBusy(true);
      const payload = newPresetName.trim() ? { name: newPresetName.trim() } : {};
      const created = await createPreset(video.id, payload);
      setPresets((prev) => {
        const exists = prev.some((p) => p.token === created.token);
        return exists ? prev.map((p) => (p.token === created.token ? created : p)) : [...prev, created];
      });
      setNewPresetName('');
      onSuccess?.('预置点已保存');
    } catch (err: any) {
      onError?.(`保存预置点失败: ${err.message || err}`);
    } finally {
      setBusy(false);
    }
  };

  const handleGotoPreset = async (token: string) => {
    try {
      setBusy(true);
      await gotoPreset(video.id, token, fixedControlSpeed);
      onSuccess?.('已跳转到预置点');
    } catch (err: any) {
      onError?.(`预置点跳转失败: ${err.message || err}`);
    } finally {
      setBusy(false);
    }
  };

  const handleDeletePreset = async (token: string) => {
    try {
      setBusy(true);
      await deletePreset(video.id, token);
      setPresets((prev) => prev.filter((p) => p.token !== token));
      setSelectedPresetTokens((prev) => prev.filter((item) => item !== token));
      onSuccess?.('预置点已删除');
    } catch (err: any) {
      onError?.(`删除预置点失败: ${err.message || err}`);
    } finally {
      setBusy(false);
    }
  };

  const handleBatchDeletePresets = async (tokens: string[], deleteAll: boolean = false) => {
    const uniqueTokens = Array.from(new Set(tokens));
    if (uniqueTokens.length === 0) {
      onError?.(deleteAll ? '当前没有可删除的预置点' : '请先勾选要删除的预置点');
      return;
    }

    const confirmMessage = deleteAll
      ? `确定删除全部 ${uniqueTokens.length} 个预置点吗？该操作不可撤销。`
      : `确定删除已勾选的 ${uniqueTokens.length} 个预置点吗？`;

    if (!window.confirm(confirmMessage)) {
      return;
    }

    try {
      setBusy(true);
      const result = await deletePresetsBulk(video.id, uniqueTokens);
      const deletedTokens = result.deleted_tokens || [];
      const failedTokens = result.failed_tokens || [];

      if (deletedTokens.length > 0) {
        setPresets((prev) => prev.filter((p) => !deletedTokens.includes(p.token)));
        setSelectedPresetTokens((prev) => prev.filter((item) => !deletedTokens.includes(item)));
      }

      // 以设备实际状态为准，避免本地状态和摄像头返回不一致。
      await loadPresetsAndCruiseStatus();

      if (failedTokens.length === 0) {
        onSuccess?.(`已批量删除 ${deletedTokens.length} 个预置点`);
      } else {
        onError?.(`批量删除完成：成功 ${deletedTokens.length}，失败 ${failedTokens.length}`);
      }
    } catch (err: any) {
      onError?.(`批量删除预置点失败: ${err.message || err}`);
    } finally {
      setBusy(false);
    }
  };

  const handleToggleCruisePreset = (token: string) => {
    setSelectedPresetTokens((prev) => {
      if (prev.includes(token)) {
        return prev.filter((item) => item !== token);
      }
      return [...prev, token];
    });
  };

  const handleStartCruise = async () => {
    if (selectedPresetTokens.length < 2) {
      onError?.('常规巡航至少需要选择两个预置点');
      return;
    }
    try {
      setBusy(true);
      await startCruise(video.id, {
        preset_tokens: selectedPresetTokens,
        dwell_seconds: dwellSeconds,
        rounds: null,
      });
      setIsCruising(true);
      onSuccess?.('常规巡航已启动');
    } catch (err: any) {
      onError?.(`启动巡航失败: ${err.message || err}`);
    } finally {
      setBusy(false);
    }
  };

  const handleStopCruise = async () => {
    try {
      setBusy(true);
      await stopCruise(video.id);
      setIsCruising(false);
      onSuccess?.('常规巡航已停止');
    } catch (err: any) {
      onError?.(`停止巡航失败: ${err.message || err}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-slate-950/70 rounded-lg shadow-md p-4 select-none text-slate-100 border border-blue-300/25">
      <h3 className="text-lg font-semibold mb-4 text-cyan-200">云台控制</h3>

      {/* 方向控制键盘 */}
      <div className="flex flex-col items-center gap-2 mb-6">
        {/* 上 */}
        <button
          {...bindPress('up')}
          disabled={!canPTZ}
          className="p-2 rounded-lg bg-blue-500 hover:bg-blue-600 active:bg-blue-700 text-white transition disabled:opacity-40"
          title="向上"
        >
          <ChevronUp size={24} />
        </button>

        {/* 左、中、右 */}
        <div className="flex gap-2">
          <button
            {...bindPress('left')}
            disabled={!canPTZ}
            className="p-2 rounded-lg bg-blue-500 hover:bg-blue-600 active:bg-blue-700 text-white transition disabled:opacity-40"
            title="向左"
          >
            <ChevronLeft size={24} />
          </button>

          <div className="px-6 py-2 bg-gray-100 rounded-lg flex items-center justify-center min-w-[120px]">
            {isControlling ? (
              <div className="flex items-center gap-2">
                <Loader size={20} className="animate-spin text-blue-500" />
                <span className="text-sm text-gray-700">移动中...</span>
              </div>
            ) : (
              <span className="text-sm text-gray-700">按住方向可持续移动</span>
            )}
          </div>

          <button
            {...bindPress('right')}
            disabled={!canPTZ}
            className="p-2 rounded-lg bg-blue-500 hover:bg-blue-600 active:bg-blue-700 text-white transition disabled:opacity-40"
            title="向右"
          >
            <ChevronRight size={24} />
          </button>
        </div>

        {/* 下 */}
        <button
          {...bindPress('down')}
          disabled={!canPTZ}
          className="p-2 rounded-lg bg-blue-500 hover:bg-blue-600 active:bg-blue-700 text-white transition disabled:opacity-40"
          title="向下"
        >
          <ChevronDown size={24} />
        </button>

        <div className="flex items-center gap-2 mt-2">
          <button
            {...bindPress('zoom_out')}
            disabled={!canPTZ || !canZoom}
            className="p-2 rounded-lg bg-indigo-500 hover:bg-indigo-600 active:bg-indigo-700 text-white transition disabled:opacity-40"
            title="缩小"
          >
            <ZoomOut size={20} />
          </button>
          <button
            {...bindPress('zoom_in')}
            disabled={!canPTZ || !canZoom}
            className="p-2 rounded-lg bg-indigo-500 hover:bg-indigo-600 active:bg-indigo-700 text-white transition disabled:opacity-40"
            title="放大"
          >
            <ZoomIn size={20} />
          </button>
        </div>
      </div>

      {/* 参数控制 */}
      <div className="border-t pt-4 space-y-3">
        {/*
          临时停用“变速控制”UI：
          <div>
            <label className="block text-sm font-medium text-slate-200 mb-1">
              速度: {speed.toFixed(1)}
            </label>
            <input
              type="range"
              min="0.1"
              max="1.0"
              step="0.1"
              value={speed}
              onChange={(e) => setSpeed(parseFloat(e.target.value))}
              className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer"
            />
            <div className="text-xs text-slate-400 mt-1">范围: 0.1 (慢) - 1.0 (快)</div>
          </div>
        */}
        <div className="text-xs text-slate-400">控制速度：固定 0.5</div>

        <div className="pt-2 border-t border-blue-300/20 space-y-2">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-semibold text-cyan-200">预置点</h4>
            <button
              onClick={loadPresetsAndCruiseStatus}
              className="p-1 rounded bg-slate-800 hover:bg-slate-700"
              title="刷新预置点"
            >
              <RefreshCw size={14} />
            </button>
          </div>

          <div className="flex gap-2">
            <input
              value={newPresetName}
              onChange={(e) => setNewPresetName(e.target.value)}
              placeholder="预置点名称（可选）"
              className="flex-1 bg-slate-900 border border-blue-300/25 rounded px-2 py-1.5 text-xs outline-none focus:border-cyan-300"
            />
            <button
              onClick={handleCreatePreset}
              disabled={busy || !canPreset}
              className="px-2 py-1.5 rounded bg-cyan-500 hover:bg-cyan-400 text-slate-900"
              title="保存预置点"
            >
              <Plus size={14} />
            </button>
          </div>

          <div className="max-h-32 overflow-y-auto space-y-1 pr-1">
            {presets.length === 0 ? (
              <p className="text-xs text-slate-400">暂无预置点</p>
            ) : (
              presets.map((preset) => (
                <div key={preset.token} className="flex items-center gap-1 bg-slate-900/80 border border-slate-700 rounded px-2 py-1">
                  <input
                    type="checkbox"
                    checked={selectedPresetTokens.includes(preset.token)}
                    onChange={() => handleToggleCruisePreset(preset.token)}
                    disabled={!canPreset}
                  />
                  <button
                    onClick={() => handleGotoPreset(preset.token)}
                    className="flex-1 text-left text-xs text-slate-200 hover:text-cyan-300 truncate disabled:opacity-40"
                    title={preset.token}
                    disabled={!canPreset}
                  >
                    {preset.name || preset.token}
                  </button>
                  <button
                    onClick={() => handleDeletePreset(preset.token)}
                    className="text-rose-300 hover:text-rose-200 disabled:opacity-40"
                    title="删除预置点"
                    disabled={!canPreset}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))
            )}
          </div>

          <div className="flex gap-2 pt-1">
            <button
              onClick={() => handleBatchDeletePresets(selectedPresetTokens, false)}
              disabled={busy || selectedPresetTokens.length === 0 || !canPreset}
              className="flex-1 px-2 py-1.5 rounded bg-rose-500/90 hover:bg-rose-400 disabled:opacity-50 text-slate-950 text-xs font-semibold"
              title="批量删除已勾选预置点"
            >
              删除已选({selectedPresetTokens.length})
            </button>
            <button
              onClick={() => handleBatchDeletePresets(presets.map((p) => p.token), true)}
              disabled={busy || presets.length === 0 || !canPreset}
              className="flex-1 px-2 py-1.5 rounded bg-rose-700/90 hover:bg-rose-600 disabled:opacity-50 text-slate-100 text-xs font-semibold"
              title="清空全部预置点"
            >
              清空全部({presets.length})
            </button>
          </div>
        </div>

        <div className="pt-2 border-t border-blue-300/20 space-y-2">
          <h4 className="text-sm font-semibold text-cyan-200 flex items-center gap-1">
            <Move size={14} /> 常规巡航
          </h4>
          <div>
            <label className="block text-xs text-slate-300 mb-1">停留秒数</label>
            <input
              type="number"
              min={1}
              max={120}
              value={dwellSeconds}
              onChange={(e) => setDwellSeconds(Math.max(1, Math.min(120, Number(e.target.value) || 8)))}
              className="w-full bg-slate-900 border border-blue-300/25 rounded px-2 py-1.5 text-xs outline-none focus:border-cyan-300"
            />
          </div>

          <div className="flex gap-2">
            <button
              onClick={handleStartCruise}
              disabled={busy || isCruising || !canCruise}
              className="flex-1 flex items-center justify-center gap-1 py-1.5 rounded bg-emerald-500 hover:bg-emerald-400 disabled:opacity-50 text-slate-950 text-xs font-semibold"
            >
              <Play size={12} /> 启动巡航
            </button>
            <button
              onClick={handleStopCruise}
              disabled={busy || !isCruising || !canCruise}
              className="flex-1 flex items-center justify-center gap-1 py-1.5 rounded bg-rose-500 hover:bg-rose-400 disabled:opacity-50 text-slate-950 text-xs font-semibold"
            >
              <Square size={12} /> 停止巡航
            </button>
          </div>

          <p className="text-xs text-slate-400">
            已选巡航点: {selectedPresetTokens.length}，状态: {isCruising ? '运行中' : '未运行'}
          </p>
        </div>
      </div>

      {/* 摄像头信息 */}
      <div className="mt-4 p-3 bg-slate-900 rounded-lg border border-blue-300/20">
        <p className="text-sm text-slate-300">
          <span className="font-medium">摄像头:</span> {video.name}
        </p>
        <p className="text-sm text-slate-300">
          <span className="font-medium">地址:</span> {video.ip_address || '-'}:{video.port || 80}
        </p>
        <p className="text-xs text-slate-400 mt-1">
          平台: {video.platform_type || 'onvif'} | PTZ来源: {video.ptz_source || 'onvif'}
        </p>
      </div>
    </div>
  );
};

export default PTZControlPanel;
