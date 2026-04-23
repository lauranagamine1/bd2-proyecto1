from enum import Enum, auto
import struct

class DataType(Enum):
    INT = auto()
    FLOAT = auto()
    STRING = auto()
    BOOL = auto()
    POINT = auto()

    def __str__(self):
        return self.name


class IndexType(Enum):
    NONE = auto()
    SEQFILE = auto()
    EXTHASH = auto()
    BTREE = auto()
    RTREE = auto()

    def __str__(self):
        return self.name

class Column:
    def __init__(self,name: str, data_type: DataType, primary: bool = False, data_size: int = None, index_type: IndexType = IndexType.NONE):
        self.name = name
        self.primary = primary
        self.data_type = data_type
        self.index_type = index_type
        self.data_size = data_size

class Table:
    def __init__(self, name: str = None, columns: list[Column] = None):
        self.name = name.lower() if name else None
        self.columns = columns if columns else []

    def get_column(self, column_name: str):
        for column in self.columns:
            if column.name == column_name:
                return column
        return None

    def get_column_index(self, column_name: str):
        for index, column in enumerate(self.columns):
            if column.name == column_name:
                return index
        return -1

def calc_record_format(columns: list[Column]):
    table_format = ""
    for col in columns:
        if col.data_type == DataType.INT:
            table_format += "i"
        elif col.data_type == DataType.FLOAT:
            table_format += "d"
        elif col.data_type == DataType.STRING:
            if col.data_size is None or col.data_size <= 0:
                raise ValueError(f"STRING column '{col.name}' needs a positive data_size")
            table_format += f"{col.data_size}s"
        elif col.data_type == DataType.BOOL:
            table_format += "?"
        elif col.data_type == DataType.POINT:
            table_format += "dd"
        else:
            raise NotImplementedError(f"Type not defined: {col.data_type}")
    return table_format


class Record:
    def __init__(self, table: Table, values: list):
        self.table = table
        self.values = values
        self.id = values[0] if values else None
        self.format = calc_record_format(table.columns)
        self.size = struct.calcsize(self.format)

    def pack(self):
        packed = []
        for col, val in zip(self.table.columns, self.values):
            if col.data_type == DataType.POINT:
                packed.append(val[0])
                packed.append(val[1])
            elif col.data_type == DataType.STRING:
                packed.append(val.encode().ljust(col.data_size, b"\x00"))
            else:
                packed.append(val)
        return struct.pack(self.format, *packed)

    @classmethod
    def unpack(cls, table: Table, raw_bytes):
        table_format = calc_record_format(table.columns)
        values = list(struct.unpack(table_format, raw_bytes))
        unpacked = []
        value_pos = 0

        for col in table.columns:
            if col.data_type == DataType.STRING:
                unpacked.append(values[value_pos].decode().rstrip("\x00"))
                value_pos += 1
            elif col.data_type == DataType.POINT:
                x = round(float(values[value_pos]), 6)
                y = round(float(values[value_pos + 1]), 6)
                unpacked.append((x, y))
                value_pos += 2
            elif col.data_type == DataType.FLOAT:
                unpacked.append(round(float(values[value_pos]), 6))
                value_pos += 1
            else:
                unpacked.append(values[value_pos])
                value_pos += 1

        return cls(table, unpacked)
