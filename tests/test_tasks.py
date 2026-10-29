import asyncio
import json

from backend.schemas import StepInfo
from backend.task_manager import TaskManager
from backend.task_paths import task_metadata_file


def test_recovery_marks_pending_and_running_failed_and_keeps_completed():
    manager = TaskManager()
    for status in ("pending", "running", "completed"):
        task = manager.create_task(keywords=[status])
        manager.update_task(task.id, status=status)
    restored = TaskManager()
    restored.load_existing_tasks()
    assert len(restored.tasks) == 3
    for task in restored.list_tasks():
        assert task.status == ("completed" if task.keywords == ["completed"] else "failed")
        assert json.loads(task_metadata_file(task.id).read_text())["status"] == task.status


def test_delete_does_not_reset_cancellation_or_recreate_metadata():
    manager = TaskManager()
    task = manager.create_task(keywords=["test"])
    path = task_metadata_file(task.id)
    assert manager.delete_task(task.id)
    assert manager.is_cancelled(task.id)
    assert manager.update_task(task.id, status="completed") is None
    assert not path.exists()


def test_negative_step_index_cannot_modify_last_step():
    manager = TaskManager()
    task = manager.create_task(keywords=["test"], steps=[StepInfo(label="last", status="pending")])
    manager.set_step(task.id, -1, "done")
    assert task.steps[-1].status == "pending"


def test_deleted_background_job_is_cancelled():
    async def scenario():
        manager = TaskManager()
        task = manager.create_task(keywords=["test"])
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def work():
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                stopped.set()

        manager.start_task(task.id, work())
        await started.wait()
        manager.delete_task(task.id)
        await asyncio.wait_for(stopped.wait(), timeout=1)
        await manager.shutdown()
        assert not manager._running

    asyncio.run(scenario())


def test_broadcast_from_worker_thread_and_backpressure():
    async def scenario():
        manager = TaskManager()
        queue = manager.subscribe_ws("task123")
        await asyncio.to_thread(manager.broadcast, "task123", {"type": "test"})
        assert await asyncio.wait_for(queue.get(), 1) == {"type": "test"}
        for index in range(300):
            manager.broadcast("task123", {"type": "test", "index": index})
        await asyncio.sleep(0)
        assert queue.qsize() <= 256
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        assert {"type": "resync_required"} in events
        manager.unsubscribe_ws("task123", queue)
        assert not manager.ws_subscribers

    asyncio.run(scenario())
