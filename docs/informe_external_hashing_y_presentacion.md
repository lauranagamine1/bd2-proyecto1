# External Hashing

## Descripcion General

External Hashing distribuye registros en buckets usando una funcion hash sobre la clave de join. En el proyecto se usa para resolver joins por igualdad: el `DBManager` construye una estructura `ExternalHashing` con la tabla mas pequena y luego busca las coincidencias de la tabla probe leyendo solamente las paginas asociadas al bucket de cada clave.

La consulta sigue escribiendose como SQL normal:

```sql
SELECT * FROM airbnb
JOIN reservas ON airbnb.id = reservas.airbnb_id;
```

Internamente se ejecuta con:

```text
DBManager.external_hashing_join(...)
```

## Ubicacion De Archivos

Los archivos paginados del external hashing se crean dentro del directorio de datos:

```text
data/external_hashing/join_*/external_hashing.dat
```

Si el `Engine` usa otro `data_dir`, se guarda en:

```text
<data_dir>/external_hashing/join_*/external_hashing.dat
```

El archivo generado es `.dat`, no `.bin`, para mantener consistencia con `records.dat`, `metadata.dat`, `index.dat` y `data.dat`.

## Estructura Fisica

La implementacion no crea una pagina por bucket. Eso desperdiciaria espacio cuando hay muchos buckets con pocas filas. En su lugar, usa un solo archivo compartido:

```text
external_hashing.dat
```

Ese archivo se divide en paginas de 4096 bytes mediante `FileManager` y puede pasar por `BufferManager`.

| Elemento | Funcion |
|---|---|
| Archivo `.dat` | Guarda todas las entradas del external hashing. |
| Paginas de 4096 bytes | Unidad fisica de lectura/escritura. |
| Buckets logicos | Ids calculados por hash; no son archivos separados. |
| Directorio en memoria | Mapea `bucket_id -> [page_id, ...]`. |
| Entradas binarias | Guardan `bucket_id`, tamano del payload y payload empaquetado. |

Una pagina puede contener entradas de varios buckets:

```text
page 0:
  entry(bucket 3, key, row)
  entry(bucket 8, key, row)
  entry(bucket 3, key, row)
```

Por eso se evita reservar 4096 bytes para cada bucket pequeño.

## Formato Binario

Cada pagina empieza con:

```text
used_bytes
```

Luego guarda entradas consecutivas:

```text
bucket_id | payload_size | payload
```

El `payload` contiene el par `(key, row)` empaquetado con un codec propio basado en `struct`, no con `pickle`.

### Codec De Valores

Cada valor se serializa con un tag de tipo y despues sus bytes:

| Tipo | Formato |
|---|---|
| `None` | Tag `N`. |
| `bool` | Tag `B` + `struct.pack("?")`. |
| `int` | Tag `I` + entero de 8 bytes (`q`). |
| `float` | Tag `F` + double de 8 bytes (`d`). |
| `str` | Tag `S` + longitud + bytes UTF-8. |
| `dict` | Tag `D` + cantidad de pares + llave string + valor empaquetado. |

Esto permite guardar filas completas como diccionarios sin convertirlas a texto y sin depender de serializacion opaca.

## Funcion Hash

Para calcular el bucket, primero se empaqueta la clave con el mismo codec binario y luego se aplica SHA-256:

```text
bucket_id = sha256(pack_value(key)) % bucket_count
```

Se usa SHA-256 porque el `hash()` nativo de Python puede variar entre ejecuciones. Asi la distribucion depende de los bytes reales de la clave.

## Flujo De Join

1. Se validan las columnas de join.
2. Se verifica que ambas columnas tengan el mismo tipo.
3. Se cuenta cuantas filas tiene cada tabla.
4. La tabla mas pequena se usa como build.
5. Se construye `ExternalHashing` con `bucket_count = build_size * 2`.
6. Cada fila build se escribe en `external_hashing.dat`.
7. Cada fila probe calcula su bucket y busca coincidencias.
8. Las filas coincidentes se combinan con prefijos `tabla.columna`.

## Busqueda

Al buscar una clave:

1. Se calcula su `bucket_id`.
2. Se revisa el directorio en memoria para saber que paginas contienen ese bucket.
3. Se leen solo esas paginas.
4. Dentro de cada pagina se filtra por `bucket_id`.
5. Luego se compara la clave real para manejar colisiones.

Esto evita comparar cada fila de una tabla con todas las filas de la otra.

## Estadisticas

Cuando se activa, el `Engine` retorna:

```text
external_hashing: true
```

El frontend muestra:

```text
External Hashing activo
```

Las lecturas y escrituras del archivo `external_hashing.dat` pasan por `FileManager`/`BufferManager`, asi que afectan las estadisticas de disco y buffer.

## Prueba Recomendada

```sql
CREATE TABLE airbnb (
  id INT INDEX HASH,
  name VARCHAR(200),
  price INT INDEX BTREE,
  location POINT INDEX RTREE
) FROM FILE 'dataset/Airbnb_1k.csv';

CREATE TABLE reservas (
  id INT,
  airbnb_id INT,
  usuario VARCHAR(50),
  noches INT
);

INSERT INTO reservas VALUES (1, 1001254, 'Sofia', 3);
INSERT INTO reservas VALUES (2, 1002102, 'Laura', 2);
INSERT INTO reservas VALUES (3, 9999999, 'NoExiste', 5);

SELECT * FROM airbnb JOIN reservas ON airbnb.id = reservas.airbnb_id;
```

Resultado esperado:

| Fila | Resultado |
|---|---|
| `1001254` | Hace match. |
| `1002102` | Hace match. |
| `9999999` | No aparece porque no existe en `airbnb`. |

Ademas, debe generarse un archivo como:

```text
data/external_hashing/join_*/external_hashing.dat
```

## Resumen Para Presentacion De 1 Minuto

Implemente External Hashing para resolver joins por igualdad. Primero el `DBManager` escoge como build la tabla mas pequeña, porque esa es la que se materializa. Cada fila se distribuye en un bucket logico calculando un hash sobre la clave de join.

No creo una pagina por bucket porque eso desperdicia espacio. Uso un solo archivo `external_hashing.dat` dividido en paginas de 4096 bytes. Una misma pagina puede guardar entradas de varios buckets, y un directorio en memoria recuerda que paginas pertenecen a cada bucket.

Cada entrada se guarda en binario usando `struct`: almacena `bucket_id`, tamaño del payload y el par `(clave, fila)` empaquetado con tags de tipo. Para buscar, calculo el bucket de la clave probe, leo solo las paginas de ese bucket, filtro por `bucket_id` y comparo la clave real para manejar colisiones. Cuando se usa, las estadisticas muestran `external_hashing: true`.
