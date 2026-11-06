// Server component that fetches a legal document (raw markdown with YAML
// frontmatter) from the backend and renders it with a minimal, dependency-free
// markdown pass. The stores (App Store / Play) require these to be public URLs.

const BASE = process.env.BACKEND_URL || "https://api.selai.es";

type Frontmatter = { title?: string; version?: string; effective_date?: string };

function parseFrontmatter(raw: string): { meta: Frontmatter; body: string } {
  const match = raw.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
  if (!match) return { meta: {}, body: raw };
  const meta: Frontmatter = {};
  for (const line of match[1].split(/\r?\n/)) {
    const idx = line.indexOf(":");
    if (idx === -1) continue;
    const key = line.slice(0, idx).trim();
    const value = line.slice(idx + 1).trim();
    if (key === "title" || key === "version" || key === "effective_date") {
      meta[key] = value;
    }
  }
  return { meta, body: raw.slice(match[0].length) };
}

function inline(text: string): React.ReactNode[] {
  // **bold** only — the legal docs don't use links or italics meaningfully.
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={i}>{part.slice(2, -2)}</strong>
    ) : (
      part
    )
  );
}

function renderMarkdown(body: string): React.ReactNode[] {
  const lines = body.split(/\r?\n/);
  const out: React.ReactNode[] = [];
  let list: string[] = [];
  let key = 0;

  const flushList = () => {
    if (!list.length) return;
    out.push(
      <ul key={key++} className="list-disc pl-6 space-y-1 my-3">
        {list.map((item, i) => (
          <li key={i}>{inline(item)}</li>
        ))}
      </ul>
    );
    list = [];
  };

  for (const line of lines) {
    const t = line.trim();
    if (t.startsWith("- ") || t.startsWith("* ")) {
      list.push(t.slice(2));
      continue;
    }
    flushList();
    if (!t) continue;
    if (t.startsWith("### ")) {
      out.push(
        <h3 key={key++} className="text-lg font-semibold mt-6 mb-2">
          {inline(t.slice(4))}
        </h3>
      );
    } else if (t.startsWith("## ")) {
      out.push(
        <h2 key={key++} className="text-xl font-semibold mt-8 mb-3">
          {inline(t.slice(3))}
        </h2>
      );
    } else if (t.startsWith("# ")) {
      // The page renders its own <h1> from frontmatter — skip the body title.
      continue;
    } else if (t.startsWith("> ")) {
      out.push(
        <blockquote
          key={key++}
          className="border-l-4 border-slate-300 pl-4 text-slate-600 my-4"
        >
          {inline(t.slice(2))}
        </blockquote>
      );
    } else {
      out.push(
        <p key={key++} className="my-3 leading-relaxed">
          {inline(t)}
        </p>
      );
    }
  }
  flushList();
  return out;
}

export default async function LegalDocument({ slug }: { slug: string }) {
  let res: Response | null = null;
  try {
    res = await fetch(`${BASE}/api/v1/legal/documents/${slug}`, {
      next: { revalidate: 3600 },
    });
  } catch {
    res = null;
  }
  if (!res || !res.ok) {
    return (
      <p className="text-slate-600">
        No hemos podido cargar el documento. Escríbenos a hola@selai.es y te lo
        enviamos.
      </p>
    );
  }
  const raw = await res.text();
  const { meta, body } = parseFrontmatter(raw);

  return (
    <article className="mx-auto max-w-3xl px-4 py-16 text-slate-800">
      <h1 className="text-3xl font-bold mb-2">{meta.title ?? "Documento legal"}</h1>
      {(meta.version || meta.effective_date) && (
        <p className="text-sm text-slate-500 mb-8">
          {meta.version ? `Versión ${meta.version}` : ""}
          {meta.version && meta.effective_date ? " · " : ""}
          {meta.effective_date ? `En vigor desde ${meta.effective_date}` : ""}
        </p>
      )}
      {renderMarkdown(body)}
    </article>
  );
}
