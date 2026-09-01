from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import re
import threading
import time
import webbrowser

from dataclasses import dataclass, fields
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, quote, unquote, urlparse

from am_print_automation import (
    AutomationConfig,
    AutomationWorkflowError,
    WorkflowControl,
    WorkflowStopRequested,
    create_job_id,
    run_text_to_print,
)
from am_print_executor.x1c_connection import (
    MQTT_PROTOCOL,
    PrinterConnectionError,
    PrinterConnectionStatus,
    connect_printer,
)

from .speech import SpeechTranscriber, SpeechTranscriptionError
from .delivery import DOWNLOAD_NAMES, DeliveryUnavailable, delivery_summary, verified_files


STATIC_DIRECTORY = Path(__file__).with_name("static")
_RUNTIME_DEPENDENCIES = {
    "faster_whisper": "faster-whisper",
    "jsonschema": "jsonschema",
    "mapbox_earcut": "mapbox-earcut",
    "manifold3d": "manifold3d",
    "numpy": "numpy",
    "openai": "openai",
    "paho.mqtt": "paho-mqtt",
    "shapely": "shapely",
    "skimage": "scikit-image",
    "trimesh": "trimesh",
    "ultralytics": "ultralytics",
}
_JOB_ROUTE = re.compile(
    r"^/api/jobs/(?P<job_id>[A-Za-z0-9][A-Za-z0-9._-]{2,63})"
    r"(?:/(?P<action>pause|resume|stop|retry))?$"
)
_FILE_ROUTE = re.compile(
    r"^/api/jobs/(?P<job_id>[A-Za-z0-9][A-Za-z0-9._-]{2,63})/files/(?P<kind>project|gcode|stl)$"
)
_TERMINAL_STATUSES = {
    "ready_to_print",
    "print_started",
    "awaiting_clarification",
    "manual_reconciliation_required",
    "print_rejected",
    "failed",
    "stopped",
    "credentials_required",
    "needs_geometry_regeneration",
}


def missing_runtime_dependencies() -> list[str]:
    return [
        package
        for module, package in _RUNTIME_DEPENDENCIES.items()
        if importlib.util.find_spec(module) is None
    ]


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class _RuntimeJob:
    job_id: str
    transcript: str
    config: AutomationConfig
    control: WorkflowControl
    thread: threading.Thread | None = None
    started_unix: float = 0.0
    finished_unix: float | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @property
    def alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_events(path: Path, *, limit: int = 240) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    start = max(0, len(lines) - limit)
    events: list[dict[str, Any]] = []
    for cursor, line in enumerate(lines[start:], start=start + 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            event["cursor"] = cursor
            events.append(event)
    return events


def _config_from_state(
    state: Mapping[str, Any],
    *,
    output_root: Path,
    start_print: bool | None = None,
) -> AutomationConfig:
    stored = state.get("configuration")
    values = dict(stored) if isinstance(stored, Mapping) else {}
    allowed = {item.name for item in fields(AutomationConfig)}
    values = {key: value for key, value in values.items() if key in allowed}
    values["output_root"] = output_root
    values["start_print"] = (
        bool(state.get("start_print_requested"))
        if start_print is None
        else bool(start_print)
    )
    for name in ("studio_exe", "machine_profile", "process_profile"):
        if values.get(name):
            values[name] = Path(str(values[name]))
    if values.get("filament_profiles"):
        values["filament_profiles"] = tuple(
            Path(str(item)) for item in values["filament_profiles"]
        )
    if values.get("ams_mapping") is not None:
        values["ams_mapping"] = tuple(int(item) for item in values["ams_mapping"])
    return AutomationConfig(**values)


class JobManager:
    """Own background workflows and expose safe UI-level controls."""

    def __init__(
        self,
        *,
        output_root: Path | str = Path("outputs/automatic_jobs"),
        workflow_runner: Callable[..., Any] = run_text_to_print,
        access_code_provider: Callable[[], str] | None = None,
        printer_connection_provider: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self.output_root = Path(output_root).expanduser().resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._workflow_runner = workflow_runner
        self._access_code_provider = access_code_provider
        self._printer_connection_provider = printer_connection_provider
        self._runtimes: dict[str, _RuntimeJob] = {}
        self._lock = threading.RLock()

    def set_access_code_provider(
        self,
        provider: Callable[[], str],
    ) -> None:
        with self._lock:
            self._access_code_provider = provider

    def set_printer_connection_provider(
        self,
        provider: Callable[[], Mapping[str, Any]],
    ) -> None:
        with self._lock:
            self._printer_connection_provider = provider

    def start_job(
        self,
        transcript: str,
        *,
        start_print: bool = False,
    ) -> dict[str, Any]:
        cleaned = transcript.strip()
        if not cleaned:
            raise ApiError(HTTPStatus.BAD_REQUEST, "请输入你想打印的内容。")
        if len(cleaned) > 5000:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "描述过长，请控制在 5000 个字符以内。",
            )
        job_id = create_job_id()
        config = AutomationConfig(
            output_root=self.output_root,
            start_print=bool(start_print),
        )
        runtime = _RuntimeJob(
            job_id=job_id,
            transcript=cleaned,
            config=config,
            control=WorkflowControl(),
            started_unix=time.time(),
        )
        self._launch(runtime, resume=False)
        return self.snapshot(job_id)

    def retry_job(
        self,
        job_id: str,
        *,
        start_print: bool | None = None,
    ) -> dict[str, Any]:
        state = self._state(job_id)
        with self._lock:
            existing = self._runtimes.get(job_id)
            if existing is not None and existing.alive:
                raise ApiError(HTTPStatus.CONFLICT, "这个任务仍在运行。")
        print_stage = state.get("stages", {}).get("print_start", {})
        if state.get("status") == "print_started" or print_stage.get("status") == "completed":
            raise ApiError(HTTPStatus.CONFLICT, "此任务已发送打印，不能重复开打。")
        if (
            state.get("status") == "manual_reconciliation_required"
            or print_stage.get("status") in {"running", "outcome_unknown"}
        ):
            raise ApiError(
                HTTPStatus.CONFLICT,
                "打印启动结果不明确，为避免重复打印，必须先核对打印机。",
            )
        config = _config_from_state(
            state,
            output_root=self.output_root,
            start_print=start_print,
        )
        if config.start_print:
            try:
                verified_files(state, self.output_root / job_id)
            except (DeliveryUnavailable, OSError, TypeError, AttributeError) as exc:
                raise ApiError(HTTPStatus.CONFLICT, "当前任务缺少有效的最终验收文件，不能启动打印。") from exc
        runtime = _RuntimeJob(
            job_id=job_id,
            transcript=str(state.get("request_text", "")),
            config=config,
            control=WorkflowControl(),
            started_unix=time.time(),
        )
        self._launch(runtime, resume=True)
        return self.snapshot(job_id)

    def _launch(self, runtime: _RuntimeJob, *, resume: bool) -> None:
        def work() -> None:
            try:
                kwargs: dict[str, Any] = {
                    "config": runtime.config,
                    "control": runtime.control,
                }
                if self._access_code_provider is not None:
                    kwargs["access_code_provider"] = self._access_code_provider
                if self._printer_connection_provider is not None:
                    kwargs["printer_connection_provider"] = (
                        self._printer_connection_provider
                    )
                if resume:
                    kwargs["resume_job_id"] = runtime.job_id
                else:
                    kwargs["new_job_id"] = runtime.job_id
                result = self._workflow_runner(runtime.transcript, **kwargs)
                runtime.result = (
                    result.to_dict() if hasattr(result, "to_dict") else dict(result)
                )
            except WorkflowStopRequested:
                runtime.result = {"job_id": runtime.job_id, "status": "stopped"}
            except BaseException as exc:
                runtime.error = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "stage": getattr(exc, "stage", None),
                }
            finally:
                runtime.finished_unix = time.time()

        thread = threading.Thread(
            target=work,
            name=f"automatic-print-{runtime.job_id}",
            daemon=True,
        )
        runtime.thread = thread
        with self._lock:
            self._runtimes[runtime.job_id] = runtime
        thread.start()

    def pause(self, job_id: str) -> dict[str, Any]:
        runtime = self._active_runtime(job_id)
        state = self._state(job_id, required=False) or {}
        if state.get("current_stage") == "print_start":
            raise ApiError(
                HTTPStatus.CONFLICT,
                "打印启动指令正在发送，此刻不能安全暂停。",
            )
        if not runtime.control.pause():
            raise ApiError(
                HTTPStatus.CONFLICT,
                "当前步骤已经不能安全暂停。",
            )
        return self.snapshot(job_id)

    def resume(self, job_id: str) -> dict[str, Any]:
        runtime = self._active_runtime(job_id)
        if runtime.control.status == "stop_requested":
            raise ApiError(HTTPStatus.CONFLICT, "停止请求已经生效，不能继续。")
        if not runtime.control.resume():
            raise ApiError(HTTPStatus.CONFLICT, "这个任务当前没有暂停。")
        return self.snapshot(job_id)

    def stop(self, job_id: str) -> dict[str, Any]:
        runtime = self._active_runtime(job_id)
        state = self._state(job_id, required=False) or {}
        if state.get("current_stage") == "print_start" or state.get("status") == "print_started":
            raise ApiError(
                HTTPStatus.CONFLICT,
                "打印启动指令已进入不可回滚区间，请在打印机端执行急停。",
            )
        if not runtime.control.stop():
            raise ApiError(
                HTTPStatus.CONFLICT,
                "打印启动指令已进入不可回滚区间，请在打印机端执行急停。",
            )
        return self.snapshot(job_id)

    def _active_runtime(self, job_id: str) -> _RuntimeJob:
        with self._lock:
            runtime = self._runtimes.get(job_id)
        if runtime is None or not runtime.alive:
            raise ApiError(HTTPStatus.CONFLICT, "这个任务当前没有运行。")
        return runtime

    def _state(
        self,
        job_id: str,
        *,
        required: bool = True,
    ) -> dict[str, Any] | None:
        state = _read_json(self.output_root / job_id / "workflow_state.json")
        if state is None and required:
            raise ApiError(HTTPStatus.NOT_FOUND, "没有找到这个任务。")
        return state

    def snapshot(
        self,
        job_id: str,
        *,
        include_events: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            runtime = self._runtimes.get(job_id)
        state = self._state(job_id, required=False)
        if state is None:
            if runtime is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "没有找到这个任务。")
            state = {
                "job_id": job_id,
                "status": "starting",
                "current_stage": None,
                "request_text": runtime.transcript,
                "stages": {},
                "created_unix": runtime.started_unix,
                "updated_unix": runtime.started_unix,
                "last_error": None,
            }
        control_status = "finished"
        alive = False
        runtime_error = None
        if runtime is not None:
            alive = runtime.alive
            control_status = runtime.control.status if alive else "finished"
            runtime_error = runtime.error
        status = str(state.get("status", "unknown"))
        current_stage = state.get("current_stage")
        if not alive and status in {"starting", "created", "running", "paused"}:
            runtime_error = runtime_error or {
                "type": "WorkflowInterrupted",
                "message": (
                    "后台流程已中断；已完成结果仍然保留，"
                    "可以从安全步骤重试。"
                ),
                "stage": current_stage,
            }
        if runtime_error is not None and not alive:
            status = "failed"
        dispatch_locked = current_stage == "print_start" or status == "print_started"
        from am_print_automation.preparation_recovery import can_recover_preparation
        payload: dict[str, Any] = {
            "job_id": job_id,
            "status": status,
            "current_stage": current_stage,
            "request_text": state.get("request_text", ""),
            "created_unix": state.get("created_unix"),
            "preparation_started_unix": state.get("preparation_started_unix"),
            "updated_unix": state.get("updated_unix"),
            "start_print_requested": bool(state.get("start_print_requested")),
            "stages": state.get("stages", {}),
            "last_error": state.get("last_error") or runtime_error,
            "clarification_question": state.get("clarification_question"),
            "control": {
                "status": control_status,
                "worker_alive": alive,
                "can_pause": alive and control_status == "running" and not dispatch_locked,
                "can_resume": alive and control_status == "pause_requested",
                "can_stop": alive and not dispatch_locked,
                "stop_scope": "workflow_only",
            },
            "terminal": status in _TERMINAL_STATUSES and not alive,
            "preparation_recovery_available": not alive and can_recover_preparation(state),
        }
        if include_events:
            payload["delivery"] = (delivery_summary(state, self.output_root / job_id)
                                   if not alive and status == state.get("status")
                                   else {"available": False, "message": "等待最终检查完成。"})
            payload["events"] = _read_events(
                self.output_root / job_id / "workflow_events.jsonl"
            )
        return payload

    def download(self, job_id: str, kind: str) -> tuple[Path, str, str]:
        with self._lock:
            runtime = self._runtimes.get(job_id)
            if runtime is not None and (runtime.alive or runtime.error is not None):
                raise ApiError(HTTPStatus.CONFLICT, "任务仍在运行或已失败，不能下载成品。")
        try:
            path, digest = verified_files(self._state(job_id), self.output_root / job_id)[kind]
        except (DeliveryUnavailable, OSError, KeyError, TypeError, AttributeError) as exc:
            raise ApiError(HTTPStatus.CONFLICT, "最终文件未通过核验，不能下载。") from exc
        return path, DOWNLOAD_NAMES[kind], digest

    def list_jobs(self, *, limit: int = 40) -> list[dict[str, Any]]:
        job_ids: set[str] = set()
        try:
            for child in self.output_root.iterdir():
                if child.is_dir() and (child / "workflow_state.json").is_file():
                    job_ids.add(child.name)
        except OSError:
            pass
        with self._lock:
            job_ids.update(self._runtimes)
        jobs: list[dict[str, Any]] = []
        for job_id in job_ids:
            try:
                jobs.append(self.snapshot(job_id, include_events=False))
            except ApiError:
                continue
        jobs.sort(
            key=lambda item: float(item.get("updated_unix") or 0),
            reverse=True,
        )
        return jobs[:limit]


class PrinterConnectionManager:
    """Run read-only X1C verification and retain only safe status data."""

    def __init__(
        self,
        *,
        connector: Callable[..., PrinterConnectionStatus] = connect_printer,
    ) -> None:
        self._connector = connector
        self._lock = threading.RLock()
        self._access_code = ""
        self._status: dict[str, Any] = {
            "connected": False,
            "printer_ip": None,
            "device_id": None,
            "transport": MQTT_PROTOCOL,
            "authenticated": False,
            "printer_state_observed": False,
            "error_code": None,
            "error": None,
            "credential_available": False,
            "verified_unix": None,
        }

    def access_code(self) -> str:
        with self._lock:
            return self._access_code

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._status)

    def connect(
        self,
        *,
        ip: str,
        access_code: str,
        device_id: str | None,
        timeout_seconds: float = 20.0,
    ) -> dict[str, Any]:
        secret = access_code
        access_code = ""
        try:
            status = self._connector(
                ip=ip,
                access_code=secret,
                device_id=device_id,
                timeout_seconds=timeout_seconds,
                attempts=1,
            )
        except PrinterConnectionError as exc:
            failure = exc.to_dict()
            failure["error_code"] = exc.code
            failure["error"] = str(exc)
            failure["credential_available"] = False
            failure["verified_unix"] = time.time()
            with self._lock:
                self._access_code = ""
                self._status = failure
            raise
        else:
            result = status.to_dict()
            result["error_code"] = None
            result["error"] = None
            result["credential_available"] = True
            result["verified_unix"] = time.time()
            with self._lock:
                self._access_code = secret
                self._status = result
            return copy.deepcopy(result)
        finally:
            secret = ""


def _printer_error_http_status(code: str) -> int:
    if code in {"invalid_ip", "invalid_device_id"}:
        return HTTPStatus.BAD_REQUEST
    if code == "authentication_failed":
        return HTTPStatus.UNAUTHORIZED
    if code == "timeout":
        return HTTPStatus.GATEWAY_TIMEOUT
    return HTTPStatus.BAD_GATEWAY


class ApplicationServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        manager: JobManager,
        transcriber: SpeechTranscriber,
        printer_manager: PrinterConnectionManager,
    ) -> None:
        self.manager = manager
        self.transcriber = transcriber
        self.printer_manager = printer_manager
        super().__init__(server_address, ApplicationHandler)


class ApplicationHandler(BaseHTTPRequestHandler):
    server: ApplicationServer

    _static_files = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/index.html": ("index.html", "text/html; charset=utf-8"),
        "/styles.css": ("styles.css", "text/css; charset=utf-8"),
        "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        "/model-preview.js": ("model-preview.js", "text/javascript; charset=utf-8"),
        "/guide": ("guide.html", "text/html; charset=utf-8"),
    }

    def do_GET(self) -> None:
        try:
            self._check_local_request()
            path = unquote(urlparse(self.path).path)
            if path == "/api/health":
                self._json(
                    HTTPStatus.OK,
                    {
                        "status": "ready",
                        "speech": self.server.transcriber.capabilities(),
                        "printer": self.server.printer_manager.snapshot(),
                    },
                )
                return
            if path == "/api/printer":
                self._json(
                    HTTPStatus.OK,
                    self.server.printer_manager.snapshot(),
                )
                return
            if path == "/api/jobs":
                self._json(HTTPStatus.OK, {"jobs": self.server.manager.list_jobs()})
                return
            file_match = _FILE_ROUTE.fullmatch(path)
            if file_match:
                file_path, filename, digest = self.server.manager.download(
                    file_match.group("job_id"), file_match.group("kind")
                )
                self._file(file_path, "application/octet-stream", filename=filename, digest=digest)
                return
            match = _JOB_ROUTE.fullmatch(path)
            if match and not match.group("action"):
                self._json(
                    HTTPStatus.OK,
                    self.server.manager.snapshot(match.group("job_id")),
                )
                return
            static = self._static_files.get(path)
            if static is not None:
                filename, content_type = static
                self._file(STATIC_DIRECTORY / filename, content_type)
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "页面不存在。")
        except ApiError as exc:
            self._json(exc.status, {"error": str(exc)})
        except Exception:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "本地应用遇到内部错误。"},
            )

    def do_POST(self) -> None:
        try:
            self._check_local_request()
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path == "/api/speech/transcribe":
                audio = self._request_bytes(maximum_bytes=20 * 1024 * 1024)
                language_values = parse_qs(parsed.query).get("language", ["zh"])
                language = language_values[0].strip().lower()
                if language not in {"zh", "en", "auto"}:
                    raise ApiError(
                        HTTPStatus.BAD_REQUEST,
                        "不支持的语音识别语言。",
                    )
                result = self.server.transcriber.transcribe(
                    audio,
                    content_type=self.headers.get(
                        "Content-Type",
                        "application/octet-stream",
                    ),
                    language=None if language == "auto" else language,
                )
                self._json(HTTPStatus.OK, result)
                return
            body = self._request_json()
            if path == "/api/printer/connect":
                access_code = str(body.pop("access_code", ""))
                try:
                    result = self.server.printer_manager.connect(
                        ip=str(body.get("ip", "")),
                        access_code=access_code,
                        device_id=(
                            str(body["device_id"])
                            if body.get("device_id")
                            else None
                        ),
                        timeout_seconds=20.0,
                    )
                except PrinterConnectionError as exc:
                    self._json(
                        _printer_error_http_status(exc.code),
                        self.server.printer_manager.snapshot(),
                    )
                    return
                finally:
                    access_code = ""
                self._json(HTTPStatus.OK, result)
                return
            if path == "/api/jobs":
                snapshot = self.server.manager.start_job(
                    str(body.get("transcript", "")),
                    start_print=self._print_choice(body, default=False),
                )
                self._json(HTTPStatus.ACCEPTED, snapshot)
                return
            match = _JOB_ROUTE.fullmatch(path)
            if not match or not match.group("action"):
                raise ApiError(HTTPStatus.NOT_FOUND, "接口不存在。")
            job_id = match.group("job_id")
            action = match.group("action")
            if action == "pause":
                result = self.server.manager.pause(job_id)
            elif action == "resume":
                result = self.server.manager.resume(job_id)
            elif action == "stop":
                result = self.server.manager.stop(job_id)
            else:
                start_print = body.get("start_print")
                result = self.server.manager.retry_job(
                    job_id,
                    start_print=(
                        self._print_choice(body, default=False) if start_print is not None else False
                    ),
                )
            self._json(HTTPStatus.ACCEPTED, result)
        except ApiError as exc:
            self._json(exc.status, {"error": str(exc)})
        except (AutomationWorkflowError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except SpeechTranscriptionError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "本地应用遇到内部错误。"},
            )

    def _check_local_request(self) -> None:
        authorities = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}", f"[::1]:{self.server.server_port}"}
        if self.headers.get("Host", "").lower() not in authorities:
            raise ApiError(HTTPStatus.FORBIDDEN, "仅接受本机地址的请求。")
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + authority for authority in authorities}:
            raise ApiError(HTTPStatus.FORBIDDEN, "不允许其他网站控制本地打印流程。")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise ApiError(HTTPStatus.FORBIDDEN, "不允许跨站访问本地打印流程。")

    def _print_choice(self, body: Mapping[str, Any], *, default: bool) -> bool:
        value = body.get("start_print", default)
        if not isinstance(value, bool):
            raise ApiError(HTTPStatus.BAD_REQUEST, "打印选项必须明确为开或关。")
        return value

    def _request_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "请求长度无效。") from exc
        if length < 0:
            raise ApiError(HTTPStatus.BAD_REQUEST, "请求长度无效。")
        if length > 65536:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "请求内容过大。")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "请求不是有效的 JSON。") from exc
        if not isinstance(value, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "请求必须是 JSON 对象。")
        return value

    def _request_bytes(self, *, maximum_bytes: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "录音长度无效。") from exc
        if length <= 0:
            raise ApiError(HTTPStatus.BAD_REQUEST, "没有收到录音内容。")
        if length > maximum_bytes:
            raise ApiError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "录音文件过大，请把单次录音控制在 90 秒以内。",
            )
        return self.rfile.read(length)

    def _file(self, path: Path, content_type: str, *, filename: str | None = None, digest: str | None = None) -> None:
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ApiError(HTTPStatus.NOT_FOUND, "静态资源不存在。") from exc
        if digest is not None:
            import hashlib
            if hashlib.sha256(payload).hexdigest() != digest:
                raise ApiError(HTTPStatus.CONFLICT, "文件在读取时发生变化，请重新核验。")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        if filename:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(filename))
            self.send_header("X-Artifact-SHA256", digest or "")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
            "frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, value: Mapping[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        return


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    manager: JobManager | None = None,
    transcriber: SpeechTranscriber | None = None,
    printer_manager: PrinterConnectionManager | None = None,
) -> ApplicationServer:
    active_manager = manager or JobManager()
    active_printer_manager = printer_manager or PrinterConnectionManager()
    active_manager.set_access_code_provider(active_printer_manager.access_code)
    active_manager.set_printer_connection_provider(active_printer_manager.snapshot)
    active_transcriber = transcriber or SpeechTranscriber(
        cache_directory=active_manager.output_root.parent / "speech_models"
    )
    return ApplicationServer(
        (host, port),
        active_manager,
        active_transcriber,
        active_printer_manager,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动造物台本地打印应用")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-root", default="outputs/automatic_jobs")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("为保护打印机控制接口，应用只能监听本机地址。")
    missing = missing_runtime_dependencies()
    if missing:
        parser.error(
            "当前 Python 环境缺少运行依赖："
            + ", ".join(missing)
            + "。请使用 scripts\\run_print_frontend.ps1 启动项目环境。"
        )
    manager = JobManager(output_root=args.output_root)
    server = create_server(host=args.host, port=args.port, manager=manager)
    url = f"http://{args.host}:{server.server_port}/"
    if not args.no_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    print(f"造物台已启动：{url}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
