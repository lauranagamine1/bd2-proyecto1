from collections import defaultdict

class HashTable:
    def __init__(self, capacity:int):
        self.capacity = max(1,capacity)
        self.table = [[] for _ in range(self.capacity) ]

    def _hash_function(self, key):
        return hash(key) % self.capacity 
    
    def insert(self, key, row:int):
        idx = self._hash_function(key)
        self.table[idx].append((key,row))

    def search(self, key):
        idx = self._hash_function(key)
        res = []
        for keys,row in self.table[idx]:
            if keys == key:
                res.append(row)
        return res
    
class HashJoin:
    """
    dos fases de construcción:
      1. build: full scan a la tabla más pequeña en la columna
      2. probe: full scan a la otra tabla y busca coincidencias en esa tabla
    """

    def __init__(self):
        pass
        
    def join(self, build_rows, probe_rows, build_key, probe_key, build_name, probe_name, is_left = True, build_size = 1):
        
        capacity = build_size*2
        hash_table = HashTable(capacity)

        res = []

        for row in build_rows:
            key = row[build_key]
            hash_table.insert(key,row)

        for row in probe_rows:
            key = row[probe_key]
            matches = hash_table.search(key)

            for match in matches:
                if is_left:
                    joined = self._join_rows(match, row, build_name, probe_name)
                else:
                    joined = self._join_rows(row, match, probe_name, build_name)

                res.append(joined)
        

        return res

    def _join_rows(self, left_row, right_row, left_name, right_name):
        joined = {}

        for key, value in left_row.items():
            joined[f"{left_name}.{key}"] = value

        for key, value in right_row.items():
            joined[f"{right_name}.{key}"] = value

        return joined
