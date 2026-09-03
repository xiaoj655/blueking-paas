#!/usr/bin/env python3
"""Validate a repository against the BlueKing PaaS deploy contract before spending a deploy cycle.

Errors block deployment. Warnings describe deployments that will report `successful`
while the application stays unreachable or degraded.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROC_NAME_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9])*$")
PROC_NAME_MAX_LENGTH = 12
DNS_NAME_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")

CONTAINER_PORT = 5000
PORT_PLACEHOLDER = "${PORT}"
DEFAULT_PROCESS_NAME = "web"
EXPOSED_TYPES = ("bk/http", "bk/grpc")
BUILTIN_RES_QUOTA_PLANS = ("default", "1C1G", "2C1G", "2C2G", "4C1G", "4C2G", "4C4G")

# DeploymentDescSLZ accepts exactly these per-module keys; `language` and `spec` are required.
KNOWN_MODULE_FIELDS = ("name", "language", "sourceDir", "isDefault", "spec")
# AppLanguage members. The platform matches them case-insensitively.
KNOWN_LANGUAGES = ("Python", "Go", "NodeJS")

# Fields accepted by BkAppSpecInputSLZ. Anything else is silently dropped by the platform.
KNOWN_SPEC_FIELDS = (
    "processes",
    "hooks",
    "configuration",
    "envOverlay",
    "svcDiscovery",
    "observability",
    "build",
    "addons",
    "mounts",
    "domainResolution",
)
KNOWN_BUILD_FIELDS = ("image", "imagePullPolicy", "imageCredentialsName")

# Binding forms that reliably carry a listen port. Bare numbers are never matched,
# so `gunicorn -w 4` cannot be mistaken for a port.
PORT_PATTERNS = (
    re.compile(r"(?:-b|--bind)[=\s]+(?:\[[^\]]*\]|[\w.*]*):(\d{2,5}|\$\{PORT\})"),
    re.compile(r"(?:--port|-p)[=\s]+(\d{2,5}|\$\{PORT\})"),
    re.compile(r"(?:--?addr(?:ess)?|--listen)[=\s]+(?:\[[^\]]*\]|[\w.*]*):(\d{2,5}|\$\{PORT\})"),
    re.compile(r"runserver\s+[\w.:*\[\]]*?:(\d{2,5}|\$\{PORT\})"),
)

# A process bound to loopback is unreachable from kubelet probes and from the service,
# so the release lands as `successful` while the entrance keeps returning 502.
LOOPBACK_BIND_PATTERNS = (
    re.compile(r"(?:-b|--bind|--host|--addr(?:ess)?|--listen)[=\s]+(?:tcp://)?\[?(127\.0\.0\.1|localhost|::1)\]?\b"),
    re.compile(r"runserver\s+\[?(127\.0\.0\.1|localhost|::1)\]?:"),
)

LANGUAGE_BUILDPACK_FILES = {
    "python": ("requirements.txt",),
    "nodejs": ("package.json",),
    "go": ("go.mod",),
}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def note(self, message: str) -> None:
        self.notes.append(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check a repo against the PaaS deploy contract before deploying.")
    parser.add_argument("--repo-dir", default=".", help="Repository root that will be built (default: .)")
    parser.add_argument(
        "--module",
        default="default",
        help="Module whose process definitions are checked when app_desc.yaml declares several (default: default)",
    )
    parser.add_argument(
        "--build-method",
        choices=("dockerfile", "buildpack"),
        help="Module build method; inferred from the presence of a Dockerfile when omitted",
    )
    parser.add_argument(
        "--dockerfile-path",
        help="Dockerfile path relative to --repo-dir, matching the module build config (default: Dockerfile)",
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON instead of text")
    return parser.parse_args()


def load_yaml(path: Path, report: Report) -> Any:
    try:
        import yaml
    except ImportError:
        report.error("PyYAML is not installed; run `pip install pyyaml` so app_desc.yaml can be parsed")
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        report.error(f"{path.name} is not valid YAML: {error}")
    except OSError as error:
        report.error(f"cannot read {path.name}: {error}")
    return None


def find_desc_file(repo: Path) -> Path | None:
    for name in ("app_desc.yaml", "app_desc.yml"):
        candidate = repo / name
        if candidate.is_file():
            return candidate
    return None


def check_spec_version(doc: dict[str, Any], report: Report) -> None:
    spec_version = doc.get("specVersion") or doc.get("spec_version")
    if spec_version is None:
        report.error("app_desc.yaml has no specVersion; add `specVersion: 3`")
    elif spec_version != 3:
        report.warn(
            f"specVersion is {spec_version!r}, not 3. This check only covers 3; "
            "older versions take a different code path where the platform auto-creates process services"
        )


def resolve_from_modules_list(doc: dict[str, Any], modules: Any, module: str, report: Report) -> dict[str, Any] | None:
    if not isinstance(modules, list):
        report.error("specVersion 3 requires `modules` to be a list; a mapping is rejected as 模块格式不正确")
        return None

    named = [m for m in modules if isinstance(m, dict)]
    defaults = [m for m in named if m.get("isDefault")]
    if len(defaults) > 1:
        report.error("more than one module sets isDefault: true; exactly one main module is allowed")
    elif not defaults and len(named) > 1:
        report.error("no module sets isDefault: true; a multi-module app must declare one main module")

    found = next((m for m in named if m.get("name") == module), None)
    if found is not None:
        return found
    if isinstance(doc.get("module"), dict):
        return doc["module"]

    available = ", ".join(str(m.get("name")) for m in named) or "<none>"
    report.error(f"module {module!r} is not defined in app_desc.yaml; declared modules: {available}")
    return None


def resolve_module_spec(doc: Any, module: str, report: Report) -> dict[str, Any] | None:
    """Return the module body for `module`, mirroring the platform's lookup order."""
    if not isinstance(doc, dict):
        report.error("app_desc.yaml must contain a mapping at the top level")
        return None

    check_spec_version(doc, report)

    if doc.get("modules") is not None:
        return resolve_from_modules_list(doc, doc["modules"], module, report)
    if isinstance(doc.get("module"), dict):
        return doc["module"]

    report.error("app_desc.yaml declares neither `module` nor `modules`")
    return None


def extract_ports(command: str) -> set[int]:
    ports: set[int] = set()
    for pattern in PORT_PATTERNS:
        for raw in pattern.findall(command):
            value = raw if isinstance(raw, str) else raw[0]
            ports.add(CONTAINER_PORT if value == PORT_PLACEHOLDER else int(value))
    return ports


def normalize_port(value: Any) -> int | None:
    if value == PORT_PLACEHOLDER:
        return CONTAINER_PORT
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def check_service_target_port(where: str, svc: dict[str, Any], report: Report) -> int | None:
    if "targetPort" not in svc:
        report.error(f"{where} is missing `targetPort`")
        return None
    port = normalize_port(svc["targetPort"])
    if port is None:
        report.error(f"{where} targetPort {svc['targetPort']!r} is neither an integer nor '${{PORT}}'")
        return None
    if port != CONTAINER_PORT:
        report.warn(
            f"{where} targetPort is {port}, but the platform injects PORT={CONTAINER_PORT}. "
            f"Confirm the process really listens on {port}"
        )
    return port


def check_service_exposed_type(where: str, svc: dict[str, Any], report: Report) -> str | None:
    exposed_type = svc.get("exposedType")
    if exposed_type is None:
        return None
    if not isinstance(exposed_type, dict):
        report.error(f"{where} exposedType must be a mapping like {{name: bk/http}}")
        return None
    type_name = exposed_type.get("name")
    if type_name not in EXPOSED_TYPES:
        report.error(f"{where} exposedType.name must be one of {EXPOSED_TYPES}, got {type_name!r}")
        return None
    return type_name


def check_services(proc_name: str, services: Any, report: Report) -> tuple[list[int], list[str]]:
    """Validate one process's service block; return its target ports and exposed type names."""
    target_ports: list[int] = []
    exposed: list[str] = []
    if services is None:
        return target_ports, exposed
    if not isinstance(services, list):
        report.error(f"process {proc_name!r}: `services` must be a list")
        return target_ports, exposed

    for index, svc in enumerate(services):
        where = f"process {proc_name!r} services[{index}]"
        if not isinstance(svc, dict):
            report.error(f"{where} must be a mapping")
            continue

        name = svc.get("name")
        if not isinstance(name, str) or not name:
            report.error(f"{where} is missing `name`")
        elif not DNS_NAME_PATTERN.match(name) or len(name) > 63:
            report.error(f"{where} name {name!r} must be a DNS label (^[a-z0-9]([-a-z0-9]*[a-z0-9])?$, <=63)")

        port = check_service_target_port(where, svc, report)
        if port is not None:
            target_ports.append(port)

        protocol = svc.get("protocol")
        if protocol is not None and protocol not in ("TCP", "UDP"):
            report.error(f"{where} protocol must be TCP or UDP, got {protocol!r}")

        type_name = check_service_exposed_type(where, svc, report)
        if type_name is not None:
            exposed.append(type_name)

    return target_ports, exposed


def check_probes(proc_name: str, probes: Any, target_ports: list[int], report: Report) -> None:
    if probes is None:
        return
    if not isinstance(probes, dict):
        report.error(f"process {proc_name!r}: `probes` must be a mapping")
        return

    for kind in ("liveness", "readiness", "startup"):
        probe = probes.get(kind)
        if not isinstance(probe, dict):
            continue
        actions = [key for key in ("exec", "httpGet", "tcpSocket") if key in probe]
        if len(actions) != 1:
            report.error(f"process {proc_name!r} {kind} probe must declare exactly one of exec/httpGet/tcpSocket")
            continue
        action = actions[0]
        if action == "exec":
            continue
        port = normalize_port((probe[action] or {}).get("port"))
        if port is None:
            report.error(f"process {proc_name!r} {kind} probe is missing a numeric `port`")
        elif target_ports and port not in target_ports:
            report.error(
                f"process {proc_name!r} {kind} probe targets port {port}, "
                f"but its services expose {target_ports}. The probe will never pass"
            )


def check_processes(spec: dict[str, Any], report: Report) -> dict[str, Any]:
    processes = spec.get("processes")
    summary: dict[str, Any] = {"names": [], "exposed_types": [], "web_reachable": False}

    if not processes:
        report.error("spec.processes is empty; the module has no process to run")
        return summary
    if not isinstance(processes, list):
        report.error("spec.processes must be a list")
        return summary

    all_exposed: list[str] = []
    for index, proc in enumerate(processes):
        if not isinstance(proc, dict):
            report.error(f"spec.processes[{index}] must be a mapping")
            continue

        name = proc.get("name")
        if not isinstance(name, str) or not name:
            report.error(f"spec.processes[{index}] is missing `name`")
            continue
        summary["names"].append(name)

        check_process_name(name, report)
        check_process_command(name, proc, report)
        check_process_scale(name, proc, report)

        target_ports, exposed = check_services(name, proc.get("services"), report)
        all_exposed.extend(exposed)
        check_probes(name, proc.get("probes"), target_ports, report)
        check_listen_port(name, proc.get("procCommand"), target_ports, report)
        check_bind_address(name, proc.get("procCommand"), report)

    summary["exposed_types"] = all_exposed
    check_exposed_types(all_exposed, summary["names"], report)
    summary["web_reachable"] = bool(all_exposed)
    return summary


def check_process_name(name: str, report: Report) -> None:
    if len(name) > PROC_NAME_MAX_LENGTH:
        report.error(
            f"process name {name!r} is {len(name)} characters; the platform rejects anything over "
            f"{PROC_NAME_MAX_LENGTH}"
        )
    if not PROC_NAME_PATTERN.match(name):
        report.error(f"process name {name!r} must match ^[a-z0-9]([-a-z0-9])*$")


def check_process_command(name: str, proc: dict[str, Any], report: Report) -> None:
    proc_command = proc.get("procCommand")
    command = proc.get("command")
    if not proc_command and not command:
        report.error(f"process {name!r} declares neither `procCommand` nor `command`")
    elif proc_command and command:
        report.warn(f"process {name!r} sets both procCommand and command; the platform ignores command")


def check_process_scale(name: str, proc: dict[str, Any], report: Report) -> None:
    plan = proc.get("resQuotaPlan")
    if plan is not None and plan not in BUILTIN_RES_QUOTA_PLANS:
        report.warn(
            f"process {name!r} resQuotaPlan {plan!r} is not a built-in plan "
            f"({', '.join(BUILTIN_RES_QUOTA_PLANS)}); deployment fails unless the platform defines it"
        )

    replicas = proc.get("replicas")
    if isinstance(replicas, int) and replicas > 1 and name != DEFAULT_PROCESS_NAME:
        report.warn(
            f"process {name!r} runs {replicas} replicas; scheduler-style processes usually need exactly 1 "
            "to avoid duplicated periodic tasks"
        )


def check_listen_port(name: str, proc_command: Any, target_ports: list[int], report: Report) -> None:
    if not isinstance(proc_command, str) or not proc_command:
        return
    listen_ports = extract_ports(proc_command)
    if not listen_ports:
        return
    if not target_ports:
        report.warn(
            f"process {name!r} binds a port but declares no `services`; it is only reachable inside the cluster"
        )
    elif listen_ports - set(target_ports):
        report.error(
            f"process {name!r} listens on {sorted(listen_ports)} but its services expose "
            f"{sorted(set(target_ports))}. Deployment will report successful while the entrance returns 502"
        )


def check_bind_address(name: str, proc_command: Any, report: Report) -> None:
    if not isinstance(proc_command, str) or not proc_command:
        return
    for pattern in LOOPBACK_BIND_PATTERNS:
        match = pattern.search(proc_command)
        if match:
            report.error(
                f"process {name!r} binds {match.group(1)!r}. Only the container itself can reach loopback, so "
                "readiness probes fail and the entrance returns 502. Bind 0.0.0.0 or [::] instead"
            )
            return


def check_exposed_types(all_exposed: list[str], proc_names: list[str], report: Report) -> None:
    if len(all_exposed) > 1:
        report.error(
            f"{len(all_exposed)} services declare exposedType; a module may expose at most one "
            "(the platform rejects duplicates)"
        )
        if len(set(all_exposed)) > 1:
            report.error("bk/http and bk/grpc cannot be exposed by the same module")
    elif not all_exposed:
        message = (
            "no service declares exposedType. specVersion 3 does not create a default one, so the deploy reports "
            "successful without ever producing an access URL. Add `exposedType: {name: bk/http}` to the web service"
        )
        # A module carrying the platform's default web process is meant to be reachable from outside.
        if DEFAULT_PROCESS_NAME in proc_names:
            report.error(message)
        else:
            report.warn(message)


def check_env_declarations(spec: dict[str, Any], report: Report) -> None:
    configuration = spec.get("configuration")
    if isinstance(configuration, dict):
        for index, item in enumerate(configuration.get("env") or []):
            if not isinstance(item, dict):
                report.error(f"spec.configuration.env[{index}] must be a mapping")
                continue
            name = item.get("name")
            if not isinstance(name, str) or not ENV_NAME_PATTERN.match(name):
                report.error(f"spec.configuration.env[{index}] name {name!r} must match ^[A-Z][A-Z0-9_]*$")

    overlay = spec.get("envOverlay")
    if isinstance(overlay, dict):
        for section, items in overlay.items():
            for index, item in enumerate(items or []):
                if isinstance(item, dict) and item.get("envName") not in ("stag", "prod"):
                    report.error(f"spec.envOverlay.{section}[{index}] envName must be 'stag' or 'prod'")


def check_module_fields(module_spec: dict[str, Any], report: Report) -> None:
    """Validate the module body against DeploymentDescSLZ, which rejects a missing `language`."""
    language = module_spec.get("language")
    if not language:
        report.error(
            "the module declares no `language`; the deploy-time validator requires it and rejects the "
            f"description file without it. Use one of {', '.join(KNOWN_LANGUAGES)}"
        )
    elif str(language).lower() not in [known.lower() for known in KNOWN_LANGUAGES]:
        report.error(f"language {language!r} is not supported; use one of {', '.join(KNOWN_LANGUAGES)}")

    for key in module_spec:
        if key not in KNOWN_MODULE_FIELDS:
            report.warn(
                f"module field {key!r} is not part of the module schema and is silently dropped. "
                f"Only {', '.join(KNOWN_MODULE_FIELDS)} are read; process definitions belong under `spec`"
            )


def check_spec_fields(spec: dict[str, Any], report: Report) -> None:
    for key in spec:
        if key not in KNOWN_SPEC_FIELDS:
            report.warn(f"spec.{key} is not a field the platform validates; it will be silently ignored")

    build = spec.get("build")
    if isinstance(build, dict):
        for key in build:
            if key not in KNOWN_BUILD_FIELDS:
                report.warn(
                    f"spec.build.{key} is dropped by the platform. Dockerfile path and build args come from the "
                    "module build config set at creation time, not from app_desc.yaml"
                )

    if spec.get("addons"):
        report.warn(
            "spec.addons is a no-op on the repository deploy path (sync_addons only logs a warning). "
            "Bind enhanced services with scripts/bind_service.py before deploying"
        )

    check_env_declarations(spec, report)


def check_hooks(spec: dict[str, Any], report: Report) -> bool:
    hooks = spec.get("hooks")
    if not isinstance(hooks, dict):
        return False
    pre_release = hooks.get("preRelease")
    if not isinstance(pre_release, dict):
        return False
    if not pre_release.get("procCommand") and not pre_release.get("command"):
        report.error("hooks.preRelease declares neither `procCommand` nor `command`")
        return False
    report.note(
        "hooks.preRelease runs before every release and fails the deployment when it exits non-zero. "
        "If it touches a database, the enhanced service must already be bound to this module"
    )
    return True


def check_observability(spec: dict[str, Any], proc_names: list[str], report: Report) -> None:
    observability = spec.get("observability")
    if not isinstance(observability, dict):
        return
    monitoring = observability.get("monitoring")
    if not isinstance(monitoring, dict):
        return
    processes = {p.get("name"): p for p in (spec.get("processes") or []) if isinstance(p, dict)}
    for index, metric in enumerate(monitoring.get("metrics") or []):
        if not isinstance(metric, dict):
            continue
        process = metric.get("process")
        if process not in proc_names:
            report.error(f"observability.monitoring.metrics[{index}] targets unknown process {process!r}")
            continue
        service_names = {
            svc.get("name") for svc in (processes[process].get("services") or []) if isinstance(svc, dict)
        }
        if metric.get("serviceName") not in service_names:
            report.error(
                f"observability.monitoring.metrics[{index}] serviceName {metric.get('serviceName')!r} does not "
                f"match any service of process {process!r} ({sorted(n for n in service_names if n)})"
            )


def check_procfile(repo: Path, spec: dict[str, Any], report: Report) -> None:
    procfile = repo / "Procfile"
    if not procfile.is_file():
        return
    declared = {p.get("name") for p in (spec.get("processes") or []) if isinstance(p, dict)}
    if not declared:
        return
    try:
        lines = procfile.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        report.warn(f"cannot read Procfile: {error}")
        return
    in_procfile = {line.split(":", 1)[0].strip() for line in lines if ":" in line and not line.strip().startswith("#")}
    in_procfile.discard("")
    if in_procfile and in_procfile != declared:
        report.error(
            f"Procfile defines {sorted(in_procfile)} while app_desc.yaml defines {sorted(declared)}. "
            "The platform aborts with 'Process definitions conflict'; delete the Procfile"
        )
    elif in_procfile:
        report.warn("Procfile duplicates app_desc.yaml process definitions; delete it to avoid future drift")


def check_build_inputs(repo: Path, args: argparse.Namespace, module_spec: dict[str, Any], report: Report) -> str:
    dockerfile_rel = args.dockerfile_path or "Dockerfile"
    dockerfile = repo / dockerfile_rel
    method = args.build_method or ("dockerfile" if dockerfile.is_file() else "buildpack")

    if method == "dockerfile":
        if not dockerfile.is_file():
            report.error(
                f"build method is dockerfile but {dockerfile_rel} does not exist in the repository root. "
                "Add it, or create the module with buildpack"
            )
        else:
            content = dockerfile.read_text(encoding="utf-8", errors="replace")
            has_entry = re.search(r"^\s*(CMD|ENTRYPOINT)\b", content, re.MULTILINE)
            processes = module_spec.get("spec", {}).get("processes") or []
            has_proc_command = any(isinstance(p, dict) and p.get("procCommand") for p in processes)
            if has_entry and has_proc_command:
                report.note(
                    "the Dockerfile sets CMD/ENTRYPOINT while app_desc.yaml sets procCommand; procCommand wins. "
                    "Keep them consistent so local `docker run` matches the platform"
                )
            elif not has_entry and not has_proc_command:
                report.error(
                    "neither the Dockerfile nor app_desc.yaml defines a start command; the container has nothing to run"
                )
    else:
        language = str(module_spec.get("language") or "").lower()
        for required in LANGUAGE_BUILDPACK_FILES.get(language, ()):
            if not (repo / required).is_file():
                report.error(f"buildpack build of a {language} module needs {required} in the repository root")
        if language == "python" and not (repo / "runtime.txt").is_file():
            report.warn("no runtime.txt; the Python buildpack picks a default interpreter that may break dependencies")

    return method


def render_text(report: Report, context: dict[str, Any]) -> str:
    lines = [f"preflight {context['repo_dir']} module={context['module']} build={context['build_method']}"]
    for label, items in (("error", report.errors), ("warn", report.warnings), ("note", report.notes)):
        for item in items:
            lines.append(f"  {label}: {item}")
    if report.errors:
        lines.append(f"preflight failed: {len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    else:
        lines.append(f"preflight ok: 0 errors, {len(report.warnings)} warning(s)")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    repo = Path(args.repo_dir).resolve()
    report = Report()

    if not repo.is_dir():
        sys.stderr.write(f"error: --repo-dir is not a directory: {repo}\n")
        return 2

    context: dict[str, Any] = {
        "repo_dir": str(repo),
        "module": args.module,
        "build_method": args.build_method or "auto",
        "processes": [],
    }

    desc_file = find_desc_file(repo)
    if desc_file is None:
        if (repo / "Procfile").is_file():
            report.warn(
                "only a Procfile is present. It deploys, but specVersion 3 features (process services, probes, "
                "envOverlay) are unavailable. Prefer app_desc.yaml"
            )
            context["build_method"] = check_build_inputs(repo, args, {}, report)
        else:
            report.error(
                "the repository root has neither app_desc.yaml nor Procfile; the platform cannot resolve any process"
            )
    else:
        doc = load_yaml(desc_file, report)
        module_spec = resolve_module_spec(doc, args.module, report) if doc is not None else None
        if module_spec is not None:
            check_module_fields(module_spec, report)
            spec = module_spec.get("spec")
            if not isinstance(spec, dict):
                report.error(
                    f"module {args.module!r} has no `spec` mapping. `spec` is required; processes, hooks and "
                    "envOverlay all live under it, not directly under the module"
                )
                spec = {}
            summary = check_processes(spec, report)
            context["processes"] = summary["names"]
            context["exposed_types"] = summary["exposed_types"]
            check_spec_fields(spec, report)
            context["has_pre_release_hook"] = check_hooks(spec, report)
            check_observability(spec, summary["names"], report)
            check_procfile(repo, spec, report)
            context["build_method"] = check_build_inputs(repo, args, module_spec, report)

    if args.json:
        payload = {
            "ok": not report.errors,
            **context,
            "errors": report.errors,
            "warnings": report.warnings,
            "notes": report.notes,
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(render_text(report, context) + "\n")

    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
