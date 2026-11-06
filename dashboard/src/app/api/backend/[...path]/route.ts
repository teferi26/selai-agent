/**
 * Authenticated proxy for client-side calls to the FastAPI backend.
 *
 * Client components fetch /api/backend/<path>; this handler attaches the
 * Clerk JWT server-side (the token never ships to the browser) and forwards
 * to BACKEND_URL. Replaces the old next.config rewrite, which forwarded
 * nothing.
 */
import { type NextRequest, NextResponse } from "next/server";

const BASE = process.env.BACKEND_URL || "http://localhost:8000";
const clerkEnabled = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
const READ_TIMEOUT_MS = 30_000;
const WRITE_TIMEOUT_MS = 120_000;

/**
 * (15) Rutas que el backend sirve sin sesión y que el panel necesita poder
 * pedir sin sesión también. Hoy sólo el parte de salud, que es lo que pinta
 * /estado — una página pública a propósito: cuando el cliente no consigue
 * entrar es cuando tiene que poder comprobar si el caído somos nosotros.
 *
 * Lista literal y sólo para GET: cualquier ruta de datos exige token igual que
 * antes. Se compara con la ruta ya reconstruida por Next (`path.join`), no con
 * la URL cruda, así que ni `..` ni un `%2e%2e` pueden colar otra cosa aquí.
 */
const PUBLIC_GET_PATHS = new Set(["readyz"]);

async function forward(req: NextRequest, path: string[]): Promise<NextResponse> {
  const headers = new Headers();
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);

  const isPublicGet = req.method === "GET" && PUBLIC_GET_PATHS.has(path.join("/"));

  if (clerkEnabled) {
    const { auth } = await import("@clerk/nextjs/server");
    const { getToken } = await auth();
    const token = await getToken();
    if (!token && !isPublicGet) {
      return NextResponse.json({ detail: "unauthenticated" }, { status: 401 });
    }
    if (token) headers.set("authorization", `Bearer ${token}`);
  } else if (process.env.DEV_USER_EMAIL) {
    headers.set("x-dev-email", process.env.DEV_USER_EMAIL);
  }

  const url = `${BASE}/${path.join("/")}${req.nextUrl.search}`;
  let res: Response;
  try {
    res = await fetch(url, {
      method: req.method,
      headers,
      body: req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer(),
      cache: "no-store",
      signal: AbortSignal.timeout(
        req.method === "GET" || req.method === "HEAD" ? READ_TIMEOUT_MS : WRITE_TIMEOUT_MS,
      ),
    });
  } catch (error) {
    if (error instanceof Error && ["AbortError", "TimeoutError"].includes(error.name)) {
      return NextResponse.json({ detail: "backend_timeout" }, { status: 504 });
    }
    return NextResponse.json({ detail: "backend_unavailable" }, { status: 502 });
  }

  return new NextResponse(res.body, {
    status: res.status,
    headers: {
      "content-type": res.headers.get("content-type") || "application/json",
    },
  });
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
export async function POST(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
export async function PUT(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
export async function PATCH(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
export async function DELETE(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
