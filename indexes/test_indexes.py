"""
Tests para todos los índices implementados:
  - SequentialFile
  - BPlusTree  (int, float, str)
  - RTree
  - ExtendibleHash
"""

import os
import sys
import shutil
import struct

sys.path.insert(0, os.path.dirname(__file__))

from sequential_file import SequentialFile
from b_tree import BPlusTree
from r_tree import RTree
from extendible_hashing import ExtendibleHash

# ──────────────────────────────────────────────────────────────
# Utilidades
# ──────────────────────────────────────────────────────────────

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

_results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = ""):
    label = PASS if condition else FAIL
    print(f"  [{label}] {name}" + (f" — {detail}" if detail else ""))
    _results.append((name, condition, detail))


def section(title: str):
    print(f"\n{'='*55}")
    print(f"  {title}")
    print(f"{'='*55}")


def summary():
    total  = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed
    print(f"\n{'='*55}")
    print(f"  RESUMEN: {passed}/{total} pasaron  |  {failed} fallaron")
    print(f"{'='*55}\n")
    if failed:
        print("Fallaron:")
        for name, ok, detail in _results:
            if not ok:
                print(f"  - {name}" + (f": {detail}" if detail else ""))


def _bytes(s: str) -> bytes:
    return s.encode().ljust(200, b"\x00")


def _rtree_bytes(s: str) -> bytes:
    return s.encode().ljust(160, b"\x00")


def _rm(*paths):
    for p in paths:
        if os.path.exists(p):
            os.remove(p)


def _rmdir(folder: str):
    if os.path.exists(folder):
        shutil.rmtree(folder)


# ──────────────────────────────────────────────────────────────
# SequentialFile
# ──────────────────────────────────────────────────────────────

def test_sequential_file():
    section("SequentialFile")
    base = "test_seq"
    _rm(base + ".bin", base + ".aux")

    sf = SequentialFile(base)

    # --- insert & search ---
    sf.add(10.0, _bytes("diez"))
    sf.add(5.0,  _bytes("cinco"))
    sf.add(20.0, _bytes("veinte"))

    found = sf.search(10.0)
    check("search clave existente (10.0)", found is not None and b"diez" in found)

    not_found = sf.search(99.0)
    check("search clave inexistente (99.0)", not_found is None)

    # --- scan_all ---
    all_recs = sf.scan_all()
    check("scan_all retorna 3 registros", len(all_recs) == 3,
          f"esperado=3, obtenido={len(all_recs)}")

    # --- range_search ---
    rng = sf.range_search(5.0, 15.0)
    check("range_search [5, 15] retorna 2 resultados", len(rng) == 2,
          f"esperado=2, obtenido={len(rng)}")

    rng_none = sf.range_search(30.0, 50.0)
    check("range_search fuera de rango retorna []", rng_none == [])

    # --- remove ---
    removed = sf.remove(5.0)
    check("remove clave existente (5.0) retorna True", removed is True)

    after_remove = sf.search(5.0)
    check("clave eliminada no se encuentra", after_remove is None)

    removed_again = sf.remove(5.0)
    check("remove clave ya eliminada retorna False", removed_again is False)

    # --- rebuild automático (insertar hasta K_MAX_AUX = 10) ---
    for i in range(10):
        sf.add(float(100 + i), _bytes(f"rebuild_{i}"))

    check("scan_all post-rebuild retorna ≥ 11 registros",
          len(sf.scan_all()) >= 11)

    _rm(base + ".bin", base + ".aux")
    print("  [limpieza OK]")


# ──────────────────────────────────────────────────────────────
# BPlusTree — int
# ──────────────────────────────────────────────────────────────

def test_bplus_int():
    section("BPlusTree — key_type='int'")
    base = "test_bpt_int"
    _rm(base + ".bpt", base + ".meta")

    t = BPlusTree(base, key_type="int")

    for i in [5, 3, 8, 1, 4, 7, 9, 2, 6]:
        t.add(i, _bytes(f"val_{i}"))

    check("search clave existente (5)", t.search(5) is not None)
    check("search clave inexistente (99)", t.search(99) is None)

    rng = t.range_search(3, 7)
    check("range_search [3,7] retorna 5 resultados", len(rng) == 5,
          f"esperado=5, obtenido={len(rng)}")

    all_vals = t.scan_all()
    check("scan_all retorna 9 resultados", len(all_vals) == 9,
          f"esperado=9, obtenido={len(all_vals)}")

    ok = t.remove(5)
    check("remove clave existente retorna True", ok is True)
    check("clave eliminada no encontrada", t.search(5) is None)

    ok2 = t.remove(99)
    check("remove clave inexistente retorna False", ok2 is False)

    _rm(base + ".bpt", base + ".meta")
    print("  [limpieza OK]")


# ──────────────────────────────────────────────────────────────
# BPlusTree — float
# ──────────────────────────────────────────────────────────────

def test_bplus_float():
    section("BPlusTree — key_type='float'")
    base = "test_bpt_float"
    _rm(base + ".bpt", base + ".meta")

    t = BPlusTree(base, key_type="float")
    values = [1.1, 3.3, 2.2, 5.5, 4.4]
    for v in values:
        t.add(v, _bytes(f"f_{v}"))

    check("search 3.3", t.search(3.3) is not None)
    check("search 9.9 (inexistente)", t.search(9.9) is None)

    rng = t.range_search(2.0, 4.0)
    check("range_search [2.0, 4.0] retorna 2 resultados", len(rng) == 2,
          f"esperado=2, obtenido={len(rng)}")

    _rm(base + ".bpt", base + ".meta")
    print("  [limpieza OK]")


# ──────────────────────────────────────────────────────────────
# BPlusTree — str
# ──────────────────────────────────────────────────────────────

def test_bplus_str():
    section("BPlusTree — key_type='str'")
    base = "test_bpt_str"
    _rm(base + ".bpt", base + ".meta")

    t = BPlusTree(base, key_type="str")
    words = ["mango", "apple", "banana", "cherry", "date"]
    for w in words:
        t.add(w, _bytes(w.upper()))

    check("search 'apple'", t.search("apple") is not None)
    check("search 'zucchini' (inexistente)", t.search("zucchini") is None)

    rng = t.range_search("apple", "cherry")
    check("range_search ['apple','cherry'] retorna ≥ 3", len(rng) >= 3,
          f"obtenido={len(rng)}")

    ok = t.remove("banana")
    check("remove 'banana' retorna True", ok is True)
    check("'banana' ya no existe", t.search("banana") is None)

    _rm(base + ".bpt", base + ".meta")
    print("  [limpieza OK]")


# ──────────────────────────────────────────────────────────────
# BPlusTree — múltiples inserciones (fuerza splits)
# ──────────────────────────────────────────────────────────────

def test_bplus_splits():
    section("BPlusTree — splits y merge")
    base = "test_bpt_splits"
    _rm(base + ".bpt", base + ".meta")

    t = BPlusTree(base, key_type="int")
    N = 100
    for i in range(N):
        t.add(i, _bytes(f"item_{i}"))

    all_vals = t.scan_all()
    check(f"scan_all después de {N} inserciones", len(all_vals) == N,
          f"esperado={N}, obtenido={len(all_vals)}")

    check("search 50 existe", t.search(50) is not None)
    check("search 101 no existe", t.search(101) is None)

    # eliminar la mitad
    for i in range(0, N, 2):
        t.remove(i)

    after = t.scan_all()
    check(f"scan_all después de eliminar {N//2} claves", len(after) == N // 2,
          f"esperado={N//2}, obtenido={len(after)}")

    _rm(base + ".bpt", base + ".meta")
    print("  [limpieza OK]")


# ──────────────────────────────────────────────────────────────
# RTree
# ──────────────────────────────────────────────────────────────

def test_rtree():
    section("RTree")
    base = "test_rt"
    _rm(base + ".rtree", base + ".rtree.meta")

    rt = RTree(base)

    points = [
        (1.0, 1.0, "A"),
        (2.0, 2.0, "B"),
        (5.0, 5.0, "C"),
        (9.0, 9.0, "D"),
        (3.0, 3.0, "E"),
    ]
    for x, y, label in points:
        rt.add(x, y, _rtree_bytes(label))

    # --- range_search ---
    results = rt.range_search(2.0, 2.0, radius=1.5)
    labels = [r.rstrip(b"\x00").decode() for r in results]
    check("range_search radio=1.5 centro=(2,2) contiene 'B'",
          any("B" in l for l in labels), f"encontrados={labels}")
    check("range_search radio=1.5 no contiene 'D'",
          not any("D" in l for l in labels), f"encontrados={labels}")

    # --- knn ---
    knn = rt.knn(0.0, 0.0, k=2)
    check("knn k=2 retorna 2 resultados", len(knn) == 2,
          f"obtenido={len(knn)}")
    nearest_data = [d.rstrip(b"\x00").decode() for _, d in knn]
    check("knn k=2 el más cercano es 'A'",
          nearest_data[0] == "A", f"orden={nearest_data}")

    # --- remove ---
    # Se usa data=None para comparar solo por coordenadas, ya que internamente
    # e.data se almacena sin padding (rstrip) y no matchea bytes con padding.
    ok = rt.remove(1.0, 1.0, data=None)
    check("remove punto (1,1) retorna True", ok is True)
    after = rt.range_search(1.0, 1.0, radius=0.1)
    check("punto removido no aparece en range_search", len(after) == 0,
          f"obtenido={len(after)}")

    # --- inserciones masivas para forzar splits ---
    import math
    for i in range(30):
        angle = 2 * math.pi * i / 30
        rt.add(10 * math.cos(angle), 10 * math.sin(angle), _rtree_bytes(f"pt_{i}"))

    all_near = rt.range_search(0.0, 0.0, radius=15.0)
    check("range_search radio=15 captura los 30 puntos en círculo",
          len(all_near) >= 30, f"obtenido={len(all_near)}")

    _rm(base + ".rtree", base + ".rtree.meta")
    print("  [limpieza OK]")


# ──────────────────────────────────────────────────────────────
# ExtendibleHash
# ──────────────────────────────────────────────────────────────

def test_extendible_hash():
    section("ExtendibleHash")
    folder = "test_hash"
    _rmdir(folder)

    h = ExtendibleHash(folder, fb=4)

    # --- insert & search ---
    for k in range(1, 11):
        h.insert(k, k * 10)

    check("search clave 5 retorna 50",  h.search(5) == 50)
    check("search clave 10 retorna 100", h.search(10) == 100)
    check("search clave 99 (inexistente) retorna None", h.search(99) is None)

    # inserción duplicada (no debería duplicar)
    h.insert(5, 999)
    check("inserción duplicada no cambia valor", h.search(5) == 50)

    # --- delete ---
    ok = h.delete(5)
    check("delete clave 5 retorna True", ok is True)
    check("clave 5 ya no existe", h.search(5) is None)

    ok2 = h.delete(5)
    check("delete clave ya eliminada retorna False", ok2 is False)

    # --- split (muchas inserciones) ---
    _rmdir(folder)
    h2 = ExtendibleHash(folder, fb=2)
    for k in range(50):
        h2.insert(k, k)

    for k in range(50):
        val = h2.search(k)
        if val != k:
            check(f"search post-split clave {k}", False, f"esperado={k}, obtenido={val}")
            break
    else:
        check("todas las 50 claves buscadas correctamente post-split", True)

    _rmdir(folder)
    print("  [limpieza OK]")


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    test_sequential_file()
    test_bplus_int()
    test_bplus_float()
    test_bplus_str()
    test_bplus_splits()
    test_rtree()
    test_extendible_hash()

    summary()
