/**
 * AddAgent.jsx — agent provisioning form
 *
 * MULTI-STEP UI STATE
 * --------------------
 * The form has three visual states: idle → loading → success/error.
 * We manage this with:
 *   loading: boolean  — shows spinner, disables button
 *   success: string|null — filename if bundle was downloaded
 *   error:   string|null — error message to show
 *
 * Rather than a single `step` enum (which would need a switch statement
 * to render), we use three independent booleans/values. They cannot all
 * be truthy simultaneously in normal flow, which keeps the logic simple.
 *
 * BLOB DOWNLOAD FROM JSX
 * ----------------------
 * The downloadBlob() utility in utils.js handles the browser download.
 * It is called after a successful provision response. The download is
 * transparent to the user — the browser's native save dialog appears.
 *
 * TEXTAREA AS CONTROLLED INPUT
 * -----------------------------
 * Same controlled pattern as <input>:
 *   value={services} onChange={e => setServices(e.target.value)}
 * The newline character (\n) in the textarea value is preserved by React —
 * splitting on '\n' correctly gives one service per line.
 */

import { useState, useEffect } from "react";
import { api } from "../api";
import { downloadBlob } from "../utils";

export default function AddAgent() {
  const [agentId, setAgentId] = useState("");
  const [services, setServices] = useState("");
  const [serverHost, setServerHost] = useState("");
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(null); // filename string
  const [error, setError] = useState(null);

  // Pre-fill server host from server config on mount
  useEffect(() => {
    api.getConfig().then((cfg) => {
      if (cfg?.default_server_host) setServerHost(cfg.default_server_host);
    });
  }, []);

  async function handleProvision() {
    const id = agentId.trim();
    if (!id) return;

    setLoading(true);
    setError(null);
    setSuccess(null);

    // Parse services textarea — split on newlines, trim, drop empty lines
    const servicesList = services
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);

    const result = await api.provision(
      id,
      servicesList,
      serverHost.trim() || "localhost",
    );

    if (result?.blob) {
      downloadBlob(result.blob, result.filename);
      setSuccess(result.filename);
      setAgentId("");
      setServices("");
    } else {
      setError(result?.error || "Provisioning failed");
    }

    setLoading(false);
  }

  function reset() {
    setSuccess(null);
    setError(null);
  }

  // Validate agent ID: letters, numbers, hyphens, underscores, 1-64 chars
  const agentIdValid =
    /^[a-zA-Z0-9][a-zA-Z0-9\-_]{0,62}[a-zA-Z0-9]$|^[a-zA-Z0-9]$/.test(
      agentId.trim(),
    );

  return (
    <>
      <header className="page-header">
        <h1 className="page-title">Add Agent</h1>
      </header>

      <div className="page-content">
        <div className="card card--narrow">
          <h2 className="card-title">Provision New Agent</h2>
          <p className="text-muted" style={{ marginBottom: "1.5rem" }}>
            Fill in the details below. A <code>bundle.zip</code> will download
            containing certificates and config. Copy it to the target machine
            and run <code>sudo bash install.sh</code>.
          </p>

          {/* Success state */}
          {success && (
            <div>
              <div className="alert alert--success">
                <strong>Bundle downloaded: </strong>
                <code>{success}</code>
                <br />
                Copy to the target machine and run:{" "}
                <code>sudo bash install.sh</code>
              </div>
              <button className="btn btn--ghost btn--sm" onClick={reset}>
                Provision another agent
              </button>
            </div>
          )}

          {/* Form state (shown when not success) */}
          {!success && (
            <>
              <div className="field">
                <label className="field-label" htmlFor="agent-id">
                  Agent ID{" "}
                  <span className="required" aria-label="required">
                    *
                  </span>
                </label>
                <input
                  className="field-input field-input--mono"
                  id="agent-id"
                  type="text"
                  value={agentId}
                  onChange={(e) => setAgentId(e.target.value)}
                  placeholder="e.g. web-01"
                  maxLength={64}
                  required
                  autoFocus
                  disabled={loading}
                />
                <span className="field-hint">
                  Letters, numbers, hyphens, underscores only. Used as the
                  certificate Common Name.
                </span>
              </div>

              <div className="field">
                <label className="field-label" htmlFor="services">
                  Monitored Services{" "}
                  <span className="badge badge--optional">optional</span>
                </label>
                <textarea
                  className="field-input field-textarea"
                  id="services"
                  value={services}
                  onChange={(e) => setServices(e.target.value)}
                  rows={5}
                  placeholder={
                    "nginx.service\npostgresql.service\nredis.service"
                  }
                  spellCheck={false}
                  disabled={loading}
                />
                <span className="field-hint">
                  One systemd service name per line. Leave empty to skip service
                  monitoring.
                </span>
              </div>

              <div className="field">
                <label className="field-label" htmlFor="server-host">
                  Server Host
                </label>
                <input
                  className="field-input field-input--mono"
                  id="server-host"
                  type="text"
                  value={serverHost}
                  onChange={(e) => setServerHost(e.target.value)}
                  placeholder="localhost"
                  disabled={loading}
                />
                <span className="field-hint">
                  The address this agent will connect to via gRPC. Must be
                  reachable from the agent machine.
                </span>
              </div>

              {error && <div className="alert alert--error">{error}</div>}

              <div className="form-actions">
                <button
                  className="btn btn--primary"
                  onClick={handleProvision}
                  disabled={loading || !agentIdValid}
                >
                  {loading ? "Generating…" : "Generate Bundle"}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
