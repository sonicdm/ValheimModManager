import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import Layout from "./components/Layout";
import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import InstalledPage from "./pages/InstalledPage";
import DiscoverPage from "./pages/DiscoverPage";
import PackageDetailPage from "./pages/PackageDetailPage";
import ConfigPage from "./pages/ConfigPage";
import HistoryPage from "./pages/HistoryPage";
import BackupsPage from "./pages/BackupsPage";
import ProfilesPage from "./pages/ProfilesPage";
import SettingsPage from "./pages/SettingsPage";

function Protected({ children }: { children: React.ReactNode }) {
  const { auth, loading } = useAuth();
  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center text-bark/70">
        Loading…
      </div>
    );
  }
  if (!auth?.authenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <Protected>
              <Layout />
            </Protected>
          }
        >
          <Route index element={<DashboardPage />} />
          <Route path="installed" element={<InstalledPage />} />
          <Route path="discover" element={<DiscoverPage />} />
          <Route path="discover/:source/:fullName" element={<PackageDetailPage />} />
          <Route path="config" element={<ConfigPage />} />
          <Route path="history" element={<HistoryPage />} />
          <Route path="profiles" element={<ProfilesPage />} />
          <Route path="backups" element={<BackupsPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </AuthProvider>
  );
}
