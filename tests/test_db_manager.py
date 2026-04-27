from pathlib import Path
import shutil
import sys


BASE_DIR = Path(__file__).resolve().parent.parent
MANAGER_DIR = BASE_DIR / "manager"
if str(MANAGER_DIR) not in sys.path:
    sys.path.append(str(MANAGER_DIR))

from db_manager import DBManager
from schemas import Column, DataType, IndexType, Table


CSV_PATH = BASE_DIR / "dataset" / "Airbnb_Open_Data.csv"
TEST_DB_PATH = BASE_DIR / "data" / "tests" / "db_manager"


def reset_test_data():
    if TEST_DB_PATH.exists():
        shutil.rmtree(TEST_DB_PATH)


def build_airbnb_table():
    return Table(
        "airbnb",
        [
            Column("id", DataType.INT, primary=True, index_type=IndexType.EXTHASH),
            Column("name", DataType.STRING, data_size=200),
            Column("price", DataType.INT, index_type=IndexType.BTREE),
            Column("location", DataType.POINT, index_type=IndexType.RTREE),
        ],
    )

def test_csv_load():
    print("\n" + "=" * 60)
    reset_test_data()

    db = DBManager(base_path=str(TEST_DB_PATH))
    table = build_airbnb_table()

    print("Creando tabla airbnb y cargando 20 filas del CSV...")
    inserted = db.create_table(table, str(CSV_PATH), max_rows=20)
    print(f"filas insertadas: {inserted}")
    assert inserted == 20

    first_record = db.read_record("airbnb", 0)
    print("Primer registro cargado:")
    print(first_record)
    assert first_record["id"] == 1001254

    all_rows = db.select_all("airbnb")
    print(f"Total visible por select_all: {len(all_rows)}")
    assert len(all_rows) == 20

    print("\nBuscando por id usando EXTHASH en todo el flujo DBManager...")
    row_by_id = db.select_equal("airbnb", "id", "1007964")
    print(row_by_id)
    assert len(row_by_id) == 1

    hash_index = db.get_index("airbnb", "id")
    print("\nEstado fisico del hash conectado al DBManager:")
    hash_index.print_file()

if __name__ == "__main__":
    test_csv_load()
