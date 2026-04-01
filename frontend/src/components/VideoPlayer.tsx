import React, { useEffect, useRef, useState } from 'react';

interface VideoPlayerProps {
  src: string;
  playType?: string;
  accessToken?: string;
  onError?: (error: string) => void;
}

const VideoPlayer: React.FC<VideoPlayerProps> = ({ src, playType, accessToken, onError }) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const ezContainerRef = useRef<HTMLDivElement>(null);
  const ezContainerIdRef = useRef(`ez-player-${Math.random().toString(36).slice(2)}`);
  const playerRef = useRef<any>(null);
  const ezPlayerRef = useRef<any>(null);
  const retryCountRef = useRef(0);
  const retryTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<'connecting' | 'connected' | 'error'>('connecting');
  const [useIframeFallback, setUseIframeFallback] = useState(false);
  const [iframeLoaded, setIframeLoaded] = useState(false);

  const maxRetries = 10; // 增加到10次
  const retryDelay = 1000; // 减少到1秒，让重试更频繁

  const cleanupPlayer = () => {
    if (retryTimeoutRef.current) {
      clearTimeout(retryTimeoutRef.current);
      retryTimeoutRef.current = null;
    }
    
    if (playerRef.current) {
      try {
        playerRef.current.destroy();
        playerRef.current = null;
      } catch (e) {
        console.warn('Error destroying player:', e);
      }
    }

    if (ezPlayerRef.current) {
      try {
        ezPlayerRef.current.stop?.();
        ezPlayerRef.current.destroy?.();
      } catch (e) {
        console.warn('Error destroying EZUIKit player:', e);
      } finally {
        ezPlayerRef.current = null;
      }
    }
  };

  const initPlayer = () => {
    try {
      const effectivePlayType = (playType || '').toLowerCase();
      const isEzopen = effectivePlayType === 'ezopen' || (src || '').toLowerCase().startsWith('ezopen://');

      if (isEzopen) {
        const EZUIKit = (window as any).EZUIKit;
        if (EZUIKit && ezContainerRef.current) {
          try {
            const PlayerCtor = EZUIKit.EZUIKitPlayer || EZUIKit.EZUIPlayer;
            if (!PlayerCtor) {
              throw new Error('SDK 已加载但未找到可用播放器构造器');
            }
            ezPlayerRef.current = new PlayerCtor({
              id: ezContainerIdRef.current,
              url: src,
              accessToken: accessToken,
              autoplay: true,
              width: '100%',
              height: '100%'
            });
            setConnectionStatus('connected');
            setUseIframeFallback(false);
            return;
          } catch (e: any) {
            // SDK 构造失败时切换到 iframe 兜底，不立即判定为失败。
            setConnectionStatus('connecting');
            setUseIframeFallback(true);
            onError?.(`萤石播放器初始化失败，已切换备用播放通道: ${e?.message || e}`);
            return;
          }
        }
        setConnectionStatus('connecting');
        setUseIframeFallback(true);
        onError?.('当前页面未加载萤石播放器 SDK，已切换备用播放通道');
        return;
      }

      if (!videoRef.current) return;

      const flvjs = (window as any).flvjs;
      
      if (!flvjs) {
        console.warn('flv.js not loaded yet, using native video player');
        if (videoRef.current && src) {
          videoRef.current.src = src;
          videoRef.current.play().catch(() => {
            console.warn('Autoplay blocked by browser policy');
          });
        }
        return;
      }

      // Ensure FLV URL
      let flvUrl = src;
      if (src && src.includes('.m3u8')) {
        flvUrl = src.replace('/index.m3u8', '.flv');
      }

      if (!flvUrl) {
        console.warn('No stream URL provided');
        setConnectionStatus('error');
        return;
      }

      if (flvjs.isSupported && flvjs.isSupported()) {
        // 清理之前的播放器
        if (playerRef.current) {
          try {
            playerRef.current.destroy();
          } catch (e) {
            console.warn('Error destroying previous player:', e);
          }
        }

        try {
          console.log(`Initializing player (attempt ${retryCountRef.current + 1}/${maxRetries + 1})`);
          
          playerRef.current = flvjs.createPlayer({
            type: 'flv',
            url: flvUrl,
            isLive: true,
            hasAudio: false,
            hasVideo: true,
            deferredLoadThreshold: 0,
            bufferingDuration: 0.5,
            bufferingTimeout: 3000,
            stashInitialSize: 128 * 1024,
            autoplay: true
          });

          if (playerRef.current && videoRef.current) {
            playerRef.current.attachMediaElement(videoRef.current);
            playerRef.current.load();
            
            // 错误处理
            playerRef.current.on('error', (type: string, detail: any, msg: string) => {
              console.error('FLV player error:', { type, detail, msg });
              
              // NETWORK_ERROR 和 MEDIA_ERROR 都需要重试
              if (retryCountRef.current < maxRetries) {
                console.log(`Stream not ready, retrying... (${retryCountRef.current + 1}/${maxRetries})`);
                setConnectionStatus('connecting');
                retryCountRef.current++;
                
                // 清理当前播放器
                cleanupPlayer();
                
                // 延迟重试
                retryTimeoutRef.current = setTimeout(() => {
                  initPlayer();
                }, retryDelay);
              } else {
                const errorMsg = `无法连接到视频流（已重试 ${maxRetries} 次）。请检查摄像头是否在线或稍后重试。`;
                console.error(errorMsg);
                setConnectionStatus('error');
                onError?.(errorMsg);
              }
            });

            // 成功连接
            playerRef.current.on('statistics_info', (stats: any) => {
              if (connectionStatus !== 'connected') {
                console.log('✅ Stream connected successfully');
                setConnectionStatus('connected');
                retryCountRef.current = 0; // 重置重试计数
              }
            });

            // 监听播放事件
            if (videoRef.current) {
              videoRef.current.onplaying = () => {
                console.log('Video is playing');
                setConnectionStatus('connected');
                retryCountRef.current = 0;
              };
            }

            // 开始播放
            playerRef.current.play().catch((e: any) => {
              console.warn('Auto-play failed:', e);
              // 自动播放失败不算错误，浏览器策略导致的
            });
          }
        } catch (e) {
          console.error('Failed to create FLV player:', e);
          
          if (retryCountRef.current < maxRetries) {
            setConnectionStatus('connecting');
            retryCountRef.current++;
            
            retryTimeoutRef.current = setTimeout(() => {
              initPlayer();
            }, retryDelay);
          } else {
            setConnectionStatus('error');
            onError?.(`无法创建播放器: ${e}`);
          }
        }
      } else {
        console.warn('FLV is not supported, falling back to native player');
        if (videoRef.current) {
          videoRef.current.src = flvUrl;
          videoRef.current.play().catch(() => {
            console.warn('Autoplay blocked');
          });
        }
      }
    } catch (e) {
      console.error('Unexpected error initializing player:', e);
      setConnectionStatus('error');
    }
  };

  useEffect(() => {
    if (!src) return;
    
    console.log('Video source changed:', src);
    retryCountRef.current = 0; // 重置重试计数
    setConnectionStatus('connecting');
    setUseIframeFallback(false);
    setIframeLoaded(false);
    initPlayer();

    return () => {
      console.log('Cleaning up video player');
      cleanupPlayer();
    };
  }, [src, playType, accessToken]);

  return (
    <div className="w-full h-full bg-black rounded-lg overflow-hidden relative">
      {(playType || '').toLowerCase() === 'ezopen' || (src || '').toLowerCase().startsWith('ezopen://') ? (
        <div className="w-full h-full flex flex-col items-center justify-center text-white gap-3 p-4">
          {useIframeFallback ? (
            <iframe
              className="w-full h-full border-0"
              src={`https://open.ys7.com/ezopen/h5/iframe?url=${encodeURIComponent(src)}&autoplay=1&accessToken=${encodeURIComponent(accessToken || '')}`}
              allow="autoplay; fullscreen"
              title="ezopen-player"
              onLoad={() => {
                setIframeLoaded(true);
                setConnectionStatus('connected');
              }}
              onError={() => {
                setIframeLoaded(false);
                setConnectionStatus('error');
                onError?.('备用播放器加载失败，请检查 token、设备在线状态或浏览器策略');
              }}
            />
          ) : (
            <div id={ezContainerIdRef.current} ref={ezContainerRef} className="w-full h-full" />
          )}
          {connectionStatus === 'error' && !useIframeFallback && (
            <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/80 p-4 text-center">
              <div className="text-lg font-semibold">EZOPEN 播放器不可用</div>
              <div className="text-sm text-gray-300 mt-2 break-all">{src}</div>
            </div>
          )}
          {useIframeFallback && !iframeLoaded && connectionStatus === 'connecting' && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/55 p-4 text-center">
              <div className="text-sm text-gray-200">正在加载备用播放器...</div>
            </div>
          )}
        </div>
      ) : (
      <video
        ref={videoRef}
        className="w-full h-full object-contain"
        controls
        autoPlay
        muted // 添加 muted 避免浏览器自动播放策略限制
      />
      )}
      
      {/* 连接状态指示器 */}
      <div className="absolute top-2 right-2 flex items-center gap-2 bg-black/60 px-3 py-1 rounded text-xs">
        <div className={`w-2 h-2 rounded-full ${
          connectionStatus === 'connected' ? 'bg-green-500 animate-pulse' :
          connectionStatus === 'connecting' ? 'bg-yellow-500 animate-pulse' :
          'bg-red-500'
        }`} />
        <span className="text-white">
          {connectionStatus === 'connected' ? '直播中' :
           connectionStatus === 'connecting' ? `连接中${retryCountRef.current > 0 ? ` (${retryCountRef.current}/${maxRetries})` : '...'}` :
           '连接失败'}
        </span>
      </div>
      
      {/* 错误提示 */}
      {connectionStatus === 'error' && !((playType || '').toLowerCase() === 'ezopen' || (src || '').toLowerCase().startsWith('ezopen://')) && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/80">
          <div className="text-center text-white p-6">
            <div className="text-4xl mb-4">⚠️</div>
            <div className="text-lg font-semibold mb-2">视频流连接失败</div>
            <div className="text-sm text-gray-300 mb-4">
              请检查摄像头是否在线，或稍后重试
            </div>
            <button
              onClick={() => {
                retryCountRef.current = 0;
                setConnectionStatus('connecting');
                initPlayer();
              }}
              className="px-4 py-2 bg-blue-500 hover:bg-blue-600 rounded text-white"
            >
              重新连接
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default VideoPlayer;