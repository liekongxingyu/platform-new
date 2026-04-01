import React, { useState, useEffect } from 'react';
import {
  LayoutDashboard,
  Video,
  MapPin,
  ShieldAlert,
  Users,
  Bell,
  Settings,
  ChevronDown,
  User,
  Power,
  Sun,
  Cloud,
  CloudRain,
  Snowflake,
  KeyRound,
  Loader2,
  Briefcase
} from 'lucide-react';
import { MenuKey } from './types';
import Dashboard from './views/Dashboard';
import FenceManagement from './views/Fence/index';
import ProjectManagement from './views/Project/index';
import VideoCenter from './views/VideoCenter';
import VideoPlayback from './views/VideoPlayback';
import TrackPlayback from './views/TrackPlayback';
import SettingsView from './views/SettingsView';
import GroupCall from './views/GroupCall';
import AlarmRecords from './views/Alarm/index';

// --------------------
// ✅ 登录接口地址（你后端真实路径）
// --------------------
const LOGIN_API = '/api/auth/login';

type BranchInfo = {
  id: number;
  province?: string;
  name?: string;
  coord?: [number, number] | null;
  address?: string | null;
  project?: string | null;
  manager?: string | null;
  phone?: string | null;
  deviceCount?: number;
  status?: string;
  updatedAt?: string | null;
  remark?: string | null;
};

type LoginResp = {
  userId?: number;
  username?: string;
  full_name?: string;
  role?: string; // HQ / BRANCH
  department_id?: number | null;
  branch?: BranchInfo | null;
};

// --- Login Component ---
const LoginView = ({ onLogin }: { onLogin: () => void }) => {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    try {
      const res = await fetch(LOGIN_API, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });

      if (!res.ok) {
        throw new Error(`login http ${res.status}`);
      }

      const data: LoginResp = await res.json();

      // ✅ 你后端登录不会返回 token，所以这里直接保存“身份信息”
      const role = (data.role || 'HQ').toUpperCase();
      const depId = data.department_id ?? null;

      // 保存一个总对象，Dashboard / 其它页面都可以直接读
      localStorage.setItem(
        'auth',
        JSON.stringify({
          userId: data.userId ?? null,
          username: data.username ?? username,
          full_name: data.full_name ?? null,
          role,
          department_id: depId,
          branch: data.branch ?? null,
        })
      );

      // 兼容你后续 Dashboard 读取（更简单）
      localStorage.setItem('role', role);
      localStorage.setItem('department_id', depId === null ? '' : String(depId));
      localStorage.setItem('username', data.username ?? username);

      // 标记已登录
      localStorage.setItem('logged_in', '1');

      onLogin();
    } catch (err) {
      console.error('login failed:', err);
      alert('登录失败：请确认账号密码是否正确，以及后端是否已启动（/api/auth/login）');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="h-screen w-screen flex items-center justify-center bg-gradient-to-br from-gray-50 to-gray-100">
      <div className="relative z-10 w-[450px] bg-white p-8 rounded-2xl shadow-xl animate-in fade-in zoom-in duration-500 border border-gray-200">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-gray-800 tracking-wider mb-2">公司安全监控系统</h1>
          <p className="text-gray-500 text-sm">现场安全监控</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-6">
          <div className="space-y-2">
            <label className="text-xs font-bold text-gray-600 tracking-wider ml-1">账户</label>
            <div className="relative group">
              <User
                className="absolute left-3 top-3 text-gray-400 group-focus-within:text-blue-500 transition-colors"
                size={20}
              />
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full bg-gray-50 border border-gray-300 rounded-lg py-3 pl-10 pr-4 text-gray-800 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 transition-all"
                placeholder="请输入账号"
              />
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-xs font-bold text-gray-600 tracking-wider ml-1">密码</label>
            <div className="relative group">
              <KeyRound
                className="absolute left-3 top-3 text-gray-400 group-focus-within:text-blue-500 transition-colors"
                size={20}
              />
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full bg-gray-50 border border-gray-300 rounded-lg py-3 pl-10 pr-4 text-gray-800 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 transition-all"
                placeholder="请输入密码"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-bold py-3 rounded-lg shadow-lg shadow-blue-500/50 transition-all transform active:scale-95 disabled:opacity-70 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {loading ? (
              <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin"></div>
            ) : (
              <>
                登录系统 <span className="text-xl">→</span>
              </>
            )}
          </button>
        </form>

        <div className="mt-8 pt-4 border-t border-gray-200 text-center text-xs text-gray-400">
          © 2024 智能安全系统 V2.0
        </div>
      </div>
    </div>
  );
};

// --- Sidebar Component ---
const Sidebar = ({
  activeMenu,
  setActiveMenu,
}: {
  activeMenu: MenuKey;
  setActiveMenu: (key: MenuKey) => void;
}) => {
  const menuItems = [
    { key: MenuKey.DASHBOARD, label: '现场管理', icon: LayoutDashboard },
    { key: MenuKey.VIDEO, label: '视频中心', icon: Video },
    { key: MenuKey.VIDEO_PLAYBACK, label: '视频回放', icon: MapPin },
    // { key: MenuKey.TRACK, label: '轨迹回放', icon: MapPin },
    { key: MenuKey.FENCE, label: '电子围栏', icon: ShieldAlert },
    { key: MenuKey.PROJECT, label: '项目管理', icon: Briefcase },
    // { key: MenuKey.GROUP_CALL, label: '群组通话', icon: Users },
    { key: MenuKey.ALARM, label: '报警记录', icon: Bell },
    { key: MenuKey.SETTINGS, label: '管理员设置', icon: Settings },
  ];

  return (
    <div
      className="w-64 h-full flex flex-col relative z-20"
      style={{
        background: 'linear-gradient(180deg, #0b4db3 0%, #0a3f99 42%, #0a2f73 100%)',
      }}
    >
      <div className="p-4 flex items-center justify-center border-b border-white/10">
        {/* 你可以放 logo/标题 */}
      </div>

      <nav className="flex-1 overflow-y-auto py-4">
        {menuItems.map((item, idx) => (
          <button
            key={`${item.key}-${idx}`}
            onClick={() => setActiveMenu(item.key)}
            className={`w-full flex items-center gap-3 px-6 py-4 text-sm transition-all duration-200 border-l-4
              ${activeMenu === item.key
                ? 'text-white bg-white/20 border-white font-semibold'
                : 'text-blue-100 hover:text-white hover:bg-white/10 border-transparent'
              }`}
          >
            <item.icon size={18} />
            <span>{item.label}</span>
          </button>
        ))}
      </nav>

      <div className="p-4 text-xs text-white/70 text-center border-t border-white/10">
        现场安全系统 V2.0
      </div>
    </div>
  );
};

// --- Header Component ---
const Header = ({ onLogout }: { onLogout: () => void }) => {
  const [currentTime, setCurrentTime] = useState(new Date());
  const [weather, setWeather] = useState<{ temp: number; code: number } | null>(null);
  const [isLoadingWeather, setIsLoadingWeather] = useState(true);

  useEffect(() => {
    // Clock Timer
    const timer = setInterval(() => {
      setCurrentTime(new Date());
    }, 1000);

    // Weather Fetcher (Open-Meteo API)
    // Coordinates: Shanghai (31.2304, 121.4737)
    const fetchWeather = async () => {
      try {
        setIsLoadingWeather(true);
        const res = await fetch(
          'https://api.open-meteo.com/v1/forecast?latitude=31.2304&longitude=121.4737&current=temperature_2m,weather_code&timezone=Asia%2FShanghai'
        );
        const data = await res.json();

        if (data.current) {
          setWeather({
            temp: Math.round(data.current.temperature_2m),
            code: data.current.weather_code,
          });
        }
      } catch (error) {
        console.error('Failed to fetch weather data:', error);
      } finally {
        setIsLoadingWeather(false);
      }
    };

    fetchWeather();
    // Refresh weather every 15 minutes
    const weatherTimer = setInterval(fetchWeather, 15 * 60 * 1000);

    return () => {
      clearInterval(timer);
      clearInterval(weatherTimer);
    };
  }, []);

  const formatDate = (date: Date) => {
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(date);
  };

  const formatTime = (date: Date) => {
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).format(date);
  };

  const getWeatherIcon = (code: number) => {
    // WMO Weather interpretation codes
    if (code === 0 || code === 1) return <Sun size={16} className="text-yellow-300" />;
    if (code >= 2 && code <= 3) return <Cloud size={16} className="text-white/80" />;
    if ((code >= 51 && code <= 67) || (code >= 80 && code <= 82))
      return <CloudRain size={16} className="text-blue-200" />;
    if (code >= 71 && code <= 77) return <Snowflake size={16} className="text-cyan-100" />;
    // Default fallback
    return <Cloud size={16} className="text-white/80" />;
  };

  return (
    <header
      className="h-16 flex items-center justify-between px-6 relative z-20 shadow-md"
      style={{
        background: 'linear-gradient(180deg, #0b4db3 0%, #0a3f99 42%, #0a2f73 100%)',
      }}
    >
      <div className="flex items-center gap-4">
        <h1 className="text-xl font-bold text-white tracking-widest uppercase">公司安全监控系统</h1>
      </div>

      <div className="flex items-center gap-6">
        <div className="flex items-center gap-4 text-white text-sm font-mono bg-white/20 px-4 py-1.5 rounded-full border border-white/20">
          <div className="flex items-center gap-2 min-w-[60px] justify-center">
            {isLoadingWeather ? (
              <Loader2 size={14} className="animate-spin text-white/70" />
            ) : weather ? (
              <>
                {getWeatherIcon(weather.code)}
                <span>{weather.temp}°C</span>
              </>
            ) : (
              <span className="text-xs text-white/70">无数据</span>
            )}
          </div>

          <span className="text-white/40">|</span>
          <span>{formatDate(currentTime)}</span>
          <span className="text-white font-bold w-20 text-center">{formatTime(currentTime)}</span>
        </div>

        <div className="flex items-center gap-4">
          <div className="relative">
            <Bell size={20} className="text-white/80 hover:text-white cursor-pointer" />
            <span className="absolute -top-1 -right-1 w-2 h-2 bg-red-500 rounded-full animate-pulse"></span>
          </div>

          <div className="flex items-center gap-2 cursor-pointer hover:bg-white/10 p-2 rounded-lg transition-colors group">
            <div className="w-8 h-8 rounded-full bg-white/20 flex items-center justify-center border border-white/20 group-hover:border-white/40">
              <User size={16} className="text-white" />
            </div>
            <div className="flex flex-col items-end">
              <span className="text-xs text-white">管理员</span>
              <span className="text-[10px] text-white/70">系统管理员</span>
            </div>
            <ChevronDown size={14} className="text-white/70" />
          </div>

          <button
            onClick={onLogout}
            className="text-white/80 hover:text-red-400 transition-colors"
            title="退出登录"
          >
            <Power size={20} />
          </button>
        </div>
      </div>
    </header>
  );
};

// --- Main App Component ---
export default function App() {
  const [activeMenu, setActiveMenu] = useState<MenuKey>(MenuKey.FENCE);
  const [isLoggedIn, setIsLoggedIn] = useState(false);

  // ✅ 启动时如果本地有 logged_in 或 auth，认为已登录（不再依赖 access_token）
  useEffect(() => {
    const ok = localStorage.getItem('logged_in');
    const auth = localStorage.getItem('auth');
    if (ok === '1' || (auth && auth.length > 0)) {
      setIsLoggedIn(true);
    }
  }, []);

  const logout = () => {
    localStorage.removeItem('logged_in');
    localStorage.removeItem('auth');
    localStorage.removeItem('role');
    localStorage.removeItem('department_id');
    localStorage.removeItem('username');
    setIsLoggedIn(false);
  };

  if (!isLoggedIn) {
    return <LoginView onLogin={() => setIsLoggedIn(true)} />;
  }

  const renderContent = () => {
    switch (activeMenu) {
      case MenuKey.DASHBOARD:
        return <Dashboard />;
      case MenuKey.VIDEO:
        return <VideoCenter />;
      case MenuKey.VIDEO_PLAYBACK:
        return <VideoPlayback />;
      case MenuKey.FENCE:
        return <FenceManagement />;
      case MenuKey.PROJECT:
        return <ProjectManagement />;
      case MenuKey.TRACK:
        return <TrackPlayback />;
      case MenuKey.SETTINGS:
        return <SettingsView />;
      case MenuKey.GROUP_CALL:
        return <GroupCall />;
      case MenuKey.ALARM:
        return <AlarmRecords />;
      default:
        return <Dashboard />;
    }
  };

  return (
    <div
      className="flex h-screen w-screen overflow-hidden"
      style={{
        background: 'linear-gradient(180deg, #0b4db3 0%, #0a3f99 42%, #0a2f73 100%)',
      }}
    >
      <div className="relative z-10 flex w-full h-full">
        <Sidebar activeMenu={activeMenu} setActiveMenu={setActiveMenu} />
        <div
          className="flex-1 flex flex-col h-full overflow-hidden"
          style={{
            background: 'linear-gradient(180deg, #0b4db3 0%, #0a3f99 42%, #0a2f73 100%)',
          }}
        >

          <Header onLogout={logout} />
          <main className="flex-1 overflow-hidden relative bg-transparent">
            {/* Decorative HUD Elements */}
            <div className="absolute top-0 left-0 w-32 h-32 border-t-2 border-l-2 border-blue-400/20 rounded-tl-3xl pointer-events-none"></div>
            <div className="absolute bottom-0 right-0 w-32 h-32 border-b-2 border-r-2 border-blue-400/20 rounded-br-3xl pointer-events-none"></div>

            {renderContent()}
          </main>
        </div>
      </div>
    </div>
  );
}
