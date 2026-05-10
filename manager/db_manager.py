import csv
import os
import pickle
import shutil
import sys
import tempfile
from pathlib import Path
from record_file import RecordFile
from schemas import Column, DataType, IndexType, Record, Table

BASE_DIR = Path(__file__).resolve().parent.parent
INDEXES_DIR = BASE_DIR / "indexes"
EXTERNAL_DIR = BASE_DIR / "external"
for _d in (INDEXES_DIR, EXTERNAL_DIR):
    if str(_d) not in sys.path:
        sys.path.append(str(_d))

from b_tree import BPlusTree
from extendible_hashing import ExtendibleHash
from r_tree import RTree
from sequential_file import SequentialFile
from external_sort import ExternalSort
from external_hashing import ExternalHashing
from replacement_selection import ReplacementSelection

class DBManager:
    def __init__(self, base_path="data", buffer_manager=None):
        self.base_path = base_path
        self.tables_path = os.path.join(base_path, "tables")
        self.indexes = {}
        self._schema_cache: dict = {}
        self._record_file_cache: dict = {}
        self.buffer_manager = buffer_manager
        self.last_load_report = {"inserted": 0, "skipped": 0, "errors": []}
        os.makedirs(self.tables_path, exist_ok=True)

    def create_table(self, table: Table, csv_path=None, max_rows=None):
        self.validate_table(table)
        self.reset_table_storage(table.name)
        self.save_schema(table)
        self.create_record_file(table)
        self.create_indexes(table)

        if csv_path:
            return self.load_csv(table.name, csv_path, max_rows=max_rows)
        return 0

    def validate_table(self, table: Table):
        if not table.name:
            raise ValueError("Table needs a name")
        if not table.columns:
            raise ValueError("Table needs at least one column")

        seen = set()
        primary_keys = 0
        for column in table.columns:
            if column.name in seen:
                raise ValueError(f"Duplicated column: {column.name}")
            seen.add(column.name)

            if column.primary:
                primary_keys += 1

            if column.data_type == DataType.STRING:
                if column.data_size is None or column.data_size <= 0:
                    raise ValueError(f"STRING column '{column.name}' needs a positive data_size")
            if column.data_type == DataType.POINT and column.index_type not in (IndexType.NONE, IndexType.RTREE):
                raise ValueError("POINT columns can only use RTREE indexes")
            if column.index_type == IndexType.RTREE and column.data_type != DataType.POINT:
                raise ValueError("RTREE indexes can only be used with POINT columns")

        if primary_keys > 1:
            raise ValueError("Table cannot have more than one primary key")

    def save_schema(self, table: Table):
        table_path = self.get_table_path(table.name)
        os.makedirs(table_path, exist_ok=True)
        with open(os.path.join(table_path, "metadata.dat"), "wb") as file:
            pickle.dump(table, file)
        self._schema_cache[table.name.lower()] = table

    def get_schema(self, table_name: str) -> Table:
        key = table_name.lower()
        if key in self._schema_cache:
            return self._schema_cache[key]
        metadata_path = os.path.join(self.get_table_path(table_name), "metadata.dat")
        if not os.path.exists(metadata_path):
            raise FileNotFoundError(f"Table '{table_name}' does not exist")
        with open(metadata_path, "rb") as file:
            table = pickle.load(file)
        self._schema_cache[key] = table
        return table

    def create_record_file(self, table: Table):
        RecordFile(table, self.get_record_path(table.name), self.buffer_manager)

    def get_record_file(self, table_name: str) -> RecordFile:
        key = table_name.lower()
        if key not in self._record_file_cache:
            table = self.get_schema(table_name)
            self._record_file_cache[key] = RecordFile(table, self.get_record_path(table.name), self.buffer_manager)
        return self._record_file_cache[key]

    def create_indexes(self, table: Table):
        for column in table.columns:
            if column.index_type != IndexType.NONE:
                index_key = self.get_index_key(table.name, column.name)
                self.indexes[index_key] = self.build_index(table.name, column)

    def get_index(self, table_name: str, column_name: str):
        index_key = self.get_index_key(table_name, column_name)
        index = self.indexes.get(index_key)
        if index is not None:
            return index

        table = self.get_schema(table_name)
        column = table.get_column(column_name)
        if column is None or column.index_type == IndexType.NONE:
            return None

        index = self.build_index(table.name, column)
        self.indexes[index_key] = index
        return index

    def build_index(self, table_name: str, column):
        os.makedirs(os.path.dirname(self.get_index_path(table_name, column.name)), exist_ok=True)
        if column.index_type == IndexType.EXTHASH:
            return ExtendibleHash(self.get_hash_index_path(table_name, column.name), self.buffer_manager, fb=24)
        if column.index_type == IndexType.SEQFILE:
            return SequentialFile(self.get_index_path(table_name, column.name), self.buffer_manager)
        if column.index_type == IndexType.BTREE:
            return BPlusTree(
                self.get_index_path(table_name, column.name),
                self.buffer_manager,
                key_type=self.get_btree_key_type(column.data_type),
            )
        if column.index_type == IndexType.RTREE:
            return RTree(self.get_index_path(table_name, column.name), self.buffer_manager)
        return None

    def insert(self, table_name: str, values: list):
        table = self.get_schema(table_name)
        if len(values) != len(table.columns):
            raise ValueError("The number of values does not match the table schema")

        converted_values = [
            self.convert_value(value, column.data_type)
            for value, column in zip(values, table.columns)
        ]

        self._validate_primary_key(table, converted_values)

        record = Record(table, converted_values)
        record_file = self.get_record_file(table.name)
        record_id = record_file.add(record)
        self.insert_into_indexes(table, record, record_id)
        self.flush_indexes(table)
        return record_id

    def insert_into_indexes(self, table: Table, record: Record, record_id: int):
        for value, column in zip(record.values, table.columns):
            if column.index_type == IndexType.NONE:
                continue

            index_key = self.get_index_key(table.name, column.name)
            index = self.indexes.get(index_key)
            if index is not None:
                if column.index_type == IndexType.EXTHASH:
                    index.insert(value, record_id)
                elif column.index_type == IndexType.SEQFILE:
                    index.add(float(value), self.pack_record_id(record_id))
                elif column.index_type == IndexType.BTREE:
                    index.add(value, self.pack_record_id(record_id))
                elif column.index_type == IndexType.RTREE:
                    x, y = value
                    index.add(float(x), float(y), self.pack_record_id(record_id))

    def flush_indexes(self, table: Table):
        for column in table.columns:
            index = self.get_index(table.name, column.name)
            if index is not None and hasattr(index, "flush"):
                index.flush()

    def load_csv(self, table_name: str, csv_path: str, delimiter=";", max_rows=None):
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"CSV not found at route {csv_path}")

        table = self.get_schema(table_name)
        inserted = 0
        skipped = 0
        errors = []

        with open(csv_path, "r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file, delimiter=delimiter)
            record_file = self.get_record_file(table.name)
            seen_primary_values = self.existing_primary_values(table)
            records_file = record_file._fh

            next_record_id = record_file._read_next_record_id(records_file)

            for row_number, row in enumerate(reader, start=2):
                if not row or all((value or "").strip() == "" for value in row.values()):
                    continue

                try:
                    values = [self.csv_value_for_column(row, column) for column in table.columns]
                    converted_values = [
                        self.convert_value(value, column.data_type)
                        for value, column in zip(values, table.columns)
                    ]
                    self._validate_primary_key_values(table, converted_values, seen_primary_values)
                    record = Record(table, converted_values)
                    record_id = next_record_id
                    records_file.seek(record_file._record_offset(record_id))
                    records_file.write(record_file._pack_slot(record))
                    next_record_id += 1
                    self.insert_into_indexes(table, record, record_id)
                    inserted += 1
                    if max_rows is not None and inserted >= max_rows:
                        break
                except Exception as error:
                    skipped += 1
                    if len(errors) < 5:
                        errors.append(f"fila {row_number}: {error}")

            record_file._write_next_record_id(records_file, next_record_id)
            records_file.flush()

        self.last_load_report = {
            "inserted": inserted,
            "skipped": skipped,
            "errors": errors,
        }
        self.flush_indexes(table)
        return inserted

    def csv_value_for_column(self, row: dict, column: Column):
        value = row.get(column.name, "")

        if column.data_type == DataType.POINT and (value is None or str(value).strip() == ""):
            fallback = self.point_from_lat_long_columns(row)
            if fallback is not None:
                return fallback

        return value

    @staticmethod
    def point_from_lat_long_columns(row: dict):
        pairs = [
            ("lat", "long"),
            ("lat", "lng"),
            ("latitude", "longitude"),
            ("x", "y"),
        ]
        lower_row = {str(key).lower(): value for key, value in row.items()}
        for x_key, y_key in pairs:
            x = lower_row.get(x_key)
            y = lower_row.get(y_key)
            if x is None or y is None:
                continue
            if str(x).strip() == "" or str(y).strip() == "":
                continue
            return f"{x},{y}"
        return None

    def select_all(self, table_name: str) -> list[dict]:
        table = self.get_schema(table_name)
        record_file = self.get_record_file(table.name)
        return [
            self._record_to_dict(table, record)
            for _, record in record_file.scan_all()
        ]

    def select_equal(self, table_name: str, column_name: str, value) -> list[dict]:
        table = self.get_schema(table_name)
        column = self._require_column(table, column_name)
        value = self.convert_value(value, column.data_type)

        index = self.get_index(table.name, column.name)
        if index is not None and hasattr(index, "search"):
            record_file = self.get_record_file(table.name)
            if column.index_type == IndexType.EXTHASH:
                record_id = index.search(value)
                if not column.primary:
                    return self._scan_equal(table, column, value)
                if record_id is None:
                    return []
                record = record_file.read(record_id)
                return [self._record_to_dict(table, record)]

            if column.index_type == IndexType.SEQFILE and hasattr(index, "range_search"):
                raw_ids = index.range_search(float(value), float(value))
            elif column.index_type == IndexType.BTREE and hasattr(index, "range_search"):
                raw_ids = index.range_search(value, value)
            elif column.index_type == IndexType.SEQFILE:
                raw_ids = [index.search(float(value))]
            elif column.index_type == IndexType.BTREE:
                raw_ids = [index.search(value)]
            else:
                raw_ids = index.search(value)

            results = []
            for raw_id in raw_ids:
                if raw_id is None:
                    continue
                record_id = self.unpack_record_id(raw_id)
                try:
                    record = record_file.read(record_id)
                    results.append(self._record_to_dict(table, record))
                except ValueError:
                    continue
            return results

        return self._scan_equal(table, column, value)

    def _scan_equal(self, table: Table, column: Column, value) -> list[dict]:
        column_pos = table.get_column_index(column.name)
        results = []
        for _, record in self.get_record_file(table.name).scan_all():
            if record.values[column_pos] == value:
                results.append(self._record_to_dict(table, record))
        return results

    def select_range(self, table_name: str, column_name: str, low, high) -> list[dict]:
        table = self.get_schema(table_name)
        column = self._require_column(table, column_name)
        low = self.convert_value(low, column.data_type)
        high = self.convert_value(high, column.data_type)

        index = self.get_index(table.name, column.name)
        if index is not None and hasattr(index, "range_search"):
            record_file = self.get_record_file(table.name)
            results = []
            if column.index_type == IndexType.SEQFILE:
                raw_ids = index.range_search(float(low), float(high))
            elif column.index_type == IndexType.BTREE:
                raw_ids = index.range_search(low, high)
            else:
                raw_ids = index.range_search(low, high)

            for raw_id in raw_ids:
                record_id = self.unpack_record_id(raw_id)
                try:
                    record = record_file.read(record_id)
                    results.append(self._record_to_dict(table, record))
                except ValueError:
                    continue
            return results

        column_pos = table.get_column_index(column.name)
        results = []
        for _, record in self.get_record_file(table.name).scan_all():
            value = record.values[column_pos]
            if low <= value <= high:
                results.append(self._record_to_dict(table, record))
        return results

    def delete_where_equal(self, table_name: str, column_name: str, value) -> int:
        table = self.get_schema(table_name)
        column = self._require_column(table, column_name)
        value = self.convert_value(value, column.data_type)
        record_file = self.get_record_file(table.name)
        column_pos = table.get_column_index(column.name)

        deleted = 0
        for record_id, record in record_file.scan_all():
            if record.values[column_pos] == value:
                if record_file.delete(record_id):
                    self.delete_from_indexes(table, record, record_id)
                    deleted += 1
        if deleted:
            self.flush_indexes(table)
        return deleted

    def delete_from_indexes(self, table: Table, record: Record, record_id: int):
        for value, column in zip(record.values, table.columns):
            if column.index_type == IndexType.NONE:
                continue

            index = self.get_index(table.name, column.name)
            if index is None:
                continue

            if hasattr(index, "remove"):
                try:
                    if column.index_type == IndexType.RTREE:
                        x, y = value
                        index.remove(float(x), float(y), self.pack_record_id(record_id))
                    elif column.index_type == IndexType.SEQFILE:
                        index.remove(float(value))
                    elif column.index_type == IndexType.EXTHASH:
                        index.remove(value)
                    else:
                        index.remove(value, record_id)
                except TypeError:
                    index.remove(value)

    def convert_value(self, raw_value, data_type: DataType):
        if not isinstance(raw_value, str):
            return raw_value

        raw_value = raw_value.strip()
        if raw_value == "" and data_type == DataType.STRING:
            return ""
        if raw_value == "":
            raise ValueError(f"Empty value cannot be converted to {data_type}")

        if data_type == DataType.INT:
            return int(raw_value)
        if data_type == DataType.FLOAT:
            return float(raw_value)
        if data_type == DataType.STRING:
            return raw_value
        if data_type == DataType.BOOL:
            return raw_value.lower() in ("true", "1", "yes", "verified")
        if data_type == DataType.POINT:
            normalized = raw_value.removeprefix("POINT(").removesuffix(")")
            if "," not in normalized:
                raise ValueError(f"Invalid POINT value: {raw_value}")
            x, y = normalized.split(",", 1)
            return (float(x), float(y))

        raise ValueError(f"Unsupported type: {data_type}")

    def get_table_path(self, table_name: str):
        return os.path.join(self.tables_path, table_name.lower())

    def get_record_path(self, table_name: str):
        return os.path.join(self.get_table_path(table_name), "records.dat")

    def get_index_key(self, table_name: str, column_name: str):
        return f"{table_name.lower()}.{column_name}"

    def get_hash_index_path(self, table_name: str, column_name: str):
        return os.path.join(self.base_path, "indexes", table_name.lower(), column_name)

    def get_index_path(self, table_name: str, column_name: str):
        return os.path.join(self.base_path, "indexes", table_name.lower(), column_name)

    def read_record(self, table_name, record_id):
        table = self.get_schema(table_name)
        record_file = self.get_record_file(table.name)
        record = record_file.read(record_id)
        return self._record_to_dict(table, record)

    def _require_column(self, table: Table, column_name: str):
        column = table.get_column(column_name)
        if column is None:
            raise ValueError(f"Column '{column_name}' does not exist in table '{table.name}'")
        return column

    def _record_to_dict(self, table: Table, record: Record):
        return {
            column.name: self.format_output_value(column, value)
            for column, value in zip(table.columns, record.values)
        }

    def _validate_primary_key(self, table: Table, values: list):
        primary_columns = [column for column in table.columns if column.primary]
        if not primary_columns:
            return

        primary_column = primary_columns[0]
        primary_index = table.get_column_index(primary_column.name)
        primary_value = values[primary_index]

        for _, record in self.get_record_file(table.name).scan_all():
            if record.values[primary_index] == primary_value:
                raise ValueError(
                    f"Duplicated primary key '{primary_value}' for column '{primary_column.name}'"
                )

    def existing_primary_values(self, table: Table) -> set | None:
        primary_columns = [column for column in table.columns if column.primary]
        if not primary_columns:
            return None

        primary_column = primary_columns[0]
        primary_index = table.get_column_index(primary_column.name)
        return {
            record.values[primary_index]
            for _, record in self.get_record_file(table.name).scan_all()
        }

    def _validate_primary_key_values(self, table: Table, values: list, seen_primary_values: set | None):
        if seen_primary_values is None:
            return

        primary_column = [column for column in table.columns if column.primary][0]
        primary_index = table.get_column_index(primary_column.name)
        primary_value = values[primary_index]
        if primary_value in seen_primary_values:
            raise ValueError(
                f"Duplicated primary key '{primary_value}' for column '{primary_column.name}'"
            )
        seen_primary_values.add(primary_value)

    def reset_table_storage(self, table_name: str):
        table_path = self.get_table_path(table_name)
        index_path = os.path.join(self.base_path, "indexes", table_name.lower())

        self.invalidate_buffer_prefix(table_path)
        self.invalidate_buffer_prefix(index_path)

        # Cerrar el archivo de registros si está abierto para evitar WinError 32
        rf = self._record_file_cache.pop(table_name.lower(), None)
        if rf:
            rf.close()

        if os.path.exists(table_path):
            shutil.rmtree(table_path)
        if os.path.exists(index_path):
            # Cerrar los índices que puedan estar abiertos
            prefix = table_name.lower() + "."
            keys_to_remove = [k for k in self.indexes.keys() if k.startswith(prefix)]
            for k in keys_to_remove:
                idx = self.indexes.pop(k)
                if hasattr(idx, "close"):
                    idx.close()
            shutil.rmtree(index_path)

        prefix = f"{table_name.lower()}."
        for key in list(self.indexes):
            if key.startswith(prefix):
                del self.indexes[key]
        self._schema_cache.pop(table_name.lower(), None)
        self._record_file_cache.pop(table_name.lower(), None)

    @staticmethod
    def format_output_value(column: Column, value):
        if column.data_type == DataType.POINT:
            x, y = value
            return {"type": "POINT", "x": x, "y": y}
        return value

    def invalidate_buffer_prefix(self, path_prefix: str):
        if self.buffer_manager is None:
            return

        normalized_prefix = os.path.normpath(path_prefix)
        with self.buffer_manager._pool_lock:
            keys = [
                key for key in self.buffer_manager._pool
                if os.path.normpath(key[0]).startswith(normalized_prefix)
            ]
            for key in keys:
                del self.buffer_manager._pool[key]

    def select_spatial_radius(self, table_name: str, column_name: str, point, radius: float) -> list[dict]:
        table = self.get_schema(table_name)
        column = self._require_column(table, column_name)
        if column.data_type != DataType.POINT:
            raise ValueError(f"Column '{column_name}' is not POINT")

        cx, cy = self.convert_value(point, DataType.POINT)
        index = self.get_index(table.name, column.name)
        record_file = self.get_record_file(table.name)
        results = []

        if index is not None and hasattr(index, "range_search"):
            raw_ids = index.range_search(cx, cy, float(radius))
            for raw_id in raw_ids:
                try:
                    record = record_file.read(self.unpack_record_id(raw_id))
                    results.append(self._record_to_dict(table, record))
                except ValueError:
                    continue
            return results

        column_pos = table.get_column_index(column.name)
        for _, record in record_file.scan_all():
            x, y = record.values[column_pos]
            if ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 <= radius:
                results.append(self._record_to_dict(table, record))
        return results

    def select_spatial_knn(self, table_name: str, column_name: str, point, k: int) -> list[dict]:
        table = self.get_schema(table_name)
        column = self._require_column(table, column_name)
        if column.data_type != DataType.POINT:
            raise ValueError(f"Column '{column_name}' is not POINT")

        cx, cy = self.convert_value(point, DataType.POINT)
        index = self.get_index(table.name, column.name)
        record_file = self.get_record_file(table.name)
        results = []

        if index is not None and hasattr(index, "knn"):
            for distance, raw_id in index.knn(cx, cy, int(k)):
                try:
                    row = self._record_to_dict(table, record_file.read(self.unpack_record_id(raw_id)))
                    row["_distance"] = round(distance, 4)
                    results.append(row)
                except ValueError:
                    continue
            return results

        column_pos = table.get_column_index(column.name)
        scored = []
        for _, record in record_file.scan_all():
            x, y = record.values[column_pos]
            distance = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            scored.append((distance, record))

        for distance, record in sorted(scored, key=lambda item: item[0])[:int(k)]:
            row = self._record_to_dict(table, record)
            row["_distance"] = round(distance, 4)
            results.append(row)
        return results

    def sort_by(self, table_name: str, column_name: str, direction: str,
                rows: list[dict], file_manager, algorithm: str = "merge") -> list[dict]:
        if not rows:
            return rows

        print(f"[{'ReplacementSelection' if algorithm == 'replacement' else 'ExternalSort'}] Ordenando {len(rows)} registros por '{column_name}' {direction}", flush=True)

        table  = self.get_schema(table_name)
        column = self._require_column(table, column_name)

        # calcula el offset y formato de la clave dentro del registro empaquetado
        key_offset = 0
        import struct as _struct
        from schemas import calc_record_format
        for col in table.columns:
            if col.name == column_name:
                break
            fmt = calc_record_format([col])
            key_offset += _struct.calcsize(fmt)

        key_fmt = {
            DataType.INT:    "i",
            DataType.FLOAT:  "d",
            DataType.BOOL:   "?",
        }.get(column.data_type)

        if column.data_type == DataType.STRING:
            key_fmt = f"{column.data_size}s"
        elif column.data_type == DataType.POINT:
            key_fmt = "d"  # ordena por coordenada x

        record_size = _struct.calcsize(calc_record_format(table.columns))

        import tempfile
        tmp_in  = tempfile.mktemp(suffix=".sort.bin")
        tmp_out = tempfile.mktemp(suffix=".sort.bin")

        try:
            # escribe los rows al archivo temporal en formato binario
            from schemas import Record as _Record
            records_bin = [
                _Record(table, [row[col.name] if col.data_type != DataType.POINT
                                else (row[col.name]["x"], row[col.name]["y"])
                                for col in table.columns]).pack()
                for row in rows
            ]

            PAGE_SIZE = 4096
            rpp = PAGE_SIZE // record_size
            open(tmp_in, "wb").close()
            page_id = 0
            for i in range(0, len(records_bin), rpp):
                batch = records_bin[i: i + rpp]
                page = bytearray(PAGE_SIZE)
                for j, rec in enumerate(batch):
                    page[j * record_size: (j + 1) * record_size] = rec
                file_manager.write_page(tmp_in, page_id, bytes(page))
                page_id += 1

            if algorithm == "replacement":
                rs = ReplacementSelection(file_manager, record_size, key_offset, key_fmt)
                # ReplacementSelection genera runs (fase 1), ExternalSort hace el merge final (fase 2).
                
                # Necesitamos un directorio para los runs
                run_dir = tempfile.mkdtemp(suffix=".runs")
                rs_stats = rs.generate_runs(tmp_in, run_dir, buffer_pages=8)
                
                # Ahora hacemos el merge de los runs generados por RS
                ms = ExternalSort(file_manager, record_size, key_offset, key_fmt, buffer_pages=8)
                ms._phase2_merge(rs_stats["run_paths"], tmp_out)
                
                # Limpiar run_dir
                if os.path.exists(run_dir):
                    import shutil
                    shutil.rmtree(run_dir)
            else:
                ms = ExternalSort(file_manager, record_size, key_offset, key_fmt, buffer_pages=8)
                ms.sort(tmp_in, tmp_out)

            # lee el resultado ordenado
            from schemas import Record as _Record
            sorted_rows = []
            total_pages = file_manager.page_count(tmp_out)
            for pid in range(total_pages):
                page = file_manager.read_page(tmp_out, pid)
                for slot in range(rpp):
                    offset = slot * record_size
                    raw = page[offset: offset + record_size]
                    if raw == b"\x00" * record_size:
                        break
                    rec = _Record.unpack(table, raw)
                    sorted_rows.append(self._record_to_dict(table, rec))

            if direction == "DESC":
                sorted_rows.reverse()
            return sorted_rows
        finally:
            for p in (tmp_in, tmp_out):
                if os.path.exists(p):
                    os.remove(p)

    def _scan_rows(self, table):
        record_file = self.get_record_file(table.name)

        for _, record in record_file.scan_all():
            yield self._record_to_dict(table, record)

    def _count_rows(self, table):
        count = 0
        record_file = self.get_record_file(table.name)

        for _, _ in record_file.scan_all():
            count += 1

        return count
    
    def external_hashing_join(self, left_table_name: str, right_table_name: str, left_column_name: str,
                              right_column_name: str) -> list[dict]:

        left_table = self.get_schema(left_table_name)
        right_table = self.get_schema(right_table_name)

        left_column = self._require_column(left_table, left_column_name)
        right_column = self._require_column(right_table, right_column_name)

        if left_column.data_type != right_column.data_type:
            raise ValueError(f"No se puede hacer JOIN entre tipos distintos: " 
                             f"{left_column.data_type} y {right_column.data_type}" )

        left_count = self._count_rows(left_table)
        right_count = self._count_rows(right_table)

        if left_count <= right_count:
            return self._external_hashing_join(
                build_rows=self._scan_rows(left_table),
                probe_rows=self._scan_rows(right_table),
                build_key=left_column.name,
                probe_key=right_column.name,
                build_name=left_table.name,
                probe_name=right_table.name,
                build_is_left=True,
                build_size=left_count,
            )

        return self._external_hashing_join(
            build_rows=self._scan_rows(right_table),
            probe_rows=self._scan_rows(left_table),
            build_key=right_column.name,
            probe_key=left_column.name,
            build_name=right_table.name,
            probe_name=left_table.name,
            build_is_left=False,
            build_size=right_count,
        )

    def _external_hashing_join(self, build_rows, probe_rows, build_key, probe_key,
                               build_name, probe_name, build_is_left, build_size):
        external_hash = ExternalHashing(
            bucket_count=max(1, build_size * 2),
            base_dir=self._external_hashing_temp_dir(),
            buffer_manager=self.buffer_manager,
            cleanup_on_close=False,
        )
        try:
            external_hash.build(build_rows, build_key)

            rows = []
            for probe_row in probe_rows:
                matches = external_hash.search(probe_row[probe_key])
                for build_row in matches:
                    if build_is_left:
                        rows.append(self._join_rows(build_row, probe_row, build_name, probe_name))
                    else:
                        rows.append(self._join_rows(probe_row, build_row, probe_name, build_name))
            return rows
        finally:
            external_hash.close()

    def _external_hashing_temp_dir(self):
        base_dir = os.path.join(self.base_path, "external_hashing")
        os.makedirs(base_dir, exist_ok=True)
        return tempfile.mkdtemp(prefix="join_", dir=base_dir)

    @staticmethod
    def _join_rows(left_row, right_row, left_name, right_name):
        joined = {}

        for key, value in left_row.items():
            joined[f"{left_name}.{key}"] = value

        for key, value in right_row.items():
            joined[f"{right_name}.{key}"] = value

        return joined

    @staticmethod
    def pack_record_id(record_id: int) -> bytes:
        return str(record_id).encode("ascii")

    @staticmethod
    def unpack_record_id(raw_id) -> int:
        if isinstance(raw_id, int):
            return raw_id
        return int(bytes(raw_id).decode("ascii"))

    @staticmethod
    def get_btree_key_type(data_type: DataType) -> str:
        if data_type == DataType.FLOAT:
            return "float"
        if data_type == DataType.STRING:
            return "str"
        return "int"
