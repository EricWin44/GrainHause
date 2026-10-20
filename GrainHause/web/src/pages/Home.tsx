import {
  ArrowRight,
  Aperture,
  Film,
  Image as ImageIcon,
  Lock,
  Sparkles,
  SunMedium,
  Upload,
  Wand2,
} from "lucide-react";
import { BeforeAfterSlider } from "@/components/BeforeAfterSlider";

const NAV_LINKS: [string, string][] = [
  ["#features", "Features"],
  ["#how-it-works", "How it works"],
  ["#looks", "Looks"],
];

const FEATURES: { icon: typeof Film; title: string; body: string }[] = [
  {
    icon: Film,
    title: "Real film stocks",
    body: "Kodak Portra, Fujifilm, CineStill and more — each tuned from the film's own colour and contrast character, not a one-size preset.",
  },
  {
    icon: Sparkles,
    title: "Grain that behaves like grain",
    body: "Size, roughness and amount respond to exposure the way real negative does, instead of a flat noise overlay.",
  },
  {
    icon: SunMedium,
    title: "Halation, bloom & light leaks",
    body: "The soft red glow around highlights and stray light leaks that make a shot feel shot on film, not filtered.",
  },
  {
    icon: Wand2,
    title: "Non-destructive develop panel",
    body: "Exposure, white balance, tone curve, skin tones, dust & texture, borders — adjust anything, reset anything.",
  },
  {
    icon: Lock,
    title: "Runs on your computer",
    body: "GrainHause develops photos locally in your browser. Nothing you open is ever uploaded anywhere.",
  },
  {
    icon: ImageIcon,
    title: "Batch-friendly exports",
    body: "Sync a look across a whole shoot, then export the set as JPEG, TIFF or PNG at any size you need.",
  },
];

const STEPS: { icon: typeof Upload; title: string; body: string }[] = [
  { icon: Upload, title: "Open your photos", body: "Drop in RAW or JPEG files straight from your camera or phone." },
  { icon: Aperture, title: "Pick a film stock", body: "Choose from dozens of stocks, or dial in your own look by hand." },
  { icon: Film, title: "Develop & export", body: "Fine-tune grain, light and colour, then export ready-to-share files." },
];

const HERO_PHOTO = "https://images.unsplash.com/photo-1500530855697-b586d89ba3ee?w=1200&h=900&fit=crop&q=80";

const LOOKS: { stock: string; photo: string; filter: string; grain?: boolean }[] = [
  {
    stock: "Kodak Portra 400",
    photo: "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=900&h=900&fit=crop&q=80",
    filter: "sepia(.3) saturate(1.35) contrast(1.05) brightness(1.03) hue-rotate(-6deg)",
    grain: true,
  },
  {
    stock: "CineStill 800T",
    photo: "https://images.unsplash.com/photo-1522202176988-66273c2fd55f?w=900&h=900&fit=crop&q=80",
    filter: "contrast(1.15) saturate(1.25) hue-rotate(8deg) brightness(.92)",
    grain: true,
  },
  {
    stock: "Fujifilm Pro 400H",
    photo: "https://images.unsplash.com/photo-1517841905240-472988babdf9?w=900&h=900&fit=crop&q=80",
    filter: "saturate(1.15) contrast(.95) hue-rotate(-14deg) brightness(1.04)",
    grain: true,
  },
];

export function Home({ onNavigate }: { onNavigate: (view: "login" | "signup") => void }) {
  return (
    <div className="bg-background text-foreground">
      {/* ---------------------------------------------------------------- nav */}
      <header className="sticky top-0 z-40 border-b border-border/60 bg-background/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-6 py-4">
          <a href="#top" className="flex items-center gap-2">
            <img src="/icon.png" alt="" width={28} height={28} className="size-7 shrink-0 rounded-md" />
            <span className="text-sm font-bold tracking-[0.25em]">GRAINHAUSE</span>
          </a>
          <nav className="hidden flex-1 items-center gap-6 text-sm text-muted-foreground md:flex">
            {NAV_LINKS.map(([href, label]) => (
              <a key={href} href={href} className="transition-colors hover:text-foreground">
                {label}
              </a>
            ))}
          </nav>
          <span className="flex-1 md:hidden" />
          <div className="flex items-center gap-2">
            <button
              onClick={() => onNavigate("login")}
              className="rounded-full px-4 py-2 text-sm font-medium text-foreground/90 transition-colors hover:bg-foreground/5"
            >
              Log in
            </button>
            <button
              onClick={() => onNavigate("signup")}
              className="rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
            >
              Sign up
            </button>
          </div>
        </div>
      </header>

      {/* ---------------------------------------------------------------- hero */}
      <section id="top" className="mx-auto max-w-6xl px-6 pb-20 pt-16 md:pt-24">
        <div className="grid items-center gap-12 md:grid-cols-2">
          <div className="flex flex-col gap-6">
            <span className="w-fit rounded-full border border-border bg-foreground/5 px-3 py-1 text-xs tracking-wide text-muted-foreground">
              Digital in. Film out.
            </span>
            <h1 className="text-4xl font-semibold leading-[1.08] tracking-tight md:text-6xl">
              Turn your digital photos into <span className="text-primary">real film</span>.
            </h1>
            <p className="max-w-md text-base text-muted-foreground md:text-lg">
              GrainHause develops your shots with the colour, grain and glow of real film stocks —
              Kodak, Fujifilm, CineStill and more — right in your browser.
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <button
                onClick={() => onNavigate("signup")}
                className="flex items-center gap-2 rounded-full bg-primary px-6 py-3 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
              >
                Get started free
                <ArrowRight className="h-4 w-4" />
              </button>
              <a
                href="#how-it-works"
                className="rounded-full border border-border px-6 py-3 text-sm font-medium text-foreground/90 transition-colors hover:bg-foreground/5"
              >
                See how it works
              </a>
            </div>
            <p className="text-xs text-muted-foreground">
              No account needed to look around — the demo login is on the sign-in page.
            </p>
          </div>

          <div className="flex flex-col gap-3">
            <BeforeAfterSlider
              beforeSrc={HERO_PHOTO}
              afterSrc={HERO_PHOTO}
              afterClassName="[filter:sepia(.3)_saturate(1.35)_contrast(1.05)_brightness(1.03)_hue-rotate(-6deg)]"
              beforeLabel="Digital"
              afterLabel="Portra 400"
              grainOverlay
              className="shadow-2xl shadow-black/40"
            />
            <p className="text-center text-xs text-muted-foreground">Drag the handle to compare — your own before/afters go here.</p>
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- looks gallery */}
      <section id="looks" className="border-t border-border/60 bg-card/30 py-20">
        <div className="mx-auto max-w-6xl px-6">
          <div className="mb-10 flex flex-col gap-2">
            <h2 className="text-2xl font-semibold tracking-tight md:text-3xl">Every stock has its own character</h2>
            <p className="max-w-xl text-muted-foreground">Drag any slider below to see a stock's colour and grain for yourself.</p>
          </div>
          <div className="grid gap-6 md:grid-cols-3">
            {LOOKS.map((look) => (
              <div key={look.stock} className="flex flex-col gap-3">
                <BeforeAfterSlider
                  beforeSrc={look.photo}
                  afterSrc={look.photo}
                  afterClassName={`[filter:${look.filter.replace(/ /g, "_")}]`}
                  aspectClassName="aspect-square"
                  beforeLabel="Digital"
                  afterLabel={look.stock}
                  grainOverlay={look.grain}
                />
                <h3 className="text-sm font-medium">{look.stock}</h3>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- features */}
      <section id="features" className="py-20">
        <div className="mx-auto max-w-6xl px-6">
          <div className="mb-10 flex flex-col gap-2">
            <h2 className="text-2xl font-semibold tracking-tight md:text-3xl">Built for people who miss film</h2>
            <p className="max-w-xl text-muted-foreground">Every control in the develop panel is modeled on how the real thing behaves.</p>
          </div>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map(({ icon: Icon, title, body }) => (
              <div key={title} className="flex flex-col gap-3 rounded-2xl border border-border bg-card/40 p-6">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/15 text-primary">
                  <Icon className="h-5 w-5" />
                </div>
                <h3 className="font-medium">{title}</h3>
                <p className="text-sm text-muted-foreground">{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- how it works */}
      <section id="how-it-works" className="border-t border-border/60 bg-card/30 py-20">
        <div className="mx-auto max-w-6xl px-6">
          <div className="mb-10 flex flex-col gap-2">
            <h2 className="text-2xl font-semibold tracking-tight md:text-3xl">From upload to film in three steps</h2>
          </div>
          <div className="grid gap-8 md:grid-cols-3">
            {STEPS.map(({ icon: Icon, title, body }, i) => (
              <div key={title} className="relative flex flex-col gap-3 rounded-2xl border border-border bg-background/60 p-6">
                <span className="text-xs font-medium tracking-wide text-primary">STEP {i + 1}</span>
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/15 text-primary">
                  <Icon className="h-5 w-5" />
                </div>
                <h3 className="font-medium">{title}</h3>
                <p className="text-sm text-muted-foreground">{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- final CTA */}
      <section className="py-20">
        <div className="mx-auto flex max-w-4xl flex-col items-center gap-6 rounded-3xl border border-border bg-card/40 px-6 py-16 text-center">
          <h2 className="text-3xl font-semibold tracking-tight md:text-4xl">Ready to develop your photos?</h2>
          <p className="max-w-md text-muted-foreground">Sign up free and open your first roll in the darkroom.</p>
          <button
            onClick={() => onNavigate("signup")}
            className="flex items-center gap-2 rounded-full bg-primary px-6 py-3 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Get started free
            <ArrowRight className="h-4 w-4" />
          </button>
        </div>
      </section>

      {/* ---------------------------------------------------------------- footer */}
      <footer className="border-t border-border/60 py-10">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 text-xs text-muted-foreground md:flex-row">
          <span>© {new Date().getFullYear()} GrainHause. Film names are used only to describe the look.</span>
          <button onClick={() => onNavigate("login")} className="transition-colors hover:text-foreground">
            Log in
          </button>
        </div>
      </footer>
    </div>
  );
}
