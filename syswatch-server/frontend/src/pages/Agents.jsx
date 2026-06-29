/**
 * Agents.jsx — registered agent list with live online/offline status
 *
 * CONDITIONAL RENDERING
 * ----------------------
 * React renders JSX returned from the component function.
 * Conditional rendering uses JS expressions:
 *
 *   {loading && <Spinner />}           — renders Spinner only when loading=true
 *   {error ? <Error /> : <Table />}    — ternary: Error if error, Table otherwise
 *   {agents.length === 0 && <Empty />} — renders Empty only when no agents
 *
 * LIST RENDERING — KEY PROP
 * --------------------------
 * When rendering lists with .map(), each element needs a unique `key` prop.
 * React uses keys to track which items changed between renders, enabling
 * efficient DOM updates (only changed items re-render).
 *
 * Keys must be stable and unique within the list. Using array index as key
 * is wrong if items can be reordered — use a real ID (agent_id here).
 *
 *   {agents.map(agent => (
 *     <tr key={agent.agent_id}>...</tr>
 *   ))}
 */

import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import StatusDot from "../components/StatusDot";
import { formatRelativeTime } from "../utils";

export default function Agents() {
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    loadAgents();
    const id = setInterval(loadAgents, 30_000);
    return () => clearInterval(id);
  }, []);

  async function loadAgents() {
    const data = await api.getAgents();
    if (data) {
      setAgents(data);
      setError(null);
    } else {
      setError("Failed to load agents");
    }
    setLoading(false);
  }

  return (
    <>
      <header className="page-header">
        <h1 className="page-title">Agents</h1>
        <div className="header-actions">
          <Link to="/add-agent" className="btn btn--primary btn--sm">
            + Add Agent
          </Link>
        </div>
      </header>

      <div className="page-content">
        {error && <div className="alert alert--error">{error}</div>}

        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th
                  scope="col"
                  style={{ width: "2.5rem" }}
                  aria-label="Status"
                />
                <th scope="col">Agent ID</th>
                <th scope="col">Hostname</th>
                <th scope="col">OS</th>
                <th scope="col">CPU</th>
                <th scope="col">Last Seen</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={6} className="table-empty">
                    Loading…
                  </td>
                </tr>
              ) : agents.length === 0 ? (
                <tr>
                  <td colSpan={6} className="table-empty">
                    No agents registered yet.{" "}
                    <Link to="/add-agent" className="link">
                      Provision your first agent →
                    </Link>
                  </td>
                </tr>
              ) : (
                agents.map((agent) => (
                  <tr key={agent.agent_id} className="table-row">
                    <td>
                      <StatusDot online={agent.online} />
                    </td>
                    <td className="mono">{agent.agent_id}</td>
                    <td>{agent.hostname}</td>
                    <td className="text-muted">{agent.os_name}</td>
                    <td className="text-muted">
                      {agent.cpu_model} &middot; {agent.cpu_cores}c/
                      {agent.cpu_threads}t
                    </td>
                    <td className="text-muted text-sm">
                      {formatRelativeTime(agent.last_seen)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
