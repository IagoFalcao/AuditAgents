#!/usr/bin/env python3
"""Compile Solidity sources from contracts/3 and optional imported project trees.

Supported layouts:
  contracts/3/**/*.sol
  contracts/13/contracts/**/*.sol
  contracts/22/contracts/**/*.sol

The script tries each project's own Hardhat config first, then compiles each source
with the project's local solc/solcjs using standard JSON. Raw compiler diagnostics
and successful compiler outputs (ABI, bytecode, AST, metadata, storage layout where
supported) are written under compilation_results/.
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
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = ("contracts/3", "contracts/13/contracts", "contracts/22/contracts")


def run_command(command: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
    start = time.time()
    try:
        p = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout,
                           check=False, env={**os.environ, "FORCE_COLOR": "0", "NO_COLOR": "1"})
        return {"command": command, "returncode": p.returncode, "stdout": p.stdout,
                "stderr": p.stderr, "duration_seconds": round(time.time()-start, 3),
                "timed_out": False}
    except subprocess.TimeoutExpired as e:
        return {"command": command, "returncode": None,
                "stdout": e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
                "stderr": e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or ""),
                "duration_seconds": round(time.time()-start, 3), "timed_out": True}
    except OSError as e:
        return {"command": command, "returncode": None, "stdout": "", "stderr": str(e),
                "duration_seconds": round(time.time()-start, 3), "timed_out": False}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def read_json_output(text: str) -> dict[str, Any] | None:
    # solcjs may print a non-JSON SMT warning before standard-json output.
    for m in re.finditer(r"\{\s*\"(?:errors|sources|contracts)\"\s*:", text):
        try:
            value, _ = json.JSONDecoder().raw_decode(text[m.start():])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    return None


def diagnostics_from(obj: dict[str, Any] | None, fallback: str = "") -> list[dict[str, Any]]:
    if obj and isinstance(obj.get("errors"), list):
        return [{"severity": e.get("severity", "error"), "error_code": e.get("errorCode"),
                 "type": e.get("type"), "message": e.get("message", ""),
                 "formatted_message": e.get("formattedMessage", ""),
                 "source_location": e.get("sourceLocation")} for e in obj["errors"]]
    return ([{"severity": "error", "message": fallback}] if fallback else [])


def source_name(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


def find_project_root(source: Path, repo_root: Path) -> Path:
    # Project roots with their own package/config files take precedence.
    for parent in source.parents:
        if parent == repo_root.parent:
            break
        if parent in (repo_root / "contracts" / "13", repo_root / "contracts" / "22"):
            return parent
        if (parent / "package.json").exists() and (
            (parent / "hardhat.config.js").exists() or (parent / "hardhat.config.ts").exists()
            or (parent / "hardhat.config.cjs").exists()
        ):
            return parent
    return repo_root


def compiler_for(project_root: Path) -> str | None:
    candidates = [
        project_root / "node_modules" / ".bin" / "solcjs",
        project_root / "node_modules" / ".bin" / "solc",
        ROOT / "hhat" / "node_modules" / ".bin" / "solcjs",
        ROOT / "hhat" / "node_modules" / ".bin" / "solc",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return shutil.which("solc") or shutil.which("solcjs")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--contracts", nargs="*", default=list(DEFAULT_TARGETS),
                    help="One or more source directories, relative to repo root or absolute")
    ap.add_argument("--output", default=str(ROOT / "compilation_results"))
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--no-hardhat", action="store_true")
    ap.add_argument("--hardhat-first", action="store_true",
                    help="Run each discovered project's Hardhat compile before per-file fallback")
    args = ap.parse_args()

    output = Path(args.output).expanduser()
    if not output.is_absolute():
        output = (Path.cwd() / output).resolve()
    artifact_root = output / "artifacts"
    output.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)

    target_dirs: list[Path] = []
    for item in args.contracts:
        p = Path(item).expanduser()
        if not p.is_absolute():
            p = (ROOT / p).resolve()
        if p.is_dir():
            target_dirs.append(p)
        else:
            print(f"[WARN] Source directory not found; skipping: {p}", file=sys.stderr)

    sources = sorted({p.resolve() for d in target_dirs for p in d.rglob("*.sol") if p.is_file()})
    if not sources:
        print("ERROR: no Solidity files found. Add the source trees or pass --contracts.", file=sys.stderr)
        return 2

    projects: dict[Path, list[Path]] = {}
    for source in sources:
        root = find_project_root(source, ROOT)
        projects.setdefault(root, []).append(source)

    hardhat_runs: dict[str, Any] = {}
    if not args.no_hardhat and args.hardhat_first:
        for project_root in projects:
            config = next((n for n in ("hardhat.config.ts", "hardhat.config.js", "hardhat.config.cjs")
                           if (project_root / n).exists()), None)
            if not config:
                continue
            hardhat_bin = project_root / "node_modules" / ".bin" / "hardhat"
            if hardhat_bin.exists():
                cmd = [str(hardhat_bin), "compile", "--force"]
            elif shutil.which("npx"):
                cmd = ["npx", "--no-install", "hardhat", "compile", "--force"]
            else:
                hardhat_runs[str(project_root)] = {"returncode": None, "stderr": "Hardhat/npm not available"}
                continue
            run = run_command(cmd, project_root, args.timeout)
            hardhat_runs[str(project_root)] = run

    results: list[dict[str, Any]] = []
    for idx, source in enumerate(sources, 1):
        project_root = find_project_root(source, ROOT)
        relative_repo = source.relative_to(ROOT).as_posix() if source.is_relative_to(ROOT) else source.as_posix()
        name = re.search(r"\b(?:contract|interface|library)\s+(\w+)", source.read_text(
            encoding="utf-8", errors="replace"))
        contract_name = name.group(1) if name else source.stem
        out_dir = artifact_root / f"{idx:04d}_{source.stem}"
        out_dir.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {
            "source_path": str(source), "relative_path": relative_repo,
            "project_root": str(project_root), "file_name": source.name,
            "contract_name": contract_name, "status": "failed", "compiler": None,
            "errors": [], "warnings": [], "artifacts": [], "duration_seconds": 0.0,
            "attempts": []
        }
        start = time.time()
        compiler = compiler_for(project_root)
        if not compiler:
            record["errors"].append({"severity": "error",
                "message": "No solc/solcjs found. Install project dependencies or provide a compiler on PATH."})
        else:
            record["compiler"] = "solc" if Path(compiler).name == "solc" else "solcjs"
            # Build only the transitive import closure for this source. Feeding every
            # repository contract to solc at once makes unrelated pragmas/errors contaminate
            # otherwise independent compilations.
            all_sources: dict[str, dict[str, str]] = {}
            modules_roots = [project_root / "node_modules", ROOT / "hhat" / "node_modules"]
            pending = [source.resolve()]
            visited: set[Path] = set()
            while pending:
                current = pending.pop()
                if current in visited or not current.is_file() or current.suffix != ".sol":
                    continue
                visited.add(current)
                module_root = next((m for m in modules_roots if current.is_relative_to(m)), None)
                if module_root is not None:
                    key = current.relative_to(module_root).as_posix()
                else:
                    try:
                        key = current.relative_to(ROOT).as_posix()
                    except ValueError:
                        continue
                source_text = current.read_text(encoding="utf-8", errors="replace")
                all_sources[key] = {"content": source_text}
                for imported in re.findall(r"""import\s+(?:[^;]*?\s+from\s+)?["']([^"']+)["']\s*;""", source_text):
                    if imported.startswith("."):
                        candidate = (current.parent / imported).resolve()
                        if candidate.exists():
                            pending.append(candidate)
                    else:
                        for module_root in modules_roots:
                            candidate = (module_root / imported).resolve()
                            if candidate.exists():
                                pending.append(candidate)
                                break
            selected_key = source.relative_to(ROOT).as_posix()
            standard_input = {
                "language": "Solidity",
                "sources": all_sources,
                "settings": {"optimizer": {"enabled": True, "runs": 200},
                    "outputSelection": {"*": {"*": ["abi", "metadata", "evm.bytecode.object",
                        "evm.deployedBytecode.object", "evm.methodIdentifiers", "storageLayout"],
                        "": ["ast"]}}}
            }
            # Use standard JSON through stdin. solcjs has a known stdin truncation issue on
            # some platforms; keep a temporary input file and feed its contents via subprocess.
            cmd = [compiler, "--standard-json"]
            try:
                # solcjs reads stdin synchronously; feeding a pipe can raise EAGAIN on Linux.
                with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as input_file, \
                     tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output_file:
                    input_file.write(json.dumps(standard_input))
                    input_file.seek(0)
                    proc = subprocess.run(cmd, cwd=project_root, stdin=input_file,
                        stdout=output_file, stderr=subprocess.PIPE, text=True,
                        timeout=args.timeout, check=False,
                        env={**os.environ, "FORCE_COLOR": "0", "NO_COLOR": "1"})
                    output_file.seek(0)
                    raw = output_file.read()
                obj = read_json_output(raw)
                stderr = proc.stderr or ""
                record["attempts"].append({"compiler": record["compiler"], "command": cmd,
                    "returncode": proc.returncode, "stderr": stderr,
                    "duration_seconds": None})
                diags = diagnostics_from(obj, stderr.strip() if proc.returncode else "")
                record["errors"] = [d for d in diags if d.get("severity") == "error"]
                record["warnings"] = [d for d in diags if d.get("severity") == "warning"]
                if obj:
                    contracts_obj = obj.get("contracts", {})
                    source_key = selected_key if selected_key in contracts_obj else None
                    contract_outputs = contracts_obj.get(source_key, {}) if source_key else {}
                    for cname, artifact in contract_outputs.items():
                        if not any((artifact.get(k) for k in ("abi", "metadata", "evm"))):
                            continue
                        artifact_data = {
                            "contractName": cname, "sourceName": source_key or selected_key,
                            "abi": artifact.get("abi"), "metadata": artifact.get("metadata"),
                            "bytecode": {"object": artifact.get("evm", {}).get("bytecode", {}).get("object", "")},
                            "deployedBytecode": {"object": artifact.get("evm", {}).get("deployedBytecode", {}).get("object", "")},
                            "methodIdentifiers": artifact.get("evm", {}).get("methodIdentifiers"),
                            "storageLayout": artifact.get("storageLayout"),
                            "ast": obj.get("sources", {}).get(source_key, {}).get("ast"),
                            "compiler": {"name": record["compiler"]},
                            "auditagents": {"source_path": relative_repo, "project_root": str(project_root)}
                        }
                        out_file = out_dir / f"{cname}.json"
                        write_json(out_file, artifact_data)
                        record["artifacts"].append(str(out_file.relative_to(output)))
                    if not record["errors"] and record["artifacts"]:
                        record["status"] = "success"
                    elif not record["errors"]:
                        record["errors"].append({"severity": "error",
                            "message": "Compiler returned no contract artifact for this source unit."})
                elif not record["errors"]:
                    record["errors"].append({"severity": "error",
                        "message": "Could not parse standard-json compiler output.",
                        "stderr": stderr, "stdout_tail": raw[-4000:]})
            except subprocess.TimeoutExpired as exc:
                record["errors"].append({"severity": "error", "message": f"Compiler timed out after {args.timeout}s"})
                record["attempts"].append({"compiler": record["compiler"], "command": cmd, "timed_out": True})
        record["duration_seconds"] = round(time.time()-start, 3)
        results.append(record)
        print(f"[{idx}/{len(sources)}] {record['status'].upper():7} {relative_repo} ({record['compiler'] or 'no compiler'})")

    total = len(results)
    successes = sum(r["status"] == "success" for r in results)
    summary = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "repository_root": str(ROOT), "source_directories": [str(p) for p in target_dirs],
        "total_contract_files": total, "success_count": successes, "failed_count": total-successes,
        "success_rate_percent": round(successes*100/total, 2) if total else 0,
        "hardhat_runs": hardhat_runs, "results": results}
    write_json(output / "compilation_report.json", summary)
    lines = ["Solidity compilation report", f"Files: {total}",
        f"Success: {successes}", f"Failed: {total-successes}",
        f"Success rate: {summary['success_rate_percent']}%", ""]
    for r in results:
        lines.append(f"[{r['status'].upper()}] {r['relative_path']} | compiler={r['compiler']} | "
                     f"errors={len(r['errors'])} | artifacts={len(r['artifacts'])}")
        for err in r["errors"]:
            lines.append("  " + str(err.get("formatted_message") or err.get("message") or err))
    (output / "compilation_report.txt").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(f"\nSummary: {successes}/{total} successful ({summary['success_rate_percent']}%)")
    print(f"JSON report: {output / 'compilation_report.json'}")
    print(f"Text report: {output / 'compilation_report.txt'}")
    print(f"Artifacts: {artifact_root}")
    return 0 if successes == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
