# Gramática

---

## Leyenda (EBNF)

| Símbolo | Significado |
|---|---|
| `::=` | definición de regla |
| `\|` | alternativa |
| `{ }` | cero o más repeticiones |
| `[ ]` | opcional (cero o una vez) |
| `( )` | agrupación |
| `' '` | terminal literal |
| `MAYÚSCULAS` | terminal (token) |
| `minúsculas` | no-terminal |

---

## Regla raíz

```ebnf
program    ::= { statement ';' }

statement  ::= create_statement
             | select_statement
             | insert_statement
             | delete_statement
```

---

## CREATE TABLE

```ebnf
create_statement ::=
    'CREATE' 'TABLE' IDENTIFIER
    '(' column_def { ',' column_def } ')'
    [ 'FROM' 'FILE' PATH ]

column_def ::=
    IDENTIFIER data_type [ 'INDEX' index_type ]

data_type ::=
    'INT'
    | 'FLOAT'
    | 'VARCHAR' '(' INT ')'
    | 'BOOL'
    | 'POINT'

index_type ::=
    'SEQUENTIAL'
    | 'HASH'
    | 'BTREE'
    | 'RTREE'
```

> **Ejemplo válido:**
> ```sql
> CREATE TABLE clientes (id INT INDEX BTREE, nombre VARCHAR(50), edad INT) FROM FILE 'datos.csv';
> ```

---

## SELECT

```ebnf
select_statement ::=
    'SELECT' '*' 'FROM' IDENTIFIER [ 'WHERE' condition ]

condition ::=
    equality_condition
    | range_condition
    | spatial_radius_condition
    | spatial_knn_condition

equality_condition ::=
    IDENTIFIER '=' value

range_condition ::=
    IDENTIFIER 'BETWEEN' value 'AND' value

spatial_radius_condition ::=
    IDENTIFIER 'IN' '(' point ',' 'RADIUS' NUMBER ')'

spatial_knn_condition ::=
    IDENTIFIER 'IN' '(' point ',' 'K' INT ')'

point ::=
    'POINT' '(' NUMBER ',' NUMBER ')'
```

> **Ejemplos válidos:**
> ```sql
> SELECT * FROM clientes;
> SELECT * FROM clientes WHERE id = 42;
> SELECT * FROM ventas WHERE precio BETWEEN 100 AND 500;
> SELECT * FROM ubicaciones WHERE coords IN (POINT(-77.03, -12.04), RADIUS 5.0);
> SELECT * FROM ubicaciones WHERE coords IN (POINT(-77.03, -12.04), K 10);
> ```

---

## INSERT

```ebnf
insert_statement ::=
    'INSERT' 'INTO' IDENTIFIER 'VALUES' '(' value_list ')'

value_list ::=
    value { ',' value }
```

> **Ejemplo válido:**
> ```sql
> INSERT INTO clientes VALUES (1, 'Ana Torres', 28);
> ```

---

## DELETE

```ebnf
delete_statement ::=
    'DELETE' 'FROM' IDENTIFIER 'WHERE' equality_condition
```

> **Ejemplo válido:**
> ```sql
> DELETE FROM clientes WHERE id = 1;
> ```

---

## Reglas de valores y literales

```ebnf
value ::=
    INT
    | FLOAT
    | STRING
    | BOOLEAN
    | point

point ::=
    'POINT' '(' NUMBER ',' NUMBER ')'

value_list ::=
    value { ',' value }
```

---

## Tokens (terminales)

```ebnf
IDENTIFIER ::= LETTER { LETTER | DIGIT | '_' }

INT        ::= [ '-' ] DIGIT { DIGIT }

FLOAT      ::= [ '-' ] DIGIT { DIGIT } '.' DIGIT { DIGIT }

NUMBER     ::= INT | FLOAT

STRING     ::= "'" { CHAR } "'"

BOOLEAN    ::= 'TRUE' | 'FALSE'

PATH       ::= "'" { CHAR } "'"

LETTER     ::= 'a' | ... | 'z' | 'A' | ... | 'Z'

DIGIT      ::= '0' | ... | '9'

CHAR       ::= cualquier carácter excepto "'"
```

---

## Autómata del Scanner (AFD por categoría de token)

El scanner reconoce cada token mediante un autómata finito determinista (AFD). Los estados relevantes son:

```
Estado inicial → q0

IDENTIFICADOR / PALABRA CLAVE:
  q0 --[a-zA-Z]--> q1 --[a-zA-Z0-9_]--> q1  (acepta en q1)
  Si el lexema es una palabra clave reservada, se emite el token de esa keyword.

ENTERO:
  q0 --['-']--> q2 --[0-9]--> q3 --[0-9]--> q3  (acepta en q3)
  q0 --[0-9]--> q3

FLOTANTE (extensión de ENTERO):
  q3 --['.']--> q4 --[0-9]--> q5 --[0-9]--> q5  (acepta en q5)

CADENA:
  q0 --["'"]--> q6 --[cualquier char ≠ "'"]--> q6 --["'"]--> q7  (acepta en q7)

SÍMBOLO (un carácter):
  ( ) , ; = *  →  token directo desde q0
```

---

## Mapeo sentencia → método del índice

| Sentencia SQL | Método del índice |
|---|---|
| `SELECT * FROM tabla` | `index.scan_all()` |
| `SELECT ... WHERE col = v` | `index.search(v)` |
| `SELECT ... WHERE col BETWEEN v1 AND v2` | `index.range_search(v1, v2)` |
| `SELECT ... WHERE col IN (POINT(...), RADIUS r)` | `index.range_search(point, r)` |
| `SELECT ... WHERE col IN (POINT(...), K k)` | `index.knn(point, k)` |
| `INSERT INTO ... VALUES (...)` | `index.add(record)` |
| `DELETE FROM ... WHERE col = v` | `index.remove(v)` |
| `CREATE TABLE ... FROM FILE path` | carga CSV + `index.add(record)` por fila |

---

## Notas de implementación

- El parser es **case-insensitive** para las palabras clave (`SELECT`, `select`, `Select` son equivalentes).
- El tipo de índice a usar en cada operación se determina por la declaración `INDEX <tipo>` de la columna en `CREATE TABLE`.
- Si una columna no tiene `INDEX`, no soporta búsqueda directa por esa columna.
- `DELETE` solo soporta condición de igualdad (no rango), ya que `remove` recibe una clave puntual.
- `POINT` solo es válido como valor en columnas de tipo `POINT` con `INDEX RTREE`.
- `FLOAT` tiene precedencia sobre `INT` cuando el lexema contiene `'.'`.
- `TRUE` y `FALSE` se reconocen como `BOOLEAN`, no como `IDENTIFIER`.
