import asyncio
from typing import Optional

alarm_clients = []
_main_event_loop: Optional[asyncio.AbstractEventLoop] = None


def set_main_event_loop(loop: Optional[asyncio.AbstractEventLoop]):
    global _main_event_loop
    _main_event_loop = loop

async def push_alarm(data):

    disconnected = []

    for ws in alarm_clients:
        try:
            await ws.send_json(data)
        except:
            disconnected.append(ws)

    for ws in disconnected:
        alarm_clients.remove(ws)


def push_alarm_threadsafe(data):
    if _main_event_loop and _main_event_loop.is_running():
        asyncio.run_coroutine_threadsafe(push_alarm(data), _main_event_loop)
        return

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(push_alarm(data))
    except RuntimeError:
        # 主事件循环未就绪时，避免在子线程创建独立 loop 发送 websocket。
        pass