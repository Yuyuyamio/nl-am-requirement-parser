from __future__ import annotations

import json
import io
import traceback
import unittest

from types import SimpleNamespace
from unittest.mock import patch

from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from am_print_executor.x1c_connection import (
    MQTT_PROTOCOL,
    PrinterConnectionError,
    _reason_code_value,
    connect_printer,
)
from am_print_executor.x1c_connection_cli import main as connection_cli_main


class _ReasonCode:
    def __init__(self, value: int) -> None:
        self.value = value
        self.is_failure = value != 0

    def __int__(self) -> int:
        return self.value


class _FakeClient:
    def __init__(
        self,
        *,
        authenticate: bool = True,
        send_status: bool = True,
        observed_device_id: str = "00M09A3A1700722",
    ) -> None:
        self.authenticate = authenticate
        self.send_status = send_status
        self.observed_device_id = observed_device_id
        self.password: str | None = None
        self.topic: str | None = None
        self.publish_count = 0
        self.on_connect = None
        self.on_subscribe = None
        self.on_message = None
        self.on_disconnect = None

    def username_pw_set(self, username, password=None) -> None:
        self.password = password

    def tls_set_context(self, context) -> None:
        return None

    def tls_insecure_set(self, value) -> None:
        return None

    def connect_async(self, ip, port, keepalive) -> None:
        self.ip = ip
        self.port = port

    def subscribe(self, topic, qos=0):
        self.topic = topic
        return 0, 1

    def publish(self, *args, **kwargs):
        self.publish_count += 1
        raise AssertionError("read-only connector must never publish")

    def loop_start(self) -> None:
        reason = _ReasonCode(0 if self.authenticate else 5)
        self.on_connect(self, None, None, reason, None)
        if not self.authenticate:
            return
        self.on_subscribe(self, None, 1, [_ReasonCode(0)], None)
        if self.send_status:
            message = SimpleNamespace(
                topic=f"device/{self.observed_device_id}/report",
                payload=json.dumps(
                    {"print": {"gcode_state": "IDLE", "nozzle_temper": 30.1}}
                ).encode("utf-8"),
            )
            self.on_message(self, None, message)

    def disconnect(self) -> None:
        if self.on_disconnect is not None:
            self.on_disconnect(self, None, None, _ReasonCode(0), None)

    def loop_stop(self) -> None:
        return None


class _FakeMqtt:
    MQTT_ERR_SUCCESS = 0
    MQTTv311 = 4
    CallbackAPIVersion = SimpleNamespace(VERSION2=2)

    def __init__(self, client: _FakeClient) -> None:
        self.client = client

    def Client(self, **kwargs):
        self.client.client_options = kwargs
        return self.client


class TestX1CConnection(unittest.TestCase):
    def connect_with(self, client: _FakeClient, **kwargs):
        with patch(
            "am_print_executor.x1c_connection._tls_preflight",
            return_value="AA" * 32,
        ), patch(
            "am_print_executor.x1c_connection.mqtt",
            _FakeMqtt(client),
        ):
            return connect_printer(
                ip="192.168.1.25",
                access_code=kwargs.pop("access_code", "TOP-SECRET"),
                timeout_seconds=kwargs.pop("timeout_seconds", 0.05),
                **kwargs,
            )

    def test_autodiscovers_device_and_observes_real_status_shape(self) -> None:
        client = _FakeClient()
        result = self.connect_with(client)

        self.assertTrue(result.connected)
        self.assertTrue(result.authenticated)
        self.assertTrue(result.printer_state_observed)
        self.assertEqual(result.device_id, "00M09A3A1700722")
        self.assertEqual(result.transport, MQTT_PROTOCOL)
        self.assertEqual(result.status_summary["gcode_state"], "IDLE")
        self.assertEqual(client.topic, "device/+/report")
        self.assertEqual(client.publish_count, 0)
        self.assertNotIn("TOP-SECRET", json.dumps(result.to_dict()))

    def test_paho_v2_reason_code_value_is_parsed(self) -> None:
        success = ReasonCode(PacketTypes.CONNACK, "Success")

        self.assertFalse(hasattr(success, "__int__"))
        self.assertEqual(_reason_code_value(success), 0)

    def test_explicit_device_id_uses_exact_read_only_topic(self) -> None:
        client = _FakeClient()
        result = self.connect_with(
            client,
            device_id="00M09A3A1700722",
        )
        self.assertTrue(result.connected)
        self.assertEqual(client.topic, "device/00M09A3A1700722/report")
        self.assertEqual(client.publish_count, 0)

    def test_invalid_ip_fails_immediately(self) -> None:
        with self.assertRaises(PrinterConnectionError) as context:
            connect_printer("not-an-ip", "SECRET")
        self.assertEqual(context.exception.code, "invalid_ip")

    def test_wrong_access_code_is_classified_and_never_disclosed(self) -> None:
        secret = "VERY-WRONG-ACCESS-CODE"
        client = _FakeClient(authenticate=False)
        rendered_traceback = ""
        with self.assertRaises(PrinterConnectionError) as context:
            try:
                self.connect_with(client, access_code=secret)
            except PrinterConnectionError:
                rendered_traceback = traceback.format_exc()
                raise

        error = context.exception
        self.assertEqual(error.code, "authentication_failed")
        self.assertNotIn(secret, str(error))
        self.assertNotIn(secret, rendered_traceback)
        self.assertNotIn(secret, json.dumps(error.to_dict()))
        self.assertEqual(client.publish_count, 0)

    def test_unexpected_dependency_error_is_sanitized(self) -> None:
        secret = "DEPENDENCY-MUST-NOT-DISCLOSE-THIS"
        rendered_traceback = ""
        with patch(
            "am_print_executor.x1c_connection._tls_preflight",
            return_value="AA" * 32,
        ), patch(
            "am_print_executor.x1c_connection._connect_once",
            side_effect=RuntimeError(secret),
        ), self.assertRaises(PrinterConnectionError) as context:
            try:
                connect_printer(
                    "192.168.1.25",
                    secret,
                    retry_delay_seconds=0,
                )
            except PrinterConnectionError:
                rendered_traceback = traceback.format_exc()
                raise

        error = context.exception
        self.assertEqual(error.code, "mqtt_tls_failed")
        self.assertNotIn(secret, str(error))
        self.assertNotIn(secret, rendered_traceback)
        self.assertNotIn(secret, json.dumps(error.to_dict()))

    def test_status_timeout_is_bounded_and_preserves_authentication_fact(self) -> None:
        client = _FakeClient(send_status=False)
        with self.assertRaises(PrinterConnectionError) as context:
            self.connect_with(client, timeout_seconds=0.01)
        error = context.exception
        self.assertEqual(error.code, "timeout")
        self.assertTrue(error.partial_status.authenticated)
        self.assertTrue(error.partial_status.subscribed)

    def test_preflight_error_categories_and_finite_retry(self) -> None:
        for code in (
            "network_unreachable",
            "port_unreachable",
            "mqtt_tls_failed",
            "timeout",
        ):
            with self.subTest(code=code):
                failure = PrinterConnectionError(code, printer_ip="192.168.1.25")
                with patch(
                    "am_print_executor.x1c_connection._tls_preflight",
                    side_effect=failure,
                ) as preflight:
                    with self.assertRaises(PrinterConnectionError) as context:
                        connect_printer(
                            "192.168.1.25",
                            "SECRET",
                            attempts=2,
                            retry_delay_seconds=0,
                        )
                self.assertEqual(context.exception.code, code)
                self.assertEqual(preflight.call_count, 2)

    def test_cli_hides_access_code_and_prints_only_safe_status(self) -> None:
        client = _FakeClient()
        secret = "CLI-SECRET-MUST-NOT-APPEAR"
        with patch(
            "builtins.input",
            side_effect=["192.168.1.25", "00M09A3A1700722"],
        ), patch(
            "am_print_executor.x1c_connection_cli.getpass.getpass",
            return_value=secret,
        ) as hidden_reader, patch(
            "am_print_executor.x1c_connection._tls_preflight",
            return_value="AA" * 32,
        ), patch(
            "am_print_executor.x1c_connection.mqtt",
            _FakeMqtt(client),
        ), patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = connection_cli_main([])

        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertEqual(hidden_reader.call_count, 1)
        self.assertIn("X1C_CONNECTION_GATE = PASS", output)
        self.assertIn("printer status observed = true", output)
        self.assertNotIn(secret, output)

    def test_cli_cancel_does_not_emit_traceback(self) -> None:
        with patch(
            "builtins.input",
            side_effect=["192.168.1.25", "00M09A3A1700722"],
        ), patch(
            "am_print_executor.x1c_connection_cli.getpass.getpass",
            side_effect=KeyboardInterrupt,
        ), patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = connection_cli_main([])

        output = stdout.getvalue()
        self.assertEqual(exit_code, 130)
        self.assertIn("X1C_CONNECTION_GATE = CANCELLED", output)
        self.assertNotIn("Traceback", output)


if __name__ == "__main__":
    unittest.main()
