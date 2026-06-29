import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import Sidebar from "./components/Sidebar";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Agents from "./pages/Agents";
import AddAgent from "./pages/AddAgent";

function Layout({ children }) {
  return (
    <div className="app-body">
      <Sidebar />
      <main className="main">{children}</main>
    </div>
  );
}
function PrivateRoute({ children }) {
  const { authenticated, loading } = useAuth();

  if (loading) {
    return null;
  }

  return authenticated ? children : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          {/* Public routes — no auth required */}
          <Route path="/login" element={<Login />} />

          {/* Root redirect */}
          <Route path="/" element={<Navigate to="/dashboard" replace />} />

          {/* Protected routes — wrapped in PrivateRoute + Layout */}
          <Route
            path="/dashboard"
            element={
              <PrivateRoute>
                <Layout>
                  <Dashboard />
                </Layout>
              </PrivateRoute>
            }
          />

          <Route
            path="/agents"
            element={
              <PrivateRoute>
                <Layout>
                  <Agents />
                </Layout>
              </PrivateRoute>
            }
          />

          <Route
            path="/add-agent"
            element={
              <PrivateRoute>
                <Layout>
                  <AddAgent />
                </Layout>
              </PrivateRoute>
            }
          />

          {/* Catch-all: redirect unknown paths to dashboard */}
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
