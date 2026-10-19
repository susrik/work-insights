from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from insights.config import Config
from insights.core.llm import LLMClient
from insights.db.session import SessionFactory
from insights.jobs.registry import JobRegistry


def build_scheduler(
    config: Config, registry: JobRegistry, session_factory: SessionFactory, llm: LLMClient
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    for name, cron in config.jobs.schedules.items():
        if name not in registry.jobs:
            continue
        scheduler.add_job(
            registry.run,
            CronTrigger.from_crontab(cron),
            args=(name, session_factory, config, llm),
            id=name,
            max_instances=1,
            coalesce=True,
        )
    return scheduler
