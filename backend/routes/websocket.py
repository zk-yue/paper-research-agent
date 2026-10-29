"""Task event subscriptions with cleanup on every disconnect path."""

import asyncio

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.task_manager import task_manager

router = APIRouter(tags=["events"])


@router.websocket("/ws/{task_id}")
async def ws_endpoint(websocket: WebSocket, task_id: str):
    await websocket.accept()
    task = task_manager.get_task(task_id)
    if task is None:
        await websocket.send_json({"type": "not_found"})
        await websocket.close()
        return

    queue = task_manager.subscribe_ws(task_id)
    try:
        await websocket.send_json({"type": "init", "task": task.model_dump()})
        async with anyio.create_task_group() as group:

            async def send_events():
                try:
                    while True:
                        try:
                            event = await asyncio.wait_for(queue.get(), timeout=30)
                        except TimeoutError:
                            event = {"type": "ping"}
                        await websocket.send_json(event)
                except WebSocketDisconnect:
                    pass
                finally:
                    group.cancel_scope.cancel()

            group.start_soon(send_events)
            try:
                async for _ in websocket.iter_text():
                    pass
            finally:
                group.cancel_scope.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        task_manager.unsubscribe_ws(task_id, queue)
