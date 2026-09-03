from __future__ import annotations

import copy
import json
import ssl
import threading
import time
import uuid

from typing import Any, Callable, Mapping


ACTIVE_STATES = {"PREPARE", "RUNNING", "SLICING"}
PAUSED_STATES = {"PAUSE", "PAUSED"}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _is_nonzero_error(value: Any) -> bool:
    return value not in (None, "", 0, 0.0, "0")


def summarize_print_status(
    print_object: Mapping[str, Any],
    *,
    observed_unix: float | None = None,
    active_seen: bool = False,
) -> dict[str, Any]:
    """Return the compact, secret-free status used by the frontend.

    Layer telemetry is deliberately omitted during normal printing. It is
    included only in an alert so a layer-by-layer MQTT feed never floods the
    UI while still leaving enough context to diagnose a failure.
    """

    state = str(print_object.get("gcode_state") or "").strip().upper()
    percent = _number(print_object.get("mc_percent"))
    current_layer = _number(print_object.get("layer_num"))
    total_layers = _number(print_object.get("total_layer_num"))
    if percent is None and current_layer is not None and total_layers and total_layers > 0:
        percent = current_layer / total_layers * 100.0
    if percent is not None:
        percent = round(max(0.0, min(100.0, percent)), 1)

    remaining = _number(print_object.get("mc_remaining_time"))
    if remaining is not None:
        remaining = max(0.0, remaining)

    hms = print_object.get("hms")
    hms_alerts = copy.deepcopy(hms) if isinstance(hms, list) and hms else []
    print_error = print_object.get("print_error")
    has_alert = state == "FAILED" or _is_nonzero_error(print_error) or bool(hms_alerts)

    nozzle_target = _number(print_object.get("nozzle_target_temper"))
    bed_target = _number(print_object.get("bed_target_temper"))
    restart_ready = (
        state in {"IDLE", "FINISH", "FAILED"}
        and state not in ACTIVE_STATES
        and state not in PAUSED_STATES
        and print_error == 0
        and print_object.get("sdcard") is True
        and isinstance(hms, list)
        and not hms
        and nozzle_target is not None
        and nozzle_target <= 40
        and bed_target is not None
        and bed_target <= 40
    )

    if has_alert:
        status = "error"
    elif state in PAUSED_STATES:
        status = "paused"
    elif state in ACTIVE_STATES:
        status = "printing"
    elif active_seen and state in {"FINISH", "IDLE"}:
        status = "completed"
        percent = 100.0
    elif state:
        status = "waiting"
    else:
        status = "connecting"

    result: dict[str, Any] = {
        "status": status,
        "printer_state": state or None,
        "percent": percent,
        "remaining_minutes": remaining,
        "observed_unix": observed_unix if observed_unix is not None else time.time(),
        "has_alert": has_alert,
        "restart_ready": restart_ready,
    }
    identity = {
        key: str(print_object[key])
        for key in ("task_id", "subtask_id", "subtask_name", "gcode_file")
        if print_object.get(key) not in (None, "", "0", 0)
    }
    if identity:
        result["job_identity"] = identity
    if has_alert:
        result["alert"] = {
            "message": "打印机报告异常，请检查设备后再决定是否继续。",
            "print_error": print_error,
            "hms": hms_alerts,
            "layer_num": int(current_layer) if current_layer is not None else None,
            "total_layer_num": int(total_layers) if total_layers is not None else None,
        }
    return result


class X1CLiveMonitor:
    """Maintain one read-only MQTT subscription for compact live status."""

    def __init__(
        self,
        *,
        ip: str,
        device_id: str,
        access_code: str,
        on_update: Callable[[dict[str, Any]], None],
    ) -> None:
        self.ip = ip
        self.device_id = device_id
        self._access_code = access_code
        self._on_update = on_update
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: Any = None
        self._active_seen = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name=f"x1c-live-monitor-{self.device_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        client = self._client
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _publish_update(self, print_object: Mapping[str, Any]) -> None:
        state = str(print_object.get("gcode_state") or "").strip().upper()
        if state in ACTIVE_STATES or state in PAUSED_STATES:
            self._active_seen = True
        self._on_update(
            summarize_print_status(
                print_object,
                observed_unix=time.time(),
                active_seen=self._active_seen,
            )
        )

    def _run(self) -> None:
        import paho.mqtt.client as mqtt

        secret = self._access_code
        self._access_code = ""
        connected = threading.Event()
        first_status = threading.Event()
        callback_errors: list[str] = []

        def reason_failed(reason_code: Any) -> bool:
            marker = getattr(reason_code, "is_failure", None)
            if marker is not None:
                return bool(marker)
            try:
                return int(reason_code) != 0
            except Exception:
                return str(reason_code).lower() not in {"0", "success"}

        def on_connect(client, userdata, flags, reason_code, properties=None):
            if reason_failed(reason_code):
                callback_errors.append(f"MQTT connection rejected: {reason_code}")
                connected.set()
                return
            connected.set()
            client.subscribe(f"device/{self.device_id}/report", qos=0)

        def on_message(client, userdata, message):
            try:
                payload = json.loads(message.payload.decode("utf-8", errors="strict"))
            except Exception:
                return
            print_object = payload.get("print") if isinstance(payload, dict) else None
            if not isinstance(print_object, dict):
                return
            first_status.set()
            self._publish_update(print_object)

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"nl-am-ui-monitor-{uuid.uuid4().hex[:10]}",
            protocol=mqtt.MQTTv311,
        )
        self._client = client
        client.username_pw_set("bblp", secret)
        secret = ""
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
        client.on_connect = on_connect
        client.on_message = on_message

        try:
            client.connect_async(self.ip, 8883, keepalive=60)
            client.loop_start()
            if not connected.wait(45) or callback_errors:
                raise RuntimeError(callback_errors[-1] if callback_errors else "打印机监控连接超时。")
            if not first_status.wait(10) and not self._stop.is_set():
                request = {
                    "pushing": {
                        "sequence_id": str(int(time.time() * 1000)),
                        "command": "pushall",
                        "version": 1,
                        "push_target": 1,
                    }
                }
                client.publish(
                    f"device/{self.device_id}/request",
                    json.dumps(request, ensure_ascii=False, separators=(",", ":")),
                    qos=0,
                    retain=False,
                )
            while not self._stop.wait(1.0):
                pass
        except Exception as exc:
            if not self._stop.is_set():
                self._on_update(
                    {
                        "status": "unavailable",
                        "printer_state": None,
                        "percent": None,
                        "remaining_minutes": None,
                        "observed_unix": time.time(),
                        "has_alert": False,
                        "message": str(exc),
                    }
                )
        finally:
            try:
                client.loop_stop()
            except Exception:
                pass
            try:
                client.disconnect()
            except Exception:
                pass
            self._client = None
