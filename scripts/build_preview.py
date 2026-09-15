"""Build an embedded-JS Android preview and sign it with a private local key."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECURE = ROOT / ".secrets"
KEY = SECURE / "android-preview.p12"
PASSWORD = SECURE / "android-preview-password"


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, text=True, check=True, **kwargs)
    except subprocess.CalledProcessError as error:
        details = error.stderr or error.stdout or ""
        if PASSWORD.exists():
            details = details.replace(PASSWORD.read_text().strip(), "[REDACTED]")
        raise RuntimeError(
            f"{Path(command[0]).name} failed ({error.returncode}): {details[:2000]}"
        ) from None


def prepare_key(create: bool) -> None:
    if KEY.exists() and PASSWORD.exists():
        return
    if KEY.exists() or PASSWORD.exists():
        raise RuntimeError(
            "Incomplete signing files; restore them without rotating the key."
        )
    if not create:
        raise RuntimeError(
            "Run once with --create-key to create private local signing files."
        )
    SECURE.mkdir(mode=0o700, exist_ok=True)
    descriptor = os.open(PASSWORD, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(secrets.token_urlsafe(36))
    run(
        [
            "keytool",
            "-genkeypair",
            "-alias",
            "quietpilot",
            "-keyalg",
            "RSA",
            "-keysize",
            "3072",
            "-validity",
            "3650",
            "-dname",
            "CN=QuietPilot Preview",
            "-storetype",
            "PKCS12",
            "-keystore",
            str(KEY),
            "-storepass:file",
            str(PASSWORD),
            "-keypass:file",
            str(PASSWORD),
            "-noprompt",
        ],
        capture_output=True,
    )
    KEY.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create-key", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--architectures", default="arm64-v8a")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/submission/quietpilot-preview.apk",
    )
    args = parser.parse_args()
    if not re.fullmatch(
        r"(?:arm64-v8a|armeabi-v7a|x86|x86_64)(?:,(?:arm64-v8a|armeabi-v7a|x86|x86_64))*",
        args.architectures,
    ):
        parser.error("Use supported Android ABI names separated by commas.")
    sdk = Path(os.environ.get("ANDROID_HOME", str(Path.home() / "Library/Android/sdk")))
    build_tools = (
        sdk / "build-tools" / os.environ.get("QUIETPILOT_ANDROID_BUILD_TOOLS", "36.0.0")
    )
    signer, aapt = build_tools / "apksigner", build_tools / "aapt"
    if not signer.is_file() or not aapt.is_file():
        raise RuntimeError(
            "Android build tools are missing; set ANDROID_HOME and build-tools version."
        )
    prepare_key(args.create_key)
    android = ROOT / "apps/mobile/android"
    if not (android / "gradlew").is_file():
        run(
            [
                "npm",
                "exec",
                "-w",
                "mobile",
                "--",
                "expo",
                "prebuild",
                "--platform",
                "android",
                "--no-install",
            ],
            cwd=ROOT,
        )
    if not args.skip_build:
        run(
            [
                "./gradlew",
                ":app:assembleRelease",
                "--no-daemon",
                "-Dorg.gradle.workers.max=2",
                f"-PreactNativeArchitectures={args.architectures}",
            ],
            cwd=android,
        )
    original = android / "app/build/outputs/apk/release/app-release.apk"
    if not original.is_file():
        raise RuntimeError("The release APK was not produced.")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".preview-sign-", dir=output.parent
    ) as temp:
        signed = Path(temp) / "signed.apk"
        run(
            [
                str(signer),
                "sign",
                "--ks",
                str(KEY),
                "--ks-key-alias",
                "quietpilot",
                "--ks-pass",
                f"file:{PASSWORD}",
                "--out",
                str(signed),
                str(original),
            ],
            capture_output=True,
        )
        certificate = run(
            [str(signer), "verify", "--print-certs", str(signed)], capture_output=True
        ).stdout
        fingerprint = re.findall(
            r"Signer #\d+ certificate SHA-256 digest: ([0-9a-f]+)", certificate
        )
        if len(fingerprint) != 1:
            raise RuntimeError("Expected one verified APK signer.")
        badging = run(
            [str(aapt), "dump", "badging", str(signed)], capture_output=True
        ).stdout
        if "application-debuggable" in badging:
            raise RuntimeError("The preview must not be debuggable.")
        package = json.loads((ROOT / "apps/mobile/app.json").read_text())["expo"][
            "android"
        ]["package"]
        if f"package: name='{package}'" not in badging:
            raise RuntimeError("The APK package does not match the app configuration.")
        with zipfile.ZipFile(signed) as archive:
            bundle = archive.getinfo("assets/index.android.bundle")
            if bundle.file_size == 0:
                raise RuntimeError("The embedded app bundle is empty.")
            abis = sorted(
                {
                    name.split("/")[1]
                    for name in archive.namelist()
                    if name.startswith("lib/") and name.endswith(".so")
                }
            )
        os.replace(signed, output)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "apk": output.name,
        "bytes": output.stat().st_size,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "package": package,
        "abis": abis,
        "embedded_js_bytes": bundle.file_size,
        "debuggable": False,
        "signer_sha256": fingerprint[0],
        "device_runtime_verified": False,
    }
    output.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
