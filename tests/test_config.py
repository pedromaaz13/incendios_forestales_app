"""Invariantes de la configuración.

Son cosas que se comprueban a ojo al editar `config.py` y que por eso se dejan
de comprobar. Cada una de aquí abajo se escribió después de que fallara.
"""

from __future__ import annotations

from itertools import combinations

from incendios import config


def _solapan(a: str, b: str) -> bool:
    """Dos bboxes `oeste,sur,este,norte` se solapan si se cruzan en los dos ejes."""
    ao, asur, ae, an = (float(v) for v in a.split(","))
    bo, bsur, be, bn = (float(v) for v in b.split(","))
    return ao < be and bo < ae and asur < bn and bsur < an


def test_los_bboxes_de_firms_no_se_solapan():
    """Un bbox contenido en otro gasta cuota de FIRMS sin aportar ni un foco.

    Regresión: `baleares` = 1.10,38.60,4.40,40.15 estaba dentro de `peninsula`
    por completo. Cuatro de las doce peticiones —una por sensor— pedían lo que
    la otra ya traía. Nada fallaba: solo se desperdiciaba un tercio de la cuota,
    del tiempo de pipeline y de la superficie de fallo de red.
    """
    for (na, a), (nb, b) in combinations(config.AREAS.items(), 2):
        assert not _solapan(a, b), (
            f"los bboxes '{na}' y '{nb}' se solapan. Uno de los dos pedirá a "
            f"FIRMS focos que el otro ya trae, y el duplicado se descartará "
            f"aguas abajo después de haber gastado la petición."
        )


def test_los_bboxes_cubren_el_territorio_esperado():
    """Quitar un bbox por redundante no debe dejar territorio sin cubrir.

    Contrapartida del test anterior: sin esto, «no se solapan» se podría
    satisfacer borrando bboxes hasta quedarse con uno.
    """
    # (nombre, lon, lat) de puntos que tienen que caer en alguna de las áreas.
    controles = [
        ("Galicia", -8.4, 42.9),
        ("Andalucía", -5.0, 37.0),
        ("Cataluña", 2.2, 41.6),
        ("Mallorca", 3.0, 39.6),
        ("Menorca", 4.1, 40.0),
        ("Tenerife", -16.6, 28.3),
        ("La Palma", -17.9, 28.7),
    ]

    for nombre, lon, lat in controles:
        cubierto = any(
            float(bbox.split(",")[0]) <= lon <= float(bbox.split(",")[2])
            and float(bbox.split(",")[1]) <= lat <= float(bbox.split(",")[3])
            for bbox in config.AREAS.values()
        )
        assert cubierto, f"{nombre} ({lat}, {lon}) no cae en ningún bbox de FIRMS"
