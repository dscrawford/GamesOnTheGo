"""The controller suite on the cluster, start to finish: `nix run .#controllers-cluster`.

It was five hand-typed steps -- build, push, sed the tag in, apply, and a
loop polling the pod every twenty seconds -- and each run paid for all of
them: a push of an image the registry already had, a poll that noticed the
end up to twenty seconds late, a new GC root in /tmp for every build (five
of them held 5.9 GB), and one pod doing twelve and a half minutes of tests
one after another. Now: one build behind one out-link, a push only when the
registry lacks the tag, a pod per node each running a share of the suite
(tests/e2e/shards.py), their logs followed as they run, and one summary.

    nix run .#controllers-cluster                      # the whole suite
    nix run .#controllers-cluster -- -k top_bar        # pytest's own arguments
    nix run .#controllers-cluster -- --shards 1 -k x   # one pod
    nix run .#controllers-cluster -- --durations       # and rewrite durations.json

Logs land in ~/.cache/gotg/controllers-runs/<tag>-<time>/, one per share.
Stdlib only; kubectl, skopeo and nix come from PATH (the flake app puts the
first two there).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time

JOB = "gotg-controllers"
NAMESPACE = "default"
REPOSITORY = "gotg-controllers"
# Pushed to a node's address; pulled as localhost, the name the nodes'
# containerd trusts without a certificate (see README.md).
PUSH_REGISTRY = os.environ.get("GOTG_REGISTRY", "192.168.0.2:30500")
SHARDS = 3
TIMINGS_PREFIX = "gotg-e2e-durations "
CACHE = pathlib.Path(os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache")) / "gotg"
# How long a share may sit unscheduled before the run says why it is waiting:
# the anti-affinity keeps it off a node where k8s/qa is making its own pad.
PENDING_NOTICE = 30.0
# The suite took 12.5 minutes in one pod; anything past this is stuck.
DEADLINE = 40 * 60.0


def say(message: str) -> None:
    print(f"controllers-cluster: {message}", file=sys.stderr, flush=True)


def run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=False, text=True, **kwargs)


def kubectl(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return run(["kubectl", "-n", NAMESPACE, *args], capture_output=True, **kwargs)


def build(root: pathlib.Path) -> pathlib.Path:
    """One out-link, replaced every build, so the old image is garbage the
    next `nix-collect-garbage` takes rather than a root nobody remembers."""
    link = CACHE / "controllers-image"
    link.parent.mkdir(parents=True, exist_ok=True)
    done = run(
        [
            "nix",
            "build",
            f"{root}#controllers-image",
            "--max-jobs",
            "2",
            "--cores",
            "4",
            "-o",
            str(link),
            "--print-out-paths",
        ],
        stdout=subprocess.PIPE,
    )
    if done.returncode != 0:
        sys.exit("controllers-cluster: the image did not build")
    return pathlib.Path(done.stdout.strip().splitlines()[-1])


def cert_dir() -> str:
    # Empty, and ours: the default /etc/docker/certs.d holds a root-only key.
    path = CACHE / "regcerts"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def push(image: pathlib.Path, tag: str) -> None:
    ref = f"docker://{PUSH_REGISTRY}/{REPOSITORY}:{tag}"
    have = run(
        ["skopeo", "inspect", "--raw", "--tls-verify=false", f"--cert-dir={cert_dir()}", ref],
        capture_output=True,
    )
    if have.returncode == 0:
        say(f"{tag} is already in the registry")
        return
    started = time.monotonic()
    copied = run(
        ["skopeo", "copy", f"--dest-cert-dir={cert_dir()}", "--dest-tls-verify=false", f"docker-archive:{image}", ref],
        capture_output=True,
    )
    if copied.returncode != 0:
        sys.exit(f"controllers-cluster: push failed: {copied.stderr.strip()[-400:]}")
    say(f"pushed {tag} in {time.monotonic() - started:.0f} s")


def render(template: str, tag: str, shards: int, pytest_args: list[str]) -> str:
    text = template.replace("@TAG@", tag).replace("@SHARDS@", str(shards))
    replaced, count = re.subn(r'args: \["-q"\]', "args: " + json.dumps(pytest_args), text)
    if count != 1:
        sys.exit("controllers-cluster: job.yaml no longer has the args line this fills in")
    return replaced


def start(manifest: str) -> None:
    kubectl("delete", "job", JOB, "--ignore-not-found", "--wait=true", "--timeout=120s")
    applied = kubectl("apply", "-f", "-", input=manifest)
    if applied.returncode != 0:
        sys.exit(f"controllers-cluster: {applied.stderr.strip()}")


def pods() -> list[dict]:
    got = kubectl("get", "pods", "-l", f"job-name={JOB}", "-o", "json")
    if got.returncode != 0:
        return []
    return json.loads(got.stdout).get("items", [])


def index_of(pod: dict) -> int:
    labels = pod["metadata"].get("labels", {})
    return int(labels.get("batch.kubernetes.io/job-completion-index", 0))


def follow(shards: int, out: pathlib.Path) -> dict[int, dict]:
    """Stream every share's log as it runs, and return the pods once all have
    finished -- with their logs fetched whole, since a `logs -f` that lost its
    connection halfway would otherwise be the record."""
    streams: dict[int, subprocess.Popen] = {}
    began = time.monotonic()
    noticed = False
    last: dict[int, dict] = {}
    while True:
        last = {index_of(pod): pod for pod in pods()}
        for index, pod in sorted(last.items()):
            if index not in streams and _phase(pod) in ("Running", "Succeeded", "Failed"):
                streams[index] = subprocess.Popen(
                    ["kubectl", "-n", NAMESPACE, "logs", "-f", pod["metadata"]["name"]],
                    stdout=open(out / f"share-{index}.log", "w"),  # the child owns it from here
                    stderr=subprocess.DEVNULL,
                )
                node = pod["spec"].get("nodeName")
                say(f"share {index} {_phase(pod).lower()} on {node}: tail -f {out}/share-{index}.log")
        waiting = [i for i in range(shards) if i not in streams]
        if waiting and not noticed and time.monotonic() - began > PENDING_NOTICE:
            why = sorted({_waiting_reason(last[i]) for i in waiting if i in last}) or ["no pod yet"]
            say(f"share(s) {waiting} not running yet: {'; '.join(why)}")
            noticed = True
        if len(last) >= shards and all(_phase(pod) in ("Succeeded", "Failed") for pod in last.values()):
            break
        if time.monotonic() - began > DEADLINE:
            say(f"gave up after {DEADLINE / 60:.0f} minutes; the job is left for kubectl to inspect")
            break
        time.sleep(2.0)
    for stream in streams.values():
        if stream.poll() is None:
            stream.terminate()
    for index, pod in last.items():
        logs = kubectl("logs", pod["metadata"]["name"])
        (out / f"share-{index}.log").write_text(logs.stdout)
    return last


def _phase(pod: dict) -> str:
    return pod.get("status", {}).get("phase", "Unknown")


def _waiting_reason(pod: dict) -> str:
    for status in pod.get("status", {}).get("containerStatuses", []) or []:
        waiting = status.get("state", {}).get("waiting")
        if waiting:
            return waiting.get("reason", "waiting")
    for condition in pod.get("status", {}).get("conditions", []) or []:
        if condition.get("reason") == "Unschedulable":
            return "unschedulable -- another uinput job holds a node?"
    return pod.get("status", {}).get("phase", "unknown")


FAILURE = re.compile(r"^_{3,} (.+?) _{3,}$|^E {3,}\S.*|^\[XPASS\(strict\)\].*")
VERDICT = re.compile(r"^\d+ (passed|failed)|^=+ .*(passed|failed|error).* =+$|^no tests ran")


def summarise(
    out: pathlib.Path, final: dict[int, dict], shards: int, write_durations: bool, root: pathlib.Path
) -> bool:
    ok = True
    timings: dict[str, float] = {}
    for index in range(shards):
        path = out / f"share-{index}.log"
        lines = path.read_text(errors="replace").splitlines() if path.exists() else []
        pod = final.get(index, {})
        phase = pod.get("status", {}).get("phase", "missing")
        node = pod.get("spec", {}).get("nodeName", "?")
        verdict = next((line for line in reversed(lines) if VERDICT.search(line)), "no verdict")
        print(f"\nshare {index} on {node}: {phase} -- {verdict.strip('= ')}")
        for line in lines:
            if FAILURE.search(line):
                print("  " + line[:200])
            if line.startswith(TIMINGS_PREFIX):
                timings.update(json.loads(line[len(TIMINGS_PREFIX) :]))
        ok = ok and phase == "Succeeded"
    if timings:
        slowest = sorted(timings.items(), key=lambda kv: -kv[1])[:8]
        print(f"\n{sum(timings.values()):.0f} s of tests; slowest:")
        for name, seconds in slowest:
            print(f"  {seconds:6.1f} s  {name}")
    if write_durations and timings:
        target = root / "tests" / "e2e" / "durations.json"
        target.write_text(json.dumps(dict(sorted(timings.items())), indent=1) + "\n")
        say(f"wrote {len(timings)} timings to {target}")
    print(f"\nlogs: {out}")
    return ok


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Run tests/e2e on the cluster.",
        epilog="Anything else is handed to pytest in every share.",
    )
    parser.add_argument("--shards", type=int, default=SHARDS, help="pods, one per node (default 3)")
    parser.add_argument("--durations", action="store_true", help="rewrite tests/e2e/durations.json")
    ours, pytest_args = parser.parse_known_args(argv)
    if ours.shards < 1:
        parser.error("--shards needs at least one")
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]
    pytest_args = ["-q", *pytest_args] if "-q" not in pytest_args else pytest_args

    root = pathlib.Path(run(["git", "rev-parse", "--show-toplevel"], capture_output=True).stdout.strip() or os.getcwd())
    template = (root / "k8s" / "controllers" / "job.yaml").read_text()
    started = time.monotonic()
    image = build(root)
    tag = image.name[:12]
    push(image, tag)
    start(render(template, tag, ours.shards, pytest_args))
    out = CACHE / "controllers-runs" / f"{tag}-{time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    say(f"{JOB} {tag}, {ours.shards} share(s), pytest {' '.join(pytest_args)}")
    final = follow(ours.shards, out)
    ok = summarise(out, final, ours.shards, ours.durations, root)
    print(f"{'passed' if ok else 'FAILED'} in {time.monotonic() - started:.0f} s, build to verdict")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
