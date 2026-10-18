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

Uso, desde la raíz del repositorio::

    PYTHONIOENCODING=utf-8 python docs/auditoria_frases.py          # informe
    PYTHONIOENCODING=utf-8 python docs/auditoria_frases.py --check  # sale con 1 si hay avisos

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
    "motora": r"motora",
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
#: Una lista con más coletillas que esto se marca: la otra mitad, frases normales (la Biblia).
MAX_CATCHPHRASE_SHARE = 0.5
#: Listas más cortas no se miran: con dos frases, una coletilla ya es la mitad.
MIN_LIST = 3


@dataclass
class PhraseList:
    """Una lista de frases encontrada en el código."""

    where: str
    phrases: list[str] = field(default_factory=list)

    def catchphrase_share(self) -> float:
        """Qué parte de sus frases lleva alguna coletilla."""
        hits = sum(1 for p in self.phrases if catchphrases_in(p))
        return hits / len(self.phrases)


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
        # Las exclamaciones cortas («¡Eso es!») y las coletillas solas («¡Toma mangó!»)
        # pueden salir en varios juegos: no son una broma repetida.
        words = len(phrase.split())
        if words <= 3 or (words <= 4 and catchphrases_in(phrase)):
            continue
        if len(places) > 1:
            print(f"  «{phrase[:70]}» en {', '.join(sorted(places))}")
            warnings.append(f"repetida: {phrase[:40]}")

    print("\nFuentes de ironía (veces en todos los textos del bot):")
    for name, count in sorted(sources.items(), key=lambda kv: kv[1]):
        print(f"  {count:4d}  {name}")
    return warnings


def main() -> None:
    """Punto de entrada: `--check` sale con 1 si hay avisos."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="salir con 1 si hay avisos")
    args = parser.parse_args()
    warnings = report(*collect())
    if args.check and warnings:
        sys.exit(1)


if __name__ == "__main__":
    main()
