"""最小存储层：文件锁、原子写、JSON / JSONL 存取。

只依赖标准库，不认识任何业务类型（这是分层红线）。
"""

from __future__ import annotations

import json
import os
import random
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator


# ============================== 时间与 ID ==============================

def now_iso() -> str:
    """带本地时区的 ISO 8601，秒级。落盘时间一律用它。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def new_id(prefix: str) -> str:
    """前缀 + 6 位十六进制，例如 T-3f2a1c。"""
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


# ============================== 日志错误 ==============================

class LockTimeout(RuntimeError):
    """拿不到文件锁。"""


# ============================== 文件锁 ==============================

class FileLock:
    """跨平台互斥锁：用 O_CREAT|O_EXCL 创建锁文件实现。

    用法：
        with FileLock(path):
            ...

    注意：
    - 不要在已持锁的代码里再次获取同一把锁（非重入，会等到超时）。
    - 持锁时间要短：只包住读/写那几行，不要把网络调用包进来。
    """

    def __init__(self, path: str | Path, timeout: float = 10.0,
                 stale_after: float = 60.0, poll: float = 0.02) -> None:
        self.path = Path(path)
        self.timeout = timeout
        self.stale_after = stale_after
        self.poll = poll
        self._fd: int | None = None

    # ---- 内部 ----
    def _break_if_stale(self) -> None:
        try:
            age = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return
        if age > self.stale_after:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    # ---- 公开 ----
    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                payload = f"pid={os.getpid()}\nts={time.time()}\n".encode("utf-8")
                os.write(self._fd, payload)
                return
            except FileExistsError:
                self._break_if_stale()
                if time.monotonic() >= deadline:
                    raise LockTimeout(
                        f"获取文件锁超时（{self.timeout}s）：{self.path}。"
                        f"若确认没有其他进程在用，可删除该 .lock 文件。"
                    )
                time.sleep(self.poll + random.random() * self.poll)
            except PermissionError:
                # Windows：锁文件正被其他线程/进程打开时，O_EXCL 会抛
                # PermissionError 而非 FileExistsError —— 视为"锁被占用"，重试。
                if time.monotonic() >= deadline:
                    raise LockTimeout(
                        f"获取文件锁超时（{self.timeout}s）：{self.path}。"
                        f"若确认没有其他进程在用，可删除该 .lock 文件。"
                    )
                self._break_if_stale()
                time.sleep(self.poll + random.random() * self.poll)

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self.release()
        return False


def lock_path_for(path: str | Path) -> Path:
    p = Path(path)
    return p.with_suffix(p.suffix + ".lock")


@contextmanager
def locked(path: str | Path, timeout: float = 10.0) -> Iterator[None]:
    with FileLock(lock_path_for(path), timeout=timeout):
        yield


# ============================== 原子写 ==============================

def atomic_write_text(path: str | Path, text: str) -> None:
    """同一文件系统内原子替换：tmp 写完再 os.replace。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:4]}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)   # 关键：原子
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def atomic_write_json(path: str | Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


# ============================== JSON ==============================

def read_json(path: str | Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: str | Path, data: Any) -> None:
    atomic_write_json(path, data)


class JsonStore:
    """单个 JSON 文件的读写门面。所有写操作都会加锁 + 原子替换。"""

    def __init__(self, path: str | Path, default: dict[str, Any] | None = None) -> None:
        self.path = Path(path)
        self.lock_path = lock_path_for(self.path)
        self._default = default if default is not None else {}

    def _read_raw(self) -> dict[str, Any]:
        if not self.path.exists():
            return json.loads(json.dumps(self._default))  # 深拷贝
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return json.loads(json.dumps(self._default))
        return data if isinstance(data, dict) else json.loads(json.dumps(self._default))

    def _read_for_update(self) -> dict[str, Any]:
        """写路径专用：文件损坏时拒绝写入，绝不拿空数据覆盖用户状态。"""
        if not self.path.exists():
            return json.loads(json.dumps(self._default))
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            backup = self.path.with_suffix(self.path.suffix + ".corrupt")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            raise ValueError(
                f"状态文件损坏，已备份到 {backup.name}，拒绝写入：{self.path}"
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(f"状态文件顶层不是 JSON 对象：{self.path}")
        return data

    def read(self) -> dict[str, Any]:
        return self._read_raw()

    def write(self, data: dict[str, Any]) -> None:
        with FileLock(self.lock_path):
            atomic_write_json(self.path, data)

    def update(self, mutator: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        """读-改-写，整个过程持锁。mutator 返回新 dict。"""
        with FileLock(self.lock_path):
            current = self._read_for_update()
            updated = mutator(current)
            if not isinstance(updated, dict):
                raise TypeError("JsonStore.update 的 mutator 必须返回 dict")
            atomic_write_json(self.path, updated)
            return updated


# ============================== JSONL ==============================

class JsonlStore:
    """追加型 JSONL。每条记录一行，读坏的行走跳过并计数。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = lock_path_for(self.path)
        self.last_skipped = 0

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with FileLock(self.lock_path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def append_many(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        lines = "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n"
        with FileLock(self.lock_path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(lines)

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        skipped = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if isinstance(obj, dict):
                records.append(obj)
            else:
                skipped += 1
        self.last_skipped = skipped
        return records

    def drain(self, archive_path: str | Path | None = None) -> list[dict[str, Any]]:
        """读全部 → （可选）追加到归档 → 清空。整个过程在同一把锁里。"""
        with FileLock(self.lock_path):
            records = self.read_all()
            if archive_path is not None and records:
                archive = Path(archive_path)
                archive.parent.mkdir(parents=True, exist_ok=True)
                lines = "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n"
                with archive.open("a", encoding="utf-8") as handle:
                    handle.write(lines)
            atomic_write_text(self.path, "")
            return records
