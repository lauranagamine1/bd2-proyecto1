import csv
import os
import pickle
from record_file import RecordFile
from schemas import DataType, IndexType, Record, Table

"""
- create_table: crea metadata + RecordFile + placeholders de índices
- insert: convierte tipos y agrega registros
- select_all: permite escanear toda la tabla
- select_equal: prueba search puntual
- select_range: prueba search por rango
- read_record: lee un registro por record_id
- delete_where_equal: borrado lógico
- load_csv: carga registros desde archivo
- convert_value: transforma strings del CSV a tipos reales
"""

class DBManager:
    def __init__(self, base_path="data"):
        self.base_path = base_path
        self.tables_path = os.path.join(base_path, "tables")
        self.indexes = {}
        os.makedirs(self.tables_path, exist_ok=True)

    def create_table(self, table: Table, csv_path=None, max_rows=None):
        self.validate_table(table)
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

    def get_schema(self, table_name: str) -> Table:
        metadata_path = os.path.join(self.get_table_path(table_name), "metadata.dat")
        if not os.path.exists(metadata_path):
            raise FileNotFoundError(f"Table '{table_name}' does not exist")
        with open(metadata_path, "rb") as file:
            return pickle.load(file)

    def create_record_file(self, table: Table):
        RecordFile(table, self.get_record_path(table.name))

    def get_record_file(self, table_name: str) -> RecordFile:
        table = self.get_schema(table_name)
        return RecordFile(table, self.get_record_path(table.name))

    def create_indexes(self, table: Table):
        for column in table.columns:
            if column.index_type != IndexType.NONE:
                index_key = self.get_index_key(table.name, column.name)
                self.indexes[index_key] = None

    def get_index(self, table_name: str, column_name: str):
        return self.indexes.get(self.get_index_key(table_name, column_name))

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
        return record_id

    def insert_into_indexes(self, table: Table, record: Record, record_id: int):
        for value, column in zip(record.values, table.columns):
            if column.index_type == IndexType.NONE:
                continue

            index_key = self.get_index_key(table.name, column.name)
            index = self.indexes.get(index_key)
            if index is not None:
                index.add(value, record_id)

    def load_csv(self, table_name: str, csv_path: str, delimiter=";", max_rows=None):
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"CSV not found at route {csv_path}")

        table = self.get_schema(table_name)
        with open(csv_path, "r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file, delimiter=delimiter)
            
            inserted = 0
            for row_number, row in enumerate(reader, start=2):
                if not row or all((value or "").strip() == "" for value in row.values()):
                    continue
                
                try:
                    values = [row[column.name] for column in table.columns]
                    self.insert(table.name, values)
                    inserted += 1
                    if max_rows is not None and inserted >= max_rows:
                        break
                except Exception as error:
                    raise RuntimeError(f"Error loading CSV row {row_number}: {error}") from error

        return inserted

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
            results = []
            for record_id in index.search(value):
                try:
                    record = record_file.read(record_id)
                    results.append(self._record_to_dict(table, record))
                except ValueError:
                    continue
            return results

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
            for record_id in index.range_search(low, high):
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
            column.name: value
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
