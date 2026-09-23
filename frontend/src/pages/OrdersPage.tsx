import { useEffect, useState } from "react";
import { api } from "../api/client";
type O = { id: number; ticket_code: string; garment_name: string; length_cm: number; status: string; due_at: string };
type BatchItem = {
  order_id: number; ticket_code: string; success: boolean;
  rail_id: number | null; rail_label: string | null; start_cm: number | null; end_cm: number | null;
  reason: string | null;
};
const hangable = (s: string) => s === "ready" || s === "overdue";

export default function OrdersPage() {
  const [rows, setRows] = useState<O[]>([]);
  const [msg, setMsg] = useState(""); const [err, setErr] = useState("");
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [results, setResults] = useState<BatchItem[] | null>(null);
  const [busy, setBusy] = useState(false);
  const reload = () => api<O[]>("/orders").then(setRows);
  useEffect(() => { reload(); }, []);

  function toggle(id: number) {
    setPicked(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function hang(id: number) {
    setMsg(""); setErr("");
    try {
      const o = await api<O>("/hang", { method: "POST", body: JSON.stringify({ order_id: id }) });
      setMsg(`${o.ticket_code} 已上杆`);
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }

  async function batchHang() {
    setMsg(""); setErr(""); setResults(null);
    if (picked.size === 0) {
      setErr("请先勾选要上杆的工单");
      return;
    }
    setBusy(true);
    try {
      const body = { order_ids: [...picked] };
      const res = await api<{ results: BatchItem[] }>("/hang/batch", {
        method: "POST", body: JSON.stringify(body),
      });
      setResults(res.results);
      const ok = res.results.filter(r => r.success).length;
      const fail = res.results.length - ok;
      setMsg(`批量上杆完成：成功 ${ok} 单${fail ? `，失败 ${fail} 单` : ""}（按到期先后占位）`);
      setPicked(new Set());
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (<>
    <h2>工单</h2>
    {msg && <div className="ok">{msg}</div>}
    {err && <div className="err">{err}</div>}

    <div className="toolbar">
      <button onClick={batchHang} disabled={busy || picked.size === 0}>
        {busy ? "上杆中…" : `批量上杆（${picked.size}）`}
      </button>
      <span className="hint">勾选 ready / overdue 工单，服务端按到期时间从早到晚依次 First-Fit 占位</span>
    </div>

    {results && (
      <div className="batch-results">
        <div className="batch-results-title">本次批量上杆结果</div>
        <table className="table"><thead><tr>
          <th>票号</th><th>结果</th><th>挂杆</th><th>占位区间</th><th>原因</th>
        </tr></thead><tbody>
          {results.map(r => (
            <tr key={r.order_id}>
              <td className="mono">{r.ticket_code || `#${r.order_id}`}</td>
              <td>{r.success ? <span className="ok">成功</span> : <span className="err">失败</span>}</td>
              <td>{r.rail_label ?? "—"}</td>
              <td className="mono">{r.success ? `${r.start_cm}–${r.end_cm} cm` : "—"}</td>
              <td>{r.reason ?? ""}</td>
            </tr>
          ))}
        </tbody></table>
      </div>
    )}

    <table className="table"><thead><tr>
      <th></th><th>票号</th><th>衣物</th><th>衣长</th><th>状态</th><th>到期</th><th></th>
    </tr></thead>
    <tbody>{rows.map(o => <tr key={o.id}>
      <td>{hangable(o.status) && (
        <input type="checkbox" checked={picked.has(o.id)} onChange={() => toggle(o.id)} aria-label={`选择 ${o.ticket_code}`} />
      )}</td>
      <td className="mono">{o.ticket_code}</td><td>{o.garment_name}</td>
      <td className="mono">{o.length_cm}cm</td><td>{o.status}</td>
      <td className="mono">{new Date(o.due_at).toLocaleString()}</td>
      <td>{hangable(o.status) && <button onClick={() => hang(o.id)}>上杆</button>}</td>
    </tr>)}</tbody></table>
  </>);
}
