#!/usr/bin/env python3
"""Deploy a BlueKing PaaS module and prove the result is actually serving traffic.

`status=successful` only means the release pipeline finished. This script additionally waits for
the processes to become ready, so a deployment that lands in CrashLoopBackOff is reported as a
failure instead of a success.

Exit codes:
  0  deployed and every process is ready
  1  preflight rejected the repo, or the deployment ended in failed/interrupted, or the workflow broke
  2  bad arguments
  3  deployment succeeded but the processes never became ready
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent

TERMINAL_STATUSES = ("successful", "failed", "interrupted")
UNRECOVERABLE_INSTANCE_STATES = (
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "CreateContainerConfigError",
    "CreateContainerError",
    "InvalidImageName",
)


class WorkflowError(RuntimeError):
    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app-code", required=True)
    parser.add_argument("--module", default="default")
    parser.add_argument("--env", default="stag", choices=("stag", "prod"))
    version = parser.add_mutually_exclusive_group(required=True)
    version.add_argument("--branch")
    version.add_argument("--tag")
    parser.add_argument("--revision", help="Commit SHA; resolved from get_repo_branches when omitted")
    parser.add_argument("--context")
    parser.add_argument("--repo-dir", help="Local checkout to preflight before deploying")
    parser.add_argument("--skip-preflight", action="store_true", help="Deploy even if the repo cannot be checked")
    parser.add_argument(
        "--allow-stale-revision",
        action="store_true",
        help="Deploy even when the platform revision differs from the local HEAD",
    )
    parser.add_argument("--poll-sec", type=int, default=5)
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=1800,
        help="Wait budget for the deployment itself; dependency installs alone can take 15 minutes",
    )
    parser.add_argument("--settle-sec", type=int, default=240, help="Wait budget for processes to become ready")
    return parser.parse_args()


def run(command: list[str], *, echo_stderr: bool = True) -> str:
    try:
        process = subprocess.run(command, check=False, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise WorkflowError(f"command not found: {command[0]}") from error
    if echo_stderr and process.stderr:
        sys.stderr.write(process.stderr)
    if process.returncode != 0 and not process.stdout.strip():
        raise WorkflowError(f"{command[0]} exited with status {process.returncode}", process.stderr)
    return process.stdout


def parse_response(raw: str, command_name: str) -> tuple[dict[str, Any], Any]:
    """Return (document, data). Raises when the CLI reported ok=false."""
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        raise WorkflowError(f"{command_name} returned non-JSON output", raw) from error
    if not isinstance(document, dict):
        raise WorkflowError(f"{command_name} returned an unexpected JSON value", raw)
    if document.get("ok") is not True:
        raise WorkflowError(f"{command_name} returned ok=false", raw)
    return document, document.get("data")


class Cli:
    def __init__(self, context: str | None) -> None:
        self._prefix = ["bk-cli"] + (["--context", context] if context else [])

    def paas(self, *args: str) -> str:
        return run(self._prefix + ["paas", *args])


def log(message: str) -> None:
    sys.stderr.write(message + "\n")


def run_preflight(args: argparse.Namespace) -> dict[str, Any] | None:
    if not args.repo_dir:
        return None
    script = SCRIPT_DIR / "preflight.py"
    command = [
        sys.executable,
        str(script),
        "--repo-dir",
        args.repo_dir,
        "--module",
        args.module,
        "--json",
    ]
    raw = run(command)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as error:
        raise WorkflowError("preflight returned non-JSON output", raw) from error

    for warning in result.get("warnings", []):
        log(f"preflight warn: {warning}")
    for error_message in result.get("errors", []):
        log(f"preflight error: {error_message}")

    if not result.get("ok"):
        if args.skip_preflight:
            log("preflight found blocking problems but --skip-preflight was set; deploying anyway")
        else:
            raise WorkflowError(
                "preflight rejected the repository. Fix the errors above, or pass --skip-preflight "
                "to deploy regardless"
            )
    else:
        log("preflight ok")
    return result


def resolve_revision(cli: Cli, args: argparse.Namespace) -> tuple[str, str, str]:
    """Return (version_type, version_name, revision) taken from a single get_repo_branches entry."""
    version_type = "branch" if args.branch else "tag"
    version_name = args.branch or args.tag
    if args.revision:
        return version_type, version_name, args.revision

    log("resolving revision from repo branches...")
    raw = cli.paas("get_repo_branches", "--app_code", args.app_code, "--module", args.module)
    _, data = parse_response(raw, "get_repo_branches")
    results = data.get("results") if isinstance(data, dict) else data
    if not isinstance(results, list):
        raise WorkflowError("get_repo_branches returned an unexpected payload", raw)

    for item in results:
        if item.get("name") == version_name and item.get("type") == version_type:
            revision = item.get("revision")
            if not revision:
                raise WorkflowError(f"{version_type} {version_name!r} has an empty revision", raw)
            return version_type, version_name, revision

    available = ", ".join(f"{i.get('type')}:{i.get('name')}" for i in results if isinstance(i, dict))
    raise WorkflowError(f"{version_type} {version_name!r} not found. Available: {available or '<none>'}")


def git_output(repo_dir: str, *args: str) -> str | None:
    """Return stripped stdout, or None when git fails (missing binary, not a checkout, unknown revision)."""
    command = ["git", "-C", repo_dir, *args]
    try:
        process = subprocess.run(command, check=False, capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if process.returncode != 0:
        return None
    return process.stdout.strip()


def check_local_matches_remote(args: argparse.Namespace, version: tuple[str, str, str]) -> dict[str, Any] | None:
    """Compare the local checkout with the commit the platform will build.

    The build pulls from the remote, so unpushed work silently deploys older code. That is the usual
    reason a deployment reports success while the user still sees the previous version.
    """
    if not args.repo_dir:
        return None
    head = git_output(args.repo_dir, "rev-parse", "HEAD")
    if head is None:
        log(f"{args.repo_dir} is not a git checkout; skipping the revision comparison")
        return None

    version_type, version_name, revision = version
    branch = git_output(args.repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
    state: dict[str, Any] = {
        "local_head": head,
        "local_branch": branch,
        "platform_revision": revision,
        "matches": head == revision,
        "dirty": bool(git_output(args.repo_dir, "status", "--porcelain")),
    }
    if state["dirty"]:
        log("the working tree has uncommitted changes; they are not part of this deployment")
    if state["matches"]:
        return state

    source = "the revision you passed" if args.revision else f"the platform's {version_type} {version_name!r}"
    reason = ""
    unpushed = git_output(args.repo_dir, "rev-list", "--count", f"{revision}..HEAD")
    if unpushed and unpushed.isdigit() and int(unpushed) > 0:
        state["unpushed_commits"] = int(unpushed)
        reason = f" The local branch is {unpushed} commit(s) ahead, so those changes were never pushed."
    elif version_type == "branch" and branch and branch != version_name:
        reason = f" The checkout is on {branch!r} while the deployment targets {version_name!r}."

    message = (
        f"{source} is {revision}, but the local HEAD is {head}.{reason} The build uses the commit the platform "
        f"resolved, not your working tree, so this deployment would ship different code. Push with "
        f"`git -C {args.repo_dir} push`, or pass --allow-stale-revision to deploy the platform's copy anyway"
    )
    # An explicit --revision is a deliberate choice, a rollback for instance, so it only warns.
    if args.revision or args.allow_stale_revision:
        log(f"warning: {message}")
        return state
    raise WorkflowError(message)


def adopt_ongoing_deployment(cli: Cli, args: argparse.Namespace, conflict_raw: str) -> str:
    """CANNOT_DEPLOY_ONGOING_EXISTS carries no deployment_id; find the pending one and track it."""
    log("another deployment is already running; looking for it in the deployment history...")
    raw = cli.paas(
        "get_deployments_list",
        "--app_code",
        args.app_code,
        "--module",
        args.module,
        "--environment",
        args.env,
        "--limit",
        "12",
        "--offset",
        "0",
    )
    _, data = parse_response(raw, "get_deployments_list")
    results = (data or {}).get("results") if isinstance(data, dict) else data
    if not isinstance(results, list):
        raise WorkflowError("get_deployments_list returned an unexpected payload", raw)

    pending = next((item for item in results if isinstance(item, dict) and item.get("status") == "pending"), None)
    if pending is None or not pending.get("id"):
        raise WorkflowError(
            "the platform refused a new deployment but no pending deployment exists in the history; "
            "this needs manual inspection",
            conflict_raw + "\n" + raw,
        )
    log(f"tracking the in-flight deployment {pending['id']} instead of starting a new one")
    return str(pending["id"])


def start_deployment(cli: Cli, args: argparse.Namespace, version: tuple[str, str, str]) -> str:
    version_type, version_name, revision = version
    body = json.dumps(
        {
            "revision": revision,
            "version_type": version_type,
            "version_name": version_name,
            "advanced_options": {"image_pull_policy": "IfNotPresent"},
        }
    )
    log(f"deploying {args.app_code} module={args.module} env={args.env} {version_type}={version_name} rev={revision}")
    try:
        raw = cli.paas(
            "deploy_with_module",
            "--app_code",
            args.app_code,
            "--module",
            args.module,
            "--env",
            args.env,
            "--body",
            body,
        )
        _, data = parse_response(raw, "deploy_with_module")
    except WorkflowError as error:
        # The conflict may surface on either stream depending on how bk-cli reports it.
        conflict = "CANNOT_DEPLOY_ONGOING_EXISTS"
        if conflict in str(error) or conflict in error.raw:
            return adopt_ongoing_deployment(cli, args, error.raw)
        raise

    if "FILL_EXTRA_INFO" in raw:
        log("platform asked for extra info (FILL_EXTRA_INFO); continuing with the deployment")
    deployment_id = (data or {}).get("deployment_id")
    if not deployment_id:
        raise WorkflowError("deploy_with_module did not return a deployment_id", raw)
    return str(deployment_id)


def wait_for_terminal(cli: Cli, args: argparse.Namespace, deployment_id: str) -> tuple[str, dict[str, Any]]:
    deadline = time.monotonic() + args.timeout_sec
    last: dict[str, Any] = {}
    log_size = 0
    log_grew = False
    while True:
        raw = cli.paas(
            "get_deployment_result",
            "--app_code",
            args.app_code,
            "--module",
            args.module,
            "--deployment_id",
            deployment_id,
        )
        _, data = parse_response(raw, "get_deployment_result")
        last = data if isinstance(data, dict) else {}
        status = str(last.get("status") or "")
        logs = str(last.get("logs") or "")
        log_grew = len(logs) > log_size
        log_size = len(logs)
        log(f"status={status}")
        if status in TERMINAL_STATUSES:
            return status, last
        if time.monotonic() >= deadline:
            # A long `pending` is normal; whether the log is still advancing is what separates a slow
            # dependency install from a genuinely stuck deployment.
            progress = (
                "the build log was still growing, so the platform is working and just needs longer"
                if log_grew
                else "the build log stopped growing, so the deployment may be stuck"
            )
            raise WorkflowError(
                f"still {status or 'pending'} after {args.timeout_sec}s; {progress}. The deployment is not "
                f"cancelled; keep polling get_deployment_result --deployment_id {deployment_id}",
                "\n".join(logs.splitlines()[-15:]),
            )
        time.sleep(args.poll_sec)


def summarize_processes(data: dict[str, Any]) -> dict[str, Any]:
    processes = ((data.get("processes") or {}).get("items")) or []
    instances = ((data.get("instances") or {}).get("items")) or []

    desired = sum(int(p.get("replicas") or 0) for p in processes if isinstance(p, dict))
    ready = sum(1 for i in instances if isinstance(i, dict) and i.get("ready"))
    broken = [
        {
            "process": i.get("process_type"),
            "instance": i.get("display_name") or i.get("name"),
            "state": i.get("state"),
            "state_message": i.get("state_message"),
            "restart_count": i.get("restart_count"),
            "image": i.get("image"),
        }
        for i in instances
        if isinstance(i, dict) and i.get("state") in UNRECOVERABLE_INSTANCE_STATES
    ]
    # Reported so the caller can prove the running pods actually carry the new build; a stale image here
    # is what "I deployed but nothing changed" looks like from the platform side.
    images = sorted({i["image"] for i in instances if isinstance(i, dict) and i.get("image")})
    return {
        "desired_replicas": desired,
        "ready_instances": ready,
        "total_instances": len(instances),
        "images": images,
        "broken": broken,
        "healthy": bool(processes) and desired > 0 and ready >= desired and not broken,
    }


def wait_until_ready(cli: Cli, args: argparse.Namespace) -> dict[str, Any]:
    """Poll list_processes until every replica reports ready, or the budget runs out."""
    deadline = time.monotonic() + args.settle_sec
    summary: dict[str, Any] = {"healthy": False, "ready_instances": 0, "desired_replicas": 0, "broken": []}
    while True:
        raw = cli.paas("list_processes", "--app_code", args.app_code, "--module", args.module, "--env", args.env)
        try:
            _, data = parse_response(raw, "list_processes")
        except WorkflowError as error:
            log(f"list_processes unavailable yet: {error}")
            data = {}
        summary = summarize_processes(data if isinstance(data, dict) else {})
        log(f"processes ready={summary['ready_instances']}/{summary['desired_replicas']}")

        if summary["healthy"]:
            return summary
        if summary["broken"]:
            # CrashLoopBackOff and friends will not fix themselves; stop burning the budget.
            for item in summary["broken"]:
                log(f"process {item['process']} is {item['state']}: {item['state_message']}")
            return summary
        if time.monotonic() >= deadline:
            log(f"processes did not all become ready within {args.settle_sec}s")
            return summary
        time.sleep(args.poll_sec)


def is_first_release(cli: Cli, args: argparse.Namespace, deployment_id: str) -> bool | None:
    """Whether this release is the one that created the environment's ingress.

    Returns None when the history spans more than one page, because an older successful release could
    then sit on a page we did not fetch.
    """
    raw = cli.paas(
        "get_deployments_list",
        "--app_code",
        args.app_code,
        "--module",
        args.module,
        "--environment",
        args.env,
        "--limit",
        "12",
        "--offset",
        "0",
    )
    try:
        _, data = parse_response(raw, "get_deployments_list")
    except WorkflowError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return None

    results = data["results"]
    for item in results:
        if not isinstance(item, dict):
            continue
        if item.get("status") == "successful" and str(item.get("id")) != deployment_id:
            return False

    count = data.get("count")
    if isinstance(count, int) and count > len(results):
        return None
    return True


def read_exposed_url(cli: Cli, args: argparse.Namespace) -> str | None:
    raw = cli.paas(
        "module_env_released_state",
        "--code",
        args.app_code,
        "--module_name",
        args.module,
        "--environment",
        args.env,
    )
    try:
        _, data = parse_response(raw, "module_env_released_state")
    except WorkflowError:
        return None
    link = (data or {}).get("exposed_link") if isinstance(data, dict) else None
    url = (link or {}).get("url") if isinstance(link, dict) else None
    return url or None


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    args = parse_args()
    cli = Cli(args.context)

    preflight = run_preflight(args)

    guard = [
        "bash",
        str(SCRIPT_DIR / "guard.sh"),
        *(["--context", args.context] if args.context else []),
    ]
    run(guard)

    version = resolve_revision(cli, args)
    source_state = check_local_matches_remote(args, version)
    deployment_id = start_deployment(cli, args, version)
    log(f"deployment_id={deployment_id}")

    status, result = wait_for_terminal(cli, args, deployment_id)

    payload: dict[str, Any] = {
        "app_code": args.app_code,
        "module": args.module,
        "env": args.env,
        "version": {"type": version[0], "name": version[1], "revision": version[2]},
        "deployment_id": deployment_id,
        "status": status,
        "preflight": preflight,
        "source": source_state,
    }

    if status != "successful":
        payload["ok"] = False
        payload["deployment_result"] = result
        emit(payload)
        return 1

    # "Rolling upgrade" is reported in error_detail on an otherwise healthy release.
    error_detail = result.get("error_detail")
    if error_detail and error_detail != "Rolling upgrade":
        payload["error_detail"] = error_detail

    processes = wait_until_ready(cli, args)
    payload["processes"] = processes
    payload["url"] = read_exposed_url(cli, args)

    if not processes["healthy"]:
        payload["ok"] = False
        payload["hint"] = (
            "the release pipeline finished but the processes are not serving. Inspect them with "
            f"`bk-cli paas list_processes --app_code {args.app_code} --module {args.module} --env {args.env}` "
            "and the runtime logs via search_standard_log_with_post"
        )
        emit(payload)
        return 3

    if not payload["url"]:
        payload["hint"] = (
            "no exposed_link.url. In specVersion 3 an access URL only exists when a process service declares "
            "exposedType: {name: bk/http}"
        )
    elif is_first_release(cli, args, deployment_id):
        payload["first_release"] = True
        payload["hint"] = (
            "this is the first successful release of this environment, so the ingress was only just created. "
            "The URL may answer 502 for about a minute while the routing rule propagates. Tell the user to "
            "wait and retry rather than reporting the deployment as broken"
        )

    payload["ok"] = True
    emit(payload)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkflowError as error:
        sys.stderr.write(f"error: {error}\n")
        if error.raw:
            sys.stderr.write(error.raw if error.raw.endswith("\n") else error.raw + "\n")
        raise SystemExit(1) from error
    except KeyboardInterrupt:
        sys.stderr.write("interrupted; the platform-side deployment keeps running\n")
        raise SystemExit(1) from None
