# ruff: noqa: INP001
"""Safely bind a BlueKing PaaS add-on service through bk-cli."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

KNOWN_ENV_PLAN_ID_MAPS = {
    # GCS-MySQL requires an explicit plan in both environments.
    "946ee404-df67-4013-a92f-9cc116ff50dc": {
        "stag": "8c52a7f8-a8ff-47da-b0f0-0ef744b37562",
        "prod": "8c52a7f8-a8ff-47da-b0f0-0ef744b37562",
    }
}


class WorkflowError(RuntimeError):
    """A user-facing workflow failure with optional raw CLI output."""

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


@dataclass(frozen=True)
class Service:
    group: str
    uuid: str
    name: str
    display_name: str
    entry: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve, bind, and verify one add-on service without guessing UUIDs or plans."
    )
    parser.add_argument("--app-code", required=True)
    parser.add_argument("--module", default="default")
    parser.add_argument("--context")
    parser.add_argument("--stage", default="prod")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--service", help="Exact service name or display_name returned by list_module_services")
    target.add_argument("--service-id", help="Exact service UUID returned by list_module_services")
    parser.add_argument("--plan-id", help="Use one explicit plan for both environments")
    parser.add_argument("--stag-plan-id", help="Explicit stag plan; requires --prod-plan-id")
    parser.add_argument("--prod-plan-id", help="Explicit prod plan; requires --stag-plan-id")
    parser.add_argument("--dry-run", action="store_true", help="Resolve and print the body without binding")
    args = parser.parse_args()

    has_env_plan = bool(args.stag_plan_id or args.prod_plan_id)
    if args.plan_id and has_env_plan:
        parser.error("--plan-id cannot be combined with --stag-plan-id or --prod-plan-id")
    if bool(args.stag_plan_id) != bool(args.prod_plan_id):
        parser.error("--stag-plan-id and --prod-plan-id must be provided together")
    return args


def cli_prefix(context: str | None) -> list[str]:
    command = ["bk-cli"]
    if context:
        command.extend(["--context", context])
    return command


def run_command(command: list[str]) -> str:
    try:
        process = subprocess.run(command, check=False, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise WorkflowError(f"command not found: {command[0]}") from error

    if process.stderr:
        sys.stderr.write(process.stderr)
    if process.returncode != 0:
        raise WorkflowError(f"command exited with status {process.returncode}", process.stdout)
    return process.stdout


def parse_cli_response(raw: str, command_name: str) -> tuple[dict[str, Any], Any]:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        raise WorkflowError(f"{command_name} returned non-JSON output", raw) from error
    if not isinstance(document, dict):
        raise WorkflowError(f"{command_name} returned an unexpected JSON value", raw)
    if document.get("ok") is not True:
        raise WorkflowError(f"{command_name} returned ok=false", raw)
    return document, document.get("data")


def list_services(args: argparse.Namespace) -> tuple[list[Service], str]:
    command = cli_prefix(args.context) + [
        "paas",
        "list_module_services",
        "--app_code",
        args.app_code,
        "--module",
        args.module,
        "--stage",
        args.stage,
    ]
    raw = run_command(command)
    _, data = parse_cli_response(raw, "list_module_services")
    if not isinstance(data, dict):
        raise WorkflowError("list_module_services data is not an object", raw)

    services: list[Service] = []
    for group in ("bound", "shared", "unbound"):
        entries = data.get(group)
        if not isinstance(entries, list):
            raise WorkflowError(f"list_module_services data.{group} is not a list", raw)
        for entry in entries:
            if not isinstance(entry, dict):
                raise WorkflowError(f"list_module_services data.{group} contains a non-object", raw)
            service_data = entry.get("service", entry)
            if not isinstance(service_data, dict):
                raise WorkflowError(f"list_module_services data.{group} has invalid service data", raw)
            uuid = service_data.get("uuid")
            name = service_data.get("name")
            if not isinstance(uuid, str) or not uuid or not isinstance(name, str) or not name:
                raise WorkflowError(f"list_module_services data.{group} is missing service uuid/name", raw)
            display_name = service_data.get("display_name")
            services.append(
                Service(
                    group=group,
                    uuid=uuid,
                    name=name,
                    display_name=display_name if isinstance(display_name, str) else "",
                    entry=entry,
                )
            )
    return services, raw


def describe_inventory(services: list[Service]) -> str:
    lines = ["services returned by list_module_services:"]
    for service in services:
        display = f" / {service.display_name}" if service.display_name else ""
        lines.append(f"  {service.group}: {service.name}{display} [{service.uuid}]")
    return "\n".join(lines)


def resolve_service(args: argparse.Namespace, services: list[Service]) -> Service:
    if args.service_id:
        matches = [service for service in services if service.uuid == args.service_id]
    else:
        target = args.service.casefold()
        matches = [
            service
            for service in services
            if service.name.casefold() == target or service.display_name.casefold() == target
        ]

    unique_matches = {(service.group, service.uuid): service for service in matches}
    if not unique_matches:
        target = args.service_id or args.service
        raise WorkflowError(
            f"service did not exactly match list_module_services: {target}\n{describe_inventory(services)}"
        )
    if len(unique_matches) > 1:
        raise WorkflowError(
            f"service target is ambiguous; retry with --service-id\n{describe_inventory(list(unique_matches.values()))}"
        )
    return next(iter(unique_matches.values()))


def shared_from(service: Service) -> str:
    ref_module = service.entry.get("ref_module")
    if isinstance(ref_module, dict):
        name = ref_module.get("name")
        if isinstance(name, str) and name:
            return name
    return "unknown"


def build_body(args: argparse.Namespace, service: Service) -> tuple[dict[str, Any], str]:
    body: dict[str, Any] = {
        "code": args.app_code,
        "service_id": service.uuid,
        "module_name": args.module,
    }
    if args.plan_id:
        body["plan_id"] = args.plan_id
        return body, "explicit plan_id"
    if args.stag_plan_id:
        body["env_plan_id_map"] = {"stag": args.stag_plan_id, "prod": args.prod_plan_id}
        return body, "explicit env_plan_id_map"
    if service.uuid in KNOWN_ENV_PLAN_ID_MAPS:
        body["env_plan_id_map"] = KNOWN_ENV_PLAN_ID_MAPS[service.uuid]
        return body, "built-in GCS-MySQL env_plan_id_map"
    return body, "platform default"


def emit_result(action: str, args: argparse.Namespace, service: Service, **extra: Any) -> None:
    result = {
        "ok": True,
        "action": action,
        "app_code": args.app_code,
        "module": args.module,
        "service": {
            "uuid": service.uuid,
            "name": service.name,
            "display_name": service.display_name,
        },
        **extra,
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


def fail(message: str, raw: str = "") -> NoReturn:
    sys.stderr.write(f"error: {message}\n")
    if raw:
        sys.stderr.write(raw)
        if not raw.endswith("\n"):
            sys.stderr.write("\n")
    raise SystemExit(1)


def main() -> None:
    args = parse_args()
    services, _ = list_services(args)
    service = resolve_service(args, services)

    if service.group == "bound":
        emit_result("already_bound", args, service)
        return
    if service.group == "shared":
        fail(
            f"{service.name} is shared from module {shared_from(service)}; direct binding was not attempted. "
            "Keep the shared service, or remove sharing in the console before binding it independently."
        )
    if service.group != "unbound":
        fail(f"unexpected service group: {service.group}")

    body, plan_source = build_body(args, service)
    if args.dry_run:
        emit_result("would_bind", args, service, plan_source=plan_source, body=body)
        return

    guard = Path(__file__).resolve().with_name("guard.sh")
    guard_command = ["bash", str(guard)]
    if args.context:
        guard_command.extend(["--context", args.context])
    run_command(guard_command)

    bind_command = cli_prefix(args.context) + [
        "paas",
        "bind_service",
        "--stage",
        args.stage,
        "--body",
        json.dumps(body, ensure_ascii=False, separators=(",", ":")),
    ]
    try:
        bind_raw = run_command(bind_command)
        _, binding = parse_cli_response(bind_raw, "bind_service")
    except WorkflowError as error:
        if "CANNOT_BIND_SERVICE" in error.raw or "4313010" in error.raw:
            if plan_source == "platform default":
                hint = (
                    "The platform could not select a unique plan. The current bk-cli has no plan-list command; "
                    "get the plan ID from the console and retry with --plan-id or both environment plan options."
                )
            else:
                hint = f"The selected plan source may be unavailable: {plan_source}."
            raise WorkflowError(f"{error}. {hint}", error.raw) from error
        raise

    current_services, verification_raw = list_services(args)
    verified = next(
        (item for item in current_services if item.uuid == service.uuid and item.group == "bound"),
        None,
    )
    if verified is None:
        current = next((item.group for item in current_services if item.uuid == service.uuid), "missing")
        raise WorkflowError(
            f"bind_service returned success but verification found the service in {current}, not bound",
            verification_raw,
        )
    emit_result("bound", args, verified, plan_source=plan_source, binding=binding)


if __name__ == "__main__":
    try:
        main()
    except WorkflowError as error:
        fail(str(error), error.raw)
