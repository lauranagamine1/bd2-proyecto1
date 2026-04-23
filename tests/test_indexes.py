import os
import sys
import math

from sequential_file import SequentialFile
from r_tree import RTree


def cleanup(*paths):
    for p in paths:
        if os.path.exists(p):
            os.remove(p)


# -----------------------------------------------------------------------
# Sequential File
# -----------------------------------------------------------------------

def test_sequential():
    print("=" * 50)
    print("SEQUENTIAL FILE")
    print("=" * 50)

    cleanup("seq_test.bin", "seq_test.aux")
    sf = SequentialFile("seq_test")

    # inserta desordenado para verificar que el rebuild ordena
    for k in [5, 3, 8, 1, 9, 2, 7, 4, 6, 10, 11, 12, 13]:
        sf.add(float(k), f"dato_{k}".encode())

    sf.dump()
    print()

    # búsqueda puntual
    r = sf.search(7.0)
    print(f"search(7)   -> {r}")
    assert r == b"dato_7", "fallo search"

    r = sf.search(99.0)
    print(f"search(99)  -> {r}  (esperado None)")
    assert r is None

    # rango
    rng = sf.range_search(3.0, 6.0)
    print(f"range(3,6)  -> {[x.decode() for x in rng]}")
    assert set(rng) == {b"dato_3", b"dato_4", b"dato_5", b"dato_6"}

    # borrado lógico
    sf.remove(7.0)
    r = sf.search(7.0)
    print(f"search(7) post-remove -> {r}  (esperado None)")
    assert r is None

    print(f"\ndisk accesses: {sf.disk_accesses}")
    cleanup("seq_test.bin", "seq_test.aux")
    print("-> sequential OK\n")


# -----------------------------------------------------------------------
# R-Tree
# -----------------------------------------------------------------------

def test_rtree():
    print("=" * 50)
    print("R-TREE")
    print("=" * 50)

    cleanup("rt_test.rtree", "rt_test.rtree.meta")
    rt = RTree("rt_test")

    # puntos de Lima
    puntos = [
        (-77.03, -12.04, "Miraflores"),
        (-77.05, -12.07, "Barranco"),
        (-77.00, -12.00, "San Isidro"),
        (-77.10, -12.10, "Chorrillos"),
        (-76.90, -11.90, "La Molina"),
        (-77.20, -12.05, "Callao"),
        (-77.02, -12.15, "Villa Maria"),
        (-76.95, -12.02, "Ate"),
    ]
    for x, y, nombre in puntos:
        rt.add(x, y, nombre.encode())

    rt.dump()
    print()

    # range search: radio pequeño alrededor de Miraflores
    cx, cy, r = -77.03, -12.04, 0.05
    rs = rt.range_search(cx, cy, r)
    nombres_rs = [b.decode() for b in rs]
    print(f"range_search(centro Miraflores, r={r}) -> {nombres_rs}")
    assert "Miraflores" in nombres_rs
    assert "La Molina" not in nombres_rs   # está muy lejos

    # knn
    vecinos = rt.knn(-77.03, -12.04, 3)
    print(f"\nknn(Miraflores, k=3):")
    for dist, data in vecinos:
        print(f"  {data.decode():15s}  dist={dist:.4f}")
    assert vecinos[0][1] == b"Miraflores"  # el más cercano es él mismo

    # verifica distancias crecientes
    dists = [d for d, _ in vecinos]
    assert dists == sorted(dists), "knn no devuelve orden creciente"

    print(f"\ndisk accesses: {rt.disk_accesses}")
    cleanup("rt_test.rtree", "rt_test.rtree.meta")
    print("-> rtree OK\n")


if __name__ == "__main__":
    test_sequential()
    test_rtree()
