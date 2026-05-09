import os
import struct
from schemas import Record, Table, calc_record_format


PAGE_SIZE = 4096

class RecordFile:
    HEADER_FORMAT = "i"
    SLOT_HEADER_FORMAT = "?"

    def __init__(self, table: Table, path: str, buffer_manager=None):
        self.buffer_manager = buffer_manager
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
        self._fh = open(path, "r+b")

    def add(self, record: Record) -> int:
        if self.buffer_manager:
            self.buffer_manager.lock_page(self.path, -1, "exclusive")
        try:
            file = self._fh
            record_id = self._read_next_record_id(file)
            page_id = record_id // self.records_per_page
            if self.buffer_manager:
                self.buffer_manager.lock_page(self.path, page_id, "exclusive")
            try:
                file.seek(self._record_offset(record_id))
                file.write(self._pack_slot(record))
                file.flush()
                self._write_next_record_id(file, record_id + 1)
                file.flush()
            finally:
                if self.buffer_manager:
                    self.buffer_manager.unlock_page(self.path, page_id, "exclusive")
        finally:
            if self.buffer_manager:
                self.buffer_manager.unlock_page(self.path, -1, "exclusive")
        return record_id

    def read(self, record_id: int) -> Record:
        deleted, record = self.read_slot(record_id)
        if deleted:
            raise ValueError(f"record_id {record_id} was deleted")
        return record

    def read_slot(self, record_id: int) -> tuple[bool, Record]:
        if record_id < 0:
            raise ValueError("record_id must be non-negative")

        page_id = record_id // self.records_per_page
        if self.buffer_manager:
            self.buffer_manager.lock_page(self.path, -1, "shared")
            self.buffer_manager.lock_page(self.path, page_id, "shared")
        try:
            file = self._fh
            next_record_id = self._read_next_record_id(file)
            if record_id >= next_record_id:
                raise IndexError(f"record_id {record_id} does not exist")
            file.seek(self._record_offset(record_id))
            raw_slot = file.read(self.record_size)
        finally:
            if self.buffer_manager:
                self.buffer_manager.unlock_page(self.path, page_id, "shared")
                self.buffer_manager.unlock_page(self.path, -1, "shared")

        if len(raw_slot) != self.record_size:
            raise IndexError(f"record_id {record_id} does not exist")
        return self._unpack_slot(raw_slot)

    def delete(self, record_id: int) -> bool:
        page_id = record_id // self.records_per_page
        if self.buffer_manager:
            self.buffer_manager.lock_page(self.path, -1, "shared")
            self.buffer_manager.lock_page(self.path, page_id, "exclusive")
        try:
            file = self._fh
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
            file.flush()
            return True
        finally:
            if self.buffer_manager:
                self.buffer_manager.unlock_page(self.path, page_id, "exclusive")
                self.buffer_manager.unlock_page(self.path, -1, "shared")

    def scan_all(self, include_deleted: bool = False) -> list[tuple[int, Record]]:
        results = []
        if self.buffer_manager:
            self.buffer_manager.lock_page(self.path, -1, "shared")
        try:
            file = self._fh
            next_record_id = self._read_next_record_id(file)
            locked_pages = set()
            try:
                for record_id in range(next_record_id):
                    page_id = record_id // self.records_per_page
                    if self.buffer_manager and page_id not in locked_pages:
                        self.buffer_manager.lock_page(self.path, page_id, "shared")
                        locked_pages.add(page_id)
                    file.seek(self._record_offset(record_id))
                    raw_slot = file.read(self.record_size)
                    if len(raw_slot) != self.record_size:
                        break
                    deleted, record = self._unpack_slot(raw_slot)
                    if not deleted or include_deleted:
                        results.append((record_id, record))
            finally:
                if self.buffer_manager:
                    for page_id in locked_pages:
                        self.buffer_manager.unlock_page(self.path, page_id, "shared")
        finally:
            if self.buffer_manager:
                self.buffer_manager.unlock_page(self.path, -1, "shared")
        return results

    def record_count(self) -> int:
        return self._read_next_record_id(self._fh)

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

    def close(self):
        if hasattr(self, "_fh") and self._fh:
            self._fh.close()
            self._fh = None
