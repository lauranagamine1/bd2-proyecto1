import { useState } from "react"
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Label,
} from "recharts"
import "./App.css"

const API = "http://localhost:8000"

function StatsPanel({ stats }) {
  if (!stats) return null
  return (
    <div className="stats">
      <span>Tiempo: <b>{stats.time_ms} ms</b></span>
      <span>Lecturas disco: <b>{stats.disk.reads}</b></span>
      <span>Escrituras disco: <b>{stats.disk.writes}</b></span>
      <span>Buffer hits: <b>{stats.buffer.hits}</b></span>
      <span>Buffer misses: <b>{stats.buffer.misses}</b></span>
    </div>
  )
}

function ResultTable({ rows }) {
  if (!rows || rows.length === 0) return <p className="empty">Sin resultados</p>
  const keys = Object.keys(rows[0])
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{keys.map(k => <th key={k}>{k}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {keys.map(k => (
                <td key={k}>
                  {typeof row[k] === "object" ? JSON.stringify(row[k]) : String(row[k])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function PointPlot({ rows }) {
  if (!rows || rows.length === 0) return null
  const pointKeys = Object.keys(rows[0]).filter(
    k => rows[0][k] && typeof rows[0][k] === "object" && rows[0][k].type === "POINT"
  )
  if (pointKeys.length === 0) return null

  const key = pointKeys[0]
  const nameKey = Object.keys(rows[0]).find(k => k !== key) || "idx"
  const data = rows.map((r, i) => ({
    x: r[key].x,
    y: r[key].y,
    name: r[nameKey] || i,
  }))

  return (
    <div className="plot">
      <h3>Visualización espacial</h3>
      <ResponsiveContainer width="100%" height={340}>
        <ScatterChart margin={{ top: 10, right: 30, bottom: 30, left: 30 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="x" type="number" domain={["auto", "auto"]}>
            <Label value="longitud" offset={-10} position="insideBottom" />
          </XAxis>
          <YAxis dataKey="y" type="number" domain={["auto", "auto"]}>
            <Label value="latitud" angle={-90} position="insideLeft" />
          </YAxis>
          <Tooltip
            content={({ payload }) => {
              if (!payload?.length) return null
              const d = payload[0].payload
              return (
                <div className="tooltip-box">
                  <b>{d.name}</b>
                  <div>lon: {d.x}</div>
                  <div>lat: {d.y}</div>
                </div>
              )
            }}
          />
          <Scatter data={data} fill="#6366f1" />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  )
}

export default function App() {
  const [sql, setSql] = useState(
    "CREATE TABLE clientes (id INT INDEX SEQUENTIAL, nombre VARCHAR(50), edad INT);\n" +
    "INSERT INTO clientes VALUES (1, 'Laura', 28);\n" +
    "INSERT INTO clientes VALUES (2, 'Sofia', 22);\n" +
    "SELECT * FROM clientes;"
  )
  const [results, setResults] = useState([])
  const [stats, setStats]     = useState(null)
  const [error, setError]     = useState(null)
  const [loading, setLoading] = useState(false)

  async function runQuery() {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API}/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql }),
      })
      const json = await res.json()
      if (!res.ok) {
        setError(json.detail)
        setResults([])
        setStats(null)
      } else {
        setResults(json.results)
        setStats(json.stats)
      }
    } catch {
      setError("No se pudo conectar con el servidor (¿está corriendo la API?)")
    } finally {
      setLoading(false)
    }
  }

  const rows     = results.flatMap(r => Array.isArray(r) ? r : [r])
  const dataRows = rows.filter(r => !r.status)
  const msgRows  = rows.filter(r => r.status)

  return (
    <div className="app">
      <header>
        <h1>BD2 · Mini SQL Engine</h1>
      </header>

      <main>
        <section className="editor-section">
          <label>Consulta SQL</label>
          <textarea
            value={sql}
            onChange={e => setSql(e.target.value)}
            rows={8}
            spellCheck={false}
          />
          <button onClick={runQuery} disabled={loading}>
            {loading ? "Ejecutando..." : "Ejecutar"}
          </button>
        </section>

        {error && <div className="error">{error}</div>}

        <StatsPanel stats={stats} />

        {dataRows.length > 0 && (
          <>
            <ResultTable rows={dataRows} />
            <PointPlot rows={dataRows} />
          </>
        )}

        {!error && dataRows.length === 0 && msgRows.length > 0 && (
          <div className="messages">
            {msgRows.map((r, i) => <div key={i} className="msg">✓ {r.message}</div>)}
          </div>
        )}
      </main>
    </div>
  )
}
