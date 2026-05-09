"""
Test para GROUP BY + COUNT(*) — group by añadido
Verifica el flujo completo: scanner → parser → engine
con distintos tipos de índice: HASH, BTREE y sin índice.
"""

import os
import sys
import shutil

# Rutas al proyecto
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "parser"))
sys.path.insert(0, os.path.join(BASE_DIR, "indexes"))
sys.path.insert(0, os.path.join(BASE_DIR, "manager"))
sys.path.insert(0, os.path.join(BASE_DIR, "external"))

sys.path.insert(0, BASE_DIR)
from engine import Engine, EngineError

# ──────────────────────────────────────────────────────────────
# Utilidades
# ──────────────────────────────────────────────────────────────

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

_results: list[tuple[str, bool, str]] = []

TEST_DATA_DIR = os.path.join(BASE_DIR, "data", "test_group_by")


def check(name: str, condition: bool, detail: str = ""):
    label = PASS if condition else FAIL
    print(f"  [{label}] {name}" + (f" — {detail}" if detail else ""))
    _results.append((name, condition, detail))


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def summary():
    total  = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed
    print(f"\n{'='*60}")
    print(f"  RESUMEN: {passed}/{total} pasaron  |  {failed} fallaron")
    print(f"{'='*60}\n")
    if failed:
        print("Fallaron:")
        for name, ok, detail in _results:
            if not ok:
                print(f"  - {name}" + (f": {detail}" if detail else ""))


def make_engine():
    if os.path.exists(TEST_DATA_DIR):
        shutil.rmtree(TEST_DATA_DIR)
    return Engine(data_dir=TEST_DATA_DIR)


# ──────────────────────────────────────────────────────────────
# group by añadido - test 1: GROUP BY sin índice (full scan)
# ──────────────────────────────────────────────────────────────

def test_group_by_no_index():
    section("GROUP BY sin índice (full scan)")
    engine = make_engine()

    engine.run("""
        CREATE TABLE ventas (
            id INT,
            categoria VARCHAR(30),
            monto INT
        );
    """)
    engine.run("INSERT INTO ventas VALUES (1, 'A', 100);")
    engine.run("INSERT INTO ventas VALUES (2, 'B', 200);")
    engine.run("INSERT INTO ventas VALUES (3, 'A', 150);")
    engine.run("INSERT INTO ventas VALUES (4, 'C', 300);")
    engine.run("INSERT INTO ventas VALUES (5, 'B', 120);")
    engine.run("INSERT INTO ventas VALUES (6, 'A', 80);")

    result = engine.run("SELECT categoria, COUNT(*) FROM ventas GROUP BY categoria;")[0]

    counts = {row["categoria"]: row["COUNT(*)"] for row in result}

    check("Categoría A tiene 3 registros",  counts.get("A") == 3, str(counts))
    check("Categoría B tiene 2 registros",  counts.get("B") == 2, str(counts))
    check("Categoría C tiene 1 registro",   counts.get("C") == 1, str(counts))
    check("Resultado tiene 3 grupos",        len(result) == 3,     f"len={len(result)}")


# ──────────────────────────────────────────────────────────────
# group by añadido - test 2: GROUP BY con índice HASH en la PK
# ──────────────────────────────────────────────────────────────

def test_group_by_hash_index():
    section("GROUP BY con índice HASH")
    engine = make_engine()

    engine.run("""
        CREATE TABLE pedidos (
            id INT INDEX HASH,
            estado VARCHAR(20),
            total INT
        );
    """)
    engine.run("INSERT INTO pedidos VALUES (1, 'pendiente', 50);")
    engine.run("INSERT INTO pedidos VALUES (2, 'enviado',   80);")
    engine.run("INSERT INTO pedidos VALUES (3, 'pendiente', 30);")
    engine.run("INSERT INTO pedidos VALUES (4, 'entregado', 200);")
    engine.run("INSERT INTO pedidos VALUES (5, 'enviado',   90);")

    result = engine.run("SELECT estado, COUNT(*) FROM pedidos GROUP BY estado;")[0]

    counts = {row["estado"]: row["COUNT(*)"] for row in result}

    check("Estado pendiente tiene 2",  counts.get("pendiente") == 2, str(counts))
    check("Estado enviado tiene 2",    counts.get("enviado")   == 2, str(counts))
    check("Estado entregado tiene 1",  counts.get("entregado") == 1, str(counts))
    check("Resultado tiene 3 grupos",  len(result) == 3,             f"len={len(result)}")


# ──────────────────────────────────────────────────────────────
# group by añadido - test 3: GROUP BY con WHERE + índice BTREE
# ──────────────────────────────────────────────────────────────

def test_group_by_btree_with_where():
    section("GROUP BY con WHERE + índice BTREE (filtrado previo)")
    engine = make_engine()

    engine.run("""
        CREATE TABLE productos (
            id INT,
            tipo VARCHAR(20),
            precio INT INDEX BTREE
        );
    """)
    engine.run("INSERT INTO productos VALUES (1, 'libro',     10);")
    engine.run("INSERT INTO productos VALUES (2, 'ropa',      50);")
    engine.run("INSERT INTO productos VALUES (3, 'libro',     15);")
    engine.run("INSERT INTO productos VALUES (4, 'ropa',     120);")
    engine.run("INSERT INTO productos VALUES (5, 'electronico', 80);")
    engine.run("INSERT INTO productos VALUES (6, 'libro',     200);")
    engine.run("INSERT INTO productos VALUES (7, 'ropa',       30);")

    # Solo filas con precio entre 10 y 60, luego GROUP BY tipo
    result = engine.run(
        "SELECT tipo, COUNT(*) FROM productos WHERE precio BETWEEN 10 AND 60 GROUP BY tipo;"
    )[0]

    counts = {row["tipo"]: row["COUNT(*)"] for row in result}

    # precio 10, 15 → libro(2); precio 50, 30 → ropa(2); electronico queda fuera (80)
    check("Libros baratos (precio ≤60): 2",   counts.get("libro") == 2, str(counts))
    check("Ropa barata (precio ≤60): 2",      counts.get("ropa")  == 2, str(counts))
    check("Electrónico excluido del rango",   "electronico" not in counts, str(counts))
    check("Resultado tiene 2 grupos",          len(result) == 2,         f"len={len(result)}")


# ──────────────────────────────────────────────────────────────
# group by añadido - test 4: GROUP BY sobre SELECT *
# ──────────────────────────────────────────────────────────────

def test_group_by_select_star():
    section("GROUP BY con SELECT *")
    engine = make_engine()

    engine.run("""
        CREATE TABLE empleados (
            id INT,
            departamento VARCHAR(20),
            salario INT
        );
    """)
    engine.run("INSERT INTO empleados VALUES (1, 'IT',      3000);")
    engine.run("INSERT INTO empleados VALUES (2, 'RRHH',    2500);")
    engine.run("INSERT INTO empleados VALUES (3, 'IT',      3500);")
    engine.run("INSERT INTO empleados VALUES (4, 'RRHH',    2800);")
    engine.run("INSERT INTO empleados VALUES (5, 'Ventas',  2000);")

    result = engine.run("SELECT * FROM empleados GROUP BY departamento;")[0]

    counts = {row["departamento"]: row["COUNT(*)"] for row in result}

    check("IT tiene 2 empleados",      counts.get("IT")     == 2, str(counts))
    check("RRHH tiene 2 empleados",    counts.get("RRHH")   == 2, str(counts))
    check("Ventas tiene 1 empleado",   counts.get("Ventas") == 1, str(counts))
    check("Resultado tiene 3 grupos",  len(result) == 3,          f"len={len(result)}")


# ──────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        test_group_by_no_index()
        test_group_by_hash_index()
        test_group_by_btree_with_where()
        test_group_by_select_star()
    finally:
        if os.path.exists(TEST_DATA_DIR):
            shutil.rmtree(TEST_DATA_DIR)
        summary()
