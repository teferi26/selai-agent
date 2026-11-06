// Client half de "Oficinas": crear, renombrar, cerrar y repartir el equipo.
//
// Regla que manda en toda la pantalla: una agencia sin oficinas no cambia de
// comportamiento en ningún otro sitio del panel. Por eso esta pantalla no
// crea nada sola ni asigna a nadie por defecto — mientras la lista esté
// vacía, "sin oficina" significa "toda la agencia" y todo funciona como
// siempre. Se dice en pantalla para que nadie tenga que deducirlo.
"use client";

import { useState } from "react";
import { ApiError, api } from "@/lib/api";

export type Office = {
  id: string;
  name: string;
  address: string | null;
  created_at: string;
  user_count: number;
  property_count: number;
};

export type OfficeMember = {
  user_id: string;
  full_name: string;
  email: string;
  role: string;
  office_id: string | null;
};

export type OfficeProperty = {
  property_id: string;
  reference: string;
  title: string;
  city: string;
  office_id: string | null;
};

const MAX_NAME = 120;
const MAX_ADDRESS = 512;

/** Traduce el error del backend a una frase que se pueda leer sin saber HTTP. */
function readableError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403 && err.body.includes("feature_not_in_plan")) {
      return "Las varias oficinas son del plan Agencia. Cambia de plan en Facturación para usarlas.";
    }
    if (err.status === 403) {
      return "Solo el titular de la cuenta puede tocar las oficinas.";
    }
    if (err.status === 409) {
      return "Ya tienes una oficina con ese nombre.";
    }
    if (err.status === 404) {
      return "Esa oficina ya no existe. Recarga la página.";
    }
  }
  return "No se pudo guardar el cambio. Inténtalo de nuevo.";
}

export default function OficinasClient({
  agencyId,
  offices: initialOffices,
  members: initialMembers,
  properties: initialProperties,
  planCode,
  isOwner,
}: {
  agencyId: string;
  offices: Office[];
  members: OfficeMember[];
  properties: OfficeProperty[];
  planCode: string | null;
  isOwner: boolean;
}) {
  const [offices, setOffices] = useState<Office[]>(initialOffices);
  const [members, setMembers] = useState<OfficeMember[]>(initialMembers);
  const [properties, setProperties] = useState<OfficeProperty[]>(initialProperties);
  const [search, setSearch] = useState("");
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  // El plan se comprueba de verdad en el backend; esto solo evita ofrecer un
  // botón que va a contestar 403. Si el plan no se conoce (null) se deja
  // intentar: es mejor un error honesto que esconder la función por si acaso.
  const planAllows = planCode === null || planCode === "agencia";
  const canEdit = isOwner && planAllows;

  const base = `/api/v1/agencies/${agencyId}/offices`;

  function flash(message: string) {
    setSaved(message);
    setError(null);
    window.setTimeout(() => setSaved(null), 2500);
  }

  async function createOffice(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api<Office>(base, {
        method: "POST",
        body: JSON.stringify({ name: trimmed, address: address.trim() || null }),
      });
      setOffices((prev) => [...prev, created]);
      setName("");
      setAddress("");
      flash("Oficina creada");
    } catch (err) {
      setError(readableError(err));
    } finally {
      setBusy(false);
    }
  }

  async function renameOffice(office: Office, nextName: string) {
    const trimmed = nextName.trim();
    if (!trimmed || trimmed === office.name || busy) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api<Office>(`${base}/${office.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name: trimmed }),
      });
      setOffices((prev) => prev.map((o) => (o.id === office.id ? updated : o)));
      flash("Nombre actualizado");
    } catch (err) {
      setError(readableError(err));
    } finally {
      setBusy(false);
    }
  }

  async function deleteOffice(office: Office) {
    // Cerrar una oficina no borra nada: suelta a su gente y sus inmuebles.
    // El confirm lo dice con números para que la decisión sea informada.
    const detail =
      office.user_count + office.property_count === 0
        ? ""
        : ` Su equipo (${office.user_count}) y sus inmuebles (${office.property_count}) pasarán a "toda la agencia".`;
    if (!window.confirm(`¿Cerrar la oficina "${office.name}"?${detail}`)) return;

    setBusy(true);
    setError(null);
    try {
      await api<void>(`${base}/${office.id}`, { method: "DELETE" });
      setOffices((prev) => prev.filter((o) => o.id !== office.id));
      setMembers((prev) =>
        prev.map((m) => (m.office_id === office.id ? { ...m, office_id: null } : m))
      );
      setProperties((prev) =>
        prev.map((p) => (p.office_id === office.id ? { ...p, office_id: null } : p))
      );
      flash("Oficina cerrada");
    } catch (err) {
      setError(readableError(err));
    } finally {
      setBusy(false);
    }
  }

  async function searchProperties(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      // El listado viene recortado del servidor (el plan Agencia admite 2.000
      // inmuebles): buscar es la única forma de llegar a los que no salen.
      const query = search.trim() ? `?q=${encodeURIComponent(search.trim())}` : "";
      setProperties(await api<OfficeProperty[]>(`${base}/properties${query}`));
    } catch (err) {
      setError(readableError(err));
    } finally {
      setBusy(false);
    }
  }

  async function assignProperty(listing: OfficeProperty, officeId: string | null) {
    if (officeId === listing.office_id || busy) return;
    const previous = listing.office_id;
    setProperties((prev) =>
      prev.map((p) =>
        p.property_id === listing.property_id ? { ...p, office_id: officeId } : p,
      ),
    );
    setBusy(true);
    setError(null);
    try {
      await api<{ property_id: string; office_id: string | null }>(
        `${base}/properties/${listing.property_id}`,
        { method: "PUT", body: JSON.stringify({ office_id: officeId }) },
      );
      flash("Inmueble actualizado");
    } catch (err) {
      setProperties((prev) =>
        prev.map((p) =>
          p.property_id === listing.property_id ? { ...p, office_id: previous } : p,
        ),
      );
      setError(readableError(err));
    } finally {
      setBusy(false);
    }
  }

  async function assignMember(member: OfficeMember, officeId: string | null) {
    if (officeId === member.office_id || busy) return;
    const previous = member.office_id;
    setMembers((prev) =>
      prev.map((m) => (m.user_id === member.user_id ? { ...m, office_id: officeId } : m))
    );
    setBusy(true);
    setError(null);
    try {
      await api<OfficeMember>(`${base}/members/${member.user_id}`, {
        method: "PUT",
        body: JSON.stringify({ office_id: officeId }),
      });
      flash("Equipo actualizado");
    } catch (err) {
      setMembers((prev) =>
        prev.map((m) => (m.user_id === member.user_id ? { ...m, office_id: previous } : m))
      );
      setError(readableError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-3xl">
      <header className="mb-8">
        <h2 className="text-2xl font-semibold">Oficinas</h2>
        <p className="text-sm text-slate-500">
          Si trabajáis desde varias sucursales, dilo aquí y podrás ver la bandeja y los
          contactos de una sola oficina.
        </p>
      </header>

      {!planAllows && (
        <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          Varias oficinas es una función del plan Agencia.{" "}
          <a href="/billing" className="font-medium underline">
            Ver planes
          </a>
        </div>
      )}
      {planAllows && !isOwner && (
        <div className="mb-6 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
          Solo el titular de la cuenta puede crear oficinas o mover a alguien de sitio.
        </div>
      )}
      {error && (
        <div
          role="alert"
          className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
        >
          {error}
        </div>
      )}
      {saved && <p className="mb-4 text-xs text-emerald-700">{saved} ✓</p>}

      <section className="mb-10">
        <h3 className="text-lg font-medium">Tus oficinas</h3>
        {offices.length === 0 ? (
          <p className="mt-2 rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-600">
            Todavía no tienes ninguna. Mientras no crees oficinas, todo el equipo ve toda la
            agencia, exactamente como hasta ahora.
          </p>
        ) : (
          <ul className="mt-3 space-y-2">
            {offices.map((office) => (
              <li
                key={office.id}
                className="rounded-xl border border-slate-200 bg-white p-4"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <input
                      type="text"
                      defaultValue={office.name}
                      maxLength={MAX_NAME}
                      // Renombrar no está limitado por plan en el backend a
                      // propósito (una agencia que baja de plan tiene que
                      // poder ordenar lo que creó), así que aquí tampoco.
                      disabled={!isOwner || busy}
                      aria-label={`Nombre de la oficina ${office.name}`}
                      onBlur={(e) => renameOffice(office, e.target.value)}
                      className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 font-medium hover:border-slate-200 focus:border-selai-500 focus:outline-none disabled:cursor-not-allowed"
                    />
                    <p className="mt-1 px-1 text-xs text-slate-500">
                      {office.address || "Sin dirección"} · {office.user_count} del equipo ·{" "}
                      {office.property_count} inmuebles
                    </p>
                  </div>
                  <button
                    type="button"
                    disabled={!isOwner || busy}
                    onClick={() => deleteOffice(office)}
                    className="rounded-md border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:border-red-300 hover:text-red-700 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    Cerrar
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}

        <form onSubmit={createOffice} className="mt-4 rounded-xl border border-slate-200 bg-white p-4">
          <h4 className="text-sm font-medium">Añadir una oficina</h4>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <label className="block text-sm">
              <span className="text-slate-600">Nombre</span>
              <input
                type="text"
                value={name}
                maxLength={MAX_NAME}
                disabled={!canEdit || busy}
                onChange={(e) => setName(e.target.value)}
                placeholder="Oficina Centro"
                className="mt-1 w-full rounded-md border border-slate-200 px-3 py-2 focus:border-selai-500 focus:outline-none disabled:bg-slate-50"
              />
            </label>
            <label className="block text-sm">
              <span className="text-slate-600">Dirección (opcional)</span>
              <input
                type="text"
                value={address}
                maxLength={MAX_ADDRESS}
                disabled={!canEdit || busy}
                onChange={(e) => setAddress(e.target.value)}
                placeholder="Calle Mayor 1, Madrid"
                className="mt-1 w-full rounded-md border border-slate-200 px-3 py-2 focus:border-selai-500 focus:outline-none disabled:bg-slate-50"
              />
            </label>
          </div>
          <button
            type="submit"
            disabled={!canEdit || busy || !name.trim()}
            className="mt-3 rounded-md bg-selai-600 px-4 py-2 text-sm font-medium text-white hover:bg-selai-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Crear oficina
          </button>
        </form>
      </section>

      <section>
        <h3 className="text-lg font-medium">Quién trabaja en cada una</h3>
        <p className="mb-3 mt-1 text-sm text-slate-500">
          &quot;Toda la agencia&quot; es lo que tenéis ahora: esa persona ve todo, sin filtro.
        </p>
        <ul className="space-y-2">
          {members.map((member) => (
            <li
              key={member.user_id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3"
            >
              <div className="min-w-0">
                <p className="truncate font-medium">{member.full_name}</p>
                <p className="truncate text-xs text-slate-500">{member.email}</p>
              </div>
              <select
                value={member.office_id ?? ""}
                disabled={!canEdit || busy || offices.length === 0}
                aria-label={`Oficina de ${member.full_name}`}
                onChange={(e) => assignMember(member, e.target.value || null)}
                className="rounded-md border border-slate-200 px-3 py-1.5 text-sm focus:border-selai-500 focus:outline-none disabled:bg-slate-50"
              >
                <option value="">Toda la agencia</option>
                {offices.map((office) => (
                  <option key={office.id} value={office.id}>
                    {office.name}
                  </option>
                ))}
              </select>
            </li>
          ))}
        </ul>
      </section>

      <section className="mt-10">
        <h3 className="text-lg font-medium">Qué oficina lleva cada inmueble</h3>
        <p className="mb-3 mt-1 text-sm text-slate-500">
          Esto es lo que hace verdad &quot;cada contacto va a la oficina que le
          corresponde&quot;: quien pide ver un piso de la oficina Centro aparece en la
          bandeja de la oficina Centro.
        </p>

        {offices.length === 0 ? (
          <p className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-600">
            Crea antes una oficina y aquí podrás repartir el catálogo.
          </p>
        ) : (
          <>
            <form onSubmit={searchProperties} className="mb-3 flex gap-2">
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Buscar por referencia, título o ciudad"
                aria-label="Buscar inmuebles"
                className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm focus:border-selai-500 focus:outline-none"
              />
              <button
                type="submit"
                disabled={busy}
                className="rounded-md border border-slate-200 px-4 py-2 text-sm hover:bg-slate-50 disabled:opacity-50"
              >
                Buscar
              </button>
            </form>

            {properties.length === 0 ? (
              <p className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-600">
                No hay inmuebles que coincidan.
              </p>
            ) : (
              <ul className="space-y-2">
                {properties.map((listing) => (
                  <li
                    key={listing.property_id}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3"
                  >
                    <div className="min-w-0">
                      <p className="truncate font-medium">{listing.title}</p>
                      <p className="truncate text-xs text-slate-500">
                        {listing.reference} · {listing.city}
                      </p>
                    </div>
                    <select
                      value={listing.office_id ?? ""}
                      disabled={!canEdit || busy}
                      aria-label={`Oficina del inmueble ${listing.reference}`}
                      onChange={(e) => assignProperty(listing, e.target.value || null)}
                      className="rounded-md border border-slate-200 px-3 py-1.5 text-sm focus:border-selai-500 focus:outline-none disabled:bg-slate-50"
                    >
                      <option value="">Toda la agencia</option>
                      {offices.map((office) => (
                        <option key={office.id} value={office.id}>
                          {office.name}
                        </option>
                      ))}
                    </select>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>
    </div>
  );
}
