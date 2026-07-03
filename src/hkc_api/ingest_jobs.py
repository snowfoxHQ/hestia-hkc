"""
hkc-api / ingest_jobs.py
异步摄入任务注册表(方案A)。

上传文件后立即返回 job_id,真正的摄入(解析 + 逐 chunk 调 LLM,耗时数分钟)
在后台线程池里跑,前端轮询 job 状态拿真进度,期间可继续上传下一个。

- 内存态(崩溃后 job 丢失可接受,重传即可);线程安全。
- 进度来自 Extractor 逐 chunk 回调 progress_cb(processed, total)。
"""
from __future__ import annotations
import threading, time, uuid, logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class IngestJob:
    job_id:    str
    filename:  str
    status:    str = "queued"    # queued | running | done | error
    processed: int = 0           # 已抽取 chunk 数
    total:     int = 0           # chunk 总数(截断后的实际处理数)
    ku_count:  int = 0           # 产出 KU 数
    error:     str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class IngestJobRegistry:
    """维护 job_id → IngestJob,并用线程池在后台执行摄入。"""

    def __init__(self, max_workers: int = 2, max_jobs: int = 200):
        self._jobs: dict[str, IngestJob] = {}
        self._lock = threading.RLock()
        self._max_jobs = max_jobs        # 内存里最多保留多少条 job 记录
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="ingest"
        )

    def create(self, filename: str) -> IngestJob:
        job = IngestJob(job_id="job_" + uuid.uuid4().hex[:12], filename=filename)
        with self._lock:
            self._jobs[job.job_id] = job
            self._evict_locked()
        return job

    def _evict_locked(self):
        """超过上限时,淘汰最旧的【已结束】job(done/error)。绝不淘汰进行中的
        (queued/running)——前端只轮询进行中的,最旧的已完成 job 早已无人查看。
        若全是进行中的,宁可暂时超限也不丢活跃 job。须在持锁下调用。"""
        over = len(self._jobs) - self._max_jobs
        if over <= 0:
            return
        finished = sorted(
            (j for j in self._jobs.values() if j.status in ("done", "error")),
            key=lambda j: j.created_at,
        )
        for j in finished[:over]:
            self._jobs.pop(j.job_id, None)

    def get(self, job_id: str) -> Optional[dict]:
        """返回 job 状态快照(dict)。不存在返回 None。"""
        with self._lock:
            job = self._jobs.get(job_id)
            return asdict(job) if job else None

    def submit(self, job: IngestJob, work: Callable[[Callable[[int, int], None]], list]) -> None:
        """
        提交后台执行。work 接收一个 progress(processed,total) 回调,
        返回写入的 KU 列表(用于 ku_count)。异常会被捕获记入 job.error。
        """
        self._executor.submit(self._run, job, work)

    def _run(self, job: IngestJob, work):
        with self._lock:
            job.status = "running"; job.updated_at = time.time()

        def progress(processed: int, total: int):
            with self._lock:
                job.processed = processed; job.total = total
                job.updated_at = time.time()

        try:
            kus = work(progress)
            with self._lock:
                job.status = "done"
                job.ku_count = len(kus) if kus else 0
                if job.total == 0:            # 空文档等:没触发过进度回调
                    job.total = job.processed
                job.updated_at = time.time()
            logger.info("摄入 job 完成: %s (%s), %d KU", job.job_id, job.filename, job.ku_count)
        except Exception as e:
            with self._lock:
                job.status = "error"; job.error = str(e)[:200]
                job.updated_at = time.time()
            logger.exception("摄入 job 失败: %s (%s)", job.job_id, job.filename)

    def shutdown(self):
        self._executor.shutdown(wait=False)
