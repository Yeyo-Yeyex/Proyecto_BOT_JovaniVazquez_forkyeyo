#!/usr/bin/env bash
# Autoactualización del bot en el NAS.
#
# Uso: ./actualizar.sh    (a mano o desde cron; ver "Actualización automática"
#                          en el README para dejarlo programado a las 5:00)
#
# Qué hace, en orden:
#   1. Descarga la rama main de GitHub (git fetch) sin tocar nada aún.
#   2. Si no hay commits nuevos desde el último despliegue y la imagen tiene
#      menos de 7 días, termina sin hacer nada (el bot ni se entera).
#   3. Guarda la imagen actual como `bot-jovani-vazquez:anterior`, pone el
#      código en el último commit y reconstruye la imagen. Si la construcción
#      falla, el bot viejo sigue funcionando y se reintenta al día siguiente.
#   4. Reinicia el contenedor con la imagen nueva y espera 90 s. Si en ese
#      tiempo el bot se cae o entra en bucle de reinicios (un PR roto), vuelve
#      a la imagen anterior y no reintenta ese commit hasta que haya otro.
#   5. Borra las imágenes huérfanas para no llenar el disco.
#
# La reconstrucción semanal aunque no haya cambios trae la última versión de
# yt-dlp, que YouTube deja inservible cada pocas semanas.
#
# Requisitos: git, docker con el plugin compose (o docker-compose) y flock.
# El usuario que lo ejecute tiene que poder usar docker y ser dueño del clon.
# Nunca toca `.env` ni los datos: el token está ignorado por git y la base de
# datos vive en el volumen `bot-jovani-vazquez-data`.
#
# Si alguien ha modificado a mano archivos versionados del clon (por ejemplo
# docker-compose.yml), el script se niega a actualizar para no pisarlos.
#
# Archivos que deja junto a este script (todos ignorados por git):
#   .despliegue/commit      commit desplegado ahora mismo
#   .despliegue/fallido     último commit que tumbó el bot (no se reintenta)
#   .despliegue/actualizar.log   registro de cada ejecución (últimas 2000 líneas)
#
# Variables opcionales: RAMA (main), ESPERA_ARRANQUE (90), DIAS_RECONSTRUIR (7).

# Todo va dentro de funciones y la llamada final cabe en una sola línea: bash
# lee los scripts a trozos mientras los ejecuta, y este se sobrescribe a sí
# mismo con el `git reset`. Así está leído entero antes de que cambie.

set -uo pipefail

main() {
    local dir estado log rama espera dias contenedor imagen compose
    dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    estado="$dir/.despliegue"
    log="$estado/actualizar.log"
    rama="${RAMA:-main}"
    espera="${ESPERA_ARRANQUE:-90}"
    dias="${DIAS_RECONSTRUIR:-7}"
    contenedor="bot-jovani-vazquez"
    imagen="bot-jovani-vazquez"

    # cron arranca con un PATH mínimo; los NAS suelen tener docker en /usr/local/bin.
    export PATH="$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

    mkdir -p "$estado"
    # Desde cron todo va al log; a mano, además, se ve en pantalla.
    if [[ -t 1 ]]; then
        exec > >(tee -a "$log") 2>&1
    else
        exec >>"$log" 2>&1
    fi

    # Una sola ejecución a la vez (por si alguien lo lanza a mano a las 5:00).
    exec 9>"$estado/lock"
    if ! flock -n 9; then
        echo "$(date '+%F %T') Ya hay otra actualización en marcha; salgo."
        return 0
    fi

    echo "===== $(date '+%F %T') ====="
    cd "$dir" || return 1

    if docker compose version >/dev/null 2>&1; then
        compose=(docker compose)
    elif command -v docker-compose >/dev/null 2>&1; then
        compose=(docker-compose)
    else
        echo "No encuentro docker compose ni docker-compose."
        return 1
    fi

    # safe.directory evita el error de "dubious ownership" si cron corre como
    # otro usuario distinto del que clonó.
    local git=(git -c "safe.directory=$dir")

    if ! "${git[@]}" diff --quiet HEAD --; then
        echo "Hay cambios locales en archivos versionados; no actualizo para no pisarlos:"
        "${git[@]}" status --short --untracked-files=no
        return 1
    fi

    if ! "${git[@]}" fetch --quiet origin "$rama"; then
        echo "git fetch ha fallado (¿sin red o GitHub caído?)."
        return 1
    fi

    local nuevo actual fallido
    nuevo="$("${git[@]}" rev-parse "origin/$rama")"
    actual="$(cat "$estado/commit" 2>/dev/null || "${git[@]}" rev-parse HEAD)"
    fallido="$(cat "$estado/fallido" 2>/dev/null || true)"

    if [[ "$nuevo" == "$fallido" ]]; then
        echo "origin/$rama sigue en ${nuevo:0:7}, que ya tumbó el bot; espero a un commit nuevo."
        return 0
    fi

    local motivo=""
    if [[ "$nuevo" != "$actual" ]]; then
        motivo="commits nuevos (${actual:0:7} -> ${nuevo:0:7})"
    elif [[ -z "$(find "$estado/commit" -mtime "-$dias" 2>/dev/null)" ]]; then
        motivo="reconstrucción periódica (imagen de más de $dias días, yt-dlp al día)"
    else
        echo "Sin cambios (${actual:0:7})."
        return 0
    fi
    echo "Actualizo: $motivo"

    # Imagen de respaldo para volver atrás sin reconstruir.
    local hay_respaldo=0
    if docker image inspect "$imagen:latest" >/dev/null 2>&1; then
        docker tag "$imagen:latest" "$imagen:anterior" && hay_respaldo=1
    fi

    "${git[@]}" reset --quiet --hard "$nuevo"

    if ! "${compose[@]}" build --pull; then
        echo "La imagen no se ha podido construir; el bot sigue con la versión anterior."
        # Se deja el código como estaba para que el próximo intento parta limpio.
        "${git[@]}" reset --quiet --hard "$actual" 2>/dev/null || true
        return 1
    fi

    # --force-recreate deja el contador de reinicios a cero para la comprobación.
    if ! "${compose[@]}" up -d --force-recreate --no-build; then
        echo "docker compose up ha fallado."
        volver_atras
        return 1
    fi

    echo "Esperando ${espera} s para comprobar que el bot arranca..."
    sleep "$espera"

    local corriendo reinicios
    corriendo="$(docker inspect -f '{{.State.Running}}' "$contenedor" 2>/dev/null || echo false)"
    reinicios="$(docker inspect -f '{{.RestartCount}}' "$contenedor" 2>/dev/null || echo 99)"
    if [[ "$corriendo" != "true" || "$reinicios" != "0" ]]; then
        echo "El bot no aguanta en pie (en marcha: $corriendo, reinicios: $reinicios). Últimas líneas del log:"
        docker logs --tail 40 "$contenedor" 2>&1 || true
        echo "$nuevo" >"$estado/fallido"
        volver_atras
        return 1
    fi

    echo "$nuevo" >"$estado/commit"
    rm -f "$estado/fallido"
    docker image prune -f >/dev/null 2>&1 || true
    echo "Desplegado ${nuevo:0:7}: $("${git[@]}" log -1 --format=%s "$nuevo")"
}

# Vuelve a la imagen guardada antes de construir. El código queda en el
# commit desplegado para que el clon refleje lo que corre de verdad.
volver_atras() {
    if [[ "$hay_respaldo" != 1 ]]; then
        echo "No hay imagen anterior a la que volver; revisa el bot a mano."
        return
    fi
    echo "Vuelvo a la imagen anterior (${actual:0:7})."
    docker tag "$imagen:anterior" "$imagen:latest"
    "${git[@]}" reset --quiet --hard "$actual" 2>/dev/null || true
    "${compose[@]}" up -d --force-recreate --no-build || echo "No he podido arrancar la imagen anterior; revisa el bot a mano."
}

recortar_log() {
    local log="$1"
    [[ -f "$log" ]] || return 0
    if (($(wc -l <"$log") > 2000)); then
        tail -n 2000 "$log" >"$log.tmp" && mv "$log.tmp" "$log"
    fi
}

main "$@"; codigo=$?; recortar_log "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.despliegue/actualizar.log"; exit "$codigo"
