"""Auditoría de las frases de Jovani: coletillas, repeticiones y fuentes de ironía.

Recorre `src/bot` con `ast` y junta las **listas de frases**: cada tupla o lista
de tres o más textos que el bot elige al azar (`EXPIRED_CLICK`, `CARE_LINES`…),
también las que van dentro de un diccionario. Para cada una mide:

- **Coletillas:** qué parte de sus frases lleva una de Jovani («¡Wepa!», «¡Toma
  mangó!»…). Son la voz del bot y salen a menudo, pero la Biblia pide al menos la
  mitad de frases normales: una lista por encima de `MAX_CATCHPHRASE_SHARE` se marca.
- **Repeticiones:** frases idénticas en dos listas distintas (la misma broma con
  otro nombre de variable).
- **Fuentes:** cuántas veces sale cada fuente de ironía de la Biblia en todo el
  bot, para ver cuáles se quedan cortas.
- **Por partes** (`--parte`): por lista, qué parte de las frases parece normal (sin
  tema, coletilla ni comparación; la Biblia pide la mitad), las comparaciones «como…»,
  lo boricua que no es de Jovani y la familia de temas que más sale. Sirve para
  comparar una parte antes y después de revisarla.

Uso, desde la raíz del repositorio::

    PYTHONIOENCODING=utf-8 python docs/auditoria_frases.py          # informe
    PYTHONIOENCODING=utf-8 python docs/auditoria_frases.py --check  # sale con 1 si hay avisos
    PYTHONIOENCODING=utf-8 python docs/auditoria_frases.py --parte cogs/tower  # tabla de una parte

Solo lee el código; no cambia nada. El resultado comentado está en
`docs/auditoria-frases.md`.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "bot"

#: Coletillas de Jovani (sección «Personalidad» de la Biblia), como expresiones.
CATCHPHRASES: dict[str, str] = {
    "Toma mangó": r"toma mang[oó]",
    "mango": r"want mango",
    "Quiero café": r"quiero caf[eé]",
    "Sin pulpa": r"sin pulpa",
    "jugo de china": r"jugo de china",
    "lagartijo": r"lagartijo",
    "mosca": r"ser una mosca",
    "motora": r"motora|v[ií]ctor le dijo a joel",
    "Yo soy yo": r"yo soy yo",
    "Jota Uve": r"jota uve",
    "destruido": r"estoy destruido",
    "mar azul": r"mar azul",
    "Sofía": r"sof[ií]a, c[aá]llate",
}
#: Fuentes de ironía de la Biblia y cómo se reconocen en un texto.
SOURCES: dict[str, str] = {
    "Sánchez y el PSOE": r"s[aá]nchez|sanxe|psoe|falc[oó]n|moncloa|paguita",
    "PP": r"feij[oó]o|ayuso|rajoy|b[aá]rcenas|\bpp\b|montoro",
    "Vox": r"abascal|\bvox\b|reconquista",
    "Podemos y Sumar": r"podemos|galapagar|yolanda|sumar\b|pablo iglesias",
    "Junts": r"puigdemont|waterloo|junts|amnist[ií]a",
    "Franco": r"franco\b|no-do|nodo\b|atado y bien atado|seiscientos|racionamiento",
    "Bruselas y Europa": r"bruselas|macron|boris|brexit|erdo[gğ]an|eurovisi[oó]n|tap[oó]n",
    "Trump": r"trump|arancel",
    "Musk": r"musk",
    "Clinton, Obama, Biden, Kamala": r"clinton|obama|biden|kamala",
    "Putin": r"putin|kremlin",
    "Kim Jong-un": r"\bkim\b|pyongyang|corea del norte",
    "África": r"sahel|golpe de estado|cayuco|litio",
    "Sudamérica": r"milei|motosierra|maduro|bolsonaro|lula|bukele|d[oó]lar blue",
    "Xi y China": r"\bxi\b|cr[eé]dito social|temu|aliexpress|tiktok",
    "Netanyahu (Bibi)": r"netanyahu|\bbibi\b|knesset|c[uú]pula de hierro",
    "Groypers (Nick Fuentes)": r"groyper|nick fuentes|\bpepe\b la rana",
    "Epstein": r"epstein",
    "Charlie Kirk": r"kirk|prove me wrong",
}
#: Familias de temas de la Biblia («El campo de donde coger es amplio»), para ver el reparto
#: de cada parte. La política junta todas las `SOURCES`. Las expresiones son pistas, no un
#: detector perfecto: una broma sin palabra clave cuenta como frase normal.
FAMILIES: dict[str, str] = {
    "Política": "|".join(SOURCES.values())
    + r"|congreso|senado|\bboe\b|\buco\b|\bcis\b|tezanos|moci[oó]n|esca[ñn]o|decreto|\bministr"
    r"|diputad|gobierno|elecci|presupuest|legislatura|oposici[oó]n|hacienda|irpf|rescate"
    r"|bankia|indult|investidura|mayor[ií]a absoluta|next generation|fondos europeos",
    "Cine y series": r"pel[ií]cula|\bcine\b|serie\b|netflix|hollywood|ocean'?s|rain man"
    r"|rocky|titanic"
    r"|star wars|jedi|matrix|terminator|james bond|\b007\b|casino royale|harry potter"
    r"|juego de tronos|poniente|daenerys|smaug|señor de los anillos|hobbit|sauron|simpson"
    r"|friends|breaking bad|casa de papel|aída|cuéntame|forrest gump|e\.t\.|regreso al futuro",
    "Cultura popular": r"operaci[oó]n triunfo|gran hermano|pasapalabra|un, dos, tres|chiquito"
    r"|mortadelo|eurovisi[oó]n|telecinco|la 1\b|telediario|jes[uú]s v[aá]zquez|ana rosa"
    r"|ibai|tiktok|meme|influencer|bruja aver[ií]a|club de la comedia|ikea|mercadona",
    "Cultura friki": r"anime|manga|dragon ball|9\.000|naruto|pok[eé]mon|one piece|marvel|\bdc\b"
    r"|superh[eé]roe|dungeons|dragones y mazmorras|tolkien|pac-?man|tetris|street fighter"
    r"|super mario|gta\b|halc[oó]n milenario|hal 9000|tamagotchi|highlander|doctor who",
    "Deporte": r"f[uú]tbol|\bliga\b|\bvar\b|cl[aá]sico|real madrid|bar[cç]a|bernab[eé]u|champions"
    r"|mundial|alonso|nadal|alcaraz|\bvuelta\b|tour\b|p[aá]del|gimnasio|lakers|jordan"
    r"|tyson|bruce lee|leicester|poulidor|rugby|boxeo",
    "Vida cotidiana": r"alquiler|casero|renfe|metro\b|cola\b|cuñad|abuela|suegra|vecino|whatsapp"
    r"|lunes|jefe|oficina|siesta|rebajas|black friday|playa|benidorm|wifi|m[oó]vil|bar\b"
    r"|ca[ñn]a\b|tabaco|psic[oó]log|ghosting|boda|navidad|nochevieja|reyes magos",
    "Comida": r"tortilla|paella|churro|kebab|caf[eé]|barraquito|papas|mojo|gofio|jam[oó]n|pipas"
    r"|huevo|pollo asado|cerveza|birra|vino\b|caldo",
    "Canarias": r"canari|guagua|teide|calima|el pino|ños|chacho|tenerife|gran canaria|lanzarote"
    r"|fuerteventura|el hierro|la palma|las palmas|teror|bandama",
    "Historia y mitos": r"romano|c[eé]sar|vici|napole[oó]n|waterloo de|cid\b|pirata|zeus|olimpo"
    r"|s[ií]sifo|[ií]caro|pandora|l[aá]zaro|david contra|goliat|vikingo|faraón|jeroglífico",
}
#: Lo típico de Puerto Rico que no es de Jovani y la Biblia quita (no son coletillas suyas).
BORICUA = r"\bwepa|bendito|mi amor|\bpapi\b|\bacho\b"
#: La muleta «X, como [referencia]» que la Biblia prohíbe en los nombres de logros.
COMPARISON = r",\s*como\s+(el|la|los|las|un|una|en)\b|\bcomo\s+[A-ZÁÉÍÓÚ]"
#: Por debajo de esto, una lista no cumple la mitad de frases normales.
MIN_PLAIN_SHARE = 0.5
#: Listas que ya estaban por debajo al acabar la auditoría (18/10/2026). Una lista nueva por
#: debajo de la mitad es un aviso; una de estas que mejora también, para sacarla de aquí.
KNOWN_UNDER_PLAIN = frozenset(
    {
        "cogs/allatu:HANG_LINES",
        "cogs/allatu:WIN_LINES",
        "cogs/backgammon:JOVANI_CHATTER",
        "cogs/backgammon:JOVANI_DOUBLES",
        "cogs/backgammon:SLEEP_LINES",
        "cogs/birthdays:ANNIVERSARY_LINES",
        "cogs/birthdays:BIRTHDAY_LINES",
        "cogs/blackjack:PIT_BOSS_LINES",
        "cogs/bus:WIN_LINES",
        "cogs/casino:LOSS_LINES",
        "cogs/gallos:TRAIN_LINES[Stat.ENDURANCE]",
        "cogs/hold_win:COLLECT_LINES['olimpo']",
        "cogs/hold_win:RESCUE_LINES",
        "cogs/message_stats:LEVEL_LINES",
        "cogs/timba:QUIPS",
        "services/horses:RACE_NAMES",
        "services/lootboxes:REVEAL_LINES[Rarity.MYTHIC]",
        "services/pets:GENERIC_LINES[Event.BIRTHDAY]",
        "services/pets:GENERIC_LINES[Event.IMV]",
        "services/pets_catalog:SPECIES",
        "services/pets_catalog:SPECIES[G]",
        "services/pets_catalog:SPECIES[N]",
        "services/tower:MILESTONES[3]",
    }
)
#: Dónde lo boricua es contenido y se queda (Biblia, «El contenido se queda»): el caballo
#: Wepa Boricua y su premio, las reglas de la beernight y la detección de «hablar en boricua».
BORICUA_CONTENT_MODULES = frozenset(
    {
        "cogs/beernight",
        "services/beernight_catalog",
        "services/horses",
        "services/horses_scene",
    }
)
BORICUA_CONTENT_TEXTS = frozenset(
    {
        "wepa",
        "bendito",
        "acho",
        "Habla en boricua (wepa, bendito, janguear…) en 10 mensajes.",
        "Gana el Gran Premio a ganador con Wepa Boricua.",
    }
)
#: Una lista con más coletillas que esto se marca: la otra mitad, frases normales (la Biblia).
MAX_CATCHPHRASE_SHARE = 0.5
#: Listas más cortas no se miran: con dos frases, una coletilla ya es la mitad.
MIN_LIST = 3


@dataclass
class PhraseList:
    """Una lista de frases encontrada en el código."""

    where: str
    phrases: list[str] = field(default_factory=list)

    def plain_share(self) -> float:
        """Qué parte de sus frases parece normal: sin tema, coletilla ni comparación."""
        return sum(1 for p in self.phrases if is_plain(p)) / len(self.phrases)

    def catchphrase_share(self) -> float:
        """Qué parte de sus frases lleva alguna coletilla."""
        hits = sum(1 for p in self.phrases if catchphrases_in(p))
        return hits / len(self.phrases)


def families_in(text: str) -> list[str]:
    """Las familias de temas que toca un texto."""
    return [name for name, pattern in FAMILIES.items() if re.search(pattern, text, re.I)]


def is_plain(text: str) -> bool:
    """Si una frase parece normal: sin familia de tema, coletilla, comparación ni lo boricua."""
    return not (
        families_in(text)
        or catchphrases_in(text)
        or re.search(COMPARISON, text)
        or re.search(BORICUA, text, re.I)
    )


def part_table(lists: list[PhraseList], prefixes: list[str]) -> list[str]:
    """Tabla de una parte del bot (`cogs/slots`, `services/work_catalog`…) para comparar.

    Por lista: frases, parte normal, coletillas, comparaciones, lo boricua y la familia
    de temas que más sale. Al final, el total de la parte y el reparto por familias.
    """
    chosen = [pl for pl in lists if any(pl.where.startswith(p) for p in prefixes)]
    rows = [
        f"{'frases':>6} {'normal':>6} {'colet':>5} {'como':>4} {'bori':>4}"
        "  lista · tema que más sale"
    ]
    families: Counter[str] = Counter()
    for pl in chosen:
        fams = Counter(f for p in pl.phrases for f in families_in(p))
        families += fams
        top = ", ".join(f"{f} {n}" for f, n in fams.most_common(2)) or "—"
        comparisons = sum(1 for p in pl.phrases if re.search(COMPARISON, p))
        boricua = sum(1 for p in pl.phrases if re.search(BORICUA, p, re.I))
        flag = " ⚠" if pl.plain_share() < MIN_PLAIN_SHARE else ""
        rows.append(
            f"{len(pl.phrases):6d} {pl.plain_share():6.0%} {pl.catchphrase_share():5.0%}"
            f" {comparisons:4d} {boricua:4d}  {pl.where} · {top}{flag}"
        )
    phrases = [p for pl in chosen for p in pl.phrases]
    if phrases:
        plain = sum(1 for p in phrases if is_plain(p)) / len(phrases)
        rows.append(f"\nTotal: {len(chosen)} listas, {len(phrases)} frases, {plain:.0%} normales")
        rows.append("Familias: " + ", ".join(f"{f} {n}" for f, n in families.most_common()))
    return rows


def catchphrases_in(text: str) -> list[str]:
    """Las coletillas que lleva un texto."""
    return [name for name, pattern in CATCHPHRASES.items() if re.search(pattern, text, re.I)]


def _text(node: ast.AST) -> str | None:
    """El texto de un literal (también los partidos en varias líneas y las f-string)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{…}" for v in node.values)
    return None


def _lists_in(node: ast.AST, where: str) -> list[PhraseList]:
    found: list[PhraseList] = []
    if isinstance(node, ast.Tuple | ast.List):
        texts = [_text(e) for e in node.elts]
        if len(texts) >= MIN_LIST and all(t is not None for t in texts):
            # Las frases tienen espacios; así no cuentan las listas de claves.
            phrases = [t for t in texts if t and " " in t.strip()]
            if len(phrases) >= MIN_LIST:
                found.append(PhraseList(where, phrases))
        else:
            for element in node.elts:
                found += _lists_in(element, where)
    elif isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values, strict=True):
            label = ast.unparse(key) if key is not None else "**"
            found += _lists_in(value, f"{where}[{label}]")
    elif isinstance(node, ast.Call):
        for argument in node.args:
            found += _lists_in(argument, where)
    return found


def boricua_texts(root: Path = ROOT) -> list[str]:
    """Textos del bot con lo boricua que no es de Jovani, fuera del contenido que se queda."""
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).with_suffix("").as_posix()
        if module in BORICUA_CONTENT_MODULES:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            text = _text(node)
            if text and text not in BORICUA_CONTENT_TEXTS and re.search(BORICUA, text, re.I):
                found.append(f"{module}: {text[:60]}")
    for path in sorted(root.rglob("*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("#") and re.search(BORICUA, line, re.I):
                found.append(f"{path.relative_to(root).as_posix()}: {line[:60]}")
    return found


def collect(root: Path = ROOT) -> tuple[list[PhraseList], Counter[str]]:
    """Las listas de frases de `root` y cuántas veces sale cada fuente en todo el texto."""
    lists: list[PhraseList] = []
    sources: Counter[str] = Counter()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = path.relative_to(root).with_suffix("").as_posix()
        for node in ast.walk(tree):
            if (text := _text(node)) is not None:
                for name, pattern in SOURCES.items():
                    sources[name] += len(re.findall(pattern, text, re.I))
        for node in tree.body:
            targets: list[ast.expr] = []
            value: ast.AST | None = None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            elif isinstance(node, ast.ClassDef):
                for inner in node.body:
                    if isinstance(inner, ast.Assign):
                        name = ast.unparse(inner.targets[0])
                        lists += _lists_in(inner.value, f"{module}:{node.name}.{name}")
            if value is not None and targets:
                lists += _lists_in(value, f"{module}:{ast.unparse(targets[0])}")
    return lists, sources


def report(lists: list[PhraseList], sources: Counter[str]) -> list[str]:
    """Imprime el informe y devuelve los avisos."""
    warnings: list[str] = []
    total = sum(len(pl.phrases) for pl in lists)
    print(f"Listas de frases: {len(lists)} ({total} frases)\n")

    print("Coletillas por lista (solo las que pasan del tope):")
    for pl in sorted(lists, key=lambda p: -p.catchphrase_share()):
        share = pl.catchphrase_share()
        if share > MAX_CATCHPHRASE_SHARE:
            line = f"  {share:4.0%}  {pl.where} ({len(pl.phrases)} frases)"
            print(line)
            warnings.append(f"coletillas: {pl.where}")
    used = Counter(name for pl in lists for p in pl.phrases for name in catchphrases_in(p))
    print("\nCada coletilla, en cuántas frases de lista sale:")
    for name in CATCHPHRASES:
        print(f"  {used[name]:4d}  {name}")

    print("\nFrases repetidas en listas distintas:")
    seen: dict[str, set[str]] = defaultdict(set)
    for pl in lists:
        for phrase in pl.phrases:
            seen[phrase.strip()].add(pl.where)
    for phrase, places in sorted(seen.items()):
        # Las exclamaciones cortas («¡Eso es!») y las frases de Jovani («Estoy destruido,
        # pero por fuera…») pueden salir en varios juegos: son su voz, no una broma repetida.
        words = len(phrase.split())
        if words <= 3 or (words <= 10 and catchphrases_in(phrase)):
            continue
        if len(places) > 1:
            print(f"  «{phrase[:70]}» en {', '.join(sorted(places))}")
            warnings.append(f"repetida: {phrase[:40]}")

    print("\nListas por debajo de la mitad de frases normales (nuevas o que ya mejoraron):")
    # Varias listas comparten nombre (una `SPECIES` por especie): cuenta la peor.
    worst: dict[str, float] = {}
    for pl in lists:
        worst[pl.where] = min(worst.get(pl.where, 1.0), pl.plain_share())
    for place, share in sorted(worst.items()):
        under = share < MIN_PLAIN_SHARE
        if under != (place in KNOWN_UNDER_PLAIN):
            note = "nueva por debajo" if under else "ya cumple: sácala de KNOWN_UNDER_PLAIN"
            print(f"  {share:4.0%}  {place} ({note})")
            warnings.append(f"normales: {place}")

    print("\nLo boricua que no es de Jovani (¡Wepa!, mi amor, ¡Ay, bendito!…):")
    for line in boricua_texts():
        print(f"  {line}")
        warnings.append(f"boricua: {line[:40]}")

    print("\nFuentes de ironía (veces en todos los textos del bot):")
    for name, count in sorted(sources.items(), key=lambda kv: kv[1]):
        print(f"  {count:4d}  {name}")
    return warnings


def main() -> None:
    """Punto de entrada: `--check` sale con 1 si hay avisos."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="salir con 1 si hay avisos")
    parser.add_argument(
        "--parte",
        nargs="+",
        metavar="PREFIJO",
        help="solo la tabla de esa parte (p. ej. cogs/slots cogs/blackjack), para comparar",
    )
    args = parser.parse_args()
    if args.parte:
        print("\n".join(part_table(collect()[0], args.parte)))
        return
    warnings = report(*collect())
    if args.check and warnings:
        sys.exit(1)


if __name__ == "__main__":
    main()
