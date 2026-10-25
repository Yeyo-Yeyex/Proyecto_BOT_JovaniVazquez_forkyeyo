"""Lo común a todas las pruebas."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from bot.services import credit


@pytest.fixture(autouse=True)
def _ipc_a_cero() -> Iterator[None]:
    """El IPC del crédito vive en memoria del proceso: cada prueba empieza sin él, para que lo
    financiado en una no encarezca la tienda de la siguiente."""
    credit._IPC.clear()
    yield
    credit._IPC.clear()
