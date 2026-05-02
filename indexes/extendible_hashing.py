import os
import struct

from file_manager import FileManager, PAGE_SIZE
from buffer_manager import BufferManager

DEFAULT_FB = 4
MAX_GLOBAL_DEPTH = 20

INDEX_HEADER_FORMAT = "iiiii" # global_depth, directory_size, free_head, bucket_count, fb
INDEX_HEADER_SIZE = struct.calcsize(INDEX_HEADER_FORMAT)

DIRECTORY_ENTRY_FORMAT = "ii" # page_id, slot_id
DIRECTORY_ENTRY_SIZE = struct.calcsize(DIRECTORY_ENTRY_FORMAT)
DIRECTORY_ENTRIES_PER_PAGE = PAGE_SIZE // DIRECTORY_ENTRY_SIZE


class Bucket:
    def __init__(self, local_depth:int, fb:int, keys=None, values=None, count:int=0, next_bucket:int=-1, free_list:int=-1):
        self.local_depth = local_depth
        self.count = count
        self.keys = keys if keys is not None else [-1] * fb
        self.values = values if values is not None else [-1] * fb
        self.next = next_bucket
        self.free_list = free_list


class ExtendibleHash:
    def __init__(self, folder="hash_files", use_buffer=True, fb=DEFAULT_FB):
        self.folder = folder
        self.index_path = os.path.join(folder, "index.dat")
        self.data_path = os.path.join(folder, "data.dat")
        self.fb = fb

        os.makedirs(folder, exist_ok=True)

        self.fm = FileManager()

        if use_buffer:
            self.bm = BufferManager(self.fm)
        else:
            self.bm = None

        self._configure_layout(self.fb)
        self._ensure_files()

    def _configure_layout(self, fb):
        self.fb = fb
        self.bucket_format = "iiii" + "ii" * self.fb
        self.bucket_size = struct.calcsize(self.bucket_format)
        self.buckets_per_page = PAGE_SIZE // self.bucket_size

    def _build_empty_bucket(self, local_depth):
        return Bucket(local_depth=local_depth, fb=self.fb)

    def _read_page(self, path, page_id):
        if self.bm:
            return self.bm.read_page(path, page_id)
        return self.fm.read_page(path, page_id)

    def _write_page(self, path, page_id, data):
        if self.bm:
            self.bm.write_page(path, page_id, data)
        else:
            self.fm.write_page(path, page_id, data)

    def flush(self):
        if self.bm:
            self.bm.flush_all()

    def _pack_bucket(self, b:Bucket):
        packed = [b.local_depth, b.count]

        for i in range(self.fb):
            packed.append(b.keys[i])
            packed.append(b.values[i])

        packed.append(b.next)
        packed.append(b.free_list)

        return struct.pack(self.bucket_format, *packed)

    def _unpack_bucket(self, data):
        unpacked = struct.unpack(self.bucket_format, data[:self.bucket_size])
        keys = []
        values = []
        pos = 2

        for _ in range(self.fb):
            keys.append(unpacked[pos])
            values.append(unpacked[pos + 1])
            pos += 2

        return Bucket(unpacked[0], self.fb, keys, values, unpacked[1], unpacked[pos], unpacked[pos + 1])

    def _ensure_files(self):
        self.fm.ensure_file(self.index_path)
        self.fm.ensure_file(self.data_path)

        if self.fm.page_count(self.index_path) == 0:
            self._init_hash()
        else:
            self._validate_layout()

    def _validate_layout(self):
        raw = self._read_page(self.index_path, 0)
        stored_fb = struct.unpack(INDEX_HEADER_FORMAT, raw[:INDEX_HEADER_SIZE])[4]
        if stored_fb != self.fb:
            raise ValueError(
                f"ExtendibleHash at '{self.folder}' was created with fb={stored_fb}, not fb={self.fb}"
            )

    def _init_hash(self):
        b0 = self._build_empty_bucket(local_depth=1)
        b1 = self._build_empty_bucket(local_depth=1)

        self._write_bucket(0, b0)
        self._write_bucket(1, b1)

        directory = [self._bucket_ref(0), self._bucket_ref(1)]
        self._write_directory(global_depth=1, directory=directory, free_head=-1, bucket_count=2)

    def _read_directory(self):
        raw = self._read_page(self.index_path, 0)

        global_depth, dir_size, free_head, bucket_count, stored_fb = struct.unpack(
            INDEX_HEADER_FORMAT,
            raw[:INDEX_HEADER_SIZE]
        )

        if stored_fb != self.fb:
            raise ValueError(
                f"ExtendibleHash at '{self.folder}' was created with fb={stored_fb}, not fb={self.fb}"
            )

        directory = []
        remaining = dir_size
        page_id = 1

        while remaining > 0:
            page = self._read_page(self.index_path, page_id)
            pos = 0
            entries_to_read = min(remaining, DIRECTORY_ENTRIES_PER_PAGE)

            for _ in range(entries_to_read):
                bucket_ref = struct.unpack(
                    DIRECTORY_ENTRY_FORMAT,
                    page[pos:pos + DIRECTORY_ENTRY_SIZE]
                )
                directory.append(bucket_ref)
                pos += DIRECTORY_ENTRY_SIZE

            remaining -= entries_to_read
            page_id += 1

        return global_depth, directory, free_head, bucket_count

    def _write_directory(self, global_depth, directory, free_head, bucket_count):
        header_page = bytearray(PAGE_SIZE)
        header = struct.pack(INDEX_HEADER_FORMAT, global_depth, len(directory), free_head, bucket_count, self.fb)
        header_page[0:INDEX_HEADER_SIZE] = header
        self._write_page(self.index_path, 0, bytes(header_page))

        remaining = len(directory)
        page_id = 1
        pos = 0

        while remaining > 0:
            page = bytearray(PAGE_SIZE)
            page_pos = 0
            entries_to_write = min(remaining, DIRECTORY_ENTRIES_PER_PAGE)

            for _ in range(entries_to_write):
                page[page_pos:page_pos + DIRECTORY_ENTRY_SIZE] = struct.pack(
                    DIRECTORY_ENTRY_FORMAT,
                    directory[pos][0],
                    directory[pos][1]
                )
                pos += 1
                page_pos += DIRECTORY_ENTRY_SIZE

            self._write_page(self.index_path, page_id, bytes(page))
            remaining -= entries_to_write
            page_id += 1

    def _get_dir_idx(self, key, global_depth):
        mask = (1 << global_depth) - 1
        return key & mask

    def _get_bucket_id(self, key):
        global_depth, directory, free_head, bucket_count = self._read_directory()
        dir_idx = self._get_dir_idx(key, global_depth)
        return self._ref_to_bucket_id(directory[dir_idx])

    def _bucket_contains_key(self, bucket, key):
        for i in range(bucket.count):
            if bucket.keys[i] == key:
                return True
        return False

    def _bucket_value_for_key(self, bucket, key):
        for i in range(bucket.count):
            if bucket.keys[i] == key:
                return bucket.values[i]
        return None

    def _chain_contains_key(self, bucket_id, key):
        temp_id = bucket_id

        while temp_id != -1:
            bucket = self._read_bucket(temp_id)
            if self._bucket_contains_key(bucket, key):
                return True
            temp_id = bucket.next

        return False
    
    def _bucket_ref(self, bucket_id):
        page_id = bucket_id // self.buckets_per_page
        slot_id = bucket_id % self.buckets_per_page
        return page_id, slot_id

    def _ref_to_bucket_id(self, bucket_ref):
        return (bucket_ref[0] * self.buckets_per_page) + bucket_ref[1]

    def _read_bucket_by_ref(self, bucket_ref):
        page_id, slot_id = bucket_ref
        page = self._read_page(self.data_path, page_id)
        offset = slot_id * self.bucket_size
        return self._unpack_bucket(page[offset:offset + self.bucket_size])

    def _write_bucket_by_ref(self, bucket_ref, bucket):
        page_id, slot_id = bucket_ref
        page = bytearray(self._read_page(self.data_path, page_id))
        offset = slot_id * self.bucket_size
        page[offset:offset + self.bucket_size] = self._pack_bucket(bucket)
        self._write_page(self.data_path, page_id, bytes(page))

    def _read_bucket(self, bucket_id):
        return self._read_bucket_by_ref(self._bucket_ref(bucket_id))

    def _write_bucket(self, bucket_id, bucket):
        self._write_bucket_by_ref(self._bucket_ref(bucket_id), bucket)

    def _allocate_bucket(self, bucket, free_head, bucket_count):
        if free_head != -1:
            bucket_id = free_head
            free_bucket = self._read_bucket(bucket_id)
            free_head = free_bucket.free_list

            bucket.keys = [-1] * self.fb
            bucket.values = [-1] * self.fb
            bucket.count = 0
            bucket.next = -1
            bucket.free_list = -1

            self._write_bucket(bucket_id, bucket)
            return bucket_id, free_head, bucket_count

        bucket_id = bucket_count
        self._write_bucket(bucket_id, bucket)
        return bucket_id, free_head, bucket_count + 1
    
    def _double_directory(self, global_depth, directory):
        new_directory = directory + directory
        global_depth += 1
        return global_depth, new_directory
    
    def insert(self, key, value=-1):
        global_depth, directory, free_head, bucket_count = self._read_directory()
        dir_idx = self._get_dir_idx(key, global_depth)
        bucket_id = self._ref_to_bucket_id(directory[dir_idx])
        bucket = self._read_bucket(bucket_id)

        if self._chain_contains_key(bucket_id, key):
            return

        if bucket.count < self.fb:
            bucket.keys[bucket.count] = key
            bucket.values[bucket.count] = value
            bucket.count +=1
            self._write_bucket(bucket_id, bucket)
        else:
            inserted = self.split(bucket_id, key, value)
            if not inserted:
                self.insert(key, value)

    def split(self, bucket_id, key, value):
        global_depth, directory, free_head, bucket_count = self._read_directory()
        old_bucket = self._read_bucket(bucket_id)

        if old_bucket.local_depth == global_depth:
            if global_depth == MAX_GLOBAL_DEPTH:
                self.overflow(bucket_id, key, value)
                return True

            global_depth, directory = self._double_directory(global_depth, directory)

        new_local_depth = old_bucket.local_depth + 1
        bucket_ref = self._bucket_ref(bucket_id)

        old_entries = []
        for i in range(old_bucket.count):
            old_entries.append((old_bucket.keys[i], old_bucket.values[i]))
        
        old_bucket.keys = [-1] * self.fb
        old_bucket.values = [-1] * self.fb
        old_bucket.count = 0
        old_bucket.local_depth = new_local_depth

        new_bucket = self._build_empty_bucket(local_depth = new_local_depth)
        new_bucket_id, free_head, bucket_count = self._allocate_bucket(new_bucket, free_head, bucket_count)

        new_bucket_ref = self._bucket_ref(new_bucket_id)

        for i in range(len(directory)):
            if directory[i] == bucket_ref:
                bit = (i >> (new_local_depth - 1)) & 1

                if bit == 1:
                    directory[i] = new_bucket_ref

        self._write_bucket(bucket_id, old_bucket)
        self._write_directory(global_depth, directory, free_head, bucket_count)

        for old_key, old_value in old_entries:
            temp_idx = self._get_dir_idx(old_key, global_depth)
            temp_bucket_id = self._ref_to_bucket_id(directory[temp_idx])
            temp_bucket = self._read_bucket(temp_bucket_id)

            temp_bucket.keys[temp_bucket.count] = old_key
            temp_bucket.values[temp_bucket.count] = old_value
            temp_bucket.count += 1

            self._write_bucket(temp_bucket_id, temp_bucket)

        return False

    def overflow(self, bucket_id, key, value):
        bucket = self._read_bucket(bucket_id)

        if self._bucket_contains_key(bucket, key):
            return

        if bucket.count < self.fb:
            bucket.keys[bucket.count] = key
            bucket.values[bucket.count] = value
            bucket.count += 1

            self._write_bucket(bucket_id, bucket)
            return 
        if bucket.next == -1 :
            global_depth, directory, free_head, bucket_count = self._read_directory()
            overflow_bucket = self._build_empty_bucket(local_depth = bucket.local_depth)
            overflow_bucket_id, free_head, bucket_count = self._allocate_bucket(
                overflow_bucket,
                free_head,
                bucket_count
            )

            bucket.next = overflow_bucket_id
            self._write_bucket(bucket_id, bucket)
            self._write_directory(global_depth, directory, free_head, bucket_count)
            self.overflow(overflow_bucket_id, key, value)
            return

        self.overflow(bucket.next, key, value)


    def search(self, key):
        global_depth, directory, free_head, bucket_count = self._read_directory()
        dir_idx = self._get_dir_idx(key, global_depth)
        bucket_id = self._ref_to_bucket_id(directory[dir_idx])
        bucket = self._read_bucket(bucket_id)

        value = self._bucket_value_for_key(bucket, key)
        if value is not None:
            return value

        if not self._bucket_contains_key(bucket, key) and bucket.next != -1:
            while True:
                temp_id = bucket.next
                temp_bucket = self._read_bucket(temp_id)
                value = self._bucket_value_for_key(temp_bucket, key)
                if value is not None:
                    return value
                if not self._bucket_contains_key(temp_bucket, key) and temp_bucket.next == -1:
                    return None
                bucket = temp_bucket
        return None

    def delete(self, key):
        global_depth, directory, free_head, bucket_count = self._read_directory()
        dir_idx = self._get_dir_idx(key, global_depth)
        bucket_id = self._ref_to_bucket_id(directory[dir_idx])
        bucket = self._read_bucket(bucket_id)
        new_keys = [-1] * self.fb
        new_values = [-1] * self.fb

        if not self._bucket_contains_key(bucket, key) and bucket.next != -1:
            while True:
                temp_id = bucket.next
                temp_bucket = self._read_bucket(temp_id)
                if self._bucket_contains_key(temp_bucket, key):
                    pos = 0
                    for i in range(temp_bucket.count):
                        if key != temp_bucket.keys[i]:
                            new_keys[pos] = temp_bucket.keys[i]
                            new_values[pos] = temp_bucket.values[i]
                            pos += 1
                    temp_bucket.count = pos
                    temp_bucket.keys = new_keys
                    temp_bucket.values = new_values
                    if temp_bucket.count == 0:
                        self.delete_bucket(temp_id, bucket_id)
                    else:
                        self._write_bucket(temp_id, temp_bucket)
                    return True
                if not self._bucket_contains_key(temp_bucket, key) and temp_bucket.next == -1:
                    return False
                bucket = temp_bucket
                bucket_id = temp_id
        if self._bucket_contains_key(bucket, key):
            pos = 0
            for i in range(bucket.count):
                if key != bucket.keys[i]:
                    new_keys[pos] = bucket.keys[i]
                    new_values[pos] = bucket.values[i]
                    pos += 1
            bucket.count = pos
            bucket.keys = new_keys
            bucket.values = new_values
            self._write_bucket(bucket_id, bucket)
            return True
        return False

    def remove(self, key, value=None):
        return self.delete(key)
    
    def delete_bucket(self, bucket_id, prev_bucket_id):
        bucket = self._read_bucket(bucket_id)
        prev_bucket = self._read_bucket(prev_bucket_id)

        prev_bucket.next = bucket.next
        self._write_bucket(prev_bucket_id, prev_bucket)

        global_depth, directory, free_head, bucket_count = self._read_directory()

        bucket.keys = [-1] * self.fb
        bucket.values = [-1] * self.fb
        bucket.count = 0
        bucket.local_depth = -1
        bucket.next = -1
        bucket.free_list = free_head
        free_head = bucket_id

        self._write_bucket(bucket_id, bucket)
        self._write_directory(global_depth, directory, free_head, bucket_count)

    def print_directory(self):
        global_depth, directory, free_head, bucket_count = self._read_directory()

        print("\n============= DIRECTORY =============")
        print("global_depth:", global_depth)
        print("dir_size    :", len(directory))
        print("free_head   :", free_head)
        print("bucket_count:", bucket_count)
        print("-------------------------------------")

        for i in range(len(directory)):
            bits = format(i, f"0{global_depth}b")
            page_id, slot_id = directory[i]
            print(f"dir[{i}] ({bits}) -> page {page_id}, slot {slot_id}")

    def print_bucket(self, bucket_id):
        b = self._read_bucket(bucket_id)
        page_id, slot_id = self._bucket_ref(bucket_id)
        entries = []
        for i in range(b.count):
            entries.append((b.keys[i], b.values[i]))

        print(f"\n------------- bucket[{bucket_id}] -------------")
        print("page_id    :", page_id)
        print("slot_id    :", slot_id)
        print("local_depth:", b.local_depth)
        print("count      :", b.count)
        print("keys       :", b.keys)
        print("values     :", b.values)
        print("entries    :", entries)
        print("next       :", b.next)
        print("free_list  :", b.free_list)

    def print_index_pages(self):
        global_depth, directory, free_head, bucket_count = self._read_directory()
        total_pages = self.fm.page_count(self.index_path)

        print("\n============= INDEX FILE =============")
        print("path        :", self.index_path)
        print("pages       :", total_pages)
        print("global_depth:", global_depth)
        print("dir_size    :", len(directory))
        print("free_head   :", free_head)
        print("bucket_count:", bucket_count)
        print("--------------------------------------")
        print("page[0] -> header")

        pos = 0
        for page_id in range(1, total_pages):
            page_entries = []
            for _ in range(DIRECTORY_ENTRIES_PER_PAGE):
                if pos >= len(directory):
                    break
                page_entries.append(directory[pos])
                pos += 1
            print(f"page[{page_id}] -> directory entries: {page_entries}")

    def print_data_pages(self):
        total_pages = self.fm.page_count(self.data_path)
        global_depth, directory, free_head, bucket_count = self._read_directory()

        print("\n============= DATA FILE =============")
        print("path            :", self.data_path)
        print("pages           :", total_pages)
        print("bucket_count    :", bucket_count)
        print("fb              :", self.fb)
        print("buckets/page    :", self.buckets_per_page)
        print("bucket_size     :", self.bucket_size)
        print("-------------------------------------")

        for page_id in range(total_pages):
            start_bucket = page_id * self.buckets_per_page
            end_bucket = min(start_bucket + self.buckets_per_page, bucket_count)
            print(f"page[{page_id}] -> buckets {start_bucket}..{end_bucket - 1}")

        for bucket_id in range(bucket_count):
            self.print_bucket(bucket_id)

    def print_file(self):
        self.print_directory()
        self.print_index_pages()
        self.print_data_pages()


def main():
    import shutil

    folder = "hash_test"

    if os.path.exists(folder):
        shutil.rmtree(folder)

    h = ExtendibleHash(folder, use_buffer=True)

    print("_____________ hash vacio _____________")
    h.print_file()

    print("\nBucket para key 5:")
    bucket_id = h._get_bucket_id(5)
    print("key 5 iria al bucket:", bucket_id)

    print("\nBucket para key 8:")
    bucket_id = h._get_bucket_id(8)
    print("key 8 iria al bucket:", bucket_id)

    h.flush()


if __name__ == "__main__":
    main()
