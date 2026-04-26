CREATE TABLE clientes (id INT INDEX SEQUENTIAL, nombre VARCHAR(50), edad INT);

INSERT INTO clientes VALUES (1, 'Ana', 28);
INSERT INTO clientes VALUES (3, 'Carlos', 35);
INSERT INTO clientes VALUES (2, 'Beto', 22);

SELECT * FROM clientes WHERE id = 2;
SELECT * FROM clientes WHERE id BETWEEN 1 AND 3;

DELETE FROM clientes WHERE id = 2;
SELECT * FROM clientes WHERE id = 2;

CREATE TABLE lugares (nombre VARCHAR(50), coords POINT INDEX RTREE);

INSERT INTO lugares VALUES ('Miraflores', POINT(-77.03, -12.04));
INSERT INTO lugares VALUES ('Barranco', POINT(-77.05, -12.07));
INSERT INTO lugares VALUES ('La Molina', POINT(-76.90, -11.90));

SELECT * FROM lugares WHERE coords IN (POINT(-77.03, -12.04), RADIUS 0.05);
SELECT * FROM lugares WHERE coords IN (POINT(-77.03, -12.04), K 2);
