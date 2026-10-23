"""The page, Ling tools, and existing scheduler share responsibility records."""
from datetime import datetime, timezone

from test_task_http import request
from test_task_http import task_live as task_live

from nexus.ling import WorkService
from nexus.responsibilities import ResponsibilityService
from nexus.runtime.scheduler import TaskScheduler
from nexus.tools import build
from nexus.web import server as web


def test_page_and_ling_tools_share_records_without_starting_work(task_live):
    base, store, runs = task_live
    status, created = request(base, "/api/responsibilities", "POST", {
        "title": "项目维护", "instruction": "持续关注项目反馈，发现问题时调查。",
    })
    assert status == 201
    assert created["origin"] == "user"
    session = web.STATE["sessions"].create("fake")
    work = WorkService(web.STATE, session, web.STATE["store"].load())
    registry, context = build(root=store.path.parent, work_service=work)
    assert all(registry.get(name) for name in (
        "responsibility_list", "responsibility_save", "responsibility_control"))
    work.responsibilities.save(id=created["id"], summary="先等待新的反馈。", origin="ling")
    _, listing = request(base, "/api/responsibilities")
    saved = listing["responsibilities"][0]
    assert saved["summary"] == "先等待新的反馈。"
    assert saved["origin"] == "user"
    assert "项目维护" in work.context_text()
    assert runs == []
    _, edited = request(base, f"/api/responsibilities/{created['id']}", "PUT", {
        "instruction": "只关注界面反馈。",
    })
    assert edited["instruction"] == "只关注界面反馈。"
    assert work.responsibilities.get(created["id"])["instruction"] == edited["instruction"]
    result = registry.execute("responsibility_save", {
        "title": "灵的观察", "instruction": "定期看看是否出现新的界面问题。",
    }, context)
    assert result.ok
    own = next(item for item in work.responsibilities.list()["responsibilities"]
               if item["title"] == "灵的观察")
    assert own["origin"] == "ling"
    assert store.list() == []


def test_ling_links_work_before_dispatch_and_preserves_reporting_flag(task_live):
    base, store, runs = task_live
    session = web.STATE["sessions"].create("fake")
    work = WorkService(web.STATE, session, web.STATE["store"].load(), input_id=42)
    responsibility = work.responsibilities.save(title="维护", instruction="调查反馈", origin="ling")
    result = work.start("调查一个反馈", responsibility_id=responsibility["id"], report_requested=True)
    metadata = work.responsibilities.work_metadata(result["id"])
    assert metadata["responsibility_id"] == responsibility["id"]
    assert metadata["report_requested"] is True
    _, listing = request(base, "/api/responsibilities")
    assert listing["responsibilities"][0]["tasks"][0]["id"] == result["id"]
    assert listing["responsibilities"][0]["origin"] == "ling"
    # Retrying the same accepted input returns the same work, not a new task.
    assert work.start("调查一个反馈", responsibility_id=responsibility["id"],
                      report_requested=True)["id"] == result["id"]


def test_paused_responsibility_holds_existing_auto_schedule(task_live):
    base, store, runs = task_live
    service = ResponsibilityService(store, web.STATE["task_controller"])
    responsibility = service.save(title="观察", instruction="按已有安排检查")
    task = store.create(title="检查", goal="检查更新", workspace=str(store.path.parent))
    service.link_work(task.id, responsibility_id=responsibility["id"])
    store.set_schedule(task.id, due_at="2026-01-01T00:00:00+00:00", timezone="UTC", mode="auto")
    service.control(responsibility["id"], "pause")
    scheduler = TaskScheduler(store, web.STATE["task_controller"])
    assert scheduler.tick(datetime(2026, 1, 2, tzinfo=timezone.utc)) == []
    assert store.schedule(task.id)["status"] == "pending"
    assert runs == []
    service.control(responsibility["id"], "resume")
    assert scheduler.tick(datetime(2026, 1, 2, tzinfo=timezone.utc)) == [task.id]
    assert store.schedule(task.id)["status"] == "consumed"


def test_ending_responsibility_keeps_running_work_and_separately_paused_work(task_live):
    base, store, runs = task_live
    service = ResponsibilityService(store, web.STATE["task_controller"])
    responsibility = service.save(title="维护", instruction="调查反馈")
    running = store.create(title="正在调查", goal="调查", workspace=str(store.path.parent))
    paused = store.create(title="等材料", goal="检查", workspace=str(store.path.parent))
    for task in (running, paused):
        service.link_work(task.id, responsibility_id=responsibility["id"])
    store.command(running.id, "running", request_id="running")
    store.command(paused.id, "paused", request_id="paused")
    status, ended = request(base, f"/api/responsibilities/{responsibility['id']}/control", "POST", {
        "operation": "end",
    })
    assert status == 200
    assert ended["state"] == "ended"
    assert store.get(running.id).state == "running"
    assert store.get(paused.id).state == "paused"
    assert running.id not in service.resumable_task_ids()
