import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, AuthStatus, setCsrfToken } from "../api/client";

type AuthContextValue = {
  auth: AuthStatus | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const status = await api.get<AuthStatus>("/api/auth/status");
    setCsrfToken(status.csrf_token ?? null);
    setAuth(status);
  }, []);

  useEffect(() => {
    refresh()
      .catch(() => setAuth({ authenticated: false }))
      .finally(() => setLoading(false));
  }, [refresh]);

  const login = useCallback(async (username: string, password: string) => {
    const status = await api.post<AuthStatus>("/api/auth/login", { username, password });
    setCsrfToken(status.csrf_token ?? null);
    setAuth(status);
  }, []);

  const logout = useCallback(async () => {
    await api.post("/api/auth/logout");
    setCsrfToken(null);
    setAuth({ authenticated: false });
  }, []);

  const value = useMemo(
    () => ({ auth, loading, login, logout, refresh }),
    [auth, loading, login, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside provider");
  return ctx;
}
