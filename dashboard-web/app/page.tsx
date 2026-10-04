import Link from "next/link";
import {
  ArrowRight,
  BookOpen,
  GitCompareArrows,
  Scale,
  ShieldCheck,
} from "lucide-react";

const CAPABILITIES = [
  {
    icon: Scale,
    title: "A claim ledger, not a summary",
    body: "Every sentence your sources make becomes a claim with a verbatim quote and character offsets, so any statement can be traced back to the exact text that said it.",
  },
  {
    icon: GitCompareArrows,
    title: "Where they disagree",
    body: "Claims that cannot both be true are grouped into opposing camps. A schedule that moved, a figure that was revised, a denial where others report a result.",
  },
  {
    icon: BookOpen,
    title: "A wiki that rebuilds itself",
    body: "Pages compile from claims, not from prose. Change a source and only the pages that cited it recompile; every sentence keeps its citation.",
  },
  {
    icon: ShieldCheck,
    title: "Claims checked as you read",
    body: "Ask a question and each sentence arrives with a verdict — supported, contradicted, disputed, unsupported — and the evidence that decided it.",
  },
];

export default function Landing() {
  return (
    <main id="main" className="mx-auto min-h-dvh max-w-5xl px-6 py-16">
      <p className="text-sm font-medium tracking-wide text-accent uppercase">
        Research, with receipts
      </p>
      <h1 className="serif mt-4 max-w-3xl text-4xl leading-tight font-semibold text-balance sm:text-5xl">
        Your sources disagree. Now you will know where — and what changed.
      </h1>
      <p className="mt-5 max-w-2xl text-lg leading-relaxed text-muted">
        SourceHero turns a pile of articles and notes into a claim-level knowledge
        system: what each source asserts, where they contradict each other, and what
        changed since you last looked. It runs entirely on your machine, and works
        with no model configured.
      </p>
      <div className="mt-8 flex flex-wrap gap-3">
        <Link
          href="/app"
          className="inline-flex items-center gap-2 rounded-md border border-accent bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          Open the app
          <ArrowRight className="h-4 w-4" aria-hidden />
        </Link>
        <Link
          href="/login"
          className="inline-flex items-center gap-2 rounded-md border border-line bg-surface px-4 py-2 text-sm font-medium hover:border-accent hover:text-accent"
        >
          Sign in
        </Link>
      </div>

      <div className="mt-16 grid gap-5 sm:grid-cols-2">
        {CAPABILITIES.map((item) => (
          <section
            key={item.title}
            className="rounded-lg border border-line bg-surface p-5"
          >
            <item.icon className="h-5 w-5 text-accent" aria-hidden />
            <h2 className="serif mt-3 text-lg font-semibold">{item.title}</h2>
            <p className="mt-2 text-sm leading-relaxed text-muted">{item.body}</p>
          </section>
        ))}
      </div>
    </main>
  );
}
