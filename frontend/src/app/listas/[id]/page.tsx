"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { getLista, criarItem, marcarComprado, atualizarPreco, Lista } from "@/lib/api";

export default function DetalheLista() {
  const { id } = useParams<{ id: string }>();
  const [lista, setLista] = useState<Lista | null>(null);
  const [novoItem, setNovoItem] = useState("");

  async function carregar() {
    const dados = await getLista(id);
    setLista(dados);
  }

  useEffect(() => {
    carregar();
  }, [id]);

  async function handleAdicionar(e: React.FormEvent) {
    e.preventDefault();
    if (!novoItem.trim() || !lista) return;
    await criarItem(lista.id, novoItem);
    setNovoItem("");
    carregar();
  }

  async function handleToggle(itemId: number, comprado: boolean) {
    await marcarComprado(itemId, !comprado);
    carregar();
  }

  async function handleEditarPreco(itemId: number, e: React.MouseEvent) {
    e.stopPropagation();
    const valor = prompt("Preço do item (ex: 9.97):");
    if (valor === null) return;
    const numero = parseFloat(valor.replace(",", "."));
    if (isNaN(numero)) return;
    await atualizarPreco(itemId, numero);
    carregar();
  }

  if (!lista) return <p className="p-6 text-text">Carregando...</p>;

  const total = lista.itens
    .filter((i) => i.comprado && i.preco)
    .reduce((soma, i) => soma + Number(i.preco), 0);

  return (
    <main className="min-h-screen max-w-sm mx-auto pb-10">
      <header className="sticky top-0 bg-bg/90 backdrop-blur px-5 pt-8 pb-4 border-b border-border z-10">
        <h1 className="font-display text-2xl font-bold mb-1">{lista.nome}</h1>
        <p className="font-mono text-2xl text-gold tabular-nums">
          R$ {total.toFixed(2)}
        </p>
      </header>

      <div className="px-5 mt-5">
        <form onSubmit={handleAdicionar} className="flex gap-2 mb-6">
          <input
            value={novoItem}
            onChange={(e) => setNovoItem(e.target.value)}
            placeholder="Adicionar item..."
            className="bg-surface border border-border rounded-full px-4 py-2.5 flex-1 outline-none focus:border-gold/60 transition-colors placeholder:text-text-muted"
          />
          <button className="bg-gold text-bg w-10 h-10 rounded-full font-bold text-lg flex items-center justify-center hover:opacity-90 transition-opacity">
            +
          </button>
        </form>

        <ul className="space-y-1">
          {lista.itens.map((item) => (
            <li
              key={item.id}
              onClick={() => handleToggle(item.id, item.comprado)}
              className="flex items-center gap-3 py-3 border-b border-border/60 cursor-pointer"
            >
              <svg
                width="22"
                height="22"
                viewBox="0 0 22 22"
                className="checkbox-circle shrink-0"
                data-checked={item.comprado}
              >
                <circle
                  cx="11"
                  cy="11"
                  r="9.5"
                  fill="none"
                  stroke={item.comprado ? "var(--color-good)" : "var(--color-border)"}
                  strokeWidth="1.5"
                />
                <path
                  d="M6 11.5L9.5 15L16 7.5"
                  fill="none"
                  stroke="var(--color-good)"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="checkbox-check"
                />
              </svg>

              <span
                className={`flex-1 transition-colors ${
                  item.comprado ? "text-text-muted line-through" : "text-text"
                }`}
              >
                {item.nome}
              </span>

              <span
                onClick={(e) => handleEditarPreco(item.id, e)}
                className="font-mono text-sm text-gold hover:underline"
              >
                {item.preco ? `R$ ${item.preco}` : "definir"}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </main>
  );
}