import { useState } from "react";
import { ArrowLeft } from "lucide-react";
import { SignInPage, type Testimonial } from "@/components/ui/sign-in";
import { checkCredentials, DEMO_EMAIL, DEMO_PASSWORD } from "@/lib/auth";

const testimonials: Testimonial[] = [
  {
    avatarSrc: "https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=100&h=100&fit=crop&q=80",
    name: "Sarah Chen",
    handle: "wedding photographer",
    text: "I shoot digital and let GrainHause give me the Portra look my clients actually ask for.",
  },
  {
    avatarSrc: "https://images.unsplash.com/photo-1506794778202-cad84cf45f1d?w=100&h=100&fit=crop&q=80",
    name: "Marcus Johnson",
    handle: "street photographer",
    text: "The grain and halation are the first I've seen online that don't look like a filter.",
  },
  {
    avatarSrc: "https://images.unsplash.com/photo-1492562080023-ab3db95bfbce?w=100&h=100&fit=crop&q=80",
    name: "David Martinez",
    handle: "film hobbyist",
    text: "Runs entirely in my browser and my photos never leave my computer. Exactly what I wanted.",
  },
];

const HERO_IMAGE = "https://images.unsplash.com/photo-1525097487452-6278ff080c31?w=1200&h=1600&fit=crop&q=80";

export function Login({
  mode,
  onAuthed,
  onNavigate,
}: {
  mode: "login" | "signup";
  onAuthed: (email: string) => void;
  onNavigate: (view: "home" | "login" | "signup") => void;
}) {
  const [error, setError] = useState("");

  const handleSignIn = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const email = String(data.get("email") || "");
    const password = String(data.get("password") || "");

    if (mode === "signup") {
      // Front-end-only demo: there's no account database, so "creating an account" just
      // signs you into the one demo account that exists.
      onAuthed(DEMO_EMAIL);
      return;
    }

    if (checkCredentials(email, password)) {
      setError("");
      onAuthed(DEMO_EMAIL);
    } else {
      setError("That email or password doesn't match the demo account below.");
    }
  };

  const handleGoogleSignIn = () => {
    window.alert("Google sign-in isn't wired up in this demo — use the email form with the demo account.");
  };

  const handleResetPassword = () => {
    window.alert(`This is a demo account, so there's nothing to reset. Sign in with ${DEMO_EMAIL}.`);
  };

  return (
    <div className="relative">
      <button
        onClick={() => onNavigate("home")}
        className="absolute left-4 top-4 z-10 flex items-center gap-1.5 rounded-full border border-border bg-background/70 px-3 py-1.5 text-sm text-muted-foreground backdrop-blur-sm transition-colors hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to site
      </button>

      <SignInPage
        title={
          <span className="font-light text-foreground tracking-tighter">
            {mode === "signup" ? "Create your account" : "Welcome back"}
          </span>
        }
        description={
          mode === "signup"
            ? "Start developing your digital photos into film in minutes."
            : "Sign in to open your darkroom."
        }
        heroImageSrc={HERO_IMAGE}
        testimonials={testimonials}
        submitLabel={mode === "signup" ? "Create account" : "Sign In"}
        error={mode === "login" ? error : ""}
        createAccountPrompt={mode === "signup" ? "Already have an account?" : "New to our platform?"}
        createAccountLabel={mode === "signup" ? "Sign in" : "Create Account"}
        onSignIn={handleSignIn}
        onGoogleSignIn={handleGoogleSignIn}
        onResetPassword={handleResetPassword}
        onCreateAccount={() => onNavigate(mode === "signup" ? "login" : "signup")}
        footerNote={
          <div className="animate-element animate-delay-900 rounded-2xl border border-dashed border-border bg-foreground/5 px-4 py-3 text-xs text-muted-foreground">
            <p className="font-medium text-foreground/90">Demo account</p>
            {mode === "signup" ? (
              <p className="mt-1">Signing up in this demo just opens the sample account below — no form fields are required.</p>
            ) : (
              <>
                <p className="mt-1">
                  Email <span className="text-foreground/90">{DEMO_EMAIL}</span>
                </p>
                <p>
                  Password <span className="text-foreground/90">{DEMO_PASSWORD}</span>
                </p>
              </>
            )}
          </div>
        }
      />
    </div>
  );
}
