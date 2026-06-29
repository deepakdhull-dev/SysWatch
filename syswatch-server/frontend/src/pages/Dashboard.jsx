import { useState, useEffect } from "react";
import { api } from "../api";

export default function Dashboard() {
  const [agents, setAgents] = useState([]);
  const [grafanaUrl, setGrafanaUrl] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Load Grafana URL from server config (only once — it doesn't change)
    api.getConfig().then((cfg) => {
      if (cfg?.grafana_url) setGrafanaUrl(cfg.grafana_url);
    });

    // Load agent data immediately then poll every 30 seconds
    loadAgents();
    const id = setInterval(loadAgents, 30_000);
    return () => clearInterval(id); // cleanup on unmount
  }, []);

  async function loadAgents() {
    const data = await api.getAgents();
    if (data) {
      setAgents(data);
      setLoading(false);
    }
  }

  // Derived state — computed from agents, not separate useState
  const total = agents.length;
  const connected = agents.filter((a) => a.online).length;
  const offline = total - connected;

  return (
    <>
      <header className="page-header">
        <h1 className="page-title">Dashboard</h1>
        <div className="header-actions">
          <button
            className="btn btn--ghost btn--sm"
            onClick={loadAgents}
            title="Refresh now"
          >
            ↻ Refresh
          </button>
        </div>
      </header>

      <div className="page-content">
        {/* Summary stat cards */}
        <div className="stats-row">
          <div className="stat-card">
            <span className="stat-value stat-value--accent">
              {loading ? "—" : connected}
            </span>
            <span className="stat-label">Connected</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{loading ? "—" : total}</span>
            <span className="stat-label">Registered</span>
          </div>
          <div className="stat-card">
            <span className="stat-value stat-value--muted">
              {loading ? "—" : offline}
            </span>
            <span className="stat-label">Offline</span>
          </div>
        </div>

        {/* Grafana embed */}
        <div className="grafana-wrap">
          {grafanaUrl ? (
            <iframe
              src={grafanaUrl}
              className="grafana-frame"
              title="syswatch metrics dashboard"
              allowFullScreen
            />
          ) : (
            <div className="grafana-placeholder">
              <p className="text-muted">
                Grafana not configured. Set <code>grafana.url</code> and{" "}
                <code>grafana.dashboard_uid</code> in config.yaml.
              </p>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
