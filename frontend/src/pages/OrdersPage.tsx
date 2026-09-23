import { useEffect, useState } from "react";
import { api } from "../api/client";
type O = { id: number; ticket_code: string; garment_name: string; length_cm: number; status: string; due_at: string };
type BatchItem = {
  order_id: number; ticket_code: string; success: boolean; reason: string | null;
  rail_id: number | null; rail_label: string | null; start_cm: number | null; end_cm: number | null;
};
type BatchResult = { requested: number; succeeded: number; failed: number; items: BatchItem[] };

const hangable = (s: string) => s === "ready" || s === "overdue";

export default function OrdersPage() {
  const [rows, setRows] = useState<O[]>([]);
  const [msg, setMsg] = useState(""); const [err, setErr] = useState("");
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [batch, setBatch] = useState<BatchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const reload = () => api<O[]>("/orders").then(setRows);
  useEffect(() => { reload(); }, []);

  function toggle(id: number, on: boolean) {
    setPicked(prev => {
      const next = new Set(prev);
      if (on) next.add(id); else next.delete(id);
      return next;
    });
  }

  function toggleAll(on: boolean) {
    setPicked(on ? new Set(rows.filter(o => hangable(o.status)).map(o => o.id)) : new Set());
  }

  async function hang(id: number) {
    setMsg(""); setErr(""); setBatch(null);
    try {
      const o = await api<O>("/hang", { method: "POST", body: JSON.stringify({ order_id: id }) });
      setMsg(`${o.ticket_code} 已上杆`);
      setPicked(prev => { const n = new Set(prev); n.delete(id); return n; });
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }

  async function hangBatch() {
    setMsg(""); setErr(""); setBatch(null);
    const ids = [...picked];
    // 空选中集合：直接失败，不发请求、不写库
    if (ids.length === 0) { setErr("未选择工单"); return; }
    setBusy(true);
    try {
      const r = await api<BatchResult>("/hang/batch", { method: "POST", body: JSON.stringify({ order_ids: ids }) });
      setBatch(r);
      setMsg(`批量上杆完成：${r.succeeded} 件成功，${r.failed} 件失败`);
      setPicked(new Set());
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  const hangableRows = rows.filter(o => hangable(o.status));

  return (<>
    <h2>工单</h2>
    <div className="toolbar">
      <span>已选 {picked.size} 件（ready / overdue）</span>
      <button onClick={hangBatch} disabled={busy}>批量上杆</button>
      <button className="btn-ghost" onClick={() => toggleAll(true)}>全选可上杆</button>
      <button className="btn-ghost" onClick={() => toggleAll(false)}>清空选择</button>
    </div>
    {msg && <div className="ok">{msg}</div>}
    {err && <div className="err">{err}</div>}
    {batch && (
      <table className="table batch-result"><thead><tr><th>票号</th><th>结果</th><th>占位（挂杆 · 段位）</th><th>原因</th></tr></thead>
        <tbody>{batch.items.map(it => (
          <tr key={it.order_id} className={it.success ? "ok-row" : "err-row"}>
            <td className="mono">{it.ticket_code || `#${it.order_id}`}</td>
            <td>{it.success ? "成功" : "失败"}</td>
            <td className="mono">{it.success ? `${it.rail_label} · ${it.start_cm}–${it.end_cm}cm` : "—"}</td>
            <td>{it.reason || ""}</td>
          </tr>))}
        </tbody></table>
    )}
    <table className="table"><thead><tr>
      <th style={{ width: 32 }}><input type="checkbox" aria-label="全选"
        checked={hangableRows.length > 0 && hangableRows.every(o => picked.has(o.id))}
        onChange={e => toggleAll(e.target.checked)} /></th>
      <th>票号</th><th>衣物</th><th>衣长</th><th>状态</th><th>到期</th><th></th>
    </tr></thead>
    <tbody>{rows.map(o => <tr key={o.id} className={picked.has(o.id) ? "row-picked" : ""}>
      <td><input type="checkbox" disabled={!hangable(o.status)} checked={picked.has(o.id)}
        onChange={e => toggle(o.id, e.target.checked)} /></td>
      <td className="mono">{o.ticket_code}</td><td>{o.garment_name}</td><td className="mono">{o.length_cm}cm</td><td>{o.status}</td>
      <td className="mono">{new Date(o.due_at).toLocaleString()}</td>
      <td>{hangable(o.status) && <button className="btn-ghost" onClick={() => hang(o.id)}>单件上杆</button>}</td>
    </tr>)}</tbody></table>
  </>);
}
