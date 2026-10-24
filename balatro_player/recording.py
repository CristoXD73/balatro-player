"""Screen recording of each run, via the ScreenCaptureKit helper in tools/.

Each run is recorded to ``recordings/run_0007.mov`` while it plays. When the
run ends the file is renamed with its outcome (``run_0007_ante5_lost.mov``)
and, if requested, a timelapse copy is written next to it
(``run_0007_ante5_lost_x8.mov``).
"""

from __future__ import annotations

import logging
import select
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_BINARY = Path(__file__).resolve().parent.parent / "tools" / "bin" / "balatro-recorder"


class Recorder:
    def __init__(self, out_dir: str = "recordings", speedup: float = 0, fps: int = 30,
                 binary: Optional[str] = None, app: str = "Balatro", keep_original: bool = True):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.speedup = speedup
        self.fps = fps
        self.app = app
        self.keep_original = keep_original
        self.binary = Path(binary) if binary else DEFAULT_BINARY
        self.proc: Optional[subprocess.Popen] = None
        self.current: Optional[Path] = None
        self.started_at: Optional[float] = None          # wall time of the video's first frame
        self.last_timelapse: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.binary.exists()

    def start(self, run_number: int) -> Optional[Path]:
        if not self.available:
            log.warning("recorder not built (%s); run scripts/setup_mac.sh", self.binary)
            return None
        self.current = self.out_dir / f"run_{run_number:04d}.mov"
        self.proc = subprocess.Popen(
            [str(self.binary), "record", str(self.current), "--app", self.app, "--fps", str(self.fps), "--wait", "20"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # Wait until the helper says it's recording, so the run's start is on film.
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                err = self.proc.stderr.read() if self.proc.stderr else ""
                log.warning("recorder exited early: %s", err.strip())
                self.proc = None
                return None
            ready, _, _ = select.select([self.proc.stdout], [], [], 0.5)
            if ready and self.proc.stdout.readline().startswith("recording"):
                self.started_at = time.time()
                self.last_timelapse = None
                return self.current
        log.warning("recorder did not start in time; continuing without video")
        self.proc.kill()
        self.proc = None
        return None

    def stop(self, label: str = "") -> Optional[Path]:
        """Stop recording; rename with ``label``; make the timelapse. Returns the main video path."""
        if not self.proc or not self.current:
            return None
        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc = None
        src = self.current
        self.current = None
        if not src.exists():
            log.warning("recording %s was not written", src)
            return None
        final = src.with_name(f"{src.stem}_{label}.mov") if label else src
        if final != src:
            shutil.move(str(src), str(final))
        if self.speedup and self.speedup > 1:
            fast = final.with_name(f"{final.stem}_x{self.speedup:g}.mov")
            r = subprocess.run([str(self.binary), "speedup", str(final), str(fast), str(self.speedup),
                                "--fps", str(self.fps)], capture_output=True, text=True)
            if r.returncode != 0:
                log.warning("timelapse failed: %s", r.stderr.strip())
            else:
                self.last_timelapse = str(fast)
            if r.returncode == 0 and not self.keep_original:
                final.unlink()
                return fast
        return final
