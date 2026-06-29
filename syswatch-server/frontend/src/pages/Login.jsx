import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";

export default function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const { login } = useAuth();
  const navigate = useNavigate();

  async function handleSubmit(e) {
    e.preventDefault(); // prevent browser default form POST
    setLoading(true);
    setError(null);

    const result = await login(username, password);

    if (result.ok) {
      navigate("/dashboard", { replace: true });
    } else {
      setError(result.error || "Invalid credentials");
      setLoading(false);
      setPassword(""); // clear password on failure
    }
  }

  return (
    <div className="login-body">
      <div className="login-wrap">
        <div className="login-card">
          <div className="login-brand">
            <span className="brand-icon brand-icon--lg">◈</span>
            <h1 className="login-title">syswatch</h1>
            <p className="login-subtitle">Infrastructure monitoring</p>
          </div>

          {error && (
            <div className="alert alert--error" role="alert">
              {error}
            </div>
          )}

          <form className="login-form" onSubmit={handleSubmit}>
            <div className="field">
              {/* htmlFor (not `for`) — JSX uses camelCase for HTML attributes */}
              <label className="field-label" htmlFor="username">
                Username
              </label>
              <input
                className="field-input"
                id="username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
                required
                disabled={loading}
              />
            </div>

            <div className="field">
              <label className="field-label" htmlFor="password">
                Password
              </label>
              <input
                className="field-input"
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
                disabled={loading}
              />
            </div>

            <button
              type="submit"
              className="btn btn--primary btn--full"
              disabled={loading || !username || !password}
            >
              {loading ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
