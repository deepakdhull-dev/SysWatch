const BASE = "/api";

async function apiFetch(path, options = {}) {
  try {
    const res = await fetch(`${BASE}${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
      },
      ...options,
    });

    if (res.status === 401) {
      window.location.href = "/login";
      return null;
    }

    return res;
  } catch (err) {
    console.error(`API fetch failed for ${path}:`, err.message);
    return null;
  }
}
async function parseJSON(res) {
  if (!res) return null;
  if (!res.ok) {
    console.error(`API error ${res.status} for ${res.url}`);
    return null;
  }
  return res.json();
}

export const api = {
  async login(username, password) {
    const res = await apiFetch("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    if (!res) return { ok: false, error: "Network error" };
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      return { ok: false, error: body.detail || "Invalid credentials" };
    }
    return { ok: true };
  },

  async logout() {
    await apiFetch("/auth/logout", { method: "POST" });
  },

  async me() {
    try {
      const res = await fetch(`${BASE}/me`, {
        headers: { "Content-Type": "application/json" },
      });
      if (res.status === 401) return { authenticated: false };
      if (!res.ok) return { authenticated: false };
      return res.json();
    } catch {
      return { authenticated: false };
    }
  },

  async getConfig() {
    return parseJSON(await apiFetch("/config"));
  },

  async getAgents() {
    return parseJSON(await apiFetch("/agents"));
  },

  async getAgent(agentId) {
    return parseJSON(await apiFetch(`/agents/${encodeURIComponent(agentId)}`));
  },

  async getAgentMetrics(agentId, hours = 24, bucketMinutes = 5) {
    return parseJSON(
      await apiFetch(
        `/agents/${encodeURIComponent(agentId)}/metrics?hours=${hours}&bucket_minutes=${bucketMinutes}`,
      ),
    );
  },

  async getAgentDisks(agentId, hours = 1) {
    return parseJSON(
      await apiFetch(
        `/agents/${encodeURIComponent(agentId)}/disks?hours=${hours}`,
      ),
    );
  },

  async getAgentNetwork(agentId, hours = 1) {
    return parseJSON(
      await apiFetch(
        `/agents/${encodeURIComponent(agentId)}/network?hours=${hours}`,
      ),
    );
  },

  async getAgentServices(agentId) {
    return parseJSON(
      await apiFetch(`/agents/${encodeURIComponent(agentId)}/services`),
    );
  },
  async getDashboardSummary() {
    return parseJSON(await apiFetch("/dashboard/summary"));
  },

  async provision(agentId, services, serverHost) {
    const res = await apiFetch("/agents/provision", {
      method: "POST",
      body: JSON.stringify({
        agent_id: agentId,
        services,
        server_host: serverHost,
      }),
    });

    if (!res) return { error: "Network error" };

    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        detail = body.detail || detail;
      } catch (_) {}
      return { error: detail };
    }

    const blob = await res.blob();
    return { blob, filename: `${agentId}_bundle.zip` };
  },
};
