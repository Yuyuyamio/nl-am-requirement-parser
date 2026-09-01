from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import ssl
import threading
import time
import uuid

from dataclasses import asdict, dataclass
from typing import Any, Literal

try:
    import paho.mqtt.client as mqtt
except ImportError as exc:  # pragma: no cover - exercised by runtime guard
    mqtt = None  # type: ignore[assignment]
    _PAHO_IMPORT_ERROR = exc
else:
    _PAHO_IMPORT_ERROR = None


MQTT_PORT = 8883
MQTT_USERNAME = "bblp"
MQTT_PROTOCOL = "MQTT/3.1.1 over TLS"
REPORT_WILDCARD = "device/+/report"

ConnectionErrorCode = Literal[
    "invalid_ip",
    "invalid_device_id",
    "network_unreachable",
    "port_unreachable",
    "authentication_failed",
    "mqtt_tls_failed",
    "timeout",
]

_ERROR_MESSAGES: dict[str, str] = {
    "invalid_ip": "Printer IP must be a private IPv4 address.",
    "invalid_device_id": "Printer Device ID is not valid.",
    "network_unreachable": "Printer network is unreachable.",
    "port_unreachable": "Printer MQTT/TLS port 8883 is unreachable.",
    "authentication_failed": "Printer rejected MQTT authentication.",
    "mqtt_tls_failed": "MQTT/TLS negotiation failed.",
    "timeout": "Timed out waiting for a read-only printer status message.",
}


@dataclass(frozen=True)
class PrinterConnectionStatus:
    connected: bool
    printer_ip: str
    device_id: str | None
    transport: str
    authenticated: bool
    printer_state_observed: bool
    subscribed: bool
    elapsed_seconds: float
    connection_attempts: int
    status_summary: dict[str, Any] | None = None
    tls_certificate_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PrinterConnectionError(RuntimeError):
    """A classified, credential-free X1C connection failure."""

    def __init__(
        self,
        code: ConnectionErrorCode,
        *,
        printer_ip: str | None = None,
        device_id: str | None = None,
        elapsed_seconds: float = 0.0,
        partial_status: PrinterConnectionStatus | None = None,
    ) -> None:
        super().__init__(_ERROR_MESSAGES[code])
        self.code = code
        self.printer_ip = printer_ip
        self.device_id = device_id
        self.elapsed_seconds = round(elapsed_seconds, 3)
        self.partial_status = partial_status

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "connected": False,
            "printer_ip": self.printer_ip,
            "device_id": self.device_id,
            "transport": MQTT_PROTOCOL,
            "authenticated": False,
            "printer_state_observed": False,
            "error_code": self.code,
            "error": str(self),
            "elapsed_seconds": self.elapsed_seconds,
        }
        if self.partial_status is not None:
            partial = self.partial_status.to_dict()
            payload["authenticated"] = partial["authenticated"]
            payload["printer_state_observed"] = partial[
                "printer_state_observed"
            ]
            payload["subscribed"] = partial["subscribed"]
        return payload


def validate_printer_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value.strip())
    except (AttributeError, ValueError):
        raise PrinterConnectionError("invalid_ip") from None
    if address.version != 4 or not address.is_private:
        raise PrinterConnectionError("invalid_ip") from None
    return str(address)


def validate_device_id(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    candidate = value.strip()
    if not 8 <= len(candidate) <= 64:
        raise PrinterConnectionError("invalid_device_id") from None
    if not all(character.isalnum() or character in {"-", "_"} for character in candidate):
        raise PrinterConnectionError("invalid_device_id") from None
    return candidate


def extract_device_id(topic: str) -> str | None:
    parts = topic.split("/")
    if (
        len(parts) == 3
        and parts[0] == "device"
        and parts[2] == "report"
        and parts[1]
    ):
        return parts[1]
    return None


def extract_status_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    print_state = payload.get("print")
    if not isinstance(print_state, dict):
        return None
    keys = (
        "gcode_state",
        "mc_percent",
        "mc_remaining_time",
        "nozzle_temper",
        "bed_temper",
        "chamber_temper",
        "wifi_signal",
        "print_error",
        "lifecycle",
        "stg_cur",
        "stg",
        "ams_status",
    )
    summary = {key: print_state.get(key) for key in keys if key in print_state}
    summary["report_has_print_object"] = True
    return summary


def _network_error_code(exc: OSError) -> ConnectionErrorCode:
    error_number = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
    if isinstance(exc, (TimeoutError, socket.timeout)) or error_number in {
        10060,
        110,
    }:
        return "timeout"
    if isinstance(exc, ConnectionRefusedError) or error_number in {
        61,
        111,
        10061,
    }:
        return "port_unreachable"
    return "network_unreachable"


def _tls_preflight(ip_address: str, timeout_seconds: float) -> str:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection(
            (ip_address, MQTT_PORT),
            timeout=timeout_seconds,
        ) as raw_socket:
            with context.wrap_socket(
                raw_socket,
                server_hostname=ip_address,
            ) as tls_socket:
                certificate = tls_socket.getpeercert(binary_form=True)
    except ssl.SSLError:
        raise PrinterConnectionError(
            "mqtt_tls_failed",
            printer_ip=ip_address,
        ) from None
    except OSError as exc:
        raise PrinterConnectionError(
            _network_error_code(exc),
            printer_ip=ip_address,
        ) from None
    if not certificate:
        raise PrinterConnectionError(
            "mqtt_tls_failed",
            printer_ip=ip_address,
        ) from None
    return hashlib.sha256(certificate).hexdigest().upper()


def _reason_code_value(reason_code: Any) -> int:
    value = getattr(reason_code, "value", reason_code)
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _partial_status(
    *,
    ip_address: str,
    device_id: str | None,
    authenticated: bool,
    subscribed: bool,
    elapsed_seconds: float,
    attempt: int,
    fingerprint: str,
) -> PrinterConnectionStatus:
    return PrinterConnectionStatus(
        connected=authenticated,
        printer_ip=ip_address,
        device_id=device_id,
        transport=MQTT_PROTOCOL,
        authenticated=authenticated,
        printer_state_observed=False,
        subscribed=subscribed,
        elapsed_seconds=round(elapsed_seconds, 3),
        connection_attempts=attempt,
        status_summary=None,
        tls_certificate_sha256=fingerprint,
    )


def _connect_once(
    ip_address: str,
    secret: str,
    device_id: str | None,
    *,
    timeout_seconds: float,
    attempt: int,
    fingerprint: str,
) -> PrinterConnectionStatus:
    if mqtt is None:
        raise PrinterConnectionError(
            "mqtt_tls_failed",
            printer_ip=ip_address,
            device_id=device_id,
        ) from None

    started = time.monotonic()
    done = threading.Event()
    state: dict[str, Any] = {
        "authenticated": False,
        "subscribed": False,
        "device_id": device_id,
        "summary": None,
        "error_code": None,
    }
    topic = f"device/{device_id}/report" if device_id else REPORT_WILDCARD

    def fail(code: ConnectionErrorCode) -> None:
        if state["error_code"] is None:
            state["error_code"] = code
        done.set()

    def on_connect(
        client: Any,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        code = _reason_code_value(reason_code)
        if code != 0:
            fail("authentication_failed" if code in {4, 5, 134, 135} else "mqtt_tls_failed")
            return
        state["authenticated"] = True
        result, _message_id = client.subscribe(topic, qos=0)
        if result != mqtt.MQTT_ERR_SUCCESS:
            fail("mqtt_tls_failed")

    def on_subscribe(
        client: Any,
        userdata: Any,
        mid: Any,
        reason_codes: Any,
        properties: Any = None,
    ) -> None:
        if any(getattr(code, "is_failure", False) for code in reason_codes):
            fail("mqtt_tls_failed")
            return
        state["subscribed"] = True

    def on_message(client: Any, userdata: Any, message: Any) -> None:
        observed_id = extract_device_id(str(message.topic))
        if observed_id is None:
            return
        if device_id is not None and observed_id != device_id:
            return
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        summary = extract_status_summary(payload)
        if summary is None:
            return
        state["device_id"] = observed_id
        state["summary"] = summary
        done.set()

    def on_disconnect(
        client: Any,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        if done.is_set() or _reason_code_value(reason_code) == 0:
            return
        fail("network_unreachable" if state["authenticated"] else "mqtt_tls_failed")

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"am-x1c-readonly-{uuid.uuid4().hex[:12]}",
        protocol=mqtt.MQTTv311,
        reconnect_on_failure=False,
    )
    client.username_pw_set(MQTT_USERNAME, secret)
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tls_context.check_hostname = False
    tls_context.verify_mode = ssl.CERT_NONE
    client.tls_set_context(tls_context)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    unexpected = False
    try:
        client.connect_async(ip_address, MQTT_PORT, keepalive=30)
        client.loop_start()
        done.wait(timeout_seconds)
    except Exception:
        unexpected = True
    finally:
        try:
            client.disconnect()
        except Exception:
            pass
        try:
            client.loop_stop()
        except Exception:
            pass
        try:
            client.username_pw_set(None)
        except Exception:
            pass

    elapsed = time.monotonic() - started
    if unexpected:
        raise PrinterConnectionError(
            "mqtt_tls_failed",
            printer_ip=ip_address,
            device_id=device_id,
            elapsed_seconds=elapsed,
            partial_status=_partial_status(
                ip_address=ip_address,
                device_id=state["device_id"],
                authenticated=bool(state["authenticated"]),
                subscribed=bool(state["subscribed"]),
                elapsed_seconds=elapsed,
                attempt=attempt,
                fingerprint=fingerprint,
            ),
        ) from None
    if state["error_code"] is not None:
        raise PrinterConnectionError(
            state["error_code"],
            printer_ip=ip_address,
            device_id=state["device_id"],
            elapsed_seconds=elapsed,
            partial_status=_partial_status(
                ip_address=ip_address,
                device_id=state["device_id"],
                authenticated=bool(state["authenticated"]),
                subscribed=bool(state["subscribed"]),
                elapsed_seconds=elapsed,
                attempt=attempt,
                fingerprint=fingerprint,
            ),
        ) from None
    if state["summary"] is None or state["device_id"] is None:
        raise PrinterConnectionError(
            "timeout",
            printer_ip=ip_address,
            device_id=state["device_id"],
            elapsed_seconds=elapsed,
            partial_status=_partial_status(
                ip_address=ip_address,
                device_id=state["device_id"],
                authenticated=bool(state["authenticated"]),
                subscribed=bool(state["subscribed"]),
                elapsed_seconds=elapsed,
                attempt=attempt,
                fingerprint=fingerprint,
            ),
        ) from None

    return PrinterConnectionStatus(
        connected=True,
        printer_ip=ip_address,
        device_id=str(state["device_id"]),
        transport=MQTT_PROTOCOL,
        authenticated=True,
        printer_state_observed=True,
        subscribed=bool(state["subscribed"]),
        elapsed_seconds=round(elapsed, 3),
        connection_attempts=attempt,
        status_summary=state["summary"],
        tls_certificate_sha256=fingerprint,
    )


def connect_printer(
    ip: str,
    access_code: str,
    device_id: str | None = None,
    *,
    timeout_seconds: float = 15.0,
    attempts: int = 1,
    retry_delay_seconds: float = 0.5,
) -> PrinterConnectionStatus:
    """Authenticate to an X1C and observe one status report without publishing.

    The access code is deliberately overwritten in this frame immediately and
    is never included in a result, exception, log, report, client ID, or topic.
    """

    started = time.monotonic()
    ip_address = validate_printer_ip(ip)
    validated_device_id = validate_device_id(device_id)
    if timeout_seconds <= 0 or timeout_seconds > 120:
        raise ValueError("timeout_seconds must be between 0 and 120")
    if attempts < 1 or attempts > 3:
        raise ValueError("attempts must be between 1 and 3")
    if retry_delay_seconds < 0 or retry_delay_seconds > 10:
        raise ValueError("retry_delay_seconds must be between 0 and 10")

    secret = access_code.strip() if isinstance(access_code, str) else ""
    access_code = ""
    if not secret:
        raise PrinterConnectionError(
            "authentication_failed",
            printer_ip=ip_address,
            device_id=validated_device_id,
        ) from None

    last_error: PrinterConnectionError | None = None
    try:
        for attempt in range(1, attempts + 1):
            try:
                preflight_timeout = min(5.0, timeout_seconds)
                fingerprint = _tls_preflight(ip_address, preflight_timeout)
                return _connect_once(
                    ip_address,
                    secret,
                    validated_device_id,
                    timeout_seconds=timeout_seconds,
                    attempt=attempt,
                    fingerprint=fingerprint,
                )
            except PrinterConnectionError as exc:
                last_error = exc
                if exc.code in {
                    "authentication_failed",
                    "invalid_ip",
                    "invalid_device_id",
                }:
                    break
                if attempt < attempts:
                    time.sleep(retry_delay_seconds)
            except Exception:
                last_error = PrinterConnectionError(
                    "mqtt_tls_failed",
                    printer_ip=ip_address,
                    device_id=validated_device_id,
                    elapsed_seconds=time.monotonic() - started,
                )
                if attempt < attempts:
                    time.sleep(retry_delay_seconds)
        assert last_error is not None
        raise PrinterConnectionError(
            last_error.code,
            printer_ip=ip_address,
            device_id=last_error.device_id or validated_device_id,
            elapsed_seconds=time.monotonic() - started,
            partial_status=last_error.partial_status,
        ) from None
    finally:
        secret = ""
