const API_URL = process.env.NEXT_PUBLIC_API_URL;

export type Item = {
  id: number;
  nome: string;
  quantidade: string;
  preco: string | null;
  comprado: boolean;
  ordem: number;
};

export type Lista = {
  id: number;
  nome: string;
  mercado: number | null;
  mercado_nome: string | null;
  criada_em: string;
  itens: Item[];
};

export type HistoricoItem = {
  id: number;
  item_nome: string;
  mercado: number;
  mercado_nome: string;
  preco: string;
  data: string;
};

export async function getListas(): Promise<Lista[]> {
  const res = await fetch(`${API_URL}/listas/`);
  if (!res.ok) throw new Error("Erro ao buscar listas");
  return res.json();
}

export async function getLista(id: string): Promise<Lista> {
  const res = await fetch(`${API_URL}/listas/${id}/`);
  if (!res.ok) throw new Error("Erro ao buscar lista");
  return res.json();
}

export async function criarItem(listaId: number, nome: string): Promise<Item> {
  const res = await fetch(`${API_URL}/itens/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lista: listaId, nome, comprado: false }),
  });
  if (!res.ok) throw new Error("Erro ao criar item");
  return res.json();
}

export async function marcarComprado(
  itemId: number,
  comprado: boolean
): Promise<Item> {
  const res = await fetch(`${API_URL}/itens/${itemId}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ comprado }),
  });
  if (!res.ok) throw new Error("Erro ao atualizar item");
  return res.json();
}

export async function atualizarPreco(itemId: number, preco: number): Promise<Item> {
  const res = await fetch(`${API_URL}/itens/${itemId}/`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ preco }),
  });
  if (!res.ok) throw new Error("Erro ao atualizar preço");
  return res.json();
}

export async function getHistoricoPreco(nomeItem: string): Promise<HistoricoItem[]> {
  const res = await fetch(`${API_URL}/historico/?item=${encodeURIComponent(nomeItem)}`);
  if (!res.ok) throw new Error("Erro ao buscar histórico");
  return res.json();
}