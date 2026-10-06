"""Pruebas de `actualizar.sh`, la autoactualización del bot en el NAS.

Montan un repositorio "origin" y un clon en un directorio temporal, y ponen
en el PATH un `docker` falso que apunta cada llamada en un archivo y simula
el estado del contenedor con variables de entorno. Así se comprueba la lógica
del script (cuándo reconstruye, cuándo vuelve atrás, cuándo no toca nada) sin
Docker ni red.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "actualizar.sh"

pytestmark = pytest.mark.skipif(
    not all(shutil.which(cmd) for cmd in ("bash", "git", "flock")),
    reason="hacen falta bash, git y flock",
)

# `docker` falso: registra los argumentos y responde según FAKE_*.
FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "compose version") exit 0 ;;
  "compose build") [[ -n "${FAKE_BUILD_FAIL:-}" ]] && exit 1; exit 0 ;;
  "image inspect") exit 0 ;;
  "inspect -f")
    if [[ "$3" == *Running* ]]; then echo "${FAKE_RUNNING:-true}"
    else echo "${FAKE_RESTARTS:-0}"; fi
    exit 0 ;;
esac
exit 0
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def entorno(tmp_path: Path) -> dict:
    """Origin con un commit, clon de trabajo y docker falso en el PATH."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "t@t")
    _git(origin, "config", "user.name", "t")
    shutil.copy(SCRIPT, origin / "actualizar.sh")
    (origin / ".gitignore").write_text(".despliegue/\n")
    (origin / "bot.txt").write_text("v1\n")
    _git(origin, "add", ".")
    _git(origin, "commit", "-q", "-m", "v1")

    clon = tmp_path / "clon"
    _git(tmp_path, "clone", "-q", str(origin), str(clon))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)

    log = tmp_path / "docker.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_LOG": str(log),
        "ESPERA_ARRANQUE": "0",
    }
    return {"origin": origin, "clon": clon, "env": env, "log": log}


def _ejecutar(entorno: dict, **extra: str) -> subprocess.CompletedProcess:
    env = {**entorno["env"], **extra}
    return subprocess.run(
        ["bash", str(entorno["clon"] / "actualizar.sh")],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


def _nuevo_commit(origin: Path, texto: str) -> str:
    (origin / "bot.txt").write_text(texto)
    _git(origin, "commit", "-q", "-am", texto)
    return _git(origin, "rev-parse", "HEAD")


def _llamadas(entorno: dict) -> str:
    return entorno["log"].read_text() if entorno["log"].exists() else ""


def _limpiar_llamadas(entorno: dict) -> None:
    entorno["log"].unlink(missing_ok=True)


def test_despliega_un_commit_nuevo(entorno):
    _ejecutar(entorno)  # primer despliegue: deja apuntado el commit actual
    _limpiar_llamadas(entorno)
    nuevo = _nuevo_commit(entorno["origin"], "v2")

    resultado = _ejecutar(entorno)

    assert resultado.returncode == 0
    estado = entorno["clon"] / ".despliegue"
    assert (estado / "commit").read_text().strip() == nuevo
    assert (entorno["clon"] / "bot.txt").read_text() == "v2"
    llamadas = _llamadas(entorno)
    assert "compose build --pull" in llamadas
    assert "compose up -d --force-recreate --no-build" in llamadas
    assert "tag bot-jovani-vazquez:latest bot-jovani-vazquez:anterior" in llamadas


def test_sin_cambios_no_toca_el_bot(entorno):
    _ejecutar(entorno)
    _limpiar_llamadas(entorno)

    resultado = _ejecutar(entorno)

    assert resultado.returncode == 0
    assert "compose build" not in _llamadas(entorno)
    assert "Sin cambios" in (entorno["clon"] / ".despliegue/actualizar.log").read_text()


def test_reconstruye_si_la_imagen_es_vieja(entorno):
    _ejecutar(entorno)
    _limpiar_llamadas(entorno)
    commit = entorno["clon"] / ".despliegue/commit"
    viejo = commit.stat().st_mtime - 8 * 86400
    os.utime(commit, (viejo, viejo))

    _ejecutar(entorno)

    assert "compose build --pull" in _llamadas(entorno)


def test_vuelve_atras_si_el_bot_no_arranca(entorno):
    _ejecutar(entorno)
    anterior = (entorno["clon"] / ".despliegue/commit").read_text().strip()
    _limpiar_llamadas(entorno)
    roto = _nuevo_commit(entorno["origin"], "roto")

    resultado = _ejecutar(entorno, FAKE_RESTARTS="3")

    assert resultado.returncode == 1
    estado = entorno["clon"] / ".despliegue"
    assert (estado / "fallido").read_text().strip() == roto
    assert (estado / "commit").read_text().strip() == anterior
    assert _git(entorno["clon"], "rev-parse", "HEAD") == anterior
    assert "tag bot-jovani-vazquez:anterior bot-jovani-vazquez:latest" in _llamadas(entorno)

    # Al día siguiente no reintenta el mismo commit roto...
    _limpiar_llamadas(entorno)
    _ejecutar(entorno)
    assert "compose build" not in _llamadas(entorno)

    # ...pero sí en cuanto llega un arreglo.
    arreglo = _nuevo_commit(entorno["origin"], "arreglo")
    _ejecutar(entorno)
    assert (estado / "commit").read_text().strip() == arreglo
    assert not (estado / "fallido").exists()


def test_si_falla_la_construccion_deja_el_bot_como_estaba(entorno):
    _ejecutar(entorno)
    anterior = (entorno["clon"] / ".despliegue/commit").read_text().strip()
    _limpiar_llamadas(entorno)
    _nuevo_commit(entorno["origin"], "v2")

    resultado = _ejecutar(entorno, FAKE_BUILD_FAIL="1")

    assert resultado.returncode == 1
    assert "compose up" not in _llamadas(entorno)
    assert _git(entorno["clon"], "rev-parse", "HEAD") == anterior
    assert not (entorno["clon"] / ".despliegue/fallido").exists()


def test_no_pisa_cambios_locales(entorno):
    _nuevo_commit(entorno["origin"], "v2")
    (entorno["clon"] / "bot.txt").write_text("tocado a mano\n")

    resultado = _ejecutar(entorno)

    assert resultado.returncode == 1
    assert (entorno["clon"] / "bot.txt").read_text() == "tocado a mano\n"
    assert "compose build" not in _llamadas(entorno)
