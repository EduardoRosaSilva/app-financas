import Link from "next/link";
import { getListas } from "@/lib/api";

export default async function Home() {
  const listas = await getListas();

  return (
    <main className="min-h-screen px-5 py-10 max-w-sm mx-auto">
      <header className="mb-8">
        <p className="text-text-muted text-sm mb-1 tracking-wide uppercase">
          Lista Mercado
        </p>
        <h1 className="font-display text-3xl font-bold">Minhas listas</h1>
      </header>

      {listas.length === 0 && (
        <p className="text-text-muted">Nenhuma lista criada ainda.</p>
      )}

      <ul className="space-y-3">
        {listas.map((lista) => (
          <Link key={lista.id} href={`/listas/${lista.id}`}>
            <li className="group bg-surface border border-border rounded-2xl px-5 py-4 flex items-center justify-between hover:bg-surface-2 hover:border-gold/40 transition-all cursor-pointer">
              <div>
                <p className="font-display font-semibold text-lg">
                  {lista.nome}
                </p>
                <p className="text-text-muted text-sm">
                  {lista.mercado_nome ?? "Sem mercado definido"}
                </p>
              </div>
              <div className="flex items-center gap-3">
                <span className="font-mono text-sm text-text-muted">
                  {lista.itens.length}
                </span>
                <span className="text-gold group-hover:translate-x-0.5 transition-transform">
                  →
                </span>
              </div>
            </li>
          </Link>
        ))}
      </ul>
    </main>
  );
}