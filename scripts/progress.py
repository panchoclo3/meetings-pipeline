"""
Utilidades compartidas de visibilidad de progreso para todos los scripts del
pipeline.

Por qué existe: pasos como la diarización de WhisperX pueden tardar decenas
de minutos en CPU sin dar ninguna señal propia de progreso — el usuario se
queda viendo una consola congelada sin saber si sigue corriendo o si se
colgó. Esto da tres cosas a cada script que las use:

1. `Stage`: imprime cuándo empieza y termina cada paso, con tiempo
   transcurrido, y un "latido" periódico mientras el paso sigue en curso
   (para pasos silenciosos como whisperx.transcribe() o una diarización).
2. `logged_run`: envuelve el main() de un script — duplica toda la salida
   a un archivo de log con timestamp en data/logs/ (para revisar después,
   si la terminal se cerró o se perdió el scroll de una corrida larga), y
   si algo revienta sin ser atrapado antes, lo reporta con un mensaje claro
   en vez de un traceback crudo, dejando el traceback completo en el log.
3. `format_duration`: "3s" / "4m 12s" / "1h 03m 02s", usado por ambos.
"""

import sys
import time
import threading
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s"


class Stage:
    """
    Context manager para un paso con progreso visible.

        with Stage("[4/4] Diarizando (identificando hablantes)"):
            ... trabajo silencioso y largo ...

    Imprime el inicio, un latido cada `heartbeat` segundos mientras el
    bloque sigue corriendo, y al salir imprime éxito o error con el tiempo
    total. No suprime la excepción — el llamador (o logged_run más arriba
    en la pila) sigue viéndola.
    """

    def __init__(self, label: str, heartbeat: float = 20.0):
        self.label = label
        self.heartbeat = heartbeat
        self._stop = threading.Event()
        self._thread = None
        self._start = None

    def _beat(self):
        while not self._stop.wait(self.heartbeat):
            elapsed = time.monotonic() - self._start
            print(f"   … sigue en curso ({format_duration(elapsed)} transcurridos)", flush=True)

    def __enter__(self):
        self._start = time.monotonic()
        print(f"▶ {self.label}...", flush=True)
        self._thread = threading.Thread(target=self._beat, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        self._thread.join(timeout=1)
        elapsed = format_duration(time.monotonic() - self._start)
        if exc_type is None:
            print(f"✅ {self.label} — listo ({elapsed})", flush=True)
        else:
            print(f"❌ {self.label} — falló tras {elapsed}: {exc}", flush=True)
        return False


class _Tee:
    """Escribe a varios streams a la vez (consola real + archivo de log)."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)

    def flush(self):
        for s in self.streams:
            s.flush()

    def isatty(self):
        return False


@contextmanager
def logged_run(script_name: str, root: Path):
    """
    Envuelve el cuerpo de main(). Duplica stdout/stderr a
    data/logs/<script_name>_<timestamp>.log, y si algo no atrapado revienta
    adentro, imprime un resumen claro (tipo de error + dónde ver el
    traceback completo) en vez de dejar que la excepción cruda se pierda en
    el scroll de la consola. Da el path del log vía `as`, útil para
    imprimirlo al arrancar (así el usuario sabe dónde mirar sin tener que
    volver acá).
    """
    logs_dir = root / "data" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"{script_name}_{ts}.log"
    log_file = open(log_path, "w", encoding="utf-8")

    real_stdout, real_stderr = sys.stdout, sys.stderr
    sys.stdout = _Tee(real_stdout, log_file)
    sys.stderr = _Tee(real_stderr, log_file)

    start = time.monotonic()
    try:
        yield log_path
    except SystemExit:
        raise
    except KeyboardInterrupt:
        elapsed = format_duration(time.monotonic() - start)
        print(f"\n⏹️  Cancelado por el usuario (Ctrl+C) tras {elapsed}.")
        sys.exit(130)
    except BaseException as e:
        elapsed = format_duration(time.monotonic() - start)
        traceback.print_exc(file=log_file)
        print(f"\n❌ {script_name} falló tras {elapsed}: {e}")
        print(f"   Traceback completo en: {log_path}")
        sys.exit(1)
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr
        if not log_file.closed:
            log_file.close()
