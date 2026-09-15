"""Review an allowlisted source snapshot; export only an unchanged reviewed manifest.

No network, Git, dependency installation or publication operations are performed.
Default mode writes hashes and value-free credential findings, never source copies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (
    ".gitignore",
    ".node-version",
    ".python-version",
    ".java-version",
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "uv.lock",
    "tsconfig.contracts.json",
    "README.md",
    "LICENSE",
    "devpost-submission.md",
)
EXACT_FILES = (
    ROOT_FILES
    + (
        "apps/mobile/app.json",
        "apps/mobile/eas.json",
        "apps/mobile/package.json",
        "apps/mobile/tsconfig.json",
        "apps/mobile/eslint.config.js",
        "apps/mobile/jest.setup.ts",
        "apps/mobile/.gitignore",
        "apps/mobile/.prettierignore",
        "apps/mobile/LICENSE",
        "apps/mobile/assets/fonts/SpaceMono-Regular.ttf",
        "apps/mobile/assets/fonts/LICENSE-SpaceMono.txt",
        "apps/mobile/assets/images/splash-icon.png",
        "apps/mobile/assets/images/android-icon-monochrome.png",
        "apps/mobile/assets/images/android-icon-foreground.png",
        "apps/mobile/assets/images/android-icon-background.png",
        "apps/mobile/assets/images/favicon.png",
        "apps/mobile/assets/images/icon.png",
        "infra/package.json",
        "infra/tsconfig.json",
        "infra/cdk.json",
        "infra/.gitignore",
        "infra/README.md",
        "services/agent/main.py",
        "services/agent/agentcore/README.md",
        "services/agent/agentcore/agentcore.json",
        "services/agent/agentcore/cdk/package.json",
        "services/agent/agentcore/cdk/package-lock.json",
        "services/agent/agentcore/cdk/tsconfig.json",
        "services/agent/agentcore/cdk/cdk.json",
        "services/agent/agentcore/cdk/.gitignore",
        "services/agent/agentcore/cdk/.prettierrc",
        "scripts/project-env.sh",
        "scripts/validate-workspace.mjs",
        "scripts/generate-contracts.mjs",
        "scripts/build_preview.py",
        "scripts/prepare_source_export.py",
        "docs/runtime-verification.md",
        "docs/hackathon-build/scope.md",
        "docs/hackathon-build/prd.md",
        "docs/hackathon-build/spec.md",
        "docs/hackathon-build/checklist.md",
        "docs/hackathon-build/stack-audit-2026-08-23.md",
        "docs/submission/README.md",
        "docs/submission/judge-testing.md",
        "docs/submission/architecture.md",
        "docs/submission/architecture.svg",
        "docs/submission/architecture.png",
        "docs/submission/video-script.md",
        "docs/submission/video-subtitles.srt",
        "docs/submission/preparation-status.md",
        "docs/submission/source-release-review.md",
    )
    + tuple(
        f"services/{name}/pyproject.toml"
        for name in ("agent", "control-api", "worker", "ingress")
    )
)

# Each recursive root has an explicit permitted source-file extension set.
TREES: dict[str, set[str]] = {
    "apps/mobile/app": {".ts", ".tsx", ".js", ".json"},
    "apps/mobile/src": {".ts", ".tsx", ".js", ".json"},
    "apps/mobile/components": {".ts", ".tsx", ".js"},
    "infra/bin": {".ts"},
    "infra/lib": {".ts"},
    "infra/test": {".ts"},
    "infra/policies": {".json"},
    "contracts": {".json", ".yaml", ".ts", ".md"},
    "tests": {".py", ".json", ".md"},
    "services/agent/scripts": {".py"},
    "services/agent/agentcore/cdk/bin": {".ts"},
    "services/agent/agentcore/cdk/lib": {".ts"},
}
for _package in ("agent", "control-api", "worker", "ingress"):
    TREES[f"services/{_package}/src"] = {".py"}
    TREES[f"services/{_package}/tests"] = {".py", ".json"}

GENERATED_FILES = {
    "tests/fixtures/.gitkeep": b"",
    "tests/integration/.gitkeep": b"",
}
DENIED_PARTS = {
    ".git",
    ".secrets",
    "tasks",
    "logs",
    "captures",
    "screenshots",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".expo",
    ".gradle",
    ".cache",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "coverage",
    "htmlcov",
    "Pods",
    "build",
    "dist",
    "cdk.out",
    "deployment_package",
    "QuietPilotAgent",
    ".cli",
    "artifacts",
    "tmp",
    "temp",
}
DENIED_NAMES = {
    "google-services.json",
    "local.properties",
    "aws-targets.json",
    ".devpost-hackathon-state.json",
    "AGENTS.md",
    ".DS_Store",
}
DENIED_SUFFIXES = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
    ".cer",
    ".crt",
    ".apk",
    ".aab",
    ".ipa",
    ".zip",
    ".tar",
    ".gz",
    ".log",
    ".mp4",
    ".mov",
    ".so",
    ".dylib",
    ".dll",
    ".class",
    ".jar",
    ".pyc",
    ".pyo",
}
BINARY_ASSETS = {name for name in EXACT_FILES if Path(name).suffix in {".png", ".ttf"}}
MAX_FILE_BYTES = 8 * 1024 * 1024
EXCLUSIONS = (
    "Git history, agent state/instructions and editor state",
    "Secrets, local env/Firebase/SDK configuration, private keys and signing stores",
    "Private tasks, logs, diagnostics and mail/device captures",
    "Dependencies, caches, coverage and generated deployment packages",
    "Native Android/iOS trees, built applications, archives and build binaries",
    "Live AWS targets, dated deployment manifests, learner notes and build history",
    "Everything outside the explicit allowlist; symlinks and unrecognized binaries",
)

PATTERNS = (
    ("private_key_header", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    ),
    ("google_oauth_client_secret", re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{15,}\b")),
    ("google_access_token", re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}\b")),
    ("google_refresh_token", re.compile(r"(?<!\w)1//[A-Za-z0-9_-]{30,}")),
    ("google_api_key", re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    (
        "jwt_literal",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    (
        "expo_push_token",
        re.compile(r"(?:Exponent|Expo)PushToken\[[A-Za-z0-9_-]{12,}\]"),
    ),
    ("credential_url", re.compile(r"https?://[^\s/:@]+:[^\s/@]+@")),
    (
        "long_credential_literal",
        re.compile(
            r"(?i)(?:aws_secret_access_key|secret_access_key|secretAccessKey|client_secret|"
            r"access_token|refresh_token|api_key|password)[\"']?\s*[:=]\s*[\"'][A-Za-z0-9_+/=-]{24,}[\"']"
        ),
    ),
)


class ExportError(ValueError):
    """A safe, value-free message suitable for a release log."""


def denied(relative: Path) -> bool:
    return (
        any(part in DENIED_PARTS for part in relative.parts)
        or relative.name in DENIED_NAMES
        or relative.name.startswith(".env")
        or relative.suffix.lower() in DENIED_SUFFIXES
    )


def safe_file(root: Path, relative: str) -> Path:
    target = root / relative
    # Reject links at every component; never follow a selected link into private data.
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ExportError(f"Symlink is not exportable: {relative}")
    if not target.is_file():
        raise ExportError(f"Required source file is missing: {relative}")
    return target


def selected_files(root: Path) -> list[str]:
    selected = set(EXACT_FILES)
    for name, extensions in TREES.items():
        directory = root / name
        if directory.is_symlink() or not directory.is_dir():
            raise ExportError(f"Required source tree is missing or linked: {name}")
        for base, dirs, files in os.walk(directory, followlinks=False):
            relative_base = Path(base).relative_to(root)
            for child in dirs:
                relative = relative_base / child
                if (root / relative).is_symlink():
                    raise ExportError(
                        f"Symlink is not exportable: {relative.as_posix()}"
                    )
            dirs[:] = sorted(
                child for child in dirs if not denied(relative_base / child)
            )
            for child in sorted(files):
                relative = relative_base / child
                if denied(relative) or relative.suffix not in extensions:
                    continue
                selected.add(relative.as_posix())
    return sorted(selected)


def snapshot(root: Path) -> tuple[dict, dict[str, bytes]]:
    entries, findings, payloads = [], [], {}
    for relative in selected_files(root):
        if denied(Path(relative)):
            raise ExportError(f"Allowlist conflicts with exclusion: {relative}")
        source = safe_file(root, relative)
        if source.stat().st_size > MAX_FILE_BYTES:
            raise ExportError(f"Selected file exceeds size cap: {relative}")
        payload = source.read_bytes()
        if len(payload) > MAX_FILE_BYTES:
            raise ExportError(f"Selected file exceeds size cap: {relative}")
        digest = hashlib.sha256(payload).hexdigest()
        if relative not in BINARY_ASSETS:
            try:
                content = payload.decode("utf-8")
            except UnicodeDecodeError:
                raise ExportError(
                    f"Unexpected binary in selected text: {relative}"
                ) from None
            if "\x00" in content:
                raise ExportError(f"Unexpected NUL in selected text: {relative}")
            for kind, pattern in PATTERNS:
                for match in pattern.finditer(content):
                    findings.append(
                        {
                            "path": relative,
                            "line": content.count("\n", 0, match.start()) + 1,
                            "type": kind,
                            "file_sha256": digest,
                        }
                    )
        payloads[relative] = payload
        entries.append(
            {
                "path": relative,
                "bytes": len(payload),
                "sha256": digest,
                "executable": bool(source.stat().st_mode & stat.S_IXUSR),
                "origin": "source",
            }
        )
    for relative, payload in GENERATED_FILES.items():
        if relative not in payloads:
            payloads[relative] = payload
            entries.append(
                {
                    "path": relative,
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "executable": False,
                    "origin": "generated_empty_directory_marker",
                }
            )
    entries.sort(key=lambda item: item["path"])
    # Report each kind only once per line, and never include matched values/snippets.
    findings = sorted({json.dumps(finding, sort_keys=True) for finding in findings})
    findings = [json.loads(finding) for finding in findings]
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "snapshot_sha256": hashlib.sha256(canonical).hexdigest(),
        "file_count": len(entries),
        "total_bytes": sum(item["bytes"] for item in entries),
        "files": entries,
        "findings": findings,
        "excluded_categories": EXCLUSIONS,
        "history_included": False,
        "source_copied": False,
    }, payloads


def report_paths(output: Path) -> tuple[Path, Path]:
    return output.with_name(output.name + ".manifest.json"), output.with_name(
        output.name + ".sha256"
    )


def write_reports(output: Path, manifest: dict) -> None:
    manifest_path, sums_path = report_paths(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    sums_path.write_text(
        "".join(f"{item['sha256']}  {item['path']}\n" for item in manifest["files"]),
        encoding="utf-8",
    )


def unreviewed_findings(manifest: dict, review: list[dict]) -> list[dict]:
    required = {"path", "line", "type", "file_sha256"}
    if not isinstance(review, list) or any(
        not isinstance(item, dict) or set(item) != required for item in review
    ):
        raise ExportError(
            "Reviewed findings must contain only path, line, type and file_sha256."
        )
    approved = {json.dumps(item, sort_keys=True) for item in review}
    current = {json.dumps(item, sort_keys=True) for item in manifest["findings"]}
    if approved - current:
        raise ExportError("A reviewed finding is stale or absent from this snapshot.")
    return [
        item
        for item in manifest["findings"]
        if json.dumps(item, sort_keys=True) not in approved
    ]


def materialize(root: Path, output: Path, expected: dict, review: list[dict]) -> dict:
    manifest, payloads = snapshot(root)
    if manifest["snapshot_sha256"] != expected.get("snapshot_sha256"):
        raise ExportError(
            "Source changed since the reviewed manifest; refresh and review it first."
        )
    if unreviewed_findings(manifest, review):
        raise ExportError(
            "Unreviewed credential-pattern findings prevent copying source."
        )
    if output.exists() or output.is_symlink():
        raise ExportError(
            "Export destination already exists; choose a fresh output name."
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".source-export-", dir=output.parent
    ) as temporary:
        staging = Path(temporary) / "source"
        staging.mkdir()
        for entry in manifest["files"]:
            target = staging / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payloads[entry["path"]])
            target.chmod(0o755 if entry["executable"] else 0o644)
            if hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
                raise ExportError(f"Copied file hash mismatch: {entry['path']}")
        current, _ = snapshot(root)
        if current["snapshot_sha256"] != manifest["snapshot_sha256"]:
            raise ExportError(
                "Source changed during export; no snapshot was finalized."
            )
        staging.rename(output)
    manifest["source_copied"] = True
    manifest["reviewed_finding_count"] = len(review)
    return manifest


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="quietpilot-export-test-") as temporary:
        root = Path(temporary)
        for name in EXACT_FILES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic source\n")
        for name in TREES:
            (root / name).mkdir(parents=True, exist_ok=True)
        private = root / ".secrets/private.txt"
        private.parent.mkdir()
        private.write_text("private fixture must not be read or exported")
        nested = root / "apps/mobile/src/.env.local"
        nested.write_text("excluded fixture")
        candidate = root / "apps/mobile/src/example.ts"
        candidate.write_text("export const answer = 42;\n")
        before, _ = snapshot(root)
        assert all(not denied(Path(item["path"])) for item in before["files"])
        output = root / "artifacts/submission/source-preview"
        result = materialize(root, output, before, [])
        assert result["source_copied"] and not (output / ".secrets").exists()
        assert not (output / "apps/mobile/src/.env.local").exists()
        assert (output / "tests/fixtures/.gitkeep").is_file()
        candidate.write_text("changed\n")
        try:
            materialize(root, output.with_name("stale"), before, [])
        except ExportError:
            pass
        else:
            raise AssertionError("A changed source was accepted")
        candidate.unlink()
        candidate.symlink_to(private)
        try:
            snapshot(root)
        except ExportError:
            pass
        else:
            raise AssertionError("A symlink was followed")
        candidate.unlink()
        candidate.write_text("credential = '" + "AKIA" + "Z" * 16 + "'\n")
        flagged, _ = snapshot(root)
        assert flagged["findings"] and all(
            "value" not in item for item in flagged["findings"]
        )
        try:
            materialize(root, output.with_name("flagged"), flagged, [])
        except ExportError:
            pass
        else:
            raise AssertionError("An unreviewed credential pattern was copied")
        assert unreviewed_findings(flagged, flagged["findings"]) == []
        reviewed_output = output.with_name("reviewed-fixture")
        reviewed_result = materialize(
            root, reviewed_output, flagged, flagged["findings"]
        )
        assert reviewed_result["source_copied"]
        candidate.write_text("credential = '" + "AKIA" + "Y" * 16 + "'\n")
        changed, _ = snapshot(root)
        try:
            unreviewed_findings(changed, flagged["findings"])
        except ExportError:
            pass
        else:
            raise AssertionError("A stale finding review authorized changed content")
    print(
        json.dumps(
            {
                "self_test": "passed",
                "checks": [
                    "allowlist and exclusions",
                    "hash-verified copy",
                    "empty directory preservation",
                    "stale manifest rejection",
                    "symlink rejection",
                    "value-free credential blocker",
                    "hash-bound explicit finding review",
                ],
            }
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/submission/source-preview")
    )
    parser.add_argument(
        "--export", action="store_true", help="Copy the exact reviewed frozen snapshot."
    )
    parser.add_argument("--expected-manifest", type=Path)
    parser.add_argument(
        "--reviewed-findings",
        type=Path,
        help="Value-free JSON list of reviewed synthetic findings, bound to file hashes.",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    output = (ROOT / args.output).absolute()
    allowed_parent = ROOT / "artifacts/submission"
    if (
        output == allowed_parent
        or not output.is_relative_to(allowed_parent)
        or ".." in args.output.parts
    ):
        raise ExportError(
            "Output must be a named directory under artifacts/submission."
        )
    current = ROOT
    for part in output.relative_to(ROOT).parts:
        current = current / part
        if current.is_symlink():
            raise ExportError("Output must not traverse symlinks.")
    if args.export and args.expected_manifest is None:
        raise ExportError(
            "--export requires --expected-manifest from the frozen source review."
        )
    review = (
        json.loads(args.reviewed_findings.read_text()) if args.reviewed_findings else []
    )
    if args.export:
        expected = json.loads(args.expected_manifest.read_text())
        manifest = materialize(ROOT, output, expected, review)
    else:
        manifest, _ = snapshot(ROOT)
    write_reports(output, manifest)
    pending = unreviewed_findings(manifest, review)
    print(
        json.dumps(
            {
                "mode": "export" if args.export else "manifest_only",
                "manifest": report_paths(output)[0].relative_to(ROOT).as_posix(),
                "sha256_manifest": report_paths(output)[1].relative_to(ROOT).as_posix(),
                "snapshot_sha256": manifest["snapshot_sha256"],
                "file_count": manifest["file_count"],
                "total_bytes": manifest["total_bytes"],
                "unreviewed_findings": [
                    {key: item[key] for key in ("path", "line", "type")}
                    for item in pending
                ],
                "source_copied": manifest["source_copied"],
                "history_included": False,
            },
            indent=2,
        )
    )
    return 2 if pending else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ExportError, json.JSONDecodeError, OSError) as error:
        # Never print file contents, tokens or an arbitrary exception representation.
        message = str(error) if isinstance(error, ExportError) else type(error).__name__
        print(json.dumps({"error": message}), file=sys.stderr)
        raise SystemExit(1) from None
