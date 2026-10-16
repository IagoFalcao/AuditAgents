#!/usr/bin/env python3
"""Compile every Solidity file in contracts/3 and save per-file results/artifacts.

Run from any directory:
    python src/compiler.py
Optional:
    python src/compiler.py --contracts contracts/3 --output compilation_results
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def run_command(command: list[str], cwd: Path, timeout: int = 300) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            command, cwd=cwd, text=True, capture_output=True, timeout=timeout,
            check=False, env={**os.environ, "FORCE_COLOR": "0"},
        )
        return {
            "command": command, "returncode": proc.returncode,
            "stdout": proc.stdout, "stderr": proc.stderr,
            "duration_seconds": round(time.time() - started, 3),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command, "returncode": None,
            "stdout": exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            "stderr": exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
            "duration_seconds": round(time.time() - started, 3),
            "timed_out": True,
        }
    except OSError as exc:
        return {
            "command": command, "returncode": None, "stdout": "",
            "stderr": str(exc), "duration_seconds": round(time.time() - started, 3),
            "timed_out": False,
        }


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def extract_diagnostics(*texts: str) -> list[dict[str, Any]]:
    """Extract Solidity/Hardhat JSON diagnostics when present, preserving raw logs too."""
    diagnostics: list[dict[str, Any]] = []
    for text in texts:
        # solc standard-json output can have non-JSON text before its JSON payload.
        for match in re.finditer(r'\{\s*"errors"\s*:', text):
            try:
                obj, _ = json.JSONDecoder().raw_decode(text[match.start():])
            except json.JSONDecodeError:
                continue
            for item in obj.get("errors", []):
                diagnostics.append({
                    "severity": item.get("severity", "error"),
                    "error_code": item.get("errorCode"),
                    "type": item.get("type"),
                    "message": item.get("message", ""),
                    "formatted_message": item.get("formattedMessage", ""),
                    "source_location": item.get("sourceLocation"),
                })
            if diagnostics:
                break
    return diagnostics


def contract_name_from_source(path: Path) -> str:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return path.stem
    # Used only as a fallback when an artifact cannot be located.
    match = re.search(r'\b(?:contract|interface|library)\s+(\w+)', source)
    return match.group(1) if match else path.stem


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts", default=str(ROOT / "contracts" / "3"),
                        help="Directory containing Solidity sources (default: contracts/3)")
    parser.add_argument("--output", default=str(ROOT / "compilation_results"),
                        help="Output directory (default: compilation_results)")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout per compiler command in seconds")
    parser.add_argument("--no-hardhat", action="store_true", help="Skip the initial Hardhat compilation")
    args = parser.parse_args()

    contracts_dir = Path(args.contracts).expanduser()
    if not contracts_dir.is_absolute():
        contracts_dir = (Path.cwd() / contracts_dir).resolve()
    output_dir = Path(args.output).expanduser()
    if not output_dir.is_absolute():
        output_dir = (Path.cwd() / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = output_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    if not contracts_dir.is_dir():
        print(f"ERROR: contracts directory does not exist: {contracts_dir}", file=sys.stderr)
        return 2

    sources = sorted(p for p in contracts_dir.rglob("*.sol") if p.is_file())
    if not sources:
        print(f"ERROR: no .sol files found in {contracts_dir}", file=sys.stderr)
        return 2

    hardhat_dir = ROOT / "hhat"
    hardhat_artifacts = hardhat_dir / "artifacts"
    hardhat_run: dict[str, Any] | None = None
    if not args.no_hardhat and (hardhat_dir / "package.json").exists():
        if (hardhat_dir / "node_modules" / ".bin" / "hardhat").exists():
            hardhat_bin = str(hardhat_dir / "node_modules" / ".bin" / "hardhat")
            hardhat_run = run_command([hardhat_bin, "compile", "--force"], hardhat_dir, args.timeout)
        elif shutil.which("npx"):
            hardhat_run = run_command(["npx", "--no-install", "hardhat", "compile", "--force"],
                                      hardhat_dir, args.timeout)
        else:
            hardhat_run = {"command": ["hardhat", "compile", "--force"], "returncode": None,
                           "stdout": "", "stderr": "Hardhat/npm not found", "duration_seconds": 0,
                           "timed_out": False}
    else:
        hardhat_run = {"command": [], "returncode": None, "stdout": "",
                       "stderr": "Hardhat skipped or hhat/package.json not found",
                       "duration_seconds": 0, "timed_out": False}

    # Index Hardhat artifacts by their compiler sourceName, preserving the complete artifact JSON.
    hardhat_artifact_index: dict[str, list[Path]] = {}
    if hardhat_artifacts.is_dir():
        for artifact_path in hardhat_artifacts.rglob("*.json"):
            if artifact_path.name.endswith(".dbg.json"):
                continue
            try:
                artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            source_name = artifact.get("sourceName")
            if source_name:
                hardhat_artifact_index.setdefault(source_name.replace("\\", "/"), []).append(artifact_path)

    results: list[dict[str, Any]] = []
    fallback_bin = hardhat_dir / "node_modules" / ".bin" / "solcjs"
    solc_command = str(fallback_bin) if fallback_bin.exists() else (shutil.which("solc") or shutil.which("solcjs"))

    for index, source in enumerate(sources, start=1):
        relative = source.relative_to(contracts_dir).as_posix()
        repo_relative = source.relative_to(ROOT).as_posix() if source.is_relative_to(ROOT) else relative
        record: dict[str, Any] = {
            "source_path": str(source), "relative_path": repo_relative,
            "file_name": source.name, "contract_name": contract_name_from_source(source),
            "status": "failed", "compiler": None, "errors": [], "warnings": [],
            "artifacts": [], "duration_seconds": 0.0,
        }
        started = time.time()

        # Hardhat compiles the dependency graph together. Use its exact artifact when available.
        possible_keys = {repo_relative, relative, f"contracts/{source.name}"}
        matching: list[Path] = []
        for key in possible_keys:
            matching.extend(hardhat_artifact_index.get(key, []))
        # Also match source names by basename only when unambiguous.
        if not matching:
            basename_matches = [
                p for key, paths in hardhat_artifact_index.items()
                if Path(key).name == source.name for p in paths
            ]
            if len(basename_matches) == 1:
                matching = basename_matches

        if matching and hardhat_run and hardhat_run.get("returncode") == 0:
            for artifact_path in sorted(set(matching)):
                try:
                    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
                    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", artifact.get("contractName", artifact_path.stem))
                    artifact_out = artifacts_dir / f"{index:03d}_{source.stem}_{safe_name}.json"
                    artifact["auditagents"] = {
                        "source_path": repo_relative, "compiled_by": "hardhat",
                        "original_artifact_path": str(artifact_path.relative_to(hardhat_dir)),
                    }
                    write_json(artifact_out, artifact)
                    record["artifacts"].append(str(artifact_out.relative_to(output_dir)))
                    record["contract_name"] = artifact.get("contractName", record["contract_name"])
                except (OSError, json.JSONDecodeError) as exc:
                    record["errors"].append({"message": f"Could not copy Hardhat artifact: {exc}"})
            if record["artifacts"]:
                record["status"] = "success"
                record["compiler"] = "hardhat"
        else:
            # Fallback: compile this file directly with solc/solcjs. --base-path resolves relative
            # imports and --include-path enables npm dependencies; compiler errors are retained.
            if solc_command:
                command = [solc_command, "--base-path", str(ROOT), "--include-path",
                           str(hardhat_dir / "node_modules"), "--abi", "--bin",
                           "--ast-compact-json", "-o", str(artifacts_dir / f"solc_{index:03d}"),
                           str(source)]
                fallback = run_command(command, ROOT, args.timeout)
                record["compiler"] = "solc" if Path(solc_command).name == "solc" else "solcjs"
                record["compiler_command"] = fallback["command"]
                record["compiler_stdout"] = fallback["stdout"]
                record["compiler_stderr"] = fallback["stderr"]
                record["errors"] = extract_diagnostics(fallback["stdout"], fallback["stderr"])
                if fallback["timed_out"]:
                    record["errors"].append({"message": f"Compiler timed out after {args.timeout} seconds"})
                elif fallback["returncode"] != 0 and not record["errors"]:
                    record["errors"].append({"message": fallback["stderr"].strip() or
                                              f"Compiler exited with code {fallback['returncode']}"})
                generated_dir = artifacts_dir / f"solc_{index:03d}"
                if generated_dir.exists():
                    record["artifacts"] = [
                        str(p.relative_to(output_dir)) for p in sorted(generated_dir.rglob("*")) if p.is_file()
                    ]
                if fallback["returncode"] == 0 and record["artifacts"]:
                    record["status"] = "success"
                elif not record["errors"]:
                    record["errors"].append({"message": "Compiler did not produce artifacts"})
            else:
                record["compiler"] = None
                record["errors"].append({
                    "message": "Hardhat compilation failed and neither solc nor solcjs is installed/resolvable.",
                    "hardhat_stderr": (hardhat_run or {}).get("stderr", ""),
                })

        record["duration_seconds"] = round(time.time() - started, 3)
        results.append(record)
        print(f"[{index}/{len(sources)}] {record['status'].upper():6} {repo_relative}"
              + (f" ({record['compiler']})" if record["compiler"] else ""))

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "repository_root": str(ROOT), "contracts_directory": str(contracts_dir),
        "total_contract_files": len(sources),
        "success_count": sum(r["status"] == "success" for r in results),
        "failed_count": sum(r["status"] != "success" for r in results),
        "hardhat": hardhat_run,
        "results": results,
    }
    write_json(output_dir / "compilation_report.json", summary)
    (output_dir / "compilation_report.txt").write_text(
        "\n".join([
            "Solidity compilation report",
            f"Contracts found: {summary['total_contract_files']}",
            f"Successful: {summary['success_count']}",
            f"Failed: {summary['failed_count']}",
            "",
            *[f"[{r['status'].upper()}] {r['relative_path']} | compiler={r['compiler']} | "
              f"errors={len(r['errors'])} | artifacts={len(r['artifacts'])}"
              + ("\n  " + "\n  ".join(
                  str(e.get("formatted_message") or e.get("message") or e) for e in r["errors"]
              ) if r["errors"] else "")
              for r in results],
        ]) + "\n", encoding="utf-8"
    )
    print(f"\nReport: {output_dir / 'compilation_report.json'}")
    print(f"Text report: {output_dir / 'compilation_report.txt'}")
    print(f"Artifacts: {artifacts_dir}")
    return 0 if summary["failed_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
