import { Link, Outlet, createRootRoute } from "@tanstack/react-router";

export const Route = createRootRoute({
  component: RootLayout,
});

function RootLayout() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-border px-6 py-3 flex items-center gap-6 text-sm">
        <Link
          to="/"
          className="font-semibold text-foreground hover:text-primary transition-colors"
        >
          oh-my-agent
        </Link>
        <nav className="flex gap-4 text-muted-foreground">
          <Link
            to="/sessions"
            activeProps={{ className: "text-foreground" }}
            className="hover:text-foreground transition-colors"
          >
            sessions
          </Link>
          <a
            href="/"
            className="hover:text-foreground transition-colors"
            title="legacy Jinja2 monitoring page"
          >
            ops monitor ↗
          </a>
        </nav>
      </header>
      <main className="flex-1 min-h-0">
        <Outlet />
      </main>
    </div>
  );
}
