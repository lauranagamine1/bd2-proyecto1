import os
import struct

from file_manager import FileManager, PAGE_SIZE
from buffer_manager import BufferManager


FB = 4
MAX_GLOBAL_DEPTH = 6

INDEX_HEADER_FORMAT = "ii" # global_depth, directory_size
INDEX_HEADER_SIZE = struct.calcsize(INDEX_HEADER_FORMAT)

BUCKET_FORMAT = "iiiiiii" # local_depth, count, key1, key2, key3, key4, next
BUCKET_SIZE = struct.calcsize(BUCKET_FORMAT)


class Bucket:
    def __init__(self, local_depth:int, keys=None, count:int=0, next_bucket:int=-1):
        self.local_depth = local_depth
        self.count = count
        self.keys = keys if keys is not None else [-1] * FB
        self.next = next_bucket


class ExtendibleHash:
    def __init__(self, folder="hash_files", use_buffer=True):
        self.folder = folder
        self.index_path = os.path.join(folder, "index.dat")
        self.data_path = os.path.join(folder, "data.dat")

        os.makedirs(folder, exist_ok=True)

        self.fm = FileManager()

        if use_buffer:
            self.bm = BufferManager(self.fm)
        else:
            self.bm = None

        self._ensure_files()

    def _read_page(self, path, page_id):
        if self.bm:
            return self.bm.read_page(path, page_id)
        return self.fm.read_page(path, page_id)

    def _write_page(self, path, page_id, data):
        if self.bm:
            self.bm.write_page(path, page_id, data)
        else:
            self.fm.write_page(path, page_id, data)

    def _append_page(self, path, data):
        if self.bm:
            return self.bm.append_page(path, data)
        return self.fm.append_page(path, data)

    def flush(self):
        if self.bm:
            self.bm.flush_all()

    def _pack_bucket(self, b):
        return struct.pack(BUCKET_FORMAT, b.local_depth, b.count, b.keys[0], b.keys[1],
                           b.keys[2], b.keys[3], b.next).ljust(PAGE_SIZE, b"\x00")

    def _unpack_bucket(self, data):
        unpacked = struct.unpack(BUCKET_FORMAT, data[:BUCKET_SIZE])
        return Bucket(unpacked[0], [unpacked[2], unpacked[3], unpacked[4], unpacked[5]],
                      unpacked[1], unpacked[6])

    def _ensure_files(self):
        self.fm.ensure_file(self.index_path)
        self.fm.ensure_file(self.data_path)

        if self.fm.page_count(self.index_path) == 0:
            self._init_hash()

    def _init_hash(self):
        # global_depth = 1, dos buckets en directory

        b0 = Bucket(local_depth=1)
        b1 = Bucket(local_depth=1)

        b0_id = self._append_page(self.data_path, self._pack_bucket(b0))
        b1_id = self._append_page(self.data_path, self._pack_bucket(b1))

        directory = [b0_id, b1_id]

        self._write_directory(global_depth=1, directory=directory)

    def _read_directory(self):
        raw = self._read_page(self.index_path, 0)

        global_depth, dir_size = struct.unpack(INDEX_HEADER_FORMAT,raw[:INDEX_HEADER_SIZE]) 

        directory = []
        pos = INDEX_HEADER_SIZE

        for i in range(dir_size):
            bucket_id = struct.unpack("i", raw[pos:pos + 4])[0]
            directory.append(bucket_id)
            pos += 4

        return global_depth, directory

    def _write_directory(self, global_depth, directory):
        data = bytearray(PAGE_SIZE)

        header = struct.pack(INDEX_HEADER_FORMAT, global_depth, len(directory))

        data[0:INDEX_HEADER_SIZE] = header

        pos = INDEX_HEADER_SIZE
        for bucket_id in directory:
            data[pos:pos + 4] = struct.pack("i", bucket_id)
            pos += 4

        self._write_page(self.index_path, 0, bytes(data))

    def _read_bucket(self, bucket_id):
        raw = self._read_page(self.data_path, bucket_id)
        return self._unpack_bucket(raw)

    def _write_bucket(self, bucket_id, bucket):
        self._write_page(self.data_path, bucket_id, self._pack_bucket(bucket))

    def _append_bucket(self, bucket):
        return self._append_page(self.data_path, self._pack_bucket(bucket))

    def _hash(self, key):
        return key

    def _get_dir_idx(self, key, global_depth):
        h = self._hash(key)
        mask = (1 << global_depth) - 1
        return h & mask

    def _get_bucket_id(self, key):
        global_depth, directory = self._read_directory()
        dir_idx = self._get_dir_idx(key, global_depth)
        return directory[dir_idx]
    
    def _double_directory(self, global_depth, directory):
        new_directory = directory + directory
        global_depth += 1
        return global_depth, new_directory
    
    def insert(self, key):
        global_depth, directory = self._read_directory()
        dir_idx = self._get_dir_idx(key, global_depth)
        bucket_id = directory[dir_idx]
        bucket = self._read_bucket(bucket_id)

        if key in bucket.keys:
            return

        if bucket.count < FB:
            bucket.keys[bucket.count] = key
            bucket.count +=1
            self._write_bucket(bucket_id, bucket)
        else:
            self.split(bucket_id, key)
            self.insert(key)

    def split(self, bucket_id, key):
        global_depth, directory = self._read_directory()
        old_bucket = self._read_bucket(bucket_id)

        if old_bucket.local_depth == global_depth:
            if global_depth == MAX_GLOBAL_DEPTH:
                self.overflow(bucket_id, key)
                return

            global_depth, directory = self._double_directory(global_depth, directory)

        new_local_depth = old_bucket.local_depth + 1

        old_keys = []
        for i in range(old_bucket.count):  #pasar keys a memoria
            old_keys.append(old_bucket.keys[i])
        
        #reset de bucket para insercion
        old_bucket.keys = [-1] * FB
        old_bucket.count = 0
        old_bucket.local_depth = new_local_depth

        new_bucket = Bucket(local_depth = new_local_depth)
        new_bucket_id = self._append_bucket(new_bucket)

        for i in range(len(directory)): #punteros
            if directory[i] == bucket_id:
                bit = (i >> (new_local_depth - 1)) & 1

                if bit == 1:
                    directory[i] = new_bucket_id

        self._write_bucket(bucket_id, old_bucket)
        self._write_bucket(new_bucket_id, new_bucket)
        self._write_directory(global_depth, directory)

        for key in old_keys:
            temp_idx = self._get_dir_idx(key, global_depth)
            temp_bucket_id = directory[temp_idx]
            temp_bucket = self._read_bucket(temp_bucket_id)

            temp_bucket.keys[temp_bucket.count] = key
            temp_bucket.count += 1

            self._write_bucket(temp_bucket_id, temp_bucket)

    def overflow(self, bucket_id, key):
        bucket = self._read_bucket(bucket_id)

        if key in bucket.keys:
            return

        if bucket.count < FB:
            bucket.keys[bucket.count] = key
            bucket.count += 1

            self._write_bucket(bucket_id, bucket)
            return 
        if bucket.next == -1 :
            overflow_bucket = Bucket(local_depth = bucket.local_depth)
            overflow_bucket_id = self._append_bucket(overflow_bucket)

            bucket.next = overflow_bucket_id
            self._write_bucket(bucket_id, bucket)
            self.overflow(overflow_bucket_id, key)
            return

        self.overflow(bucket.next, key)


    def search(self, key) -> Bucket:
        global_depth, directory = self._read_directory()
        dir_idx = self._get_dir_idx(key, global_depth)
        bucket_id = directory[dir_idx]
        bucket = self._read_bucket(bucket_id)

        if key not in bucket.keys and bucket.next != -1:
            while True:
                temp_id = bucket.next
                temp_bucket = self._read_bucket(temp_id)
                if key in temp_bucket.keys:
                    return temp_bucket
                if key not in temp_bucket.keys and temp_bucket.next == -1:
                    return None
                bucket = temp_bucket
        if key in bucket.keys:
            return bucket
        return None

    def delete(self, key):
        pass

    def print_directory(self):
        global_depth, directory = self._read_directory()

        print("\n============= DIRECTORY =============")
        print("global_depth:", global_depth)
        print("dir_size    :", len(directory))
        print("-------------------------------------")

        for i in range(len(directory)):
            bits = format(i, f"0{global_depth}b")
            print(f"dir[{i}] ({bits}) -> bucket {directory[i]}")

    def print_bucket(self, bucket_id):
        b = self._read_bucket(bucket_id)

        print(f"\n------------- bucket[{bucket_id}] -------------")
        print("local_depth:", b.local_depth)
        print("count      :", b.count)
        print("keys       :", b.keys)
        print("next       :", b.next)

    def print_file(self):
        self.print_directory()

        total_buckets = self.fm.page_count(self.data_path)

        print("\n============= DATA FILE =============")
        print("total buckets:", total_buckets)
        print("-------------------------------------")

        for i in range(total_buckets):
            self.print_bucket(i)


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