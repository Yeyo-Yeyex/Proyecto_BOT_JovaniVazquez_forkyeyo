"""Colores de los equipos de `/deportes` para dibujar su escudo (no hay logos).

`TEAMS` usa los nombres tal como los da The Odds API. `team_style` devuelve el dict que
espera la escena (`assets/deportes/escena.html`); un equipo que no está sale con colores
sacados de un hash estable del nombre (`hashlib`: `hash()` cambia en cada arranque).
"""

from __future__ import annotations

import colorsys
import hashlib
import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Team:
    """La abreviatura y la primera equipación de un club."""

    abbr: str
    primary: str
    secondary: str
    pattern: str = "plain"  # plain | stripes | hoops | halves | sash


def _t(abbr: str, primary: str, secondary: str, pattern: str = "plain") -> Team:
    return Team(abbr, primary, secondary, pattern)


TEAMS: dict[str, Team] = {
    # LaLiga
    "Alavés": _t("ALA", "#0a4aa6", "#ffffff", "stripes"),
    "Athletic Bilbao": _t("ATH", "#e4122e", "#ffffff", "stripes"),
    "Atlético Madrid": _t("ATM", "#cb3524", "#1d3a7a", "stripes"),
    "Barcelona": _t("BAR", "#a50044", "#004d98", "stripes"),
    "CA Osasuna": _t("OSA", "#d2122e", "#0a1d4d", "halves"),
    "Celta Vigo": _t("CEL", "#8ac3ee", "#ffffff"),
    "Deportivo La Coruña": _t("DEP", "#1f5ba8", "#ffffff", "stripes"),
    "Elche CF": _t("ELC", "#ffffff", "#0b7a3b", "sash"),
    "Espanyol": _t("ESP", "#1f6fc0", "#ffffff", "stripes"),
    "Getafe": _t("GET", "#005999", "#0b2a4d"),
    "Levante": _t("LEV", "#c8102e", "#0a2a6e", "stripes"),
    "Málaga": _t("MAL", "#6fb7e8", "#ffffff"),
    "Rayo Vallecano": _t("RAY", "#ffffff", "#e4002b", "sash"),
    "Real Betis": _t("BET", "#0b9444", "#ffffff", "stripes"),
    "Real Madrid": _t("RMA", "#ffffff", "#febe10"),
    "Real Racing Club de Santander": _t("RAC", "#0b8a46", "#ffffff"),
    "Real Sociedad": _t("RSO", "#0b61b0", "#ffffff", "stripes"),
    "Sevilla": _t("SEV", "#ffffff", "#d50a0a"),
    "Valencia": _t("VAL", "#ffffff", "#f08c00", "halves"),
    "Villarreal": _t("VIL", "#f7d117", "#0b3d7a"),
    "Girona": _t("GIR", "#d71920", "#ffffff", "stripes"),
    "Mallorca": _t("MLL", "#c8102e", "#111111"),
    "Las Palmas": _t("LPA", "#ffd200", "#0b4ea2"),
    "Real Valladolid": _t("VLL", "#5b2a86", "#ffffff"),
    "Leganés": _t("LEG", "#1c4fa0", "#ffffff", "stripes"),
    "Cádiz": _t("CAD", "#f5d000", "#0a3d91"),
    "Granada": _t("GRA", "#c8102e", "#ffffff", "halves"),
    "Almería": _t("ALM", "#d3122f", "#ffffff", "stripes"),
    "Real Oviedo": _t("OVI", "#1d4fa3", "#ffffff"),
    # Champions
    "AEK Athens": _t("AEK", "#f5d000", "#111111"),
    "AS Roma": _t("ROM", "#8e1f2f", "#f0b323"),
    "Arsenal": _t("ARS", "#e0161e", "#ffffff"),
    "Aston Villa": _t("AVL", "#7b003c", "#95bfe5"),
    "Bayern Munich": _t("BAY", "#dc052d", "#ffffff"),
    "Bodø/Glimt": _t("BOD", "#f7d117", "#111111"),
    "Borussia Dortmund": _t("BVB", "#fde100", "#111111"),
    "Club Brugge": _t("BRU", "#1d6fd1", "#111111", "halves"),
    "Como": _t("COM", "#1e5aa8", "#ffffff"),
    "Fenerbahce": _t("FEN", "#f7d117", "#12285e", "stripes"),
    "Feyenoord": _t("FEY", "#e4002b", "#ffffff", "halves"),
    "Galatasaray": _t("GAL", "#e30613", "#fdb913", "halves"),
    "Inter Milan": _t("INT", "#0a56a8", "#111111", "stripes"),
    "LASK": _t("LAS", "#e4002b", "#ffffff"),
    "Lille": _t("LIL", "#e4002b", "#12285e"),
    "Liverpool": _t("LIV", "#c8102e", "#f6eb61"),
    "Manchester City": _t("MCI", "#6cabdd", "#ffffff"),
    "Manchester United": _t("MUN", "#da291c", "#fbe122"),
    "Napoli": _t("NAP", "#1c8cdb", "#ffffff"),
    "PSV Eindhoven": _t("PSV", "#e4002b", "#ffffff", "halves"),
    "Paris Saint Germain": _t("PSG", "#0a2a5e", "#da291c", "sash"),
    "Porto": _t("POR", "#1c4fa0", "#ffffff", "stripes"),
    "RB Leipzig": _t("RBL", "#ffffff", "#dd0741"),
    "RC Lens": _t("LEN", "#f7d117", "#d6001c", "stripes"),
    "Sabah FK": _t("SAB", "#0b2a6e", "#ffffff"),
    "Shakhtar Donetsk": _t("SHA", "#f58220", "#111111"),
    "Slavia Praha": _t("SLA", "#ffffff", "#d6001c", "halves"),
    "Sporting Lisbon": _t("SCP", "#00834a", "#ffffff", "hoops"),
    "VfB Stuttgart": _t("VFB", "#ffffff", "#e32219", "sash"),
    "Viking FK": _t("VIK", "#1d4fa3", "#ffffff"),
    "ŠK Slovan Bratislava": _t("SLO", "#2a7de1", "#ffffff"),
    "Juventus": _t("JUV", "#ffffff", "#111111", "stripes"),
    "AC Milan": _t("MIL", "#d6001c", "#111111", "stripes"),
    "Atalanta": _t("ATA", "#1e71b8", "#111111", "stripes"),
    "Benfica": _t("BEN", "#e30613", "#ffffff"),
    "Bayer Leverkusen": _t("B04", "#e32219", "#111111"),
    "Eintracht Frankfurt": _t("SGE", "#e1000f", "#111111", "hoops"),
    "Chelsea": _t("CHE", "#034694", "#ffffff"),
    "Tottenham Hotspur": _t("TOT", "#ffffff", "#132257"),
    "Newcastle United": _t("NEW", "#241f20", "#ffffff", "stripes"),
    "Celtic": _t("CEL", "#0b7a3b", "#ffffff", "hoops"),
    "Ajax": _t("AJA", "#ffffff", "#d2122e", "sash"),
    "Monaco": _t("MON", "#e4002b", "#ffffff", "sash"),
    "Marseille": _t("OM", "#2faee0", "#ffffff"),
    "Olympiakos": _t("OLY", "#e30613", "#ffffff", "stripes"),
    "Red Star Belgrade": _t("CZV", "#d6001c", "#ffffff", "stripes"),
    "Dinamo Zagreb": _t("DZG", "#1c4fa0", "#ffffff"),
    "Young Boys": _t("YB", "#f7d117", "#111111"),
    "Salzburg": _t("SAL", "#e4002b", "#ffffff"),
    "Sparta Prague": _t("SPA", "#9b1b30", "#12285e"),
    "Union Saint-Gilloise": _t("USG", "#f7d117", "#12285e"),
    "Qarabağ": _t("QAR", "#111111", "#ffffff"),
    "Kairat Almaty": _t("KAI", "#f7d117", "#111111", "stripes"),
    "Copenhagen": _t("FCK", "#ffffff", "#12469b"),
    "Pafos": _t("PAF", "#1c6fd1", "#ffffff"),
}

#: Palabras que no identifican a un equipo cuando falta en la tabla.
_FILLER = {"fc", "cf", "ca", "cd", "ud", "sd", "rc", "real", "club", "sc", "fk", "sk", "as", "ac"}


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()


def _abbr(name: str) -> str:
    words = [w for w in re.findall(r"\w+", name) if w.lower() not in _FILLER] or re.findall(
        r"\w+", name
    )
    word = max(words, key=len) if words else name
    return (_plain(word)[:3] or "???").ljust(3, "?")


def _hex(hue: float, sat: float, light: float) -> str:
    r, g, b = colorsys.hls_to_rgb(hue, light, sat)
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


def team_style(name: str) -> dict[str, str]:
    """El estilo del equipo para la escena; con colores estables si no se conoce."""
    team = TEAMS.get(name)
    if team is None:
        digest = hashlib.sha256(name.encode()).digest()
        hue = (int.from_bytes(digest[:2], "big") % 360) / 360
        team = Team(_abbr(name), _hex(hue, 0.62, 0.42), _hex((hue + 0.42) % 1, 0.55, 0.88))
    return {
        "name": name,
        "abbr": team.abbr,
        "primary": team.primary,
        "secondary": team.secondary,
        "pattern": team.pattern,
    }
