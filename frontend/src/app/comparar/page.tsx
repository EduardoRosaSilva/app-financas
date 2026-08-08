"use client";

import { useState } from "react";
import { getHistoricoPreco, HistoricoItem } from "@/lib/api";

export default function Comparar() {
  const [busca, setBusca] = useState("");
  const [resultados, setResultados] = useState<HistoricoItem[]>([]);
  const [buscou, setBuscou] = useState(false);

  async function handleBuscar(e: React.FormEvent) {
    e.preventDefault();
    const dados = await getHistoricoPreco(busca);
    dados.sort((a, b) => Number(a.preco) - Number(b.preco));
    setResultados(dados);
    setBuscou(true);
  }

  return (
    <main className="min-h-screen px-5 py-10 max-w-sm mx-auto">
      <header className="mb-8">
        <p className="text-text-muted text-sm mb-1 tracking-wide uppercase">
          Lista Mercado
        </p>
        <h1 className="font-display text-3xl font-bold">Comparar preços</h1>
      </header>

      <form onSubmit={handleBuscar} className="flex gap-2 mb-8">
        <input
          value={busca}
          onChange={(e) => setBusca(e.target.value)}
          placeholder="Nome do item..."
          className="bg-surface border border-border rounded-full px-4 py-2.5 flex-1 outline-none focus:border-gold/60 transition-colors placeholder:text-text-muted"
        />
        <button className="bg-gold text-bg px-5 rounded-full font-semibold hover:opacity-90 transition-opacity">
          Buscar
        </button>
      </form>

      {buscou && resultados.length === 0 && (
        <p className="text-text-muted">Nenhum histórico encontrado.</p>
      )}

      <ul className="space-y-3">
        {resultados.map((r, index) => (
          <li
            key={r.id}
            className={`bg-surface border rounded-2xl px-5 py-4 flex items-center justify-between ${
              index === 0 ? "border-good" : "border-border"
            }`}
          >
            <div>
              <p className="font-semibold">{r.mercado_nome}</p>
              {index === 0 && (
                <p className="text-good text-xs font-medium mt-0.5">
                  melhor preço
                </p>
              )}
            </div>
            <span className="font-mono text-gold tabular-nums">
              R$ {r.preco}
            </span>
          </li>
        ))}
      </ul>
    </main>
  );
}