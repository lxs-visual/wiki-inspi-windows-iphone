"""Vidéos : infos, image d'aperçu, images clés pour l'IA, conversion. Utilise le ffmpeg fourni par imageio-ffmpeg."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # pas de fenêtre console sous Windows


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run([ffmpeg(), "-hide_banner", *args], capture_output=True, timeout=timeout,
                          creationflags=_NO_WINDOW)


def probe(path: Path) -> dict:
    """Durée (s), largeur, hauteur et codec vidéo, lus dans la sortie de ffmpeg -i."""
    err = _run(["-i", str(path)], 60).stderr.decode("utf-8", "ignore")
    info = {"duration": None, "width": None, "height": None, "codec": None, "audio": "Audio:" in err}
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        info["duration"] = round(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)), 2)
    m = re.search(r"Video: (\w+)[^\n]*?, (\d{2,5})x(\d{2,5})", err)
    if m:
        info["codec"], info["width"], info["height"] = m.group(1), int(m.group(2)), int(m.group(3))
    if not info["width"]:
        raise ValueError("ce fichier ne contient pas de vidéo lisible")
    if re.search(r"rotate\s*:\s*(90|270|-90)", err) or re.search(r"rotation of -?90", err):
        info["width"], info["height"] = info["height"], info["width"]
    return info


def frame(path: Path, at: float, out: Path, width: int = 1280) -> Path:
    r = _run(["-y", "-ss", f"{max(at, 0):.2f}", "-i", str(path), "-frames:v", "1",
              "-vf", f"scale='min({width},iw)':-2", "-q:v", "3", str(out)], 120)
    if not out.exists() or out.stat().st_size == 0:
        raise ValueError("impossible d'extraire une image de la vidéo : " + r.stderr.decode("utf-8", "ignore")[-200:])
    return out


def key_frames(path: Path, duration: float | None, folder: Path, n: int = 4, width: int = 768) -> list[Path]:
    d = duration or 1.0
    times = [d * (i + 0.5) / n for i in range(n)] if d > 1 else [0.0]
    return [frame(path, t, folder / f"k{i}.jpg", width) for i, t in enumerate(times)]


PLAYABLE = {"h264", "vp8", "vp9", "av1"}


def needs_transcode(info: dict, path: Path) -> bool:
    """Les .mov HEVC de l'iPhone ne se lisent pas partout : on les convertit en MP4 H.264."""
    return (info.get("codec") or "").lower() not in PLAYABLE or path.suffix.lower() in (".avi", ".mkv")


def transcode(src: Path, dst: Path) -> Path:
    r = _run(["-y", "-i", str(src), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dst)], 3600)
    if r.returncode or not dst.exists():
        raise ValueError("conversion vidéo impossible : " + r.stderr.decode("utf-8", "ignore")[-300:])
    return dst
