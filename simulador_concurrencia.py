import os
os.environ["BD2_TRANSACTION_LOG"] = "1"
import sys
import threading
import time

sys.path.insert(0, "manager")
sys.path.insert(0, "indexes")

from db_manager import DBManager
from schemas import Column, DataType, IndexType, Table
from buffer_manager import BufferManager
from file_manager import FileManager
from transaction_log import TransactionLogger
from deadlock_detector import DeadlockDetectedError

def run_deadlock_scenario(db, bm):
    print(" ESCENARIO CLÁSICO DE DEADLOCK ")
    
    table_name = "test_deadlock"
    table = Table(
        name=table_name,
        columns=[
            Column("id", DataType.INT, primary=True),
            Column("value", DataType.STRING, data_size=50)
        ]
    )
    db.create_table(table)
    db.insert(table_name, [1, "Initial Value 1"])
    db.insert(table_name, [2, "Initial Value 2"])
    
    record_file = db.get_record_file(table_name)
    path = record_file.path
    barrier = threading.Barrier(2)
    
    def tx_a():
        threading.current_thread().name = "TX-A"
        try:
            bm.lock_page(path, 1, "exclusive")
            barrier.wait()
            time.sleep(0.5)
            bm.lock_page(path, 2, "exclusive")
            print("[TX-A] Completado exitosamente!")
            bm.unlock_page(path, 2, "exclusive")
            bm.unlock_page(path, 1, "exclusive")
        except DeadlockDetectedError as e:
            print(f"[TX-A] ABORTADO POR DEADLOCK: {e}")
            try:
                bm.unlock_page(path, 1, "exclusive")
            except: pass

    def tx_b():
        threading.current_thread().name = "TX-B"
        try:
            bm.lock_page(path, 2, "exclusive")
            barrier.wait()
            time.sleep(0.5)
            bm.lock_page(path, 1, "exclusive")
            print("[TX-B] Completado exitosamente!")
            bm.unlock_page(path, 1, "exclusive")
            bm.unlock_page(path, 2, "exclusive")
        except DeadlockDetectedError as e:
            print(f"[TX-B] ABORTADO POR DEADLOCK: {e}")
            try:
                bm.unlock_page(path, 2, "exclusive")
            except: pass

    t1 = threading.Thread(target=tx_a)
    t2 = threading.Thread(target=tx_b)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    print("Insertando nuevo registro (id=3) para probar el estado del sistema...")
    try:
        db.insert(table_name, [3, "Post Deadlock Value"])
        rows = db.select_all(table_name)
        if len(rows) == 3:
            print("ÉXITO: Los índices y la data NO están corruptos. Recovery limpio comprobado.")
        else:
            print("ERROR: Inconsistencia en los datos post-deadlock.")
    except Exception as e:
        print(f"ERROR: Excepción durante la validación post-deadlock: {e}")

def run_seq_scenario(db):
    print(" ESCENARIO: SEQUENTIAL FILE ")
    
    table_name = "test_seq"
    table = Table(
        name=table_name,
        columns=[
            Column("id", DataType.FLOAT, primary=True, index_type=IndexType.SEQFILE),
            Column("data", DataType.STRING, data_size=50)
        ]
    )
    db.create_table(table)
    db.insert(table_name, [10.0, "Seq A"])
    
    barrier = threading.Barrier(2)
    success_flags = [False, False]

    def writer():
        threading.current_thread().name = "SEQ-WRITER"
        barrier.wait()
        try:
            for i in range(11, 20):
                db.insert(table_name, [float(i), f"Seq {i}"])
                time.sleep(0.01)
            success_flags[0] = True
            print("[SEQ-WRITER] Inserciones completadas sin corrupción.")
        except Exception as e:
            print(f"[SEQ-WRITER] Error: {e}")

    def reader():
        threading.current_thread().name = "SEQ-READER"
        barrier.wait()
        try:
            for _ in range(5):
                results = db.select_range(table_name, "id", 10.0, 20.0)
                time.sleep(0.02)
            success_flags[1] = True
            print("[SEQ-READER] Búsquedas por rango completadas.")
        except Exception as e:
            print(f"[SEQ-READER] Error: {e}")

    t1 = threading.Thread(target=writer)
    t2 = threading.Thread(target=reader)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    if all(success_flags):
        print("ÉXITO: Control de concurrencia validado sobre Sequential File.")

def run_btree_scenario(db):
    print(" ESCENARIO: B+ TREE ")
    
    table_name = "test_btree"
    table = Table(
        name=table_name,
        columns=[
            Column("id", DataType.INT, primary=True, index_type=IndexType.BTREE),
            Column("data", DataType.STRING, data_size=50)
        ]
    )
    db.create_table(table)
    db.insert(table_name, [100, "BTree Init"])
    
    barrier = threading.Barrier(2)
    success_flags = [False, False]

    def writer():
        threading.current_thread().name = "BTREE-WRITER"
        barrier.wait()
        try:
            for i in range(101, 150):
                db.insert(table_name, [i, f"BTree {i}"])
                time.sleep(0.01)
            success_flags[0] = True
            print("[BTREE-WRITER] Inserciones completadas.")
        except Exception as e:
            print(f"[BTREE-WRITER] Error: {e}")

    def reader():
        threading.current_thread().name = "BTREE-READER"
        barrier.wait()
        try:
            for _ in range(10):
                res = db.select_equal(table_name, "id", 100)
                time.sleep(0.02)
            success_flags[1] = True
            print("[BTREE-READER] Búsquedas puntuales completadas.")
        except Exception as e:
            print(f"[BTREE-READER] Error: {e}")

    t1 = threading.Thread(target=writer)
    t2 = threading.Thread(target=reader)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    if all(success_flags):
        final_search = db.select_equal(table_name, "id", 149)
        if len(final_search) == 1:
            print("ÉXITO: Control de concurrencia validado sobre B+ Tree. Resultados correctos.")

def run_hash_scenario(db):
    print(" ESCENARIO: EXTENDIBLE HASHING ")
    
    table_name = "test_hash"
    table = Table(
        name=table_name,
        columns=[
            Column("id", DataType.INT, primary=True, index_type=IndexType.EXTHASH),
            Column("data", DataType.STRING, data_size=50)
        ]
    )
    db.create_table(table)
    db.insert(table_name, [1, "Hash Init"])
    
    barrier = threading.Barrier(2)
    success_flags = [False, False]

    def writer():
        threading.current_thread().name = "HASH-WRITER"
        barrier.wait()
        try:
            for i in range(2, 50):
                db.insert(table_name, [i, f"Hash {i}"])
                time.sleep(0.01)
            success_flags[0] = True
            print("[HASH-WRITER] Inserciones completadas.")
        except Exception as e:
            print(f"[HASH-WRITER] Error: {e}")

    def reader():
        threading.current_thread().name = "HASH-READER"
        barrier.wait()
        try:
            for _ in range(10):
                res = db.select_equal(table_name, "id", 1)
                time.sleep(0.02)
            success_flags[1] = True
            print("[HASH-READER] Búsquedas simultáneas completadas.")
        except Exception as e:
            print(f"[HASH-READER] Error: {e}")

    t1 = threading.Thread(target=writer)
    t2 = threading.Thread(target=reader)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    if all(success_flags):
        final_search = db.select_equal(table_name, "id", 49)
        if len(final_search) == 1:
            print("ÉXITO: Control de concurrencia validado sobre Extendible Hashing. Consistencia comprobada.")

def run_rtree_scenario(db):
    print(" ESCENARIO: R-TREE ")
    
    table_name = "test_rtree"
    table = Table(
        name=table_name,
        columns=[
            Column("id", DataType.INT, primary=True),
            Column("location", DataType.POINT, index_type=IndexType.RTREE)
        ]
    )
    db.create_table(table)
    db.insert(table_name, [1, (0.0, 0.0)])
    
    barrier = threading.Barrier(2)
    success_flags = [False, False]

    def writer():
        threading.current_thread().name = "RTREE-WRITER"
        barrier.wait()
        try:
            for i in range(2, 20):
                db.insert(table_name, [i, (float(i), float(i))])
                time.sleep(0.01)
            success_flags[0] = True
            print("[RTREE-WRITER] Inserciones espaciales completadas.")
        except Exception as e:
            print(f"[RTREE-WRITER] Error: {e}")

    def reader():
        threading.current_thread().name = "RTREE-READER"
        barrier.wait()
        try:
            for _ in range(5):
                res = db.select_spatial_knn(table_name, "location", (0.0, 0.0), 3)
                time.sleep(0.02)
            success_flags[1] = True
            print("[RTREE-READER] Búsquedas kNN simultáneas completadas.")
        except Exception as e:
            print(f"[RTREE-READER] Error: {e}")

    t1 = threading.Thread(target=writer)
    t2 = threading.Thread(target=reader)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    if all(success_flags):
        res = db.select_spatial_knn(table_name, "location", (0.0, 0.0), 1)
        if len(res) == 1 and res[0]["id"] == 1:
            print("ÉXITO: Control de concurrencia validado sobre R-Tree. Resultados correctos post-inserción.")

def main():
    print("Iniciando pruebas de concurrencia en todos los índices...")
    fm = FileManager()
    bm = BufferManager(fm)
    db = DBManager(base_path="data/concurrency_test", buffer_manager=bm)
    
    run_deadlock_scenario(db, bm)
    run_seq_scenario(db)
    run_btree_scenario(db)
    run_hash_scenario(db)
    run_rtree_scenario(db)
    
    print("ÚLTIMAS LÍNEAS DEL LOG CAUSAL")
    if os.path.exists("data/transactions.log"):
        with open("data/transactions.log", "r", encoding="utf-8") as f:
            lines = f.readlines()
            shown = 0
            for line in reversed(lines):
                if 'TX-' in line or 'DEADLOCK' in line or 'WRITER' in line or 'READER' in line:
                    print(line.strip())
                    shown += 1
                    if shown > 15:
                        break

if __name__ == "__main__":
    main()
