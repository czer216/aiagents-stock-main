import logging
import schedule
import threading
import time
from datetime import datetime
from typing import Dict, List

from modules.douban_author_strategy.douban_author_db import douban_author_db
from modules.douban_author_strategy.douban_author_engine import douban_author_engine

logger = logging.getLogger(__name__)


class DoubanAuthorScheduler:
    def __init__(self):
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        self.daily_time = "08:40"
        self.author_id = "default_author"
        self.author_name = "豆瓣作者"
        self.topic_urls: List[str] = []
        self.enable_comment_analysis = True
        self.comment_start_page = 1

    def configure(
        self,
        author_id: str,
        author_name: str,
        topic_urls: List[str],
        daily_time: str = "08:40",
        enable_comment_analysis: bool = True,
        comment_max_pages: int = 3,
        comment_start_page: int = 1,
    ):
        self.author_id = author_id
        self.author_name = author_name
        self.topic_urls = [u for u in topic_urls if str(u or "").strip()]
        self.daily_time = daily_time or "08:40"
        self.enable_comment_analysis = bool(enable_comment_analysis)
        self.comment_max_pages = max(1, int(comment_max_pages or 3))
        self.comment_start_page = max(1, int(comment_start_page or 1))

    def start(self):
        if self.running:
            return
        with self.lock:
            self._clear_jobs()
            job = schedule.every().day.at(self.daily_time).do(self._run_daily)
            job.tag("douban_author")
            self.running = True
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def stop(self):
        with self.lock:
            self.running = False
            self._clear_jobs()

    def _clear_jobs(self):
        for job in [j for j in schedule.jobs if "douban_author" in j.tags]:
            schedule.cancel_job(job)

    def _loop(self):
        while self.running:
            try:
                schedule.run_pending()
            except Exception as e:
                logger.exception("douban_author scheduler loop error: %s", e)
            time.sleep(20)

    def run_now(self, progress_cb=None, max_seconds: int = 180, pool_lookback_days: int = 20, pool_limit: int = 120) -> Dict:
        started = time.time()
        result = self._run_daily(progress_cb=progress_cb, pool_lookback_days=pool_lookback_days, pool_limit=pool_limit)
        elapsed = time.time() - started
        if elapsed > max(1, int(max_seconds)):
            return {"success": False, "error": f"执行超时: {int(elapsed)}s"}
        return result

    def _run_daily(self, progress_cb=None, pool_lookback_days: int = 20, pool_limit: int = 120) -> Dict:
        started = time.time()
        retry_waits = [10, 30, 60]
        last_err = ""

        for i in range(0, 4):
            try:
                if callable(progress_cb):
                    progress_cb(f"尝试第 {i + 1}/4 次")
                if not self.topic_urls:
                    raise RuntimeError("未配置topic_urls")
                result = douban_author_engine.learn_author_pattern(
                    author_id=self.author_id,
                    author_name=self.author_name,
                    topic_urls=self.topic_urls,
                    lookback_days=60,
                    progress_cb=progress_cb,
                )
                if result.get("success") and self.enable_comment_analysis:
                    comment_result = douban_author_engine.analyze_topic_comments(
                        author_id=self.author_id,
                        topic_urls=self.topic_urls,
                        max_pages=int(self.comment_max_pages or 3),
                        start_page=int(self.comment_start_page or 1),
                        progress_cb=progress_cb,
                    )
                    result["comment_analysis"] = comment_result
                if result.get("success"):
                    douban_author_db.save_scheduler_log(
                        task_type="daily_fetch_analyze",
                        status="success",
                        message=(
                            f"ok report_id={result.get('report_id')} "
                            f"pattern_v={result.get('pattern_version')} inserted={result.get('inserted_posts', 0)}"
                        ),
                        retry_count=i,
                        duration=time.time() - started,
                    )
                    return result
                last_err = str(result.get("error", "unknown"))
                raise RuntimeError(last_err)
            except Exception as e:
                last_err = str(e)
                if callable(progress_cb):
                    progress_cb(f"第 {i + 1} 次失败: {last_err}")
                if i < 3:
                    time.sleep(retry_waits[i])
                else:
                    break

        douban_author_db.save_scheduler_log(
            task_type="daily_fetch_analyze",
            status="failed",
            message=last_err,
            retry_count=3,
            duration=time.time() - started,
        )
        return {"success": False, "error": last_err}


_douban_author_scheduler = DoubanAuthorScheduler()


def get_douban_author_scheduler() -> DoubanAuthorScheduler:
    return _douban_author_scheduler
