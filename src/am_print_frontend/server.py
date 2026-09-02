from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import shutil
import threading
import time
import uuid
import webbrowser

from dataclasses import dataclass, fields, replace
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
from .print_monitor import X1CLiveMonitor, summarize_print_status
from .support_preview import SupportPreviewError, build_support_preview


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
    r"(?:/(?P<action>pause|resume|stop|retry|reprint|pin|unpin))?$"
)
_FILE_ROUTE = re.compile(
    r"^/api/jobs/(?P<job_id>[A-Za-z0-9][A-Za-z0-9._-]{2,63})/files/(?P<kind>project|gcode|stl)$"
)
_SUPPORT_PREVIEW_ROUTE = re.compile(
    r"^/api/jobs/(?P<job_id>[A-Za-z0-9][A-Za-z0-9._-]{2,63})/support-preview$"
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
    "printability_blocked",
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
        reprint_services_factory: Callable[[AutomationConfig], Any] | None = None,
    ) -> None:
        self.output_root = Path(output_root).expanduser().resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._workflow_runner = workflow_runner
        self._access_code_provider = access_code_provider
        self._printer_connection_provider = printer_connection_provider
        self._reprint_services_factory = reprint_services_factory
        self._runtimes: dict[str, _RuntimeJob] = {}
        self._lock = threading.RLock()
        self._reprint_dispatch_in_progress = False
        self._ui_preferences_path = self.output_root / ".frontend_ui_preferences.json"

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
                raise ApiError(HTTPStatus.CONFLICT, "当前任务缺少有效的 Bambu 切片文件，不能启动打印。") from exc
        runtime = _RuntimeJob(
            job_id=job_id,
            transcript=str(state.get("request_text", "")),
            config=config,
            control=WorkflowControl(),
            started_unix=time.time(),
        )
        self._launch(runtime, resume=True)
        return self.snapshot(job_id)

    def reprint_job(self, source_job_id: str) -> dict[str, Any]:
        """Create a new print record from a verified step-1 slice."""

        source_state = self._state(source_job_id)
        if source_state.get("status") != "print_started":
            raise ApiError(
                HTTPStatus.CONFLICT,
                "只有已经启动过实体打印的任务，才能使用步骤 1 文件再次打印。",
            )
        with self._lock:
            if self._reprint_dispatch_in_progress:
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    "另一个再次打印请求正在发送，请等待打印机返回结果。",
                )
            self._reprint_dispatch_in_progress = True

        launched = False
        try:
            safety_monitor = self._live_printer_monitor_snapshot()
            if safety_monitor is None or safety_monitor.get("restart_ready") is not True:
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    "当前打印还没有安全结束。请先在打印机端结束任务，并等待设备空闲、无告警且完成降温。",
                )
            if self._access_code_provider is None:
                raise ApiError(HTTPStatus.CONFLICT, "请先重新连接打印机，再次打印需要当前访问码。")
            credential_available = False
            secret = ""
            try:
                secret = str(self._access_code_provider()).strip()
                credential_available = bool(secret)
            except Exception as exc:
                raise ApiError(HTTPStatus.CONFLICT, "请先重新连接打印机，再尝试再次打印。") from exc
            finally:
                secret = ""
            if not credential_available:
                raise ApiError(HTTPStatus.CONFLICT, "请先重新连接打印机，再尝试再次打印。")
            if self._printer_connection_provider is None:
                raise ApiError(HTTPStatus.CONFLICT, "没有可用的打印机连接，请先重新连接。")
            try:
                connection = dict(self._printer_connection_provider())
            except Exception as exc:
                raise ApiError(HTTPStatus.CONFLICT, "无法读取当前打印机连接，请先重新连接。") from exc
            if connection.get("connected") is not True:
                raise ApiError(HTTPStatus.CONFLICT, "打印机当前未连接，请先重新连接。")

            try:
                source_files = verified_files(
                    source_state,
                    self.output_root / source_job_id,
                )
            except (DeliveryUnavailable, OSError, TypeError, AttributeError) as exc:
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    "步骤 1 文件缺失或完整性校验失败，不能直接再次打印。",
                ) from exc

            runtime, reprint_state, gcode_path = self._prepare_reprint(
                source_job_id,
                source_state,
                source_files,
            )
            self._launch_reprint(runtime, reprint_state, gcode_path)
            launched = True
            return self.snapshot(runtime.job_id)
        except BaseException:
            if not launched:
                with self._lock:
                    self._reprint_dispatch_in_progress = False
            raise

    def _prepare_reprint(
        self,
        source_job_id: str,
        source_state: Mapping[str, Any],
        source_files: Mapping[str, tuple[Path, str]],
    ) -> tuple[_RuntimeJob, Any, Path]:
        from am_print_automation.workflow import _JobState

        job_id = create_job_id()
        while (self.output_root / job_id).exists():
            job_id = create_job_id()
        config = replace(
            _config_from_state(
                source_state,
                output_root=self.output_root,
                start_print=True,
            ),
            remote_name=None,
        )
        job_directory = self.output_root / job_id
        reused_directory = job_directory / "reused_step1"
        reused_directory.mkdir(parents=True, exist_ok=False)
        copied: dict[str, tuple[Path, str]] = {}
        for kind in ("project", "gcode", "stl"):
            source_path, expected_digest = source_files[kind]
            destination = reused_directory / DOWNLOAD_NAMES[kind]
            shutil.copy2(source_path, destination)
            actual_digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            if actual_digest != expected_digest:
                raise ApiError(
                    HTTPStatus.CONFLICT,
                    "复制步骤 1 文件时完整性校验失败，已停止再次打印。",
                )
            copied[kind] = (destination.resolve(), actual_digest)

        text = str(source_state.get("request_text", ""))
        state = _JobState(
            job_directory=job_directory,
            job_id=job_id,
            text=text,
            config=config,
            resume=False,
            event_sink=None,
        )
        stages = copy.deepcopy(source_state.get("stages", {}))
        if not isinstance(stages, dict):
            stages = {}
        stages.pop("printer_upload", None)
        stages.pop("print_start", None)
        for record in stages.values():
            if isinstance(record, dict):
                record["reused_from_job_id"] = source_job_id

        slice_record = stages.get("bambu_slice")
        slice_result = slice_record.get("result") if isinstance(slice_record, dict) else None
        if not isinstance(slice_result, dict):
            raise ApiError(HTTPStatus.CONFLICT, "步骤 1 的切片记录不完整，不能再次打印。")
        artifact = slice_result.get("artifact")
        project = slice_result.get("project")
        if not isinstance(artifact, dict) or not isinstance(project, dict):
            raise ApiError(HTTPStatus.CONFLICT, "步骤 1 的切片记录不完整，不能再次打印。")
        artifact["path"], artifact["sha256"] = str(copied["gcode"][0]), copied["gcode"][1]
        project["path"], project["sha256"] = str(copied["project"][0]), copied["project"][1]
        slice_result["geometry_path"] = str(copied["stl"][0])
        slice_result["geometry_sha256"] = copied["stl"][1]
        slice_result["reused_from_job_id"] = source_job_id
        slice_result["reuse_mode"] = "verified_step1_files"

        state.data["schema_version"] = "automatic-reprint-workflow-v1"
        state.data["source_job_id"] = source_job_id
        state.data["reprint_mode"] = "verified_step1_files"
        state.data["status"] = "ready_to_print"
        state.data["current_stage"] = None
        state.data["stages"] = stages
        state.save()
        state.emit(
            "reprint_source_reused",
            "bambu_slice",
            {
                "source_job_id": source_job_id,
                "reuse_mode": "verified_step1_files",
                "files_verified": True,
            },
        )
        runtime = _RuntimeJob(
            job_id=job_id,
            transcript=text,
            config=config,
            control=WorkflowControl(),
            started_unix=time.time(),
        )
        return runtime, state, copied["gcode"][0]

    def _launch_reprint(self, runtime: _RuntimeJob, state: Any, gcode_path: Path) -> None:
        def checkpoint() -> None:
            runtime.control.checkpoint(
                on_paused=state.pause,
                on_resumed=state.resume,
            )

        def work() -> None:
            access_code = ""
            try:
                checkpoint()
                state.begin_stage("printer_upload")
                try:
                    if self._access_code_provider is None:
                        raise AutomationWorkflowError("Printer access code is unavailable.")
                    access_code = str(self._access_code_provider()).strip()
                    if not access_code:
                        raise AutomationWorkflowError("Printer access code is unavailable.")
                    printer_connection = (
                        dict(self._printer_connection_provider())
                        if self._printer_connection_provider is not None
                        else None
                    )
                    services = (
                        self._reprint_services_factory(runtime.config)
                        if self._reprint_services_factory is not None
                        else self._production_services(runtime.config)
                    )
                    upload = services.upload_gcode(
                        gcode_path,
                        access_code,
                        printer_connection=printer_connection,
                    )
                    if not isinstance(upload, Mapping):
                        raise TypeError("Printer upload did not return a mapping")
                    upload = dict(upload)
                except BaseException as exc:
                    state.fail_stage("printer_upload", exc)
                    raise
                state.complete_stage("printer_upload", upload)

                checkpoint()
                while not runtime.control.begin_irreversible():
                    checkpoint()
                state.begin_stage("print_start")
                state.data["stages"]["print_start"]["dispatch_policy"] = "exactly_once_no_replay"
                state.save()
                try:
                    start_result = services.start_print(upload, access_code)
                    if not isinstance(start_result, Mapping):
                        raise TypeError("Print start did not return a mapping")
                    started = dict(start_result)
                except BaseException as exc:
                    state.fail_stage("print_start", exc, outcome_unknown=True)
                    raise
                state.complete_stage("print_start", started)
                print_status = str(started.get("status", ""))
                if print_status == "direct_print_started":
                    state.finish("print_started")
                elif print_status in {"direct_print_start_outcome_unknown", ""}:
                    error = AutomationWorkflowError(
                        f"Printer start outcome is unknown: {print_status or 'missing status'}"
                    )
                    state.fail_stage("print_start", error, outcome_unknown=True)
                    raise error
                else:
                    state.data["status"] = "print_rejected"
                    state.data["current_stage"] = None
                    state.save()
                    state.emit("job_finished", None, {"status": "print_rejected"})
                runtime.result = {
                    "job_id": runtime.job_id,
                    "status": state.data.get("status"),
                    "source_job_id": state.data.get("source_job_id"),
                }
            except WorkflowStopRequested:
                state.finish("stopped")
                runtime.result = {"job_id": runtime.job_id, "status": "stopped"}
            except BaseException as exc:
                runtime.error = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "stage": state.data.get("current_stage"),
                }
            finally:
                access_code = ""
                runtime.finished_unix = time.time()
                with self._lock:
                    self._reprint_dispatch_in_progress = False

        thread = threading.Thread(
            target=work,
            name=f"automatic-reprint-{runtime.job_id}",
            daemon=True,
        )
        runtime.thread = thread
        with self._lock:
            self._runtimes[runtime.job_id] = runtime
        try:
            thread.start()
        except BaseException:
            with self._lock:
                self._runtimes.pop(runtime.job_id, None)
                self._reprint_dispatch_in_progress = False
            raise

    @staticmethod
    def _production_services(config: AutomationConfig) -> Any:
        from am_print_automation.workflow import ProductionServices

        return ProductionServices(config)

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
            "source_job_id": state.get("source_job_id"),
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
            "pinned": self._is_pinned(job_id),
        }
        if status == "print_started" or bool(state.get("start_print_requested")):
            monitor = self._print_monitor_snapshot(state)
            payload["print_monitor"] = monitor
            payload["reprint"] = self._reprint_summary(
                status,
                self._live_printer_monitor_snapshot() or monitor,
            )
        if include_events:
            payload["delivery"] = (delivery_summary(state, self.output_root / job_id)
                                   if not alive and status == state.get("status")
                                   else {"available": False, "message": "等待 Bambu Studio 切片完成。"})
            payload["events"] = _read_events(
                self.output_root / job_id / "workflow_events.jsonl"
            )
        return payload

    @staticmethod
    def _reprint_summary(status: str, monitor: Mapping[str, Any]) -> dict[str, Any]:
        visible = status == "print_started"
        available = visible and monitor.get("restart_ready") is True
        monitor_status = str(monitor.get("status") or "unavailable")
        if available:
            message = "原任务已结束且打印机处于安全空闲状态，可复用步骤 1 文件再次打印。"
        elif monitor_status == "paused":
            message = "暂停不等于结束；请先在打印机端结束原任务，再等待设备降温。"
        elif monitor_status == "printing":
            message = "当前仍在打印；结束原任务并等待打印机安全空闲后才可再次打印。"
        elif monitor_status in {"connecting", "waiting"}:
            message = "正在确认打印机是否已经安全结束，请稍候。"
        elif monitor_status == "unavailable":
            message = "暂时无法确认打印机状态，请重新连接后再试。"
        else:
            message = "请确认原任务已结束、设备无告警并完成降温。"
        return {
            "visible": visible,
            "available": available,
            "message": message,
        }

    def _print_monitor_snapshot(self, state: Mapping[str, Any]) -> dict[str, Any]:
        waiting = {
            "status": "waiting",
            "printer_state": None,
            "percent": 0.0,
            "remaining_minutes": None,
            "observed_unix": None,
            "has_alert": False,
            "restart_ready": False,
            "message": "打印任务生成完成后，将在这里显示实体打印进度。",
        }
        if state.get("status") != "print_started":
            return waiting

        stages = state.get("stages")
        print_start = stages.get("print_start") if isinstance(stages, Mapping) else None
        result = print_start.get("result") if isinstance(print_start, Mapping) else None
        payload = result.get("payload") if isinstance(result, Mapping) else None
        print_payload = payload.get("print") if isinstance(payload, Mapping) else None
        expected_name = (
            str(print_payload.get("subtask_name"))
            if isinstance(print_payload, Mapping) and print_payload.get("subtask_name")
            else None
        )

        live = self._live_printer_monitor_snapshot()
        if live is not None:
            identity = live.get("job_identity")
            observed_name = (
                str(identity.get("subtask_name"))
                if isinstance(identity, Mapping) and identity.get("subtask_name")
                else None
            )
            if expected_name and observed_name and expected_name != observed_name:
                return {
                    **waiting,
                    "status": "unavailable",
                    "percent": None,
                    "message": "当前打印机状态属于另一项任务，未混入本任务进度。",
                }
            return copy.deepcopy(dict(live))

        events = result.get("events_tail") if isinstance(result, Mapping) else None
        if isinstance(events, list):
            for event in reversed(events):
                if isinstance(event, Mapping):
                    return summarize_print_status(event, active_seen=True)
        return waiting

    def _live_printer_monitor_snapshot(self) -> dict[str, Any] | None:
        if self._printer_connection_provider is None:
            return None
        try:
            connection = self._printer_connection_provider()
            candidate = connection.get("monitor")
        except Exception:
            return None
        if not isinstance(candidate, Mapping):
            return None
        return copy.deepcopy(dict(candidate))

    def download(self, job_id: str, kind: str) -> tuple[Path, str, str]:
        with self._lock:
            runtime = self._runtimes.get(job_id)
            if runtime is not None and (runtime.alive or runtime.error is not None):
                raise ApiError(HTTPStatus.CONFLICT, "任务仍在运行或已失败，不能下载成品。")
        try:
            path, digest = verified_files(self._state(job_id), self.output_root / job_id)[kind]
        except (DeliveryUnavailable, OSError, KeyError, TypeError, AttributeError) as exc:
            raise ApiError(HTTPStatus.CONFLICT, "Bambu 切片文件不可用，不能下载。") from exc
        return path, DOWNLOAD_NAMES[kind], digest

    def support_preview(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            runtime = self._runtimes.get(job_id)
            if runtime is not None and (runtime.alive or runtime.error is not None):
                raise ApiError(HTTPStatus.CONFLICT, "任务仍在运行或已失败，不能预览支撑。")
        try:
            files = verified_files(self._state(job_id), self.output_root / job_id)
            return build_support_preview(files)
        except (DeliveryUnavailable, SupportPreviewError, OSError, TypeError, AttributeError) as exc:
            raise ApiError(HTTPStatus.CONFLICT, "Bambu 切片文件不可用，不能预览。") from exc

    def _pinned_job_ids(self) -> list[str]:
        stored = _read_json(self._ui_preferences_path) or {}
        values = stored.get("pinned_job_ids")
        if not isinstance(values, list):
            return []
        result: list[str] = []
        for value in values:
            job_id = str(value)
            if _JOB_ROUTE.fullmatch(f"/api/jobs/{job_id}") and job_id not in result:
                result.append(job_id)
        return result

    def _write_pinned_job_ids(self, job_ids: list[str]) -> None:
        payload = json.dumps(
            {"version": 1, "pinned_job_ids": job_ids},
            ensure_ascii=False,
            indent=2,
        )
        temporary = self.output_root / f".frontend_ui_preferences.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(self._ui_preferences_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _is_pinned(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._pinned_job_ids()

    def set_pinned(self, job_id: str, pinned: bool) -> dict[str, Any]:
        self._state(job_id)
        with self._lock:
            job_ids = [value for value in self._pinned_job_ids() if value != job_id]
            if pinned:
                job_ids.insert(0, job_id)
            try:
                self._write_pinned_job_ids(job_ids)
            except OSError as exc:
                raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "无法保存置顶状态。") from exc
        return self.snapshot(job_id)

    def delete_job(self, job_id: str) -> dict[str, Any]:
        self._state(job_id)
        with self._lock:
            runtime = self._runtimes.get(job_id)
            if runtime is not None and runtime.alive:
                raise ApiError(HTTPStatus.CONFLICT, "任务仍在运行，请先停止并等待它完全结束。")
            requested_directory = self.output_root / job_id
            job_directory = requested_directory.resolve()
            if (
                job_directory.parent != self.output_root
                or not job_directory.is_dir()
                or requested_directory.is_symlink()
            ):
                raise ApiError(HTTPStatus.CONFLICT, "任务目录不安全，不能删除。")
            deleted_root = self.output_root / ".deleted_jobs"
            deleted_root.mkdir(parents=True, exist_ok=True)
            destination = deleted_root / f"{job_id}-{time.time_ns()}"
            try:
                job_directory.replace(destination)
            except OSError as exc:
                raise ApiError(HTTPStatus.CONFLICT, "任务文件正在被占用，暂时不能删除。") from exc
            self._runtimes.pop(job_id, None)
            remaining = [value for value in self._pinned_job_ids() if value != job_id]
            try:
                self._write_pinned_job_ids(remaining)
            except OSError:
                pass
        return {"job_id": job_id, "status": "deleted", "recoverable": True}

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
        pinned_order = {job_id: index for index, job_id in enumerate(self._pinned_job_ids())}
        jobs.sort(key=lambda item: (
            0 if item["job_id"] in pinned_order else 1,
            pinned_order.get(item["job_id"], 0)
            if item["job_id"] in pinned_order
            else -float(item.get("updated_unix") or 0),
        ))
        return jobs[:limit]


class PrinterConnectionManager:
    """Run read-only X1C verification and retain only safe status data."""

    def __init__(
        self,
        *,
        connector: Callable[..., PrinterConnectionStatus] = connect_printer,
        live_monitor_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._connector = connector
        self._live_monitor_factory = (
            live_monitor_factory
            if live_monitor_factory is not None
            else X1CLiveMonitor
            if connector is connect_printer
            else None
        )
        self._lock = threading.RLock()
        self._access_code = ""
        self._live_monitor: Any = None
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
            "monitor": {
                "status": "unavailable",
                "printer_state": None,
                "percent": None,
                "remaining_minutes": None,
                "observed_unix": None,
                "has_alert": False,
            },
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
            self._stop_live_monitor()
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
            initial = result.get("status_summary")
            result["monitor"] = summarize_print_status(
                initial if isinstance(initial, Mapping) else {},
                observed_unix=result["verified_unix"],
            )
            with self._lock:
                self._access_code = secret
                self._status = result
            self._start_live_monitor(
                ip=str(result.get("printer_ip") or ip),
                device_id=str(result.get("device_id") or device_id or ""),
                access_code=secret,
            )
            return copy.deepcopy(result)
        finally:
            secret = ""

    def _start_live_monitor(
        self,
        *,
        ip: str,
        device_id: str,
        access_code: str,
    ) -> None:
        factory = self._live_monitor_factory
        if factory is None or not ip or not device_id or not access_code:
            return
        self._stop_live_monitor()
        try:
            monitor = factory(
                ip=ip,
                device_id=device_id,
                access_code=access_code,
                on_update=self._update_live_status,
            )
            with self._lock:
                self._live_monitor = monitor
            monitor.start()
        except Exception as exc:
            with self._lock:
                self._live_monitor = None
            self._update_live_status(
                {
                    "status": "unavailable",
                    "printer_state": None,
                    "percent": None,
                    "remaining_minutes": None,
                    "observed_unix": time.time(),
                    "has_alert": False,
                    "message": f"实时监控未启动：{exc}",
                }
            )

    def _stop_live_monitor(self) -> None:
        with self._lock:
            monitor = self._live_monitor
            self._live_monitor = None
        if monitor is not None:
            monitor.stop()

    def _update_live_status(self, monitor: Mapping[str, Any]) -> None:
        safe_monitor = copy.deepcopy(dict(monitor))
        with self._lock:
            self._status["monitor"] = safe_monitor
            self._status["live_status_observed_unix"] = safe_monitor.get(
                "observed_unix"
            )

    def close(self) -> None:
        self._stop_live_monitor()
        with self._lock:
            self._access_code = ""


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

    def server_close(self) -> None:
        self.printer_manager.close()
        super().server_close()


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
            preview_match = _SUPPORT_PREVIEW_ROUTE.fullmatch(path)
            if preview_match:
                self._json(
                    HTTPStatus.OK,
                    self.server.manager.support_preview(preview_match.group("job_id")),
                )
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
            elif action == "pin":
                result = self.server.manager.set_pinned(job_id, True)
            elif action == "unpin":
                result = self.server.manager.set_pinned(job_id, False)
            elif action == "reprint":
                result = self.server.manager.reprint_job(job_id)
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

    def do_DELETE(self) -> None:
        try:
            self._check_local_request()
            path = unquote(urlparse(self.path).path)
            match = _JOB_ROUTE.fullmatch(path)
            if not match or match.group("action"):
                raise ApiError(HTTPStatus.NOT_FOUND, "接口不存在。")
            self._json(
                HTTPStatus.OK,
                self.server.manager.delete_job(match.group("job_id")),
            )
        except ApiError as exc:
            self._json(exc.status, {"error": str(exc)})
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
