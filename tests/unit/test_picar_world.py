"""Pruebas del mundo del juego del pico (`bot.services.picar_world`)."""

from bot.services.picar_world import (
    AIR,
    BLOCK_ID,
    BLOCKS,
    CHUNK,
    COLUMNS,
    DRAGON_ROW,
    LAYERS,
    RAVINE_REGION,
    RAVINE_ROWS,
    Dimension,
    World,
    generate_chunk,
    layer_at,
    ravines,
)


def test_la_misma_semilla_da_el_mismo_mundo_en_cualquier_orden() -> None:
    a, b = World(7), World(7)
    rows = list(range(0, 300))
    first = [list(a.row(r)) for r in rows]
    second = {r: list(b.row(r)) for r in reversed(rows)}
    assert first == [second[r] for r in rows]


def test_semillas_distintas_dan_mundos_distintos() -> None:
    assert generate_chunk(1, 3) != generate_chunk(2, 3)


def test_las_capas_van_en_orden_y_cubren_hasta_el_dragon() -> None:
    tops = [layer.top for layer in LAYERS]
    assert tops == sorted(tops) and tops[0] == 0 and tops[-1] < DRAGON_ROW
    assert layer_at(-5) is LAYERS[0]
    for layer in LAYERS:
        assert layer_at(layer.top) is layer
    assert layer_at(DRAGON_ROW - 1).dimension is Dimension.END
    assert layer_at(100).dimension is Dimension.NETHER


def test_cada_capa_solo_saca_sus_bloques() -> None:
    for chunk in range(DRAGON_ROW // CHUNK):
        for i, cells in enumerate(generate_chunk(11, chunk)):
            row = chunk * CHUNK + i
            allowed = {BLOCK_ID[key] for key, _ in layer_at(row).weights} | {AIR}
            assert len(cells) == COLUMNS
            assert set(cells) <= allowed, row


def test_todo_bloque_cuesta_vida_al_golpearlo() -> None:
    # Es lo que garantiza que una partida termina (ver `picar_physics`).
    for block in BLOCKS[1:]:
        assert block.hardness >= 1 and block.resistance >= 1, block.key


def test_hay_cuevas_y_por_debajo_del_dragon_no_hay_nada() -> None:
    world = World(3)
    caves = sum(world.row(r).count(AIR) for r in range(4, 250))
    assert caves > 50
    assert all(world.row(r) == [AIR] * COLUMNS for r in range(DRAGON_ROW, DRAGON_ROW + 20))
    assert world.row(-3) == [AIR] * COLUMNS


def test_quitar_un_bloque_lo_deja_en_aire() -> None:
    world = World(5)
    row = next(r for r in range(20, 100) if world.row(r)[4] != AIR)
    world.remove(row, 4)
    assert world.block(row, 4) == AIR and world.hp(row)[4] == 0
    assert world.block(row, -1) == AIR and world.block(row, COLUMNS) == AIR


def test_los_barrancos_son_aire_aunque_crucen_trozos() -> None:
    crossing = 0
    for seed in range(40):
        world = World(seed)
        for region in range(DRAGON_ROW // RAVINE_REGION + 1):
            for ravine in ravines(seed, region):
                rows = ravine.rows()
                assert RAVINE_ROWS[0] <= len(rows) <= RAVINE_ROWS[1] or rows.stop == DRAGON_ROW
                assert rows.start >= 1 and rows.stop <= DRAGON_ROW
                assert rows.stop <= (region + 2) * RAVINE_REGION
                crossing += rows.start // CHUNK != (rows.stop - 1) // CHUNK
                for row, col in zip(rows, ravine.cols, strict=True):
                    assert 0 <= col <= COLUMNS - ravine.width
                    cells = world.row(row)[col : col + ravine.width]
                    assert cells == [AIR] * ravine.width, (seed, row)
    assert crossing > 10


def test_los_barrancos_se_mueven_de_columna_en_columna() -> None:
    for seed in range(30):
        for region in range(DRAGON_ROW // RAVINE_REGION + 1):
            for ravine in ravines(seed, region):
                steps = {b - a for a, b in zip(ravine.cols, ravine.cols[1:], strict=False)}
                assert steps <= {-1, 0, 1}
