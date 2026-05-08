import sys
import json
import os

sys.path.insert(0, "parser")
sys.path.insert(0, "indexes")
sys.path.insert(0, "manager")
sys.path.insert(0, "external")

from parser import parse_sql, ParseError
from scanner import ScannerError
from file_manager import FileManager
from buffer_manager import BufferManager
from db_manager import DBManager
from schemas import Column, DataType, IndexType, Table

class EngineError(Exception):
    pass


class Engine:
    def __init__(self, data_dir: str = "data"):
        self._data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self._fm = FileManager()
        self._bm = BufferManager(self._fm)
        self._db = DBManager(base_path=data_dir, buffer_manager=self._bm)
        self._tables: dict[str, dict] = {}
        self._schemas: dict[str, list] = {}
        self._used_merge_sort = False

    def stats(self) -> dict:
        bm = self._bm.stats
        return {
            "disk": {
                "reads":  bm["reads"],
                "writes": bm["writes"],
            },
            "buffer": {
                "hits":      bm["hits"],
                "misses":    bm["misses"],
                "pool_used": bm["pool_used"],
                "pool_size": bm["pool_size"],
            },
            "merge_sort": self._used_merge_sort,
        }

    def reset_stats(self):
        self._fm.reset_stats()
        self._bm.reset_stats()
        self._used_merge_sort = False

    def run(self, sql: str) -> list:
        try:
            nodes = parse_sql(sql)
        except (ScannerError, ParseError) as e:
            raise EngineError(f"Error de sintaxis: {e}")

        results = []
        for node in nodes:
            try:
                results.append(self._execute(node))
            except (FileNotFoundError, ValueError, RuntimeError, IndexError) as e:
                raise EngineError(str(e)) from e
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
        table = self._table_from_node(name, columns)
        inserted = self._db.create_table(table, node["file"] if node["file"] else None)

        message = f"tabla '{name}' creada"
        if node["file"]:
            message += f" ({inserted} filas cargadas)"
        return {"status": "ok", "message": message}

    def _table_from_node(self, name: str, columns: list[dict]) -> Table:
        return Table(name, [self._column_from_node(col) for col in columns])

    def _column_from_node(self, col: dict) -> Column:
        data_type = self._data_type_from_node(col["data_type"])
        index_type = self._index_type_from_node(col["index"])
        data_size = col["data_type"].get("size") if data_type == DataType.STRING else None
        return Column(col["name"], data_type, data_size=data_size, index_type=index_type)

    def _data_type_from_node(self, data_type: dict) -> DataType:
        mapping = {
            "INT": DataType.INT,
            "FLOAT": DataType.FLOAT,
            "VARCHAR": DataType.STRING,
            "BOOL": DataType.BOOL,
            "POINT": DataType.POINT,
        }
        kind = data_type.get("kind", "INT")
        if kind not in mapping:
            raise EngineError(f"Tipo de dato {kind} no implementado")
        return mapping[kind]

    def _index_type_from_node(self, index_type: str | None) -> IndexType:
        mapping = {
            None: IndexType.NONE,
            "SEQUENTIAL": IndexType.SEQFILE,
            "HASH": IndexType.EXTHASH,
            "BTREE": IndexType.BTREE,
            "RTREE": IndexType.RTREE,
        }
        if index_type not in mapping:
            raise EngineError(f"Índice {index_type} aún no implementado")
        return mapping[index_type]

    # --- INSERT ---

    def _insert(self, node: dict):
        table = node["table"]
        cols = self._get_schema_columns(table)
        values = node["values"]

        if len(values) != len(cols):
            raise EngineError(
                f"INSERT: se esperaban {len(cols)} valores, se dieron {len(values)}"
            )

        row_values = [self._value_for_db(v) for v in values]
        self._db.insert(table, row_values)
        return {"status": "ok", "message": "registro insertado"}

    # --- SELECT ---

    def _select(self, node: dict):
        table    = node["table"]
        cond     = node["condition"]
        order_by = node.get("order_by")

        if cond is None:
            rows = self._db.select_all(table)
        else:
            col = cond["column"]
            if cond["type"] == "EQUALITY":
                rows = self._db.select_equal(table, col, self._value_for_db(cond["value"]))
            elif cond["type"] == "RANGE":
                rows = self._db.select_range(
                    table, col,
                    self._value_for_db(cond["low"]),
                    self._value_for_db(cond["high"]),
                )
            elif cond["type"] == "SPATIAL_RADIUS":
                rows = self._db.select_spatial_radius(table, col, self._value_for_db(cond["point"]), cond["radius"])
            elif cond["type"] == "SPATIAL_KNN":
                rows = self._db.select_spatial_knn(table, col, self._value_for_db(cond["point"]), cond["k"])
            else:
                raise EngineError(f"Condición no implementada: {cond['type']}")

        if order_by:
            rows = self._db.sort_by(
                table, order_by["column"], order_by["direction"],
                rows, self._bm,
            )
            self._used_merge_sort = True

        return rows

    def _select_all(self, table: str) -> list:
        return self._db.select_all(table)

    # --- DELETE ---

    def _delete(self, node: dict):
        table = node["table"]
        col = node["column"]
        removed = self._db.delete_where_equal(table, col, self._value_for_db(node["value"]))

        status = f"{removed} eliminado(s)" if removed else "no encontrado"
        return {"status": "ok", "message": status}

    # --- helpers ---

    def _check_table(self, table: str):
        try:
            self._db.get_schema(table)
        except FileNotFoundError:
            raise EngineError(f"Tabla '{table}' no existe")

    def _get_index(self, table: str, col: str) -> dict:
        index = self._db.get_index(table, col)
        if index is None:
            raise EngineError(f"Columna '{col}' no tiene índice en '{table}'")
        return index

    def _get_schema_columns(self, table: str):
        try:
            return self._db.get_schema(table).columns
        except FileNotFoundError:
            raise EngineError(f"Tabla '{table}' no existe")

    def _value_for_db(self, node: dict):
        if node["type"] == "POINT":
            return (node["x"], node["y"])
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
