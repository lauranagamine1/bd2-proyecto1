from pathlib import Path
import shutil

from db_manager import DBManager
from schemas import Column, DataType, IndexType, Table


BASE_DIR = Path(__file__).resolve().parent.parent
CSV_PATH = BASE_DIR / "dataset" / "Airbnb_Open_Data.csv"
TEST_DB_PATH = BASE_DIR / "test_data"


def reset_test_data():
    if TEST_DB_PATH.exists():
        shutil.rmtree(TEST_DB_PATH)


def build_clientes_table():
    return Table(
        "clientes",
        [
            Column("id", DataType.INT, primary=True, index_type=IndexType.EXTHASH),
            Column("name", DataType.STRING, data_size=30),
            Column("price", DataType.INT),
        ],
    )


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

def test_manual_operations():
    print("\n" + "=" * 60)
    reset_test_data()

    db = DBManager(base_path=str(TEST_DB_PATH))
    table = build_clientes_table()
    db.create_table(table)

    r0 = db.insert("clientes", ["1", "Ana", "100"])
    r1 = db.insert("clientes", ["2", "Luis", "200"])
    r2 = db.insert("clientes", ["3", "Ana", "300"])
    print(f"record_ids generados: {r0}, {r1}, {r2}")

    all_rows = db.select_all("clientes")
    print("select_all('clientes'):")
    print(all_rows)
    assert len(all_rows) == 3

    equal_rows = db.select_equal("clientes", "name", "Ana")
    print("select_equal('clientes', 'name', 'Ana'):")
    print(equal_rows)
    assert len(equal_rows) == 2

    range_rows = db.select_range("clientes", "price", "150", "350")
    print("select_range('clientes', 'price', 150, 350):")
    print(range_rows)
    assert len(range_rows) == 2

    record = db.read_record("clientes", 1)
    print("read_record('clientes', 1):")
    print(record)
    assert record["name"] == "Luis"

    deleted = db.delete_where_equal("clientes", "name", "Ana")
    print("delete_where_equal('clientes', 'name', 'Ana'):")
    print(f"registros eliminados: {deleted}")
    assert deleted == 2

    remaining = db.select_all("clientes")
    print("Registros restantes luego del delete:")
    print(remaining)
    assert len(remaining) == 1
    assert remaining[0]["name"] == "Luis"
    print("\n" + "=" * 60)

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

    all_rows = db.select_all("airbnb")
    print(f"Total visible por select_all: {len(all_rows)}")
    assert len(all_rows) == 20


if __name__ == "__main__":
    test_manual_operations()
    test_csv_load()