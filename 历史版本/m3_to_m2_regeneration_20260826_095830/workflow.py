from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol


class AutomationWorkflowError(RuntimeError):
    """A durable automatic job could not safely continue."""

    def __init__(
        self,
        message: str,
        *,
        job_id: str | None = None,
        state_file: Path | None = None,
        stage: str | None = None,
    ) -> None:
        super().__init__(message)
        self.job_id = job_id
        self.state_file = state_file
        self.stage = stage


class WorkflowStopRequested(Exception):
    """A user deliberately stopped a workflow at a safe checkpoint."""


class WorkflowControl:
    """Thread-safe cooperative pause/resume/stop control.

    Manufacturing providers and slicers are treated as atomic external calls.
    A request made during one of those calls takes effect immediately after
    the call returns and before the next stage begins. This avoids corrupting
    artifacts or creating an ambiguous printer-dispatch outcome.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._pause_requested = False
        self._stop_requested = False
        self._irreversible = False

    @property
    def status(self) -> str:
        with self._condition:
            if self._irreversible:
                return "dispatching"
            if self._stop_requested:
                return "stop_requested"
            if self._pause_requested:
                return "pause_requested"
            return "running"

    def pause(self) -> bool:
        with self._condition:
            if self._stop_requested or self._irreversible:
                return False
            changed = not self._pause_requested
            self._pause_requested = True
            self._condition.notify_all()
            return changed

    def resume(self) -> bool:
        with self._condition:
            if self._stop_requested or self._irreversible:
                return False
            changed = self._pause_requested
            self._pause_requested = False
            self._condition.notify_all()
            return changed

    def stop(self) -> bool:
        with self._condition:
            if self._irreversible:
                return False
            changed = not self._stop_requested
            self._stop_requested = True
            self._pause_requested = False
            self._condition.notify_all()
            return changed

    def checkpoint(
        self,
        *,
        on_paused: Callable[[], None] | None = None,
        on_resumed: Callable[[], None] | None = None,
    ) -> None:
        """Wait while paused, or raise when a stop has been requested."""

        notified_paused = False
        with self._condition:
            if self._stop_requested:
                raise WorkflowStopRequested("Workflow stopped by the user.")
            while self._pause_requested:
                if not notified_paused:
                    if on_paused is not None:
                        on_paused()
                    notified_paused = True
                self._condition.wait(timeout=0.25)
                if self._stop_requested:
                    raise WorkflowStopRequested(
                        "Workflow stopped by the user."
                    )
            if notified_paused and on_resumed is not None:
                on_resumed()

    def begin_irreversible(self) -> bool:
        """Atomically enter printer dispatch when no control is pending."""

        with self._condition:
            if self._stop_requested:
                raise WorkflowStopRequested("Workflow stopped by the user.")
            if self._pause_requested:
                return False
            self._irreversible = True
            self._condition.notify_all()
            return True


@dataclass(frozen=True)
class AutomationConfig:
    """Stable settings for one natural-language manufacturing workflow."""

    output_root: Path = Path("outputs/automatic_jobs")
    m1_provider: str = "openrouter"
    m1_model: str | None = None
    m2_provider: str = "triposg_local"
    provider_poll_interval_seconds: float = 5.0
    provider_timeout_seconds: float = 1800.0
    studio_exe: Path | None = None
    machine_profile: Path | None = None
    process_profile: Path | None = None
    filament_profiles: tuple[Path, ...] = ()
    start_print: bool = False
    device_evidence_request_id: str = "M2-1E4B2301FADD"
    expected_device_id: str | None = "00M09A3A1700722"
    use_ams: bool = False
    ams_mapping: tuple[int, ...] | None = None
    remote_name: str | None = None
    minimum_unsupported_component_mm2: float = 5.0
    maximum_safe_bridge_span_mm: float = 8.0

    def __post_init__(self) -> None:
        if self.provider_poll_interval_seconds <= 0:
            raise ValueError(
                "provider_poll_interval_seconds must be positive"
            )
        if self.provider_timeout_seconds <= 0:
            raise ValueError(
                "provider_timeout_seconds must be positive"
            )
        if self.use_ams and not self.ams_mapping:
            raise ValueError(
                "use_ams=True requires an explicit ams_mapping"
            )
        if not self.use_ams and self.ams_mapping is not None:
            raise ValueError(
                "ams_mapping is only valid when use_ams=True"
            )
        if self.minimum_unsupported_component_mm2 <= 0:
            raise ValueError(
                "minimum_unsupported_component_mm2 must be positive"
            )
        if self.maximum_safe_bridge_span_mm <= 0:
            raise ValueError(
                "maximum_safe_bridge_span_mm must be positive"
            )

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return _json_ready(data)

    def immutable_fingerprint(self) -> str:
        """Fingerprint settings that must not change while resuming.

        ``start_print`` and polling timings are deliberately excluded.  This
        lets a prepared job be promoted to a real print without regenerating
        or re-slicing it.
        """

        data = self.public_dict()
        data.pop("start_print", None)
        data.pop("provider_poll_interval_seconds", None)
        data.pop("provider_timeout_seconds", None)
        data.pop("output_root", None)
        canonical = json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class AutomationResult:
    job_id: str
    status: str
    current_stage: str | None
    job_directory: str
    state_file: str
    m2_task_directory: str | None
    stl_path: str | None
    gcode_path: str | None
    print_status: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkflowServices(Protocol):
    """Ports used by the orchestration state machine.

    A future desktop/voice frontend calls the state machine; it does not need
    to know anything about M1, M2, Bambu CLI, FTPS, or MQTT.
    """

    def run_m1(self, text: str, output_dir: Path) -> dict[str, Any]: ...

    def plan_m2(self, manifest: Path, output_root: Path) -> dict[str, Any]: ...

    def submit_m2(self, task_dir: Path) -> dict[str, Any]: ...

    def poll_m2(self, task_dir: Path) -> dict[str, Any]: ...

    def acquire_m2(self, task_dir: Path) -> dict[str, Any]: ...

    def validate_m2(self, task_dir: Path) -> dict[str, Any]: ...

    def repair_m2(self, task_dir: Path) -> dict[str, Any]: ...

    def normalize_m2(self, task_dir: Path) -> dict[str, Any]: ...

    def validate_normalized_m2(self, task_dir: Path) -> dict[str, Any]: ...

    def create_stl(self, task_dir: Path) -> dict[str, Any]: ...

    def slice_stl(
        self,
        stl_path: Path,
        output_path: Path,
    ) -> dict[str, Any]: ...

    def validate_gcode(
        self,
        gcode_path: Path,
        geometry_path: Path,
    ) -> dict[str, Any]: ...

    def slice_stl_with_support(
        self,
        stl_path: Path,
        output_path: Path,
    ) -> dict[str, Any]: ...

    def upload_gcode(
        self,
        gcode_path: Path,
        access_code: str,
        *,
        printer_connection: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def start_print(
        self,
        upload: dict[str, Any],
        access_code: str,
    ) -> dict[str, Any]: ...


class EnvironmentAccessCodeProvider:
    """Read the LAN access code at dispatch time without persisting it."""

    def __init__(
        self,
        environment_variable: str = "BAMBU_LAN_ACCESS_CODE",
    ) -> None:
        self.environment_variable = environment_variable

    def __call__(self) -> str:
        value = os.environ.get(
            self.environment_variable,
            "",
        ).strip()
        if not value:
            raise AutomationWorkflowError(
                "Printer access code is unavailable. Configure the app's "
                f"secret source ({self.environment_variable}) and resume "
                "the same prepared job."
            )
        return value


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _json_ready(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "to_dict"):
        return _json_ready(value.to_dict())
    return repr(value)


_ATOMIC_WRITE_LOCK = threading.RLock()
_ATOMIC_REPLACE_ATTEMPTS = 10
_ATOMIC_REPLACE_INITIAL_DELAY_SECONDS = 0.02


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    with _ATOMIC_WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(
                _json_ready(value),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            for attempt in range(_ATOMIC_REPLACE_ATTEMPTS):
                try:
                    os.replace(temporary_path, path)
                    break
                except PermissionError:
                    if attempt + 1 >= _ATOMIC_REPLACE_ATTEMPTS:
                        raise
                    delay = min(
                        0.25,
                        _ATOMIC_REPLACE_INITIAL_DELAY_SECONDS * (2 ** attempt),
                    )
                    time.sleep(delay)
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutomationWorkflowError(
            f"Could not read workflow state {path}: {exc}",
            state_file=path,
        ) from exc
    if not isinstance(data, dict):
        raise AutomationWorkflowError(
            f"Workflow state is not a JSON object: {path}",
            state_file=path,
        )
    return data


def _request_fingerprint(text: str) -> str:
    return hashlib.sha256(
        text.strip().encode("utf-8")
    ).hexdigest()


def create_job_id() -> str:
    """Return a valid application job ID before background work starts."""

    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"AUTO-{stamp}-{uuid.uuid4().hex[:8].upper()}"


_JOB_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$"
)


class _JobState:
    def __init__(
        self,
        *,
        job_directory: Path,
        job_id: str,
        text: str,
        config: AutomationConfig,
        resume: bool,
        event_sink: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        self.job_directory = job_directory.resolve()
        self.job_id = job_id
        self.state_file = self.job_directory / "workflow_state.json"
        self.events_file = self.job_directory / "workflow_events.jsonl"
        self._event_sink = event_sink

        if resume:
            state = _load_json(self.state_file)
            if state.get("job_id") != job_id:
                raise AutomationWorkflowError(
                    "Resume job ID does not match its state file.",
                    job_id=job_id,
                    state_file=self.state_file,
                )
            if state.get("request_fingerprint") != _request_fingerprint(text):
                raise AutomationWorkflowError(
                    "Resume request text differs from the original job.",
                    job_id=job_id,
                    state_file=self.state_file,
                )
            if (
                state.get("configuration_fingerprint")
                != config.immutable_fingerprint()
            ):
                raise AutomationWorkflowError(
                    "Resume configuration changes manufacturing semantics.",
                    job_id=job_id,
                    state_file=self.state_file,
                )
            self.data = state
            self.data["start_print_requested"] = bool(config.start_print)
            self.data["updated_unix"] = time.time()
            self.save()
            return

        if self.state_file.exists():
            raise AutomationWorkflowError(
                f"Workflow job already exists: {job_id}",
                job_id=job_id,
                state_file=self.state_file,
            )

        self.job_directory.mkdir(parents=True, exist_ok=True)
        now = time.time()
        self.data: dict[str, Any] = {
            "schema_version": "automatic-print-workflow-v1",
            "job_id": job_id,
            "status": "created",
            "current_stage": None,
            "request_text": text,
            "request_fingerprint": _request_fingerprint(text),
            "configuration": config.public_dict(),
            "configuration_fingerprint": config.immutable_fingerprint(),
            "start_print_requested": bool(config.start_print),
            "created_unix": now,
            "updated_unix": now,
            "credentials_stored": False,
            "stages": {},
            "last_error": None,
        }
        self.save()
        self.emit("job_created", None, {"status": "created"})

    def save(self) -> None:
        self.data["updated_unix"] = time.time()
        _atomic_write_json(self.state_file, self.data)

    def emit(
        self,
        event_type: str,
        stage: str | None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        event = {
            "schema_version": "automatic-print-event-v1",
            "event": event_type,
            "job_id": self.job_id,
            "stage": stage,
            "created_unix": time.time(),
            "details": _json_ready(details or {}),
        }
        self.events_file.parent.mkdir(parents=True, exist_ok=True)
        with self.events_file.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(event, ensure_ascii=False, sort_keys=True)
                + "\n"
            )
            handle.flush()
        if self._event_sink is not None:
            self._event_sink(dict(event))

    def stage_result(self, stage: str) -> dict[str, Any] | None:
        record = self.data["stages"].get(stage)
        if not isinstance(record, dict):
            return None
        if record.get("status") != "completed":
            return None
        result = record.get("result")
        return result if isinstance(result, dict) else {}

    def begin_stage(self, stage: str) -> None:
        previous = self.data["stages"].get(stage, {})
        attempts = int(previous.get("attempts", 0)) + 1
        self.data["status"] = "running"
        self.data["current_stage"] = stage
        self.data["last_error"] = None
        self.data["stages"][stage] = {
            "status": "running",
            "attempts": attempts,
            "started_unix": time.time(),
            "finished_unix": None,
            "result": None,
            "error": None,
        }
        self.save()
        self.emit("stage_started", stage, {"attempt": attempts})

    def complete_stage(self, stage: str, result: Mapping[str, Any]) -> None:
        record = self.data["stages"][stage]
        record["status"] = "completed"
        record["finished_unix"] = time.time()
        record["result"] = _json_ready(result)
        record["error"] = None
        self.data["current_stage"] = None
        self.save()
        self.emit("stage_completed", stage, {"status": result.get("status")})

    def skip_stage(self, stage: str, reason: str) -> None:
        existing = self.data["stages"].get(stage)
        if isinstance(existing, dict) and existing.get("status") == "completed":
            return
        self.data["stages"][stage] = {
            "status": "skipped",
            "attempts": int((existing or {}).get("attempts", 0)),
            "started_unix": None,
            "finished_unix": time.time(),
            "result": {"reason": reason},
            "error": None,
        }
        self.save()
        self.emit("stage_skipped", stage, {"reason": reason})

    def fail_stage(
        self,
        stage: str,
        exc: BaseException,
        *,
        outcome_unknown: bool = False,
    ) -> None:
        status = "outcome_unknown" if outcome_unknown else "failed"
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        record = self.data["stages"].setdefault(stage, {})
        record["status"] = status
        record["finished_unix"] = time.time()
        record["error"] = error
        self.data["status"] = (
            "manual_reconciliation_required"
            if outcome_unknown
            else "failed"
        )
        self.data["current_stage"] = stage
        self.data["last_error"] = {"stage": stage, **error}
        self.save()
        self.emit("stage_outcome_unknown" if outcome_unknown else "stage_failed", stage, error)

    def finish(self, status: str) -> None:
        self.data["status"] = status
        self.data["current_stage"] = None
        self.data["last_error"] = None
        self.save()
        self.emit("job_finished", None, {"status": status})

    def pause(self) -> None:
        if self.data.get("status") == "paused":
            return
        self.data["status_before_pause"] = self.data.get("status", "running")
        self.data["status"] = "paused"
        self.save()
        self.emit(
            "job_paused",
            self.data.get("current_stage"),
            {"status": "paused"},
        )

    def resume(self) -> None:
        if self.data.get("status") != "paused":
            return
        restored = str(self.data.pop("status_before_pause", "running"))
        if restored in {"created", "paused", "stopped"}:
            restored = "running"
        self.data["status"] = restored
        self.save()
        self.emit(
            "job_resumed",
            self.data.get("current_stage"),
            {"status": restored},
        )


class ProductionServices:
    """Connect the state machine to the project's existing production APIs."""

    def __init__(
        self,
        config: AutomationConfig,
        *,
        m1_provider_object: Any | None = None,
        m2_provider_registry: Any | None = None,
    ) -> None:
        self.config = config
        self._m1_provider = m1_provider_object
        self._m2_registry = m2_provider_registry

    def _get_m1_provider(self) -> Any:
        if self._m1_provider is not None:
            return self._m1_provider
        name = self.config.m1_provider.strip().lower()
        if name == "openrouter":
            from am_requirement_parser.providers.openrouter_provider import (
                OpenRouterProvider,
            )

            self._m1_provider = OpenRouterProvider(model=self.config.m1_model)
        elif name == "openai":
            from am_requirement_parser.providers.openai_provider import OpenAIProvider

            self._m1_provider = OpenAIProvider(model=self.config.m1_model)
        else:
            raise AutomationWorkflowError(
                f"Unsupported M1 provider: {self.config.m1_provider}"
            )
        return self._m1_provider

    def _get_m2_registry(self) -> Any:
        if self._m2_registry is None:
            from am_model_generator.providers import build_default_provider_registry

            self._m2_registry = build_default_provider_registry()
        return self._m2_registry

    def run_m1(self, text: str, output_dir: Path) -> dict[str, Any]:
        from am_requirement_parser.m1_contract import ensure_valid_m1_manifest
        from am_requirement_parser.m1_pipeline import run_m1_pipeline

        result = run_m1_pipeline(text, self._get_m1_provider())
        route_path = (output_dir / "task_route.json").resolve()
        payload_path = (output_dir / result.output_filename).resolve()
        manifest_path = (output_dir / "m1_manifest.json").resolve()
        manifest = {
            "schema_version": result.schema_version,
            "module": result.module,
            "original_input": text,
            "task_type": result.task_type,
            "status": result.status,
            "route_file": str(route_path),
            "output_file": str(payload_path),
            "next_module": result.next_module,
        }
        ensure_valid_m1_manifest(manifest)
        _atomic_write_json(route_path, result.route)
        _atomic_write_json(payload_path, result.payload)
        _atomic_write_json(manifest_path, manifest)
        return {
            "status": result.status,
            "task_type": result.task_type,
            "next_module": result.next_module,
            "manifest_file": str(manifest_path),
            "payload_file": str(payload_path),
            "payload": result.payload,
        }

    def plan_m2(self, manifest: Path, output_root: Path) -> dict[str, Any]:
        from am_model_generator.planning import plan_m2

        return plan_m2(manifest, output_root=output_root)

    def submit_m2(self, task_dir: Path) -> dict[str, Any]:
        from am_model_generator.submission import submit_m2_plan

        return submit_m2_plan(
            task_dir,
            provider_name=self.config.m2_provider,
            registry=self._get_m2_registry(),
        )

    def poll_m2(self, task_dir: Path) -> dict[str, Any]:
        from am_model_generator.status import poll_m2_task

        return poll_m2_task(
            task_dir,
            provider_name=self.config.m2_provider,
            registry=self._get_m2_registry(),
        )

    def acquire_m2(self, task_dir: Path) -> dict[str, Any]:
        from am_model_generator.artifacts import acquire_m2_artifact

        return acquire_m2_artifact(
            task_dir,
            provider_name=self.config.m2_provider,
            registry=self._get_m2_registry(),
        )

    def validate_m2(self, task_dir: Path) -> dict[str, Any]:
        from am_model_generator.mesh_validation import validate_m2_mesh

        return validate_m2_mesh(task_dir)

    def repair_m2(self, task_dir: Path) -> dict[str, Any]:
        from am_model_generator.mesh_repair import repair_m2_mesh

        return repair_m2_mesh(task_dir)

    def normalize_m2(self, task_dir: Path) -> dict[str, Any]:
        from am_model_generator.normalization import normalize_m2_model

        return normalize_m2_model(task_dir)

    def validate_normalized_m2(self, task_dir: Path) -> dict[str, Any]:
        from am_model_generator.normalized_validation import validate_normalized_m2_mesh

        return validate_normalized_m2_mesh(task_dir)

    def create_stl(self, task_dir: Path) -> dict[str, Any]:
        from am_model_generator.stl_handoff import create_m2_stl_handoff

        return create_m2_stl_handoff(task_dir)

    def slice_stl(self, stl_path: Path, output_path: Path) -> dict[str, Any]:
        from am_print_executor.developer_mode_backend_v1120 import slice_with_bambu_cli

        return slice_with_bambu_cli(
            stl_path,
            output_path,
            studio_exe=self.config.studio_exe,
            machine_json=self.config.machine_profile,
            process_json=self.config.process_profile,
            filament_jsons=self.config.filament_profiles or None,
        )

    def validate_gcode(
        self,
        gcode_path: Path,
        geometry_path: Path,
    ) -> dict[str, Any]:
        from am_print_executor.gcode_printability_gate import (
            inspect_final_gcode_printability,
        )

        result = inspect_final_gcode_printability(
            gcode_path,
            geometry_path=geometry_path,
            minimum_bad_component_mm2=(
                self.config.minimum_unsupported_component_mm2
            ),
            maximum_safe_bridge_span_mm=(
                self.config.maximum_safe_bridge_span_mm
            ),
        )
        result["module"] = "M3"
        result["stage"] = "final_gcode_printability"
        return result

    def slice_stl_with_support(
        self,
        stl_path: Path,
        output_path: Path,
    ) -> dict[str, Any]:
        from am_print_executor.developer_mode_backend_v1120 import (
            DeveloperBackendError,
            discover_bambu_profiles,
            discover_bambu_studio,
            slice_with_bambu_cli,
        )
        from am_print_executor.m3_manufacturability_pipeline import (
            build_conservative_process_profile,
        )

        studio = self.config.studio_exe or discover_bambu_studio()
        if studio is None:
            raise DeveloperBackendError("Bambu Studio was not found")
        discovered = discover_bambu_profiles(Path(studio))
        source_process = self.config.process_profile or discovered["process"]
        support_profile = (
            output_path.parent
            / ".automatic_profiles"
            / "m3_conservative_support_process.json"
        )
        build_conservative_process_profile(
            source_profile=Path(source_process),
            output_profile=support_profile,
        )
        return slice_with_bambu_cli(
            stl_path,
            output_path,
            studio_exe=Path(studio),
            machine_json=self.config.machine_profile,
            process_json=support_profile,
            filament_jsons=self.config.filament_profiles or None,
        )

    def upload_gcode(
        self,
        gcode_path: Path,
        access_code: str,
        *,
        printer_connection: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from am_print_executor.developer_mode_backend_v1120 import upload_print_artifact

        return upload_print_artifact(
            gcode_path,
            access_code,
            remote_name=self.config.remote_name,
            device_evidence_request_id=self.config.device_evidence_request_id,
            expected_device_id=self.config.expected_device_id,
            printer_connection=printer_connection,
        )

    def start_print(self, upload: dict[str, Any], access_code: str) -> dict[str, Any]:
        from am_print_executor.developer_mode_backend_v1120 import start_uploaded_print

        return start_uploaded_print(
            access_code,
            upload,
            use_ams=self.config.use_ams,
            ams_mapping=(
                list(self.config.ams_mapping)
                if self.config.ams_mapping is not None
                else None
            ),
        )


def _extract_clarification(m1_result: Mapping[str, Any]) -> str | None:
    payload = m1_result.get("payload")
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("clarification_question")
    if isinstance(value, str) and value.strip():
        return value.strip()
    questions = payload.get("clarification_questions")
    if isinstance(questions, Sequence) and not isinstance(questions, str):
        for item in questions:
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, Mapping):
                question = item.get("question")
                if isinstance(question, str) and question.strip():
                    return question.strip()
    return None


def _workflow_result(state: _JobState) -> AutomationResult:
    stages = state.data.get("stages", {})

    def result(stage: str) -> dict[str, Any]:
        record = stages.get(stage, {})
        if not isinstance(record, dict) or record.get("status") != "completed":
            return {}
        value = record.get("result", {}) if isinstance(record, dict) else {}
        return value if isinstance(value, dict) else {}

    m2_plan = result("m2_plan")
    stl = result("m2_stl_handoff")
    sliced = (
        result("bambu_support_reslice")
        or result("bambu_slice")
    )
    started = result("print_start")
    artifact = sliced.get("artifact")
    return AutomationResult(
        job_id=state.job_id,
        status=str(state.data.get("status")),
        current_stage=state.data.get("current_stage"),
        job_directory=str(state.job_directory),
        state_file=str(state.state_file),
        m2_task_directory=(
            str(m2_plan.get("output_directory"))
            if m2_plan.get("output_directory")
            else None
        ),
        stl_path=(
            str(stl.get("stl_file") or stl.get("m3_model_file"))
            if (stl.get("stl_file") or stl.get("m3_model_file"))
            else None
        ),
        gcode_path=(
            str(artifact.get("path"))
            if isinstance(artifact, Mapping) and artifact.get("path")
            else None
        ),
        print_status=(
            str(started.get("status"))
            if started.get("status")
            else None
        ),
    )


def run_text_to_print(
    transcript: str,
    *,
    config: AutomationConfig | None = None,
    new_job_id: str | None = None,
    resume_job_id: str | None = None,
    services: WorkflowServices | None = None,
    m1_provider_object: Any | None = None,
    m2_provider_registry: Any | None = None,
    access_code_provider: Callable[[], str] | None = None,
    printer_connection_provider: Callable[[], Mapping[str, Any]] | None = None,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    control: WorkflowControl | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> AutomationResult:
    """Run or resume the whole transcript-to-print workflow.

    This is the stable interface for a future speech-to-text or desktop UI.
    A frontend passes the final transcript and receives durable events and a
    result.  It never invokes M1/M2/Bambu/printer scripts itself.
    """

    active_config = config or AutomationConfig()
    cleaned = transcript.strip()
    if not cleaned:
        raise AutomationWorkflowError("The transcript cannot be empty.")

    if new_job_id is not None and resume_job_id is not None:
        raise AutomationWorkflowError(
            "new_job_id and resume_job_id cannot be used together."
        )

    output_root = Path(active_config.output_root).expanduser().resolve()
    resume = resume_job_id is not None
    job_id = resume_job_id or new_job_id or create_job_id()
    if not _JOB_ID_PATTERN.fullmatch(job_id):
        raise AutomationWorkflowError(f"Invalid job ID: {job_id}")

    state = _JobState(
        job_directory=output_root / job_id,
        job_id=job_id,
        text=cleaned,
        config=active_config,
        resume=resume,
        event_sink=event_sink,
    )
    active_services = services or ProductionServices(
        active_config,
        m1_provider_object=m1_provider_object,
        m2_provider_registry=m2_provider_registry,
    )
    active_control = control or WorkflowControl()

    def checkpoint() -> None:
        try:
            active_control.checkpoint(
                on_paused=state.pause,
                on_resumed=state.resume,
            )
        except WorkflowStopRequested:
            if state.data.get("status") != "stopped":
                state.finish("stopped")
            raise

    def stage(name: str, operation: Callable[[], Mapping[str, Any]]) -> dict[str, Any]:
        checkpoint()
        cached = state.stage_result(name)
        if cached is not None:
            state.emit("stage_reused", name, {"status": cached.get("status")})
            checkpoint()
            return cached
        state.begin_stage(name)
        try:
            value = operation()
            if not isinstance(value, Mapping):
                raise TypeError(f"Stage {name} did not return a mapping")
            result = dict(value)
        except WorkflowStopRequested:
            state.finish("stopped")
            raise
        except Exception as exc:
            state.fail_stage(name, exc)
            raise AutomationWorkflowError(
                f"Automatic print stage {name} failed: {exc}",
                job_id=job_id,
                state_file=state.state_file,
                stage=name,
            ) from exc
        state.complete_stage(name, result)
        checkpoint()
        return result

    m1 = stage(
        "m1",
        lambda: active_services.run_m1(cleaned, state.job_directory / "m1"),
    )
    if m1.get("status") not in {"ready", "complete"} or m1.get("next_module") != "M2":
        question = _extract_clarification(m1)
        state.data["clarification_question"] = question
        state.finish("awaiting_clarification")
        return _workflow_result(state)

    manifest_path = Path(str(m1["manifest_file"]))
    plan = stage(
        "m2_plan",
        lambda: active_services.plan_m2(manifest_path, state.job_directory / "m2"),
    )
    task_dir = Path(str(plan["output_directory"]))
    stage("m2_submit", lambda: active_services.submit_m2(task_dir))

    def wait_for_provider() -> dict[str, Any]:
        deadline = time.monotonic() + active_config.provider_timeout_seconds
        polls = 0
        while True:
            checkpoint()
            current = active_services.poll_m2(task_dir)
            polls += 1
            provider_status = str(current.get("provider_status", "")).lower()
            state.emit(
                "provider_status",
                "m2_wait",
                {"poll": polls, "provider_status": provider_status},
            )
            if provider_status == "completed":
                result = dict(current)
                result["poll_count"] = polls
                return result
            if provider_status in {"failed", "cancelled", "canceled"}:
                raise AutomationWorkflowError(
                    f"M2 provider ended in {provider_status}"
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Timed out waiting for the M2 generation provider"
                )
            remaining = active_config.provider_poll_interval_seconds
            while remaining > 0:
                checkpoint()
                interval = min(0.25, remaining)
                sleep(interval)
                remaining -= interval

    stage("m2_wait", wait_for_provider)
    stage("m2_artifact", lambda: active_services.acquire_m2(task_dir))
    raw_validation = stage(
        "m2_raw_validation",
        lambda: active_services.validate_m2(task_dir),
    )

    if raw_validation.get("hard_constraints_passed") is True:
        state.skip_stage("m2_mesh_repair", "raw_mesh_passed")
        state.skip_stage("m2_repaired_validation", "raw_mesh_passed")
    else:
        stage("m2_mesh_repair", lambda: active_services.repair_m2(task_dir))
        repaired_validation = stage(
            "m2_repaired_validation",
            lambda: active_services.validate_m2(task_dir),
        )
        if repaired_validation.get("hard_constraints_passed") is not True:
            error = AutomationWorkflowError(
                "The repaired model still fails M2 hard constraints."
            )
            state.fail_stage("m2_repaired_validation", error)
            raise AutomationWorkflowError(
                str(error),
                job_id=job_id,
                state_file=state.state_file,
                stage="m2_repaired_validation",
            )

    stage("m2_normalize", lambda: active_services.normalize_m2(task_dir))
    normalized = stage(
        "m2_normalized_validation",
        lambda: active_services.validate_normalized_m2(task_dir),
    )
    if normalized.get("hard_constraints_passed") is not True:
        error = AutomationWorkflowError(
            "The normalized model fails M2 hard constraints."
        )
        state.fail_stage("m2_normalized_validation", error)
        raise AutomationWorkflowError(
            str(error),
            job_id=job_id,
            state_file=state.state_file,
            stage="m2_normalized_validation",
        )

    stl = stage("m2_stl_handoff", lambda: active_services.create_stl(task_dir))
    stl_value = stl.get("stl_file") or stl.get("m3_model_file")
    stl_path = Path(str(stl_value))
    if not stl_path.is_absolute():
        stl_path = task_dir / stl_path

    gcode_path = state.job_directory / "manufacturing" / f"{job_id}.gcode.3mf"
    sliced = stage(
        "bambu_slice",
        lambda: active_services.slice_stl(stl_path, gcode_path),
    )
    artifact = sliced.get("artifact")
    actual_gcode = (
        Path(str(artifact["path"]))
        if isinstance(artifact, Mapping) and artifact.get("path")
        else gcode_path
    )
    printability = stage(
        "m3_printability",
        lambda: active_services.validate_gcode(actual_gcode, stl_path),
    )
    if printability.get("status") == "pass":
        state.skip_stage("bambu_support_reslice", "initial_slice_passed")
        state.skip_stage("m3_support_printability", "initial_slice_passed")
    else:
        supported_gcode_path = (
            state.job_directory
            / "manufacturing"
            / f"{job_id}.supported.gcode.3mf"
        )
        supported_slice = stage(
            "bambu_support_reslice",
            lambda: active_services.slice_stl_with_support(
                stl_path,
                supported_gcode_path,
            ),
        )
        supported_artifact = supported_slice.get("artifact")
        actual_gcode = (
            Path(str(supported_artifact["path"]))
            if (
                isinstance(supported_artifact, Mapping)
                and supported_artifact.get("path")
            )
            else supported_gcode_path
        )
        supported_printability = stage(
            "m3_support_printability",
            lambda: active_services.validate_gcode(actual_gcode, stl_path),
        )
        if supported_printability.get("status") != "pass":
            # A conclusive physical-printability BLOCK is a manufacturing
            # decision, not an execution failure. Preserve the completed Gate
            # result and stop before any credential, upload, or print action.
            state.data["manufacturability_feedback"] = _json_ready(
                supported_printability.get("feedback_to_m2") or {}
            )
            state.data["printability_resolution"] = str(
                supported_printability.get("resolution")
                or "needs_geometry_regeneration"
            )
            state.skip_stage(
                "printer_upload",
                "physical_printability_blocked",
            )
            state.skip_stage(
                "print_start",
                "physical_printability_blocked",
            )
            state.finish("needs_geometry_regeneration")
            return _workflow_result(state)

    if not active_config.start_print:
        state.skip_stage("printer_upload", "prepare_only")
        state.skip_stage("print_start", "prepare_only")
        state.finish("ready_to_print")
        return _workflow_result(state)

    secret_provider = access_code_provider or EnvironmentAccessCodeProvider()
    try:
        access_code = secret_provider().strip()
    except Exception as exc:
        state.data["status"] = "credentials_required"
        state.data["current_stage"] = "printer_upload"
        state.data["last_error"] = {
            "stage": "printer_upload",
            "type": type(exc).__name__,
            "message": str(exc),
        }
        state.save()
        state.emit("credentials_required", "printer_upload", {})
        raise AutomationWorkflowError(
            f"Printer credential is required: {exc}",
            job_id=job_id,
            state_file=state.state_file,
            stage="printer_upload",
        ) from exc
    if not access_code:
        raise AutomationWorkflowError(
            "The printer access-code provider returned an empty secret.",
            job_id=job_id,
            state_file=state.state_file,
            stage="printer_upload",
        )

    printer_connection: Mapping[str, Any] | None = None
    if printer_connection_provider is not None:
        try:
            printer_connection = dict(printer_connection_provider())
        except Exception as exc:
            raise AutomationWorkflowError(
                f"Verified printer connection is unavailable: {exc}",
                job_id=job_id,
                state_file=state.state_file,
                stage="printer_upload",
            ) from exc

    if printer_connection is None:
        upload_action = lambda: active_services.upload_gcode(
            actual_gcode,
            access_code,
        )
    else:
        upload_action = lambda: active_services.upload_gcode(
            actual_gcode,
            access_code,
            printer_connection=printer_connection,
        )
    upload = stage("printer_upload", upload_action)

    previous_dispatch = state.data["stages"].get("print_start")
    if isinstance(previous_dispatch, dict) and previous_dispatch.get("status") in {
        "running",
        "outcome_unknown",
    }:
        state.data["status"] = "manual_reconciliation_required"
        state.data["current_stage"] = "print_start"
        state.save()
        raise AutomationWorkflowError(
            "A previous print-start dispatch may have reached the printer. "
            "Automatic replay is blocked; reconcile the live printer state.",
            job_id=job_id,
            state_file=state.state_file,
            stage="print_start",
        )

    cached_start = state.stage_result("print_start")
    if cached_start is None:
        # Final cooperative checkpoint: after dispatch begins its result must
        # be recorded instead of being misreported as a cancelled operation.
        while not active_control.begin_irreversible():
            checkpoint()
        state.begin_stage("print_start")
        state.data["stages"]["print_start"]["dispatch_policy"] = "exactly_once_no_replay"
        state.save()
        try:
            start_result = active_services.start_print(upload, access_code)
            if not isinstance(start_result, Mapping):
                raise TypeError("Print start did not return a mapping")
            cached_start = dict(start_result)
        except BaseException as exc:
            state.fail_stage("print_start", exc, outcome_unknown=True)
            raise AutomationWorkflowError(
                "Print-start outcome is unknown; automatic replay is blocked: "
                + str(exc),
                job_id=job_id,
                state_file=state.state_file,
                stage="print_start",
            ) from exc
        state.complete_stage("print_start", cached_start)

    print_status = str(cached_start.get("status", ""))
    if print_status == "direct_print_started":
        state.finish("print_started")
    elif print_status in {"direct_print_start_outcome_unknown", ""}:
        error = AutomationWorkflowError(
            f"Printer start outcome is unknown: {print_status or 'missing status'}"
        )
        state.fail_stage("print_start", error, outcome_unknown=True)
        raise AutomationWorkflowError(
            str(error),
            job_id=job_id,
            state_file=state.state_file,
            stage="print_start",
        )
    else:
        state.data["status"] = "print_rejected"
        state.data["current_stage"] = None
        state.save()
        state.emit("job_finished", None, {"status": "print_rejected"})

    # Explicitly erase the local reference as soon as the dispatch call ends.
    access_code = ""
    return _workflow_result(state)
