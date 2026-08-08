# Lista Mercado — Status do Projeto

App para digitalizar a lista de compras manual, com foco em comparação de
preço por item entre mercados e acompanhamento de gasto em tempo real
durante as compras. Projeto de portfólio, feito para ser completo e bem
estruturado.

## Stack decidida
- **Backend:** Django + Django REST Framework, SQLite (por enquanto, trocar
  para PostgreSQL antes do deploy)
- **Frontend:** Next.js (App Router) + TypeScript + TailwindCSS, como PWA
- **OCR (ainda não implementado):** modelo de visão local via Ollama
  (ex: llama3.2-vision), para importar a lista manuscrita por foto —
  escolhido por ser gratuito e sem limite de uso
- **Deploy alvo (ainda não feito):** Vercel (frontend) + Render/Railway
  (backend) + Supabase/Neon (Postgres), tudo em camada gratuita

## O que já está pronto

### Backend (`/backend`)
- App Django `listas` criado e registrado
- Models em `listas/models.py`: `Mercado`, `Lista`, `Item`, `HistoricoPreco`
  - `Item.lista` é ForeignKey pra `Lista` com `related_name="itens"`
  - `HistoricoPreco` guarda `item_nome` (normalizado, lower/strip), `mercado`,
    `preco`, `data` — é a base da comparação de preço entre mercados
- Serializers em `listas/serializers.py` (`ListaSerializer` inclui `itens`
  aninhados e `mercado_nome`)
- ViewSets em `listas/views.py`:
  - `ItemViewSet` sobrescreve `perform_update` para criar automaticamente um
    registro em `HistoricoPreco` sempre que um item tem preço + a lista tem
    mercado definido
  - `HistoricoPrecoViewSet` sobrescreve `get_queryset` para filtrar por
    `?item=nome` (usado na tela de comparação)
- URLs via `DefaultRouter` em `listas/urls.py`, incluídas em `core/urls.py`
  sob `/api/`
- CORS configurado (`django-cors-headers`) liberando `localhost:3000`
- Admin do Django com os 4 models registrados, testado e funcionando

### Frontend (`/frontend`)
- `.env.local` com `NEXT_PUBLIC_API_URL=http://localhost:8000/api`
- `src/lib/api.ts`: cliente de API centralizado com `getListas`, `getLista`,
  `criarItem`, `marcarComprado`, `atualizarPreco`, `getHistoricoPreco`, e os
  types `Lista`, `Item`, `HistoricoItem`
- `src/app/page.tsx`: lista as listas existentes (Server Component), cada
  card é um link pra `/listas/[id]`
- `src/app/listas/[id]/page.tsx`: página de detalhe (Client Component) —
  adicionar item, riscar (marcar comprado) com efeito visual, editar preço
  por clique (usa `prompt()`, é provisório — pode virar input inline depois),
  total ao vivo somando itens comprados com preço
- `src/app/comparar/page.tsx`: busca por nome de item e mostra histórico de
  preço por mercado — **ainda mostra "Mercado #id"** porque o
  `HistoricoPrecoSerializer` não traz `mercado_nome` ainda (próximo ajuste
  pendente, opcional)

## Testado e funcionando
- Fluxo completo: criar lista/mercado no admin → aparece no frontend → abrir
  lista → adicionar item → definir preço → marcar comprado → total atualiza
  → preço salva automaticamente no histórico → tela `/comparar` encontra o
  registro

## Próximos passos sugeridos (ainda não feitos)
1. **Ajuste rápido:** adicionar `mercado_nome` no `HistoricoPrecoSerializer`
   (mesmo padrão já usado no `ListaSerializer`), pra tela de comparação
   mostrar o nome do mercado em vez do ID
2. **UX do preço:** trocar o `prompt()` por um input inline mais profissional
3. **PWA de verdade:** manifest.json + service worker para instalar no
   celular e funcionar offline
4. **OCR:** endpoint que recebe foto da lista manuscrita, chama modelo de
   visão local via Ollama, retorna itens estruturados (nome, preço, mercado,
   se já veio riscado) e cria os registros
5. **Autenticação/compartilhamento em tempo real** entre o usuário e a mãe
   (Django Channels ou polling)
6. **Testes automatizados** (pytest no backend)
7. **Deploy** nos serviços gratuitos definidos acima

## Preferências do usuário para o projeto
- Sem custos: qualquer escolha técnica deve ter camada gratuita
- Prioridade: projeto completo e bem estruturado, com foco em qualidade de
  portfólio (não é só um MVP rápido)
