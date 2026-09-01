from __future__ import annotations

import argparse
import json
import sys

from pathlib import Path

from .workflow import (
    AutomationConfig,
    AutomationWorkflowError,
    EnvironmentAccessCodeProvider,
    run_text_to_print,
)


def _mapping(value: str | None) -> tuple[int, ...] | None:
    if value is None:
        return None
    try:
        result = tuple(
            int(item.strip())
            for item in value.split(",")
            if item.strip()
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "AMS mapping must be comma-separated integers"
        ) from exc
    return result or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "One-call natural-language pipeline: M1 -> M2 -> STL -> "
            "headless Bambu slice -> optional physical print."
        )
    )
    parser.add_argument("request", nargs="+", help="Natural-language print request")
    parser.add_argument("--resume-job")
    parser.add_argument("--output-root", default="outputs/automatic_jobs")
    parser.add_argument("--m1-provider", choices=("openrouter", "openai"), default="openrouter")
    parser.add_argument("--m1-model")
    parser.add_argument("--m2-provider", choices=("triposg_local", "meshy", "mock"), default="triposg_local")
    parser.add_argument("--provider-timeout", type=float, default=1800.0)
    parser.add_argument("--provider-poll-interval", type=float, default=5.0)
    parser.add_argument("--studio", type=Path)
    parser.add_argument("--machine", type=Path)
    parser.add_argument("--process", type=Path)
    parser.add_argument("--filament", action="append", default=[], type=Path)
    parser.add_argument("--start-print", action="store_true")
    parser.add_argument("--access-code-env", default="BAMBU_LAN_ACCESS_CODE")
    parser.add_argument("--device-evidence-request-id", default="M2-1E4B2301FADD")
    parser.add_argument("--expected-device-id", default="00M09A3A1700722")
    parser.add_argument("--use-ams", action="store_true")
    parser.add_argument("--ams-mapping", type=_mapping)
    parser.add_argument("--remote-name")
    parser.add_argument("--minimum-unsupported-area", type=float, default=5.0)
    parser.add_argument("--maximum-safe-bridge-span", type=float, default=8.0)
    parser.add_argument("--json-events", action="store_true")
    args = parser.parse_args(argv)

    config = AutomationConfig(
        output_root=args.output_root,
        m1_provider=args.m1_provider,
        m1_model=args.m1_model,
        m2_provider=args.m2_provider,
        provider_poll_interval_seconds=args.provider_poll_interval,
        provider_timeout_seconds=args.provider_timeout,
        studio_exe=args.studio,
        machine_profile=args.machine,
        process_profile=args.process,
        filament_profiles=tuple(args.filament),
        start_print=args.start_print,
        device_evidence_request_id=args.device_evidence_request_id,
        expected_device_id=args.expected_device_id,
        use_ams=args.use_ams,
        ams_mapping=args.ams_mapping,
        remote_name=args.remote_name,
        minimum_unsupported_component_mm2=args.minimum_unsupported_area,
        maximum_safe_bridge_span_mm=args.maximum_safe_bridge_span,
    )

    def events(event: dict[str, object]) -> None:
        if args.json_events:
            print(json.dumps(event, ensure_ascii=False), flush=True)
            return
        stage = event.get("stage") or "job"
        print(f"[{stage}] {event.get('event')}", flush=True)

    try:
        result = run_text_to_print(
            " ".join(args.request),
            config=config,
            resume_job_id=args.resume_job,
            access_code_provider=EnvironmentAccessCodeProvider(args.access_code_env),
            event_sink=events,
        )
    except (AutomationWorkflowError, ValueError) as exc:
        payload = {
            "status": "failed",
            "message": str(exc),
            "job_id": getattr(exc, "job_id", None),
            "state_file": str(getattr(exc, "state_file", "") or ""),
            "stage": getattr(exc, "stage", None),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status in {"ready_to_print", "print_started", "awaiting_clarification"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
