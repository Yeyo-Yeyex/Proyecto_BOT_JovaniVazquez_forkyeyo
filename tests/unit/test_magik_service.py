"""Pruebas de bot.services.magik: seam carving, validación y límites."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from bot.services import image_input, magik
from bot.services.magik import (
    MAX_INPUT_BYTES,
    WORKING_MAX_SIDE,
    ImageTooLargeError,
    InvalidImageError,
    apply_magik,
)


def png_bytes(width: int, height: int, color: tuple[int, int, int] = (120, 80, 200)) -> bytes:
    """Crea un PNG liso del tamaño indicado."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def shapes_png(width: int = 96, height: int = 64) -> bytes:
    """PNG con un fondo plano y un cuadrado de borde marcado (energía alta)."""
    pixels = np.full((height, width, 3), 200, dtype=np.uint8)
    pixels[16:48, 30:66] = (220, 20, 60)
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG")
    return buffer.getvalue()


def test_la_costura_sigue_la_columna_de_menor_energia() -> None:
    """Con una única columna sin energía, la costura es esa columna en todas las filas."""
    energy = np.full((6, 5), 10.0, dtype=np.float32)
    energy[:, 2] = 0.0

    seam = magik._find_vertical_seam(energy)

    assert seam.tolist() == [2] * 6


def test_la_costura_es_continua_entre_filas() -> None:
    """Cada paso de la costura se mueve como mucho una columna."""
    rng = np.random.default_rng(0)
    energy = rng.random((30, 20)).astype(np.float32)

    seam = magik._find_vertical_seam(energy)

    assert len(seam) == 30
    assert np.abs(np.diff(seam)).max() <= 1
    assert seam.min() >= 0
    assert seam.max() < 20


def test_quitar_una_costura_reduce_el_ancho_en_un_pixel() -> None:
    """Eliminar la costura quita exactamente un píxel de ancho por fila."""
    image = np.arange(4 * 5 * 3, dtype=np.float32).reshape(4, 5, 3)
    seam = np.array([0, 1, 2, 3])

    result = magik._remove_vertical_seam(image, seam)

    assert result.shape == (4, 4, 3)
    # La fila 0 pierde la columna 0: empieza ahora por lo que era la columna 1.
    assert result[0, 0].tolist() == image[0, 1].tolist()


def test_carve_width_alcanza_el_ancho_objetivo() -> None:
    """Se eliminan costuras hasta llegar justo al ancho pedido."""
    image = np.random.default_rng(1).random((12, 30, 3)).astype(np.float32) * 255

    result = magik._carve_width(image, 18)

    assert result.shape == (12, 18, 3)


def test_el_seam_carving_conserva_los_objetos_y_sacrifica_el_fondo_plano() -> None:
    """Lo que se elimina es el fondo liso, no el objeto de bordes marcados."""
    pixels = np.full((20, 40, 3), 255.0, dtype=np.float32)
    pixels[5:15, 15:25] = (0.0, 0.0, 0.0)
    object_pixels_before = int((pixels.sum(axis=2) == 0).sum())

    result = magik._carve_width(pixels, 25)

    assert result.shape[1] == 25
    assert int((result.sum(axis=2) == 0).sum()) == object_pixels_before


def test_apply_magik_devuelve_un_png_del_tamano_de_trabajo() -> None:
    """Una imagen pequeña conserva su tamaño y el resultado es un PNG válido."""
    result = apply_magik(shapes_png(96, 64))

    image = Image.open(io.BytesIO(result))
    assert image.format == "PNG"
    assert image.size == (96, 64)


def test_apply_magik_limita_el_tamano_de_las_imagenes_grandes() -> None:
    """El resultado nunca supera el tamaño de trabajo, aunque la entrada sea mayor."""
    result = apply_magik(png_bytes(1600, 900))

    width, height = Image.open(io.BytesIO(result)).size
    assert max(width, height) == WORKING_MAX_SIDE


def test_apply_magik_deforma_realmente_la_imagen() -> None:
    """El resultado difiere de la imagen original reescalada (no es un simple resize)."""
    original = shapes_png()
    result = apply_magik(original)

    before = np.asarray(Image.open(io.BytesIO(original)).convert("RGB"), dtype=np.int16)
    after = np.asarray(Image.open(io.BytesIO(result)).convert("RGB"), dtype=np.int16)
    assert before.shape == after.shape
    assert np.abs(before - after).mean() > 1


def test_apply_magik_es_determinista() -> None:
    """La misma entrada produce siempre exactamente la misma salida."""
    data = shapes_png()

    assert apply_magik(data) == apply_magik(data)


def test_apply_magik_acepta_transparencia() -> None:
    """Las imágenes con canal alfa se procesan sobre fondo blanco."""
    buffer = io.BytesIO()
    Image.new("RGBA", (64, 64), (255, 0, 0, 0)).save(buffer, format="PNG")

    result = apply_magik(buffer.getvalue())

    assert Image.open(io.BytesIO(result)).mode == "RGB"


def test_apply_magik_usa_el_primer_fotograma_de_un_gif_animado() -> None:
    """Un GIF animado se reduce a su primer fotograma sin fallar."""
    frames = [Image.new("RGB", (48, 48), color) for color in ((255, 0, 0), (0, 255, 0))]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:])

    result = apply_magik(buffer.getvalue())

    assert Image.open(io.BytesIO(result)).size == (48, 48)


def test_un_archivo_que_no_es_imagen_se_rechaza() -> None:
    """Bytes arbitrarios producen un error controlado, no una excepción de Pillow."""
    with pytest.raises(InvalidImageError):
        apply_magik(b"esto no es una imagen")


def test_una_imagen_truncada_se_rechaza() -> None:
    """Un PNG cortado a la mitad se trata como imagen inválida."""
    data = png_bytes(200, 200)

    with pytest.raises(InvalidImageError):
        apply_magik(data[: len(data) // 2])


def test_una_imagen_demasiado_pequena_se_rechaza() -> None:
    """Las imágenes por debajo del lado mínimo no se procesan."""
    with pytest.raises(InvalidImageError):
        apply_magik(png_bytes(8, 8))


def test_un_archivo_demasiado_pesado_se_rechaza_sin_decodificarlo() -> None:
    """Se supera el límite de bytes antes de intentar abrir nada."""
    with pytest.raises(ImageTooLargeError):
        apply_magik(b"0" * (MAX_INPUT_BYTES + 1))


def test_una_imagen_con_demasiados_pixeles_se_rechaza(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las dimensiones de la cabecera se comprueban antes de decodificar los píxeles."""
    monkeypatch.setattr(image_input, "MAX_INPUT_PIXELS", 100)

    with pytest.raises(ImageTooLargeError):
        apply_magik(png_bytes(64, 64))


def test_apply_magik_respeta_la_rotacion_exif_de_las_fotos_de_movil() -> None:
    """Una foto horizontal marcada con EXIF «girar 90°» sale en vertical, como se ve."""
    pixels = np.zeros((96, 192, 3), dtype=np.uint8)
    pixels[:, :96] = (220, 30, 30)
    pixels[:, 96:] = (30, 30, 220)
    exif = Image.Exif()
    exif[0x0112] = 6
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="JPEG", exif=exif)

    width, height = Image.open(io.BytesIO(apply_magik(buffer.getvalue()))).size

    assert height > width
