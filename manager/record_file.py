import os
import struct
from schemas import Record, Table, calc_record_format


PAGE_SIZE = 4096

"""
- __init__: prepara el archivo físico y calcula layout/paginación
- add: inserta registros y devuelve record_id
- read: recupera un registro válido por su record_id
- read_slot: permite ver si el slot está borrado
- record_count: sirve para saber cuántos ids se han reservado
- scan_all: permite recorrer registros para scans y reconstrucción de índices
- delete: borrado lógico sin alterar offsets existentes
"""

class RecordFile:
    HEADER_FORMAT = "i"
    SLOT_HEADER_FORMAT = "?"

    def __init__(self, table: Table, path: str):
        self.table = table
        self.path = path
        self.data_format = calc_record_format(table.columns)
        self.data_size = struct.calcsize(self.data_format)
        self.slot_header_size = struct.calcsize(self.SLOT_HEADER_FORMAT)
        self.record_size = self.slot_header_size + self.data_size

        if self.record_size > PAGE_SIZE:
            raise ValueError("Record size cannot be larger than page size")

        self.records_per_page = PAGE_SIZE // self.record_size
        if self.records_per_page == 0:
            raise ValueError("Page size is too small for this record layout")

        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "wb") as file:
                file.write(struct.pack(self.HEADER_FORMAT, 0))
                file.write(b"\x00" * (PAGE_SIZE - struct.calcsize(self.HEADER_FORMAT)))

    def add(self, record: Record) -> int:
        with open(self.path, "r+b") as file:
            record_id = self._read_next_record_id(file)
            file.seek(self._record_offset(record_id))
            file.write(self._pack_slot(record))
            self._write_next_record_id(file, record_id + 1)
            return record_id

    def read(self, record_id: int) -> Record:
        deleted, record = self.read_slot(record_id)
        if deleted:
            raise ValueError(f"record_id {record_id} was deleted")
        return record

    def read_slot(self, record_id: int) -> tuple[bool, Record]:
        if record_id < 0:
            raise ValueError("record_id must be non-negative")

        with open(self.path, "rb") as file:
            next_record_id = self._read_next_record_id(file)
            if record_id >= next_record_id:
                raise IndexError(f"record_id {record_id} does not exist")

            file.seek(self._record_offset(record_id))
            raw_slot = file.read(self.record_size)

        if len(raw_slot) != self.record_size:
            raise IndexError(f"record_id {record_id} does not exist")

        return self._unpack_slot(raw_slot)

    def delete(self, record_id: int) -> bool:
        with open(self.path, "r+b") as file:
            next_record_id = self._read_next_record_id(file)
            if record_id < 0 or record_id >= next_record_id:
                return False

            offset = self._record_offset(record_id)
            file.seek(offset)
            raw_slot = file.read(self.record_size)
            if len(raw_slot) != self.record_size:
                return False

            deleted, _ = self._unpack_slot(raw_slot)
            if deleted:
                return False

            file.seek(offset)
            file.write(struct.pack(self.SLOT_HEADER_FORMAT, True))
            return True

    def scan_all(self, include_deleted: bool = False) -> list[tuple[int, Record]]:
        results = []
        with open(self.path, "rb") as file:
            next_record_id = self._read_next_record_id(file)

            for record_id in range(next_record_id):
                file.seek(self._record_offset(record_id))
                raw_slot = file.read(self.record_size)
                if len(raw_slot) != self.record_size:
                    break

                deleted, record = self._unpack_slot(raw_slot)
                if not deleted or include_deleted:
                    results.append((record_id, record))

        return results

    def record_count(self) -> int:
        with open(self.path, "rb") as file:
            return self._read_next_record_id(file)

    def _record_offset(self, record_id: int):
        page_id = record_id // self.records_per_page
        slot_id = record_id % self.records_per_page
        return PAGE_SIZE + (page_id * PAGE_SIZE) + (slot_id * self.record_size)

    def _read_next_record_id(self, file):
        file.seek(0)
        raw_header = file.read(struct.calcsize(self.HEADER_FORMAT))
        return struct.unpack(self.HEADER_FORMAT, raw_header)[0]

    def _write_next_record_id(self, file, next_record_id: int):
        file.seek(0)
        file.write(struct.pack(self.HEADER_FORMAT, next_record_id))

    def _pack_slot(self, record: Record) -> bytes:
        return struct.pack(self.SLOT_HEADER_FORMAT, False) + record.pack()

    def _unpack_slot(self, raw_slot: bytes) -> tuple[bool, Record]:
        deleted = struct.unpack(
            self.SLOT_HEADER_FORMAT,
            raw_slot[:self.slot_header_size],
        )[0]
        record = Record.unpack(self.table, raw_slot[self.slot_header_size:])
        return deleted, record
