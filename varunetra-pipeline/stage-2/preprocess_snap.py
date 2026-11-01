#!/usr/bin/env python3
"""
preprocess_snap.py - Stage 2: Sentinel-1 GRDH -> analysis-ready 2-band GeoTIFF via ESA SNAP gpt.

Pipeline:
    input (.SAFE.zip | .SAFE dir | manifest.safe)
      -> myGraph1.xml (Orbit, Border/Thermal noise, Sigma0 calibration) -> temp BEAM-DIMAP
      -> myGraph2.xml (Speckle, Range-Doppler TC @10 m EPSG:4326, dB)  -> GeoTIFF-BigTIFF
      -> verify, atomically move to the requested output, delete intermediates.

Library use:
    from preprocess_snap import run_snap_pipeline
    result = run_snap_pipeline("S1D_...SAFE.zip", "out/scene.tif", memory="12G")

CLI:
    python preprocess_snap.py S1D_....SAFE.zip out/scene.tif --memory=-J-Xmx12G
"""
from __future__ import annotations

import argparse
import codecs
import logging
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("preprocess_snap")

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_GRAPH1 = SCRIPT_DIR / "myGraph1.xml"
DEFAULT_GRAPH2 = SCRIPT_DIR / "myGraph2.xml"


# --------------------------------------------------------------------------- #
# Exceptions / result type
# --------------------------------------------------------------------------- #
class SnapPreprocessError(RuntimeError):
    """Base class for all errors raised by this module."""


class GptNotFoundError(SnapPreprocessError):
    pass


class GptFailedError(SnapPreprocessError):
    pass


class OutputVerificationError(SnapPreprocessError):
    pass


@dataclass
class SnapResult:
    output: Path
    gpt: Path
    total_seconds: float
    stage_seconds: dict = field(default_factory=dict)
    size_gb: float = 0.0
    info: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _hms(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}h {m:02d}m {s:02d}s" if h else f"{m:d}m {s:02d}s"


def _gpt_exe_name() -> str:
    return "gpt.exe" if os.name == "nt" else "gpt"


def _common_gpt_locations() -> list[Path]:
    exe = _gpt_exe_name()
    home = Path.home()
    system = platform.system()
    parents: list[Path] = []
    if system == "Windows":
        for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            val = os.environ.get(var)
            if val:
                parents.append(Path(val))
        parents.append(home)
    elif system == "Darwin":
        parents += [Path("/Applications"), home / "Applications", home, Path("/opt")]
    else:
        parents += [home, Path("/opt"), Path("/usr/local"), Path("/usr/share")]

    found: list[Path] = []
    for parent in parents:
        if not parent.is_dir():
            continue
        try:
            # matches esa-snap, snap, esa-snap_sentinel, ESA SNAP, ... (newest name first)
            found += sorted(parent.glob(f"*snap*/bin/{exe}"), reverse=True)
        except OSError:
            continue
    return found


def find_gpt(gpt_path: Optional[str | os.PathLike] = None) -> Path:
    """Locate SNAP's gpt executable.

    Order: explicit path (file, bin dir or SNAP home) -> $GPT_PATH -> $SNAP_HOME
    -> system PATH -> common install locations.
    """
    exe = _gpt_exe_name()

    def _from_hint(hint: str | os.PathLike) -> Optional[Path]:
        p = Path(hint).expanduser()
        for cand in (p, p / exe, p / "bin" / exe):
            if cand.is_file():
                return cand.resolve()
        return None

    if gpt_path:
        found = _from_hint(gpt_path)
        if found:
            return found
        raise GptNotFoundError(f"gpt not found at --gpt-path '{gpt_path}'")

    for env in ("GPT_PATH", "SNAP_HOME"):
        if os.environ.get(env):
            found = _from_hint(os.environ[env])
            if found:
                return found

    which = shutil.which("gpt")
    if which:
        return Path(which).resolve()

    for cand in _common_gpt_locations():
        if cand.is_file():
            return cand.resolve()

    raise GptNotFoundError(
        "Could not find SNAP's gpt. Install ESA SNAP, add its bin/ folder to PATH, "
        "set GPT_PATH/SNAP_HOME, or pass --gpt-path."
    )


def normalize_memory(memory: Optional[str]) -> Optional[str]:
    """'12G' | '-Xmx12G' | '-J-Xmx12G'  ->  '-J-Xmx12G'."""
    if not memory:
        return None
    m = memory.strip()
    if re.fullmatch(r"-J-Xmx\d+[kKmMgG]", m):
        return m
    if re.fullmatch(r"-Xmx\d+[kKmMgG]", m):
        return "-J" + m
    if re.fullmatch(r"\d+[kKmMgG]", m):
        return f"-J-Xmx{m}"
    raise ValueError(f"Invalid memory spec '{memory}' (use e.g. 12G or -J-Xmx12G)")


def _scene_stem(input_path: Path) -> str:
    name = input_path.parent.name if input_path.name.lower() == "manifest.safe" else input_path.name
    return re.sub(r"(\.zip)?(\.safe)?$", "", name, flags=re.IGNORECASE) or "scene"


def _check_graph(graph: Path) -> Path:
    graph = Path(graph).expanduser().resolve()
    if not graph.is_file():
        raise FileNotFoundError(f"Graph XML not found: {graph}")
    text = graph.read_text(encoding="utf-8", errors="replace")
    missing = [t for t in ("${input}", "${output}") if t not in text]
    if missing:
        raise SnapPreprocessError(
            f"{graph.name} is not parameterized: missing {', '.join(missing)} "
            "(replace the hardcoded Read/Write <file> paths)."
        )
    return graph


def resolve_input(input_path: str | os.PathLike, work_dir: Path, extract_zip: bool) -> Path:
    """Return the path to hand to SNAP's Read node (manifest.safe, or the .zip itself)."""
    p = Path(input_path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Input not found: {p}")

    if p.is_dir():
        manifest = p / "manifest.safe"
        if not manifest.is_file():
            raise SnapPreprocessError(f"No manifest.safe inside {p}")
        return manifest

    if p.name.lower() == "manifest.safe":
        return p

    if p.suffix.lower() == ".zip":
        if not extract_zip:
            return p  # SNAP's S1 reader can read the zip directly
        dest = work_dir / "safe"
        dest.mkdir(parents=True, exist_ok=True)
        log.info("Extracting %s -> %s", p.name, dest)
        t = time.perf_counter()
        with zipfile.ZipFile(p) as zf:
            zf.extractall(dest)
        log.info("Extraction finished in %s", _hms(time.perf_counter() - t))
        safes = [d for d in dest.iterdir() if d.is_dir() and d.name.upper().endswith(".SAFE")]
        if not safes:
            raise SnapPreprocessError(f"No .SAFE folder found after extracting {p.name}")
        manifest = safes[0] / "manifest.safe"
        if not manifest.is_file():
            raise SnapPreprocessError(f"manifest.safe missing in {safes[0]}")
        return manifest

    raise ValueError(f"Unsupported input (need .SAFE.zip, .SAFE dir or manifest.safe): {p}")


# --------------------------------------------------------------------------- #
# gpt execution
# --------------------------------------------------------------------------- #
_PCT = re.compile(r"(\d{1,3})%")
_PROGRESS_ONLY = re.compile(r"^[\s.\d%]*(done\.?)?[\s.\d%]*$", re.IGNORECASE)
_ERRISH = re.compile(r"\b(error|exception|severe|failed)\b", re.IGNORECASE)


def _fmt_cmd(cmd: list[str]) -> str:
    return subprocess.list2cmdline(cmd) if os.name == "nt" else shlex.join(cmd)


def _build_cmd(gpt: Path, graph: Path, inp: Path, out: Path,
               memory: Optional[str], cache_size: Optional[str],
               parallelism: Optional[int]) -> list[str]:
    cmd = [str(gpt), str(graph), f"-Pinput={inp}", f"-Poutput={out}", "-x"]
    if cache_size:
        cmd += ["-c", cache_size]
    if parallelism:
        cmd += ["-q", str(parallelism)]
    if memory:
        cmd.append(memory)
    return cmd


def _run_gpt(cmd: list[str], stage: str, tail_lines: int = 40) -> float:
    """Run gpt, streaming its output into the logger. Returns elapsed seconds."""
    log.info("[%s] %s", stage, _fmt_cmd(cmd))
    t0 = time.perf_counter()
    tail: deque[str] = deque(maxlen=tail_lines)

    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, bufsize=0,
        )
    except OSError as exc:
        raise GptFailedError(f"[{stage}] could not start gpt: {exc}") from exc

    def emit(line: str) -> None:
        line = line.rstrip()
        if not line or _PROGRESS_ONLY.match(line):
            return
        tail.append(line)
        log.log(logging.WARNING if _ERRISH.search(line) else logging.INFO,
                "[%s] gpt: %s", stage, line)

    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buf = ""
    last_pct = -1
    try:
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            buf += decoder.decode(chunk)
            parts = re.split(r"\r\n|\r|\n", buf)
            buf = parts.pop()
            for line in parts:
                emit(line)
            # gpt prints "....10%....20%..." on one line: surface it as progress
            if buf.lstrip().startswith(".."):
                for m in _PCT.finditer(buf):
                    pct = int(m.group(1))
                    if pct > last_pct and pct % 10 == 0 and pct <= 100:
                        last_pct = pct
                        log.info("[%s] progress %d%% (%s elapsed)", stage, pct,
                                 _hms(time.perf_counter() - t0))
        buf += decoder.decode(b"", final=True)
        emit(buf)
        rc = proc.wait()
    except BaseException:  # includes KeyboardInterrupt: don't leave gpt running
        proc.kill()
        proc.wait()
        raise
    finally:
        if proc.stdout:
            proc.stdout.close()

    elapsed = time.perf_counter() - t0
    if rc != 0:
        raise GptFailedError(
            f"[{stage}] gpt exited with code {rc} after {_hms(elapsed)}.\n"
            f"Last output:\n" + "\n".join(tail)
        )
    log.info("[%s] finished in %s", stage, _hms(elapsed))
    return elapsed


# --------------------------------------------------------------------------- #
# Output verification
# --------------------------------------------------------------------------- #
def verify_output(tif: Path, expected_bands: int = 2, expected_epsg: int = 4326) -> dict:
    if not tif.is_file() or tif.stat().st_size == 0:
        raise OutputVerificationError(f"Output missing or empty: {tif}")

    with open(tif, "rb") as f:
        head = f.read(4)
    if head not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        raise OutputVerificationError(f"{tif.name} does not look like a TIFF (header {head!r})")
    info = {"size_gb": tif.stat().st_size / 1e9, "bigtiff": head in (b"II+\x00", b"MM\x00+")}

    try:
        import numpy as np
        import rasterio
        from rasterio.windows import Window
    except ImportError:
        log.warning("rasterio/numpy not installed - skipped band/CRS verification")
        return info

    try:
        with rasterio.open(tif) as ds:
            info.update(width=ds.width, height=ds.height, count=ds.count,
                        dtype=ds.dtypes[0], nodata=ds.nodata,
                        epsg=ds.crs.to_epsg() if ds.crs else None)
            if ds.count != expected_bands:
                raise OutputVerificationError(f"Expected {expected_bands} bands, found {ds.count}")
            if info["epsg"] != expected_epsg:
                raise OutputVerificationError(f"Expected EPSG:{expected_epsg}, found {info['epsg']}")

            w, h = min(512, ds.width), min(512, ds.height)
            win = Window((ds.width - w) // 2, (ds.height - h) // 2, w, h)
            data = ds.read(window=win)
            for i in range(ds.count):
                frac = float(np.isfinite(data[i]).mean())
                log.info("Band %d centre-window finite fraction: %.3f", i + 1, frac)
            if not np.isfinite(data).any():
                log.warning("Centre window has no finite pixels - check the output visually")
    except rasterio.errors.RasterioIOError as exc:
        raise OutputVerificationError(f"rasterio cannot open {tif.name}: {exc}") from exc

    log.info("Verified %s: %dx%d, %d bands, %s, EPSG:%s, %.2f GB",
             tif.name, info["width"], info["height"], info["count"],
             info["dtype"], info["epsg"], info["size_gb"])
    return info


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def run_snap_pipeline(
    input_path: str | os.PathLike,
    output_tif: str | os.PathLike,
    *,
    gpt_path: Optional[str | os.PathLike] = None,
    memory: Optional[str] = None,
    cache_size: Optional[str] = None,
    parallelism: Optional[int] = None,
    graph1: str | os.PathLike = DEFAULT_GRAPH1,
    graph2: str | os.PathLike = DEFAULT_GRAPH2,
    extract_zip: bool = False,
    work_dir: Optional[str | os.PathLike] = None,
    keep_intermediate: bool = False,
    overwrite: bool = False,
    verify: bool = True,
    min_free_gb: float = 40.0,
) -> SnapResult:
    """Run the two-graph SNAP preprocessing and return a SnapResult.

    memory       : '12G' or '-J-Xmx12G' (JVM heap).
    cache_size   : gpt tile cache (-c), e.g. '8G'; roughly 70% of memory is sensible.
    extract_zip  : unzip .SAFE.zip first instead of letting SNAP read the zip directly.
    work_dir     : parent for the temp folder (default: next to the output, so the
                   final move is a cheap rename on the same drive).
    """
    t_total = time.perf_counter()
    stage_seconds: dict[str, float] = {}

    output = Path(output_tif).expanduser().resolve()
    if output.suffix.lower() not in (".tif", ".tiff"):
        raise ValueError(f"Output must end in .tif/.tiff: {output}")
    if output.exists() and not overwrite:
        raise FileExistsError(f"{output} exists (use overwrite=True / --overwrite)")
    output.parent.mkdir(parents=True, exist_ok=True)

    gpt = find_gpt(gpt_path)
    g1, g2 = _check_graph(Path(graph1)), _check_graph(Path(graph2))
    mem = normalize_memory(memory)
    log.info("Using gpt: %s", gpt)

    free_gb = shutil.disk_usage(output.parent).free / 1e9
    if free_gb < min_free_gb:
        log.warning("Only %.1f GB free near %s; intermediates + output may need ~%d GB",
                    free_gb, output.parent, int(min_free_gb))

    work_parent = Path(work_dir).expanduser().resolve() if work_dir else output.parent
    work_parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".snap_work_", dir=work_parent))
    log.info("Working directory: %s", work)

    stem = _scene_stem(Path(input_path))
    success = False
    info: dict = {}
    try:
        # --- input ---------------------------------------------------------
        t = time.perf_counter()
        snap_input = resolve_input(input_path, work, extract_zip)
        stage_seconds["input"] = time.perf_counter() - t
        log.info("SNAP input: %s", snap_input)

        # --- stage 1: orbit / noise / calibration -> DIMAP ------------------
        inter_dir = work / "intermediate"
        inter_dir.mkdir()
        inter_dim = inter_dir / f"{stem}_Orb_NR_Cal.dim"
        stage_seconds["graph1"] = _run_gpt(
            _build_cmd(gpt, g1, snap_input, inter_dim, mem, cache_size, parallelism), "graph1")
        if not inter_dim.is_file() or not inter_dim.with_suffix(".data").is_dir():
            raise SnapPreprocessError(f"graph1 reported success but {inter_dim.name}/.data is missing")

        # --- stage 2: speckle / terrain correction / dB -> BigTIFF ----------
        final_dir = work / "final"
        final_dir.mkdir()
        tmp_tif = final_dir / f"{stem}_Orb_NR_Cal_Spk_TC_dB.tif"
        stage_seconds["graph2"] = _run_gpt(
            _build_cmd(gpt, g2, inter_dim, tmp_tif, mem, cache_size, parallelism), "graph2")
        if not tmp_tif.is_file():  # SNAP may adjust the extension
            tifs = sorted(final_dir.glob("*.tif*"))
            if not tifs:
                raise SnapPreprocessError("graph2 reported success but produced no GeoTIFF")
            tmp_tif = tifs[0]

        # --- verify + publish ------------------------------------------------
        if verify:
            t = time.perf_counter()
            info = verify_output(tmp_tif)
            stage_seconds["verify"] = time.perf_counter() - t
        try:
            os.replace(tmp_tif, output)
        except OSError:
            shutil.move(str(tmp_tif), str(output))
        success = True
        log.info("Wrote %s (%.2f GB)", output, output.stat().st_size / 1e9)

    finally:
        if keep_intermediate:
            log.info("Keeping working directory: %s", work)
        else:
            shutil.rmtree(work, ignore_errors=True)
            if work.exists():
                log.warning("Could not fully remove %s; delete it manually", work)
            else:
                log.info("Removed intermediates (%s)", "success" if success else "after failure")

    total = time.perf_counter() - t_total
    log.info("Done in %s  (graph1 %s, graph2 %s)", _hms(total),
             _hms(stage_seconds.get("graph1", 0)), _hms(stage_seconds.get("graph2", 0)))
    return SnapResult(output=output, gpt=gpt, total_seconds=total,
                      stage_seconds=stage_seconds,
                      size_gb=output.stat().st_size / 1e9, info=info)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Sentinel-1 GRDH preprocessing with ESA SNAP gpt (2-band VH/VV dB GeoTIFF).")
    p.add_argument("input", help=".SAFE.zip, unzipped .SAFE folder, or manifest.safe")
    p.add_argument("output", help="Output GeoTIFF path (.tif)")
    p.add_argument("--gpt-path", help="gpt executable, its bin/ folder, or SNAP home "
                                      "(default: PATH, then common install locations)")
    p.add_argument("--memory", help="JVM heap, e.g. 12G or --memory=-J-Xmx12G "
                                    "(note the '=' when the value starts with '-')")
    p.add_argument("--cache-size", help="gpt tile cache (-c), e.g. 8G")
    p.add_argument("--parallelism", type=int, help="gpt parallelism (-q)")
    p.add_argument("--graph1", default=str(DEFAULT_GRAPH1), help="Calibration graph XML")
    p.add_argument("--graph2", default=str(DEFAULT_GRAPH2), help="Terrain-correction graph XML")
    p.add_argument("--extract-zip", action="store_true",
                   help="Unzip .SAFE.zip into the working dir instead of reading the zip directly")
    p.add_argument("--work-dir", help="Parent folder for temp files (default: next to output)")
    p.add_argument("--keep-intermediate", action="store_true", help="Do not delete temp files")
    p.add_argument("--overwrite", action="store_true", help="Overwrite existing output")
    p.add_argument("--no-verify", action="store_true", help="Skip output verification")
    p.add_argument("--log-file", help="Also write the log to this file")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
        handlers=handlers)

    try:
        run_snap_pipeline(
            args.input, args.output,
            gpt_path=args.gpt_path, memory=args.memory,
            cache_size=args.cache_size, parallelism=args.parallelism,
            graph1=args.graph1, graph2=args.graph2,
            extract_zip=args.extract_zip, work_dir=args.work_dir,
            keep_intermediate=args.keep_intermediate,
            overwrite=args.overwrite, verify=not args.no_verify)
    except KeyboardInterrupt:
        log.error("Interrupted")
        return 130
    except (SnapPreprocessError, FileNotFoundError, FileExistsError, ValueError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())