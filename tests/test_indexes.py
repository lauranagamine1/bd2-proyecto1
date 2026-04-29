import os
import sys
import math

from sequential_file import SequentialFile
from r_tree import RTree
from b_tree import BPlusTree


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


# -----------------------------------------------------------------------
# B+ Tree
# -----------------------------------------------------------------------

def test_btree():
    print("=" * 50)
    print("B+ TREE")
    print("=" * 50)

    def cleanup_bt(base):
        for ext in (".bpt", ".meta"):
            if os.path.exists(base + ext):
                os.remove(base + ext)

    # --- 1. clave entera ---
    print("\n[1] key_type='int'")
    cleanup_bt("bt_test_int")
    bt = BPlusTree("bt_test_int", key_type="int")

    claves = [10, 3, 7, 1, 5, 9, 2, 8, 4, 6, 15, 12, 20, 11, 13]
    for k in claves:
        bt.add(k, f"val_{k}".encode())

    # búsqueda puntual
    r = bt.search(7)
    print(f"  search(7)  -> {r}")
    assert r == b"val_7", f"fallo search(7): {r}"

    r = bt.search(99)
    print(f"  search(99) -> {r}  (esperado None)")
    assert r is None

    # rango
    rng = bt.range_search(4, 9)
    vals = sorted(v.decode() for v in rng)
    print(f"  range(4,9) -> {vals}")
    assert set(vals) == {"val_4", "val_5", "val_6", "val_7", "val_8", "val_9"}

    # scan_all: debe devolver todas las claves insertadas
    todos = bt.scan_all()
    assert len(todos) == len(claves), f"scan_all devolvió {len(todos)}, esperado {len(claves)}"
    print(f"  scan_all   -> {len(todos)} registros OK")

    # remove: clave existente
    ok = bt.remove(7)
    print(f"  remove(7)  -> {ok}  (esperado True)")
    assert ok is True
    assert bt.search(7) is None, "clave 7 sigue presente tras remove"

    # remove: clave inexistente
    ok = bt.remove(99)
    print(f"  remove(99) -> {ok}  (esperado False)")
    assert ok is False

    # rango después de remove
    rng2 = bt.range_search(4, 9)
    vals2 = sorted(v.decode() for v in rng2)
    print(f"  range(4,9) post-remove(7) -> {vals2}")
    assert "val_7" not in vals2

    # inserción masiva para forzar varios splits
    print("\n[2] inserciones masivas (100 claves) para forzar splits")
    cleanup_bt("bt_test_mass")
    bt2 = BPlusTree("bt_test_mass", key_type="int")
    N = 100
    for k in range(N):
        bt2.add(k, f"x{k}".encode())
    todos2 = bt2.scan_all()
    assert len(todos2) == N, f"scan_all tras inserciones masivas: {len(todos2)} != {N}"
    assert bt2.search(0)  == b"x0"
    assert bt2.search(99) == b"x99"
    assert bt2.range_search(50, 59) is not None
    assert len(bt2.range_search(50, 59)) == 10
    print(f"  scan_all tras {N} inserciones -> {len(todos2)} registros OK")

    # remove masivo: eliminar todos los pares
    for k in range(0, N, 2):
        bt2.remove(k)
    restantes = bt2.scan_all()
    assert len(restantes) == N // 2, f"remove masivo: {len(restantes)} != {N // 2}"
    print(f"  remove de {N // 2} claves -> {len(restantes)} restantes OK")

    # --- 3. clave flotante ---
    print("\n[3] key_type='float'")
    cleanup_bt("bt_test_float")
    btf = BPlusTree("bt_test_float", key_type="float")
    for k in [1.5, 3.14, 2.71, 0.5, 4.0]:
        btf.add(k, f"f{k}".encode())
    assert btf.search(3.14) == b"f3.14"
    assert btf.search(9.99) is None
    rng_f = btf.range_search(1.0, 3.5)
    assert len(rng_f) == 3  # 1.5, 2.71, 3.14
    print(f"  search/range float OK")

    # --- 4. clave string ---
    print("\n[4] key_type='str'")
    cleanup_bt("bt_test_str")
    bts = BPlusTree("bt_test_str", key_type="str")
    nombres = ["lima", "arequipa", "cusco", "trujillo", "piura"]
    for n in nombres:
        bts.add(n, f"ciudad:{n}".encode())
    assert bts.search("cusco") == b"ciudad:cusco"
    assert bts.search("ica") is None
    print(f"  search string OK")

    # limpieza
    for base in ("bt_test_int", "bt_test_mass", "bt_test_float", "bt_test_str"):
        cleanup_bt(base)

    print("\n-> btree OK\n")


if __name__ == "__main__":
    test_sequential()
    test_rtree()
    test_btree()
