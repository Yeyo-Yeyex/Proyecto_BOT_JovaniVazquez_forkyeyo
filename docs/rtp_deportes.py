"""Mide el retorno de cada mercado de `deportes` contra las casas de apuestas.

El bot pone sus cuotas con un modelo (`bot.services.sports.fit_goals`). Si las casas tienen
razón, cada opción devuelve `probabilidad de las casas × cuota del bot`. Este script lo
calcula para cada opción que las casas también venden, con cuotas de verdad:

- el 1X2 y las líneas de más/menos, de una consulta de cuotas (`--bajar` la hace: cuesta
  2 créditos por competición);
- «ambos marcan» y el exacto, si se le pasa una captura de `/events/{id}/odds` con
  `markets=btts,correct_score` (`--eventos`; cuesta 1-2 créditos por partido).

Lo que importa es el máximo: una opción por encima de 1 regala dinero si las casas
aciertan. El margen de las casas se quita normalizando (en el exacto eso sobrestima los
resultados raros, así que su retorno sale algo alto).

    python docs/rtp_deportes.py --bajar captura.json        # baja y mide
    python docs/rtp_deportes.py captura.json [--eventos eventos.json]

Necesita `PYTHONPATH=src` y, para `--bajar`, `ODDS_API_KEY` en `.env`.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

from bot.services.sports import SPORTS, Market, market_of, parse_odds


def download(path: Path) -> None:
    """Baja el 1X2 y el más/menos de cada competición a `path`."""
    env = Path(".env").read_text(encoding="utf-8").splitlines()
    key = next(
        line.split("=", 1)[1].strip().strip('"') for line in env if line.startswith("ODDS_API_KEY=")
    )
    capture: dict[str, object] = {"captured_at": time.time()}
    for sport in SPORTS:
        url = (
            f"https://api.the-odds-api.com/v4/sports/{sport.key}/odds/?regions=eu"
            f"&markets=h2h,totals&oddsFormat=decimal&dateFormat=unix&apiKey={key}"
        )
        with urllib.request.urlopen(url, timeout=20) as reply:  # noqa: S310 (URL fija)
            capture[sport.key] = json.load(reply)
            print(f"{sport.name}: quedan {reply.headers.get('x-requests-remaining')} créditos")
    path.write_text(json.dumps(capture), encoding="utf-8")


def _fair(prices: dict[str, float]) -> dict[str, float]:
    implied = {name: 1 / price for name, price in prices.items()}
    total = sum(implied.values())
    return {name: value / total for name, value in implied.items()}


def book_chances(event: dict, extra: dict | None) -> dict[str, list[float]]:
    """Probabilidad sin margen de cada opción del bot según cada casa que la venda."""
    home, away = event["home_team"], event["away_team"]
    seen: dict[str, list[float]] = {}
    books = list(event.get("bookmakers", [])) + list((extra or {}).get("bookmakers", []))
    for book in books:
        for market in book.get("markets", []):
            outcomes = market.get("outcomes", [])
            if market["key"] == "h2h":
                names = {home: "1", "Draw": "X", away: "2"}
                fair = _fair({names[o["name"]]: o["price"] for o in outcomes if o["name"] in names})
            elif market["key"] == "totals":
                fair = {}
                for line in {o.get("point") for o in outcomes}:
                    side = {o["name"]: o["price"] for o in outcomes if o.get("point") == line}
                    if line is not None and {"Over", "Under"} <= side.keys():
                        pair = _fair(side)
                        fair[f"+{line}"], fair[f"-{line}"] = pair["Over"], pair["Under"]
            elif market["key"] == "btts":
                pair = _fair({o["name"]: o["price"] for o in outcomes})
                fair = {"si": pair.get("Yes", 0.0), "no": pair.get("No", 0.0)}
            elif market["key"] == "correct_score":
                fair = {}
                for name, p in _fair({o["name"]: o["price"] for o in outcomes}).items():
                    goals = dict(part.rsplit(":", 1) for part in name.split("|"))
                    if {home, away} <= goals.keys():
                        fair[f"{goals[home]}-{goals[away]}"] = p
            else:
                continue
            for pick, p in fair.items():
                if market_of(pick) is not None:
                    seen.setdefault(pick, []).append(p)
    return seen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("captura", type=Path)
    parser.add_argument(
        "--bajar", action="store_true", help="baja la captura antes (gasta créditos)"
    )
    parser.add_argument("--eventos", type=Path, help="captura de ambos marcan y exacto por partido")
    args = parser.parse_args()
    if args.bajar:
        download(args.captura)
    capture = json.loads(args.captura.read_text(encoding="utf-8"))
    extra = json.loads(args.eventos.read_text(encoding="utf-8")) if args.eventos else {}
    returns: dict[Market, list[float]] = {}
    fitted = total = 0
    for sport in SPORTS:
        events = {e["id"]: e for e in capture.get(sport.key, [])}
        for match in parse_odds(list(events.values()), now=capture["captured_at"]):
            total += 1
            fitted += match.goals is not None
            for pick, chances in book_chances(
                events[match.event_id], extra.get(match.event_id)
            ).items():
                odds = match.price(pick)
                market = market_of(pick)
                if odds and market is not None and chances:
                    returns.setdefault(market, []).append(statistics.mean(chances) * odds / 100)
    print(f"{total} partidos, {fitted} con mercados de goles")
    for market in Market:
        values = returns.get(market, [])
        if not values:
            print(f"{market.label}: sin datos de las casas")
            continue
        over = sum(v > 1 for v in values)
        print(
            f"{market.label} (retorno del bot {market.rtp:.0%}): {len(values)} opciones, "
            f"media {statistics.mean(values):.1%}, máximo {max(values):.1%}, "
            f"{over} por encima del 100 %"
        )


if __name__ == "__main__":
    sys.exit(main())
