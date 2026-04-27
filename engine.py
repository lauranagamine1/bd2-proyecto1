import sys
import csv
import json
import os

sys.path.insert(0, "parser")
sys.path.insert(0, "indexes")

from parser import parse_sql, ParseError
from scanner import ScannerError
from sequential_file import SequentialFile
from r_tree import RTree
from file_manager import FileManager
from buffer_manager import BufferManager


class EngineError(Exception):
    pass


class Engine:
    def __init__(self, data_dir: str = "data"):
        self._data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self._fm = FileManager()
        self._bm = BufferManager(self._fm)
        self._tables: dict[str, dict] = {}
        self._schemas: dict[str, list] = {}

    def stats(self) -> dict:
        return {
            "disk":   self._fm.stats,
            "buffer": self._bm.stats,
        }

    def reset_stats(self):
        self._fm.reset_stats()
        self._bm.reset_stats()

    def run(self, sql: str) -> list:
        try:
            nodes = parse_sql(sql)
        except (ScannerError, ParseError) as e:
            raise EngineError(f"Error de sintaxis: {e}")

        results = []
        for node in nodes:
            results.append(self._execute(node))
        return results

    def _execute(self, node: dict):
        t = node["type"]
        if t == "CREATE_TABLE":
            return self._create_table(node)
        if t == "INSERT":
            return self._insert(node)
        if t == "SELECT":
            return self._select(node)
        if t == "DELETE":
            return self._delete(node)
        raise EngineError(f"Nodo desconocido: {t}")

    # --- CREATE TABLE ---

    def _create_table(self, node: dict):
        name = node["table"]
        columns = node["columns"]

        self._schemas[name] = columns
        self._tables[name] = {}

        for col in columns:
            if col["index"] is None:
                continue
            idx_type = col["index"]
            col_name = col["name"]
            path = os.path.join(self._data_dir, f"{name}__{col_name}")

            if idx_type == "SEQUENTIAL":
                self._tables[name][col_name] = {
                    "index_type": "SEQUENTIAL",
                    "index": SequentialFile(path, self._bm),
                }
            elif idx_type == "RTREE":
                self._tables[name][col_name] = {
                    "index_type": "RTREE",
                    "index": RTree(path, self._bm),
                }
            else:
                # BTREE y HASH aún no implementados; se pueden enchufar aquí
                raise EngineError(f"Índice {idx_type} aún no implementado")

        if node["file"]:
            self._load_csv(name, node["file"])

        return {"status": "ok", "message": f"tabla '{name}' creada"}

    def _load_csv(self, table: str, filepath: str):
        if not os.path.exists(filepath):
            raise EngineError(f"Archivo no encontrado: {filepath}")
        with open(filepath, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                self._insert_row(table, row)

    # --- INSERT ---

    def _insert(self, node: dict):
        table = node["table"]
        self._check_table(table)
        cols = self._schemas[table]
        values = node["values"]

        if len(values) != len(cols):
            raise EngineError(
                f"INSERT: se esperaban {len(cols)} valores, se dieron {len(values)}"
            )

        row = {col["name"]: self._extract_value(v) for col, v in zip(cols, values)}
        self._insert_row(table, row)
        return {"status": "ok", "message": "registro insertado"}

    def _insert_row(self, table: str, row: dict):
        for col_name, meta in self._tables[table].items():
            if col_name not in row:
                continue
            idx_type = meta["index_type"]
            idx = meta["index"]
            # el payload es la fila completa serializada como JSON
            payload = json.dumps(row, ensure_ascii=False).encode()

            if idx_type == "SEQUENTIAL":
                key = float(row[col_name])
                idx.add(key, payload)
            elif idx_type == "RTREE":
                # el valor de la columna POINT viene como "x,y" o dict
                x, y = self._parse_point_value(row[col_name])
                idx.add(x, y, payload)

    # --- SELECT ---

    def _select(self, node: dict):
        table = node["table"]
        self._check_table(table)
        cond = node["condition"]

        if cond is None:
            return self._select_all(table)

        col  = cond["column"]

        meta = self._get_index(table, col)
        idx_type = meta["index_type"]
        idx = meta["index"]

        raw_results = []

        if cond["type"] == "EQUALITY":
            if idx_type != "SEQUENTIAL":
                raise EngineError("EQUALITY solo disponible con índice SEQUENTIAL")
            key = float(self._extract_value(cond["value"]))
            r = idx.search(key)
            if r:
                raw_results = [r]

        elif cond["type"] == "RANGE":
            if idx_type != "SEQUENTIAL":
                raise EngineError("BETWEEN solo disponible con índice SEQUENTIAL")
            lo = float(self._extract_value(cond["low"]))
            hi = float(self._extract_value(cond["high"]))
            raw_results = idx.range_search(lo, hi)

        elif cond["type"] == "SPATIAL_RADIUS":
            if idx_type != "RTREE":
                raise EngineError("IN ... RADIUS solo disponible con índice RTREE")
            pt = cond["point"]
            raw_results = idx.range_search(pt["x"], pt["y"], cond["radius"])

        elif cond["type"] == "SPATIAL_KNN":
            if idx_type != "RTREE":
                raise EngineError("IN ... K solo disponible con índice RTREE")
            pt = cond["point"]
            results = []
            for dist, data in idx.knn(pt["x"], pt["y"], cond["k"]):
                record = json.loads(data.decode())
                record["_distance"] = round(dist, 4)
                results.append(record)
            return results

        return [json.loads(r.decode()) for r in raw_results]

    def _select_all(self, table: str) -> list:
        if not self._tables[table]:
            raise EngineError(f"La tabla '{table}' no tiene índices para hacer scan")
        col_name = next(iter(self._tables[table]))
        idx = self._tables[table][col_name]["index"]
        return [json.loads(r.decode()) for r in idx.scan_all()]

    # --- DELETE ---

    def _delete(self, node: dict):
        table = node["table"]
        self._check_table(table)
        col = node["column"]

        meta = self._get_index(table, col)
        idx_type = meta["index_type"]
        idx = meta["index"]

        if idx_type == "SEQUENTIAL":
            key = float(self._extract_value(node["value"]))
            removed = idx.remove(key)
        elif idx_type == "RTREE":
            x, y = self._parse_point_value(node["value"])
            removed = idx.remove(x, y)
        else:
            raise EngineError(f"DELETE no implementado para índice {idx_type}")

        status = "eliminado" if removed else "no encontrado"
        return {"status": "ok", "message": status}

    # --- helpers ---

    def _check_table(self, table: str):
        if table not in self._tables:
            raise EngineError(f"Tabla '{table}' no existe")

    def _get_index(self, table: str, col: str) -> dict:
        if col not in self._tables[table]:
            raise EngineError(f"Columna '{col}' no tiene índice en '{table}'")
        return self._tables[table][col]

    def _extract_value(self, node: dict):
        if node["type"] == "POINT":
            return node  # lo interpreta _parse_point_value
        return node["value"]

    def _parse_point_value(self, val) -> tuple[float, float]:
        if isinstance(val, dict) and val.get("type") == "POINT":
            return val["x"], val["y"]
        if isinstance(val, str) and "," in val:
            x, y = val.split(",")
            return float(x), float(y)
        raise EngineError(f"No se puede interpretar como punto: {val!r}")


# --- CLI ---

def _run_file(path: str, engine: Engine):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    try:
        results = engine.run(src)
        for r in results:
            print(json.dumps(r, indent=2, ensure_ascii=False))
    except EngineError as e:
        print(f"ERROR: {e}")


def _run_interactive(engine: Engine):
    print("Mini SQL engine. Escribe consultas (exit para salir).")
    buf = []
    while True:
        try:
            line = input("sql> ")
        except EOFError:
            break
        if line.strip().lower() == "exit":
            break
        buf.append(line)
        if line.rstrip().endswith(";"):
            sql = " ".join(buf)
            buf = []
            try:
                results = engine.run(sql)
                for r in results:
                    print(json.dumps(r, indent=2, ensure_ascii=False))
            except EngineError as e:
                print(f"ERROR: {e}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", help="archivo .sql a ejecutar")
    args = ap.parse_args()

    engine = Engine()
    if args.file:
        _run_file(args.file, engine)
    else:
        _run_interactive(engine)
