import { useState, useRef, useEffect } from "react"
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Label,
} from "recharts"
import { MapContainer, TileLayer, CircleMarker, Popup } from "react-leaflet"
import "leaflet/dist/leaflet.css"
import "./App.css"

const API = "http://localhost:8000"

const KEYWORDS = new Set([
  "SELECT","FROM","WHERE","CREATE","TABLE","INSERT","INTO","VALUES",
  "DELETE","BETWEEN","AND","IN","INDEX","FILE",
  "SEQUENTIAL","HASH","BTREE","RTREE",
  "POINT","RADIUS","K",
])
const TYPES = new Set(["INT","FLOAT","VARCHAR","BOOL"])
const BOOLEANS = new Set(["TRUE","FALSE"])

function escHtml(s) {
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
}

function highlight(code) {
  const TOKEN = /('(?:[^'])*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)|([().,;=*])/g
  let result = ""
  let last = 0
  let m
  while ((m = TOKEN.exec(code)) !== null) {
    result += escHtml(code.slice(last, m.index))
    const [full,, , word] = m
    const str = m[1], num = m[2], sym = m[4]
    if (str)       result += `<span class="hl-string">${escHtml(full)}</span>`
    else if (num)  result += `<span class="hl-number">${escHtml(full)}</span>`
    else if (word) {
      const up = word.toUpperCase()
      if (KEYWORDS.has(up))      result += `<span class="hl-keyword">${escHtml(full)}</span>`
      else if (TYPES.has(up))    result += `<span class="hl-type">${escHtml(full)}</span>`
      else if (BOOLEANS.has(up)) result += `<span class="hl-boolean">${escHtml(full)}</span>`
      else                       result += `<span class="hl-ident">${escHtml(full)}</span>`
    }
    else if (sym)  result += `<span class="hl-sym">${escHtml(full)}</span>`
    last = m.index + full.length
  }
  result += escHtml(code.slice(last))
  return result + "\n"
}

function SqlEditor({ value, onChange }) {
  const textareaRef = useRef(null)
  const preRef = useRef(null)

  function syncScroll() {
    if (preRef.current && textareaRef.current) {
      preRef.current.scrollTop = textareaRef.current.scrollTop
      preRef.current.scrollLeft = textareaRef.current.scrollLeft
    }
  }

  useEffect(() => { syncScroll() }, [value])

  return (
    <div className="editor-wrap">
      <pre
        ref={preRef}
        className="editor-highlight"
        aria-hidden="true"
        dangerouslySetInnerHTML={{ __html: highlight(value) }}
      />
      <textarea
        ref={textareaRef}
        className="editor-input"
        value={value}
        onChange={e => onChange(e.target.value)}
        onScroll={syncScroll}
        rows={8}
        spellCheck={false}
        autoCapitalize="off"
        autoCorrect="off"
      />
    </div>
  )
}

function StatsPanel({ stats }) {
  if (!stats) return null
  return (
    <div className="stats">
      <div className="stat-item">
        <span className="stat-label">Tiempo</span>
        <span className="stat-value">{stats.time_ms} ms</span>
      </div>
      <div className="stat-item">
        <span className="stat-label">Lecturas disco</span>
        <span className="stat-value">{stats.disk.reads}</span>
      </div>
      <div className="stat-item">
        <span className="stat-label">Escrituras disco</span>
        <span className="stat-value">{stats.disk.writes}</span>
      </div>
      <div className="stat-item">
        <span className="stat-label">Buffer hits</span>
        <span className="stat-value hit">{stats.buffer.hits}</span>
      </div>
      <div className="stat-item">
        <span className="stat-label">Buffer misses</span>
        <span className="stat-value miss">{stats.buffer.misses}</span>
      </div>
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
  const data = rows.map((r, i) => ({ x: r[key].x, y: r[key].y, name: r[nameKey] || i }))

  return (
    <div className="plot">
      <h3>Visualización espacial</h3>
      <ResponsiveContainer width="100%" height={300}>
        <ScatterChart margin={{ top: 10, right: 30, bottom: 30, left: 30 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e8d5f5" />
          <XAxis dataKey="x" type="number" domain={["auto","auto"]} tick={{ fill: "#9b7bb5", fontSize: 12 }}>
            <Label value="longitud" offset={-10} position="insideBottom" fill="#b89fd4" />
          </XAxis>
          <YAxis dataKey="y" type="number" domain={["auto","auto"]} tick={{ fill: "#9b7bb5", fontSize: 12 }}>
            <Label value="latitud" angle={-90} position="insideLeft" fill="#b89fd4" />
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
          <Scatter data={data} fill="#c9a8e0" />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  )
}

function MapView({ rows }) {
  if (!rows || rows.length === 0) return null
  const pointKeys = Object.keys(rows[0]).filter(
    k => rows[0][k] && typeof rows[0][k] === "object" && rows[0][k].type === "POINT"
  )
  if (pointKeys.length === 0) return null

  const key = pointKeys[0]
  const points = rows.map((r, i) => ({
    lat: r[key].x,
    lng: r[key].y,
    name: Object.keys(r).find(k => k !== key) || `Punto ${i + 1}`,
    record: r,
  }))

  const center = {
    lat: points.reduce((sum, p) => sum + p.lat, 0) / points.length,
    lng: points.reduce((sum, p) => sum + p.lng, 0) / points.length,
  }

  const bounds = points.length > 1 ? points.map(p => [p.lat, p.lng]) : null

  return (
    <div className="map-card">
      <div className="card-header">
        <span className="card-title">Mapa</span>
        <span className="hint">Solo RTree</span>
      </div>
      <div className="map-wrapper">
        <MapContainer
          className="map-container"
          center={[center.lat, center.lng]}
          bounds={bounds || undefined}
          zoom={11}
          scrollWheelZoom={true}
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          {points.map((point, index) => (
            <CircleMarker
              key={index}
              center={[point.lat, point.lng]}
              radius={7}
              fillColor="#a87ed4"
              color="#fff"
              weight={2}
              fillOpacity={0.9}
            >
              <Popup>
                <div>
                  <strong>{point.name}</strong>
                  <div>lat: {point.lat}</div>
                  <div>lng: {point.lng}</div>
                </div>
              </Popup>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>
    </div>
  )
}

const DEFAULT_SQL =
  "CREATE TABLE clientes (id INT INDEX SEQUENTIAL, nombre VARCHAR(50), edad INT);\n" +
  "INSERT INTO clientes VALUES (1, 'Laura', 28);\n" +
  "INSERT INTO clientes VALUES (2, 'Sofia', 22);\n" +
  "INSERT INTO clientes VALUES (3, 'Renato', 30);\n" +
  "INSERT INTO clientes VALUES (4, 'Mikel', 16);\n" +
  "SELECT * FROM clientes;"

export default function App() {
  const [sql, setSql]         = useState(DEFAULT_SQL)
  const [results, setResults] = useState([])
  const [stats, setStats]     = useState(null)
  const [error, setError]     = useState(null)
  const [loading, setLoading] = useState(false)

  async function runQuery() {
    setLoading(true); setError(null)
    try {
      const res = await fetch(`${API}/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql }),
      })
      const json = await res.json()
      if (!res.ok) { setError(json.detail); setResults([]); setStats(null) }
      else         { setResults(json.results); setStats(json.stats) }
    } catch {
      setError("No se pudo conectar con el servidor (¿está corriendo la API?)")
    } finally { setLoading(false) }
  }

  function handleKey(e) {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); runQuery() }
  }

  const rows     = results.flatMap(r => Array.isArray(r) ? r : [r])
  const dataRows = rows.filter(r => !r.status)
  const msgRows  = rows.filter(r => r.status)

  return (
    <div className="app">
      <header>
        <div className="header-icon">🗄️</div>
        <div>
          <h1>Heider BD</h1>
          <p className="subtitle">BD2 · Proyecto 1</p>
        </div>
      </header>

      <main>
        <section className="card editor-section">
          <div className="card-header">
            <span className="card-title">Consulta SQL</span>
            <span className="hint">Ctrl+Enter para ejecutar</span>
          </div>
          <div onKeyDown={handleKey}>
            <SqlEditor value={sql} onChange={setSql} />
          </div>
          <button onClick={runQuery} disabled={loading} className="run-btn">
            {loading ? <><span className="spinner" /> Ejecutando…</> : "▶ Ejecutar"}
          </button>
        </section>

        {error && <div className="error-box">{error}</div>}

        {stats && (
          <section className="card">
            <div className="card-header"><span className="card-title">Estadísticas</span></div>
            <StatsPanel stats={stats} />
          </section>
        )}

        {dataRows.length > 0 && (
          <section className="card">
            <div className="card-header">
              <span className="card-title">Resultados</span>
              <span className="hint">{dataRows.length} fila{dataRows.length !== 1 ? "s" : ""}</span>
            </div>
            <ResultTable rows={dataRows} />
            <MapView rows={dataRows} />
            <PointPlot rows={dataRows} />
          </section>
        )}

        {!error && dataRows.length === 0 && msgRows.length > 0 && (
          <section className="card">
            <div className="messages">
              {msgRows.map((r, i) => (
                <div key={i} className="msg">
                  <span className="msg-icon">✓</span> {r.message}
                </div>
              ))}
            </div>
          </section>
        )}
      </main>
    </div>
  )
}
