from types import SimpleNamespace

from scheduler import create_scheduler


def test_scheduler_uses_single_instance_coalesced_jobs() -> None:
    scheduler = create_scheduler(
        pool=object(),
        client=object(),
        settings=SimpleNamespace(timezone="Asia/Seoul"),
    )

    jobs = {job.id: job for job in scheduler.get_jobs()}

    assert set(jobs) == {"sync_hr_sheet", "send_today_birthdays"}
    assert jobs["sync_hr_sheet"].coalesce is True
    assert jobs["sync_hr_sheet"].max_instances == 1
    assert jobs["send_today_birthdays"].coalesce is True
    assert jobs["send_today_birthdays"].max_instances == 1
