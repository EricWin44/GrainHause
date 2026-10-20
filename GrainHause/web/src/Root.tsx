import { useCallback, useEffect, useState } from "react";
import Editor from "./App";
import { Home } from "./pages/Home";
import { Login } from "./pages/Login";
import { clearSession, loadSession, saveSession, type Session } from "./lib/auth";

type View = "home" | "login" | "signup" | "app";

function viewFromHash(hash: string): View {
  const h = hash.replace(/^#\/?/, "");
  if (h === "login") return "login";
  if (h === "signup") return "signup";
  if (h === "app") return "app";
  return "home";
}

function hashForView(view: View) {
  return view === "home" ? "#/" : `#/${view}`;
}

export default function Root() {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const [view, setView] = useState<View>(() => {
    const requested = viewFromHash(window.location.hash);
    return requested === "app" && !loadSession() ? "login" : requested;
  });

  const navigate = useCallback((next: View, replace = false) => {
    setView(next);
    const hash = hashForView(next);
    if (replace) window.history.replaceState(null, "", hash);
    else window.history.pushState(null, "", hash);
  }, []);

  // Keep the view in sync with back/forward navigation and direct hash edits.
  useEffect(() => {
    const onHashChange = () => {
      const requested = viewFromHash(window.location.hash);
      setView(requested === "app" && !loadSession() ? "login" : requested);
    };
    window.addEventListener("popstate", onHashChange);
    window.addEventListener("hashchange", onHashChange);
    return () => {
      window.removeEventListener("popstate", onHashChange);
      window.removeEventListener("hashchange", onHashChange);
    };
  }, []);

  // Guard: if we ever land on "app" without a session (e.g. signed out in another tab), bounce
  // to the login screen instead of rendering the editor.
  useEffect(() => {
    if (view === "app" && !session) navigate("login", true);
  }, [view, session, navigate]);

  // The marketing pages scroll like a normal website; the editor owns the whole viewport.
  useEffect(() => {
    document.body.classList.toggle("gh-marketing", view !== "app");
  }, [view]);

  const handleAuthed = (email: string) => {
    const next = { email };
    saveSession(next);
    setSession(next);
    navigate("app");
  };

  const handleSignOut = () => {
    clearSession();
    setSession(null);
    navigate("home", true);
  };

  if (view === "app") {
    if (!session) return null; // the guard effect above will redirect
    return (
      <div className="relative h-full">
        <Editor />
        <button
          onClick={handleSignOut}
          title={`Signed in as ${session.email} — click to sign out`}
          className="fixed right-2 top-2 z-[60] rounded-full border border-border bg-background/60 px-2.5 py-1 text-[11px] text-muted-foreground opacity-60 backdrop-blur-sm transition-opacity hover:opacity-100 hover:text-foreground"
        >
          Sign out
        </button>
      </div>
    );
  }

  if (view === "login") return <Login mode="login" onAuthed={handleAuthed} onNavigate={navigate} />;
  if (view === "signup") return <Login mode="signup" onAuthed={handleAuthed} onNavigate={navigate} />;
  return <Home onNavigate={navigate} />;
}
