import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { BackgroundJobToaster } from "./BackgroundJobToaster";

const links = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/installed", label: "Installed" },
  { to: "/discover", label: "Discover" },
  { to: "/config", label: "Config" },
  { to: "/history", label: "History" },
  { to: "/backups", label: "Backups" },
  { to: "/settings", label: "Settings" },
];

export default function Layout() {
  const { auth, logout } = useAuth();

  return (
    <div className="mx-auto flex min-h-screen max-w-6xl flex-col gap-6 px-4 py-6 md:px-8">
      <BackgroundJobToaster />
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-bark/15 pb-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-moss-deep/80">
            Server administration
          </p>
          <h1 className="font-display text-4xl tracking-tight text-ink md:text-5xl">
            Valheim Mod Manager
          </h1>
        </div>
        <div className="flex items-center gap-3 text-sm text-bark/80">
          <span>{auth?.username}</span>
          <button
            type="button"
            onClick={() => logout()}
            className="btn-secondary px-3 py-1.5 text-sm"
          >
            Log out
          </button>
        </div>
      </header>

      <nav className="flex flex-wrap gap-2">
        {links.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.end}
            className={({ isActive }) =>
              [
                "rounded-full px-3 py-1.5 text-sm transition",
                isActive
                  ? "bg-moss text-paper"
                  : "bg-paper/70 text-bark hover:bg-mist",
              ].join(" ")
            }
          >
            {link.label}
          </NavLink>
        ))}
      </nav>

      <main className="flex-1 pb-10">
        <Outlet />
      </main>
    </div>
  );
}
