from pathlib import Path
import shutil

from record_file import RecordFile
from schemas import Column, DataType, Record, Table


BASE_DIR = Path(__file__).resolve().parent.parent
TEST_RF_PATH = BASE_DIR / "test_record_file_data"


def reset_test_data():
    if TEST_RF_PATH.exists():
        shutil.rmtree(TEST_RF_PATH)
    TEST_RF_PATH.mkdir(parents=True, exist_ok=True)


def build_table():
    return Table(
        "productos",
        [
            Column("id", DataType.INT, primary=True),
            Column("name", DataType.STRING, data_size=25),
            Column("price", DataType.FLOAT),
            Column("active", DataType.BOOL),
        ],
    )


def build_record_file():
    table = build_table()
    path = TEST_RF_PATH / "records.dat"
    return table, RecordFile(table, str(path))


def test_record_file_flow():
    print("\n" + "=" * 60)
    reset_test_data()
    table, record_file = build_record_file()

    print("Tabla usada para el archivo de registros:")
    print([column.name for column in table.columns])

    r0 = Record(table, [1, "Teclado", 120.5, True])
    r1 = Record(table, [2, "Mouse", 55.0, True])
    r2 = Record(table, [3, "Monitor", 899.99, False])

    print("\nInsert")
    id0 = record_file.add(r0)
    id1 = record_file.add(r1)
    id2 = record_file.add(r2)
    print(f"record_ids generados: {id0}, {id1}, {id2}")
    assert (id0, id1, id2) == (0, 1, 2)

    count = record_file.record_count()
    print(f"\nrecord_count() -> {count}")
    assert count == 3

    record = record_file.read(1)
    print("\nread(1) ->")
    print(record.values)
    assert record.values == [2, "Mouse", 55.0, True]

    deleted_flag, slot_record = record_file.read_slot(2)
    print("\nread_slot(2) ->")
    print({"deleted": deleted_flag, "values": slot_record.values})
    assert deleted_flag is False
    assert slot_record.values[1] == "Monitor"

    visible_records = record_file.scan_all()
    print("\nscan_all() ->")
    print([(record_id, rec.values) for record_id, rec in visible_records])
    assert len(visible_records) == 3

    print("\nAplicando delete(1)...")
    deleted = record_file.delete(1)
    print(f"delete(1) -> {deleted}")
    assert deleted is True

    print("\nLeyendo slot borrado con read_slot(1)...")
    deleted_flag, slot_record = record_file.read_slot(1)
    print({"deleted": deleted_flag, "values": slot_record.values})
    assert deleted_flag is True

    print("\nIntentando leer con read(1) un registro borrado...")
    try:
        record_file.read(1)
        raise AssertionError("read(1) debió fallar para un registro borrado")
    except ValueError as error:
        print(f"read(1) lanzó ValueError como se esperaba: {error}")

    visible_after_delete = record_file.scan_all()
    print("\nscan_all() después del delete ->")
    print([(record_id, rec.values) for record_id, rec in visible_after_delete])
    assert len(visible_after_delete) == 2

    all_slots = record_file.scan_all(include_deleted=True)
    print("\nscan_all(include_deleted=True) ->")
    print([(record_id, rec.values) for record_id, rec in all_slots])
    assert len(all_slots) == 3

    print("\n" + "=" * 60)


if __name__ == "__main__":
    test_record_file_flow()