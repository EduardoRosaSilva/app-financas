import calendar
import csv
import io
import re
import urllib.parse
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache

from .forms import LoginUsuarioForm, RegistroUsuarioForm
from .models import Cartao, MetaMensal, Pessoa, Transacao


def parse_moeda(valor_raw) -> float:
    """Converte strings como '1.250,50', 'R$ 50,00' ou '50.00' com segurança."""
    if not valor_raw:
        return 0.0
    s = str(valor_raw).replace('R$', '').replace(' ', '').strip()
    if ',' in s and '.' in s:
        if s.rfind(',') > s.rfind('.'):
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
    elif ',' in s:
        s = s.replace(',', '.')
    try:
        return float(Decimal(s))
    except (InvalidOperation, ValueError):
        return 0.0


def garantir_titular_conta(username: str) -> Pessoa:
    """Garante que toda conta possua uma Pessoa titular para nunca zerar o dashboard."""
    titular = Pessoa.objects.filter(conta__iexact=username, is_titular=True).first()
    if not titular:
        primeira = Pessoa.objects.filter(conta__iexact=username).first()
        if primeira:
            primeira.is_titular = True
            primeira.conta = username
            primeira.save()
            return primeira
        titular = Pessoa.objects.create(
            nome=username.capitalize(),
            telefone='',
            is_titular=True,
            conta=username
        )
    return titular


def get_mes_fatura(data_compra, dia_fechamento, dia_vencimento=None):
    """Calcula o mês de competência da fatura baseado no dia de fechamento do cartão."""
    mes = data_compra.month
    ano = data_compra.year

    if data_compra.day >= dia_fechamento:
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1

    return mes, ano


def prever_categoria(descricao_nova, transacoes_historicas):
    if not transacoes_historicas:
        return fallback_rules(descricao_nova)

    palavras_cat = defaultdict(lambda: defaultdict(int))
    total_cat = defaultdict(int)
    vocabulario = set()

    for t in transacoes_historicas:
        cat = t.categoria
        total_cat[cat] += 1
        palavras = re.findall(r'\w+', t.descricao.lower())
        for p in palavras:
            palavras_cat[cat][p] += 1
            vocabulario.add(p)

    palavras_alvo = re.findall(r'\w+', descricao_nova.lower())
    melhor_cat = 'Outros'
    maior_score = -1.0

    V = len(vocabulario)
    for cat, total in total_cat.items():
        score = total
        for p in palavras_alvo:
            prob_palavra = (palavras_cat[cat][p] + 1) / (total + V)
            score *= prob_palavra

        if score > maior_score:
            maior_score = score
            melhor_cat = cat

    return melhor_cat


def fallback_rules(descricao):
    cat_lower = descricao.lower()
    if any(k in cat_lower for k in ('restaurante', 'food', 'alimenta', 'ifd', 'lanch')):
        return 'Alimentação'
    if any(k in cat_lower for k in ('supermercado', 'grocery', 'mercado', 'conveniencia')):
        return 'Mercado'
    if any(k in cat_lower for k in ('transporte', 'auto', 'uber', 'posto', 'combustivel', '99app')):
        return 'Transporte'
    if any(k in cat_lower for k in ('saude', 'farm', 'medic', 'odonto', 'drogaria')):
        return 'Saúde'
    if any(k in cat_lower for k in ('aluguel', 'condominio', 'energia', 'luz', 'agua', 'internet')):
        return 'Moradia'
    if any(k in cat_lower for k in ('netflix', 'spotify', 'prime', 'assinatura', 'youtube')):
        return 'Assinaturas'
    if any(k in cat_lower for k in ('loja', 'shopee', 'mercado livre', 'pernambucanas', 'amazon')):
        return 'Compras'
    return 'Outros'


# ==========================================
# VIEWS DE AUTENTICAÇÃO
# ==========================================

@never_cache
def registro_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        if request.POST.get('website_url_honeypot'):
            return redirect('login')

        form = RegistroUsuarioForm(request.POST)
        if form.is_valid():
            usuario = form.save()
            garantir_titular_conta(usuario.username)
            login(request, usuario)
            return redirect('dashboard')
    else:
        form = RegistroUsuarioForm()
    return render(request, 'gestao/register.html', {'form': form})


@never_cache
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = LoginUsuarioForm(request, data=request.POST)
        if form.is_valid():
            usuario = form.get_user()
            garantir_titular_conta(usuario.username)
            login(request, usuario)
            return redirect('dashboard')
        else:
            messages.error(request, 'Usuário ou senha inválidos.')
    else:
        form = LoginUsuarioForm()
    return render(request, 'gestao/login.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('login')


# ==========================================
# DASHBOARD
# ==========================================

@login_required(login_url='login')
def dashboard(request):
    minha_conta = request.user.username
    eu = garantir_titular_conta(minha_conta)

    hoje = date.today()
    try:
        mes_selecionado = int(request.GET.get('mes', hoje.month))
        ano_selecionado = int(request.GET.get('ano', hoje.year))
        if not (1 <= mes_selecionado <= 12):
            mes_selecionado = hoje.month
    except (ValueError, TypeError):
        mes_selecionado = hoje.month
        ano_selecionado = hoje.year

    if request.method == 'POST':
        tipo_formulario = request.POST.get('form_type')

        # 1. IMPORTAÇÃO DE FATURA CSV
        if tipo_formulario == 'upload_fatura':
            arquivo_csv = request.FILES.get('arquivo_csv')
            if arquivo_csv:
                if arquivo_csv.size > 5242880:
                    messages.error(request, 'Arquivo muito grande. O limite é 5MB.')
                    return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')
                try:
                    decoded_file = arquivo_csv.read().decode('utf-8-sig')
                except UnicodeDecodeError:
                    messages.error(request, 'Erro na leitura. Certifique-se de que é um CSV válido.')
                    return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

                primeira_linha = decoded_file.split('\n')[0]
                delimitador = ';' if ';' in primeira_linha else ','
                io_string = io.StringIO(decoded_file)
                reader = csv.DictReader(io_string, delimiter=delimitador)

                historico = list(Transacao.objects.filter(conta__iexact=minha_conta).exclude(categoria='Outros'))
                transacoes_fatura = []

                for index, row in enumerate(reader):
                    row_norm = {str(k).strip().lower(): str(v).strip() for k, v in row.items() if k}
                    data_str = row_norm.get('date', row_norm.get('data', ''))
                    titulo = row_norm.get('title', row_norm.get('titulo', ''))
                    valor_str = row_norm.get('amount', row_norm.get('valor', '0'))

                    if not data_str or not titulo:
                        continue

                    if titulo and titulo[0] in ['=', '+', '-', '@']:
                        titulo = "'" + titulo

                    valor = parse_moeda(valor_str)
                    if valor <= 0:
                        continue

                    if '/' in data_str:
                        try:
                            d = datetime.strptime(data_str, '%d/%m/%Y').date()
                            data_str = d.strftime('%Y-%m-%d')
                        except ValueError:
                            pass

                    categoria_prevista = prever_categoria(titulo, historico)

                    transacoes_fatura.append({
                        'id_temp': index,
                        'data': data_str,
                        'descricao': titulo,
                        'valor': valor,
                        'categoria': categoria_prevista
                    })

                contexto_revisao = {
                    'transacoes_fatura': transacoes_fatura,
                    'pessoas': Pessoa.objects.filter(conta__iexact=minha_conta),
                    'cartoes': Cartao.objects.filter(conta__iexact=minha_conta),
                }
                return render(request, 'gestao/revisao_fatura.html', contexto_revisao)

        # 2. SALVAR ITENS IMPORTADOS EM LOTE
        elif tipo_formulario == 'salvar_fatura_lote':
            ids_marcados = request.POST.getlist('importar_chk')
            cartao_id = request.POST.get('cartao_lote')
            cartao_obj = Cartao.objects.filter(id=cartao_id, conta__iexact=minha_conta).first() if cartao_id else None

            pessoa_padrao = eu
            qtd_salva = 0
            ultimo_mes = mes_selecionado
            ultimo_ano = ano_selecionado

            for t_id in ids_marcados:
                desc = request.POST.get(f'desc_{t_id}')
                valor = parse_moeda(request.POST.get(f'valor_{t_id}', '0'))
                data_val = request.POST.get(f'data_{t_id}')
                dono_id = request.POST.get(f'dono_{t_id}')
                categoria = request.POST.get(f'cat_{t_id}', 'Outros')

                dono_obj = None
                if dono_id and str(dono_id).isdigit():
                    dono_obj = Pessoa.objects.filter(id=dono_id, conta__iexact=minha_conta).first()
                dono_obj = dono_obj or pessoa_padrao

                data_parsed = datetime.strptime(data_val, '%Y-%m-%d').date() if data_val else hoje

                Transacao.objects.create(
                    descricao=desc,
                    valor=valor,
                    data=data_parsed,
                    tipo='DESPESA',
                    categoria=categoria,
                    dono=dono_obj,
                    cartao=cartao_obj,
                    conta=minha_conta,
                    recorrente=False
                )
                qtd_salva += 1

                if cartao_obj:
                    ultimo_mes, ultimo_ano = get_mes_fatura(data_parsed, cartao_obj.dia_fechamento, cartao_obj.dia_vencimento)
                else:
                    ultimo_mes = data_parsed.month
                    ultimo_ano = data_parsed.year

            messages.success(request, f"{qtd_salva} transações importadas com sucesso!")
            return redirect(f'/?mes={ultimo_mes}&ano={ultimo_ano}')

        # 3. NOVA TRANSAÇÃO
        elif tipo_formulario == 'nova_transacao':
            descricao_digitada = request.POST.get('descricao', '').strip()
            valor_digitado = parse_moeda(request.POST.get('valor', '0'))

            dono_id_escolhido = request.POST.get('dono_id')
            tipo_escolhido = request.POST.get('tipo', 'DESPESA')
            categoria_escolhida = request.POST.get('categoria', 'Outros')
            data_digitada = request.POST.get('data')
            cartao_id_escolhido = request.POST.get('cartao_id')

            recorrente = request.POST.get('recorrente') == '1'
            parcelas = 1 if recorrente else max(1, int(request.POST.get('parcelas', 1) or 1))

            donos_extras = request.POST.getlist('dono_id_extra')
            valores_extras_str = request.POST.getlist('valor_extra')

            dono_obj = get_object_or_404(Pessoa, id=dono_id_escolhido, conta__iexact=minha_conta) if dono_id_escolhido else eu
            cartao_obj = get_object_or_404(Cartao, id=cartao_id_escolhido, conta__iexact=minha_conta) if cartao_id_escolhido else None
            data_base = datetime.strptime(data_digitada, '%Y-%m-%d').date() if data_digitada else hoje

            if parcelas > 1:
                valor_final_parcela = round(valor_digitado / parcelas, 2)
                valores_finais_extras = [round(parse_moeda(v) / parcelas, 2) for v in valores_extras_str]
            else:
                valor_final_parcela = valor_digitado
                valores_finais_extras = [parse_moeda(v) for v in valores_extras_str]

            for i in range(parcelas):
                mes_atual = data_base.month + i
                ano_atual_loop = data_base.year + ((mes_atual - 1) // 12)
                mes_calculado = ((mes_atual - 1) % 12) + 1
                ultimo_dia_mes = calendar.monthrange(ano_atual_loop, mes_calculado)[1]
                dia_calculado = min(data_base.day, ultimo_dia_mes)
                nova_data = date(ano_atual_loop, mes_calculado, dia_calculado)

                desc_salvar = f"{descricao_digitada} ({i+1}/{parcelas})" if parcelas > 1 else descricao_digitada
                soma_extras = sum(valores_finais_extras)
                valor_dono_1 = max(0.0, round(valor_final_parcela - soma_extras, 2))

                Transacao.objects.create(
                    descricao=desc_salvar,
                    valor=valor_dono_1,
                    dono=dono_obj,
                    tipo=tipo_escolhido,
                    categoria=categoria_escolhida,
                    data=nova_data,
                    cartao=cartao_obj,
                    conta=minha_conta,
                    recorrente=recorrente
                )

                for idx, dono_extra_id in enumerate(donos_extras):
                    valor_deste_amigo = valores_finais_extras[idx] if idx < len(valores_finais_extras) else 0.0
                    if dono_extra_id and valor_deste_amigo > 0:
                        d_obj = get_object_or_404(Pessoa, id=dono_extra_id, conta__iexact=minha_conta)
                        Transacao.objects.create(
                            descricao=f"{desc_salvar} (Parte de {d_obj.nome})",
                            valor=valor_deste_amigo,
                            dono=d_obj,
                            tipo=tipo_escolhido,
                            categoria=categoria_escolhida,
                            data=nova_data,
                            cartao=cartao_obj,
                            conta=minha_conta,
                            recorrente=recorrente
                        )

            if cartao_obj:
                mes_red, ano_red = get_mes_fatura(data_base, cartao_obj.dia_fechamento, cartao_obj.dia_vencimento)
            else:
                mes_red, ano_red = data_base.month, data_base.year
            messages.success(request, "Transação registrada com sucesso!")
            return redirect(f'/?mes={mes_red}&ano={ano_red}')

        # 4. EDITAR TRANSAÇÃO
        elif tipo_formulario == 'editar_transacao':
            t_id = request.POST.get('transacao_id')
            transacao = get_object_or_404(Transacao, id=t_id, conta__iexact=minha_conta)

            descricao = request.POST.get('descricao', '').strip()
            valor = parse_moeda(request.POST.get('valor', '0'))
            data_val = request.POST.get('data')
            tipo = request.POST.get('tipo', 'DESPESA')
            categoria = request.POST.get('categoria', 'Outros')
            recorrente = request.POST.get('recorrente') == '1'
            parcelas = 1 if recorrente else max(1, int(request.POST.get('parcelas', 1) or 1))
            dono_id = request.POST.get('dono_id')
            cartao_id = request.POST.get('cartao_id')

            donos_extras = request.POST.getlist('dono_id_extra')
            valores_extras_str = request.POST.getlist('valor_extra')
            valores_extras = [parse_moeda(v) for v in valores_extras_str]

            if parcelas > 1:
                valor_parcela = round(valor / parcelas, 2)
                valores_extras = [round(v / parcelas, 2) for v in valores_extras]
            else:
                valor_parcela = valor

            soma_extras = sum(valores_extras)
            valor_principal = round(valor_parcela - soma_extras, 2) if (0 < soma_extras < valor_parcela) else valor_parcela

            data_base = datetime.strptime(data_val, '%Y-%m-%d').date() if data_val else transacao.data
            transacao.descricao = f"{descricao} (1/{parcelas})" if parcelas > 1 else descricao
            transacao.valor = valor_principal
            transacao.data = data_base
            transacao.tipo = tipo
            transacao.categoria = categoria
            transacao.recorrente = recorrente
            transacao.dono = get_object_or_404(Pessoa, id=dono_id, conta__iexact=minha_conta)
            transacao.cartao = get_object_or_404(Cartao, id=cartao_id, conta__iexact=minha_conta) if cartao_id else None
            transacao.save()

            for i in range(1, parcelas):
                mes_atual = data_base.month + i
                ano_atual_loop = data_base.year + ((mes_atual - 1) // 12)
                mes_calculado = ((mes_atual - 1) % 12) + 1
                ultimo_dia_mes = calendar.monthrange(ano_atual_loop, mes_calculado)[1]
                nova_data = date(ano_atual_loop, mes_calculado, min(data_base.day, ultimo_dia_mes))

                Transacao.objects.create(
                    descricao=f"{descricao} ({i+1}/{parcelas})",
                    valor=valor_principal,
                    data=nova_data,
                    tipo=tipo,
                    categoria=categoria,
                    dono=transacao.dono,
                    cartao=transacao.cartao,
                    conta=minha_conta,
                    recorrente=False
                )

            if donos_extras and any(v > 0 for v in valores_extras):
                for extra_id, extra_val in zip(donos_extras, valores_extras):
                    if extra_id and extra_val > 0:
                        amigo_obj = Pessoa.objects.filter(id=extra_id, conta__iexact=minha_conta).first()
                        if amigo_obj:
                            Transacao.objects.create(
                                descricao=f"{descricao} (Parte de {amigo_obj.nome})",
                                valor=extra_val,
                                data=transacao.data,
                                tipo=tipo,
                                categoria=categoria,
                                dono=amigo_obj,
                                cartao=transacao.cartao,
                                conta=minha_conta,
                                recorrente=recorrente
                            )

            messages.success(request, "Transação atualizada com sucesso!")

        # 5. OUTROS GERENCIAMENTOS
        elif tipo_formulario == 'atualizar_meta':
            novo_valor = parse_moeda(request.POST.get('valor_meta'))
            meta = MetaMensal.objects.filter(conta__iexact=minha_conta).first()
            if meta:
                meta.valor = novo_valor
                meta.save()
            else:
                MetaMensal.objects.create(valor=novo_valor, conta=minha_conta)

        elif tipo_formulario == 'nova_pessoa':
            Pessoa.objects.create(
                nome=request.POST.get('nome', '').strip(),
                telefone=request.POST.get('telefone', '').strip(),
                is_titular=not Pessoa.objects.filter(conta__iexact=minha_conta, is_titular=True).exists(),
                conta=minha_conta
            )
            messages.success(request, "Pessoa adicionada com sucesso!")

        elif tipo_formulario == 'editar_pessoa':
            p_id = request.POST.get('pessoa_id')
            p = get_object_or_404(Pessoa, id=p_id, conta__iexact=minha_conta)
            p.nome = request.POST.get('nome', '').strip()
            p.telefone = request.POST.get('telefone', '').strip()
            p.save()
            messages.success(request, f"Dados de {p.nome} atualizados!")

        elif tipo_formulario == 'excluir_pessoa':
            p_id = request.POST.get('pessoa_id')
            p = get_object_or_404(Pessoa, id=p_id, conta__iexact=minha_conta)
            if p.is_titular:
                messages.error(request, "Segurança: Você não pode excluir o Titular da conta.")
            else:
                p.delete()
                messages.success(request, "Pessoa excluída com sucesso.")

        elif tipo_formulario == 'novo_cartao':
            Cartao.objects.create(
                nome=request.POST.get('nome', '').strip(),
                dia_fechamento=int(request.POST.get('dia_fechamento', 25)),
                dia_vencimento=int(request.POST.get('dia_vencimento', 5)),
                conta=minha_conta
            )
            messages.success(request, "Cartão cadastrado com sucesso!")

        elif tipo_formulario == 'registrar_pagamento':
            dono_obj = get_object_or_404(Pessoa, id=request.POST.get('dono_id'), conta__iexact=minha_conta)
            referencia = request.POST.get('referencia_gasto')
            descricao_pagamento = f"Pagamento de {dono_obj.nome}" + (f" (Ref: {referencia})" if referencia else "")

            Transacao.objects.create(
                descricao=descricao_pagamento,
                valor=parse_moeda(request.POST.get('valor')),
                dono=dono_obj,
                tipo='PAGAMENTO',
                categoria='Outros',
                data=datetime.strptime(request.POST.get('data'), '%Y-%m-%d').date(),
                conta=minha_conta,
                recorrente=False
            )

        return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

    # ==========================================
    # CÁLCULOS E CONSULTAS OTIMIZADAS (PERFORMANCE)
    # ==========================================
    todas_pessoas = Pessoa.objects.filter(conta__iexact=minha_conta)
    todos_cartoes = Cartao.objects.filter(conta__iexact=minha_conta)
    filtra_pessoa_id = request.GET.get('pessoa')

    mes_anterior = mes_selecionado - 1 if mes_selecionado > 1 else 12
    ano_anterior = ano_selecionado if mes_selecionado > 1 else ano_selecionado - 1
    mes_proximo = mes_selecionado + 1 if mes_selecionado < 12 else 1
    ano_proximo = ano_selecionado if mes_selecionado < 12 else ano_selecionado + 1

    ultimo_dia_mes = calendar.monthrange(ano_selecionado, mes_selecionado)[1]

    # Consultas otimizadas com filtros diretos no Banco (PostgreSQL / SQLite)
    trans_normais = Transacao.objects.filter(
        conta__iexact=minha_conta,
        recorrente=False,
        data__year=ano_selecionado,
        data__month=mes_selecionado
    ).select_related('cartao', 'dono')

    trans_rec = Transacao.objects.filter(
        conta__iexact=minha_conta,
        recorrente=True,
        data__lte=date(ano_selecionado, mes_selecionado, ultimo_dia_mes)
    ).select_related('cartao', 'dono')

    lista_transacoes_mes = []

    for t in trans_normais:
        if t.cartao:
            m_fat, a_fat = get_mes_fatura(t.data, t.cartao.dia_fechamento, t.cartao.dia_vencimento)
            if m_fat == mes_selecionado and a_fat == ano_selecionado:
                lista_transacoes_mes.append(t)
        else:
            lista_transacoes_mes.append(t)

    for t in trans_rec:
        if t.cartao:
            m_fat_orig, a_fat_orig = get_mes_fatura(t.data, t.cartao.dia_fechamento, t.cartao.dia_vencimento)
            if a_fat_orig < ano_selecionado or (a_fat_orig == ano_selecionado and m_fat_orig <= mes_selecionado):
                dia_valido = min(t.data.day, ultimo_dia_mes)
                t.data_exibicao = date(ano_selecionado, mes_selecionado, dia_valido)
                lista_transacoes_mes.append(t)
        else:
            dia_valido = min(t.data.day, ultimo_dia_mes)
            t.data_exibicao = date(ano_selecionado, mes_selecionado, dia_valido)
            lista_transacoes_mes.append(t)

    lista_transacoes_mes.sort(key=lambda x: getattr(x, 'data_exibicao', x.data), reverse=True)

    if filtra_pessoa_id:
        lista_transacoes_mes = [t for t in lista_transacoes_mes if str(t.dono.id) == str(filtra_pessoa_id)]

    meus_gastos = sum(float(t.valor) for t in lista_transacoes_mes if eu and t.dono_id == eu.id and t.tipo == 'DESPESA')

    familiares = Pessoa.objects.filter(is_titular=False, conta__iexact=minha_conta)
    dividas_detalhadas = []

    for familiar in familiares:
        despesas_fam = [t for t in lista_transacoes_mes if t.dono_id == familiar.id and t.tipo == 'DESPESA']
        pagamentos_fam = [t for t in lista_transacoes_mes if t.dono_id == familiar.id and t.tipo == 'PAGAMENTO']
        saldo_pessoa = sum(float(t.valor) for t in despesas_fam) - sum(float(t.valor) for t in pagamentos_fam)

        if abs(saldo_pessoa) > 0.01:
            despesas_pendentes = []
            for desp in despesas_fam:
                pago_nesta_despesa = sum(float(p.valor) for p in pagamentos_fam if f"(Ref: {desp.descricao})" in p.descricao)
                if float(desp.valor) - pago_nesta_despesa > 0.01:
                    despesas_pendentes.append({
                        'descricao': desp.descricao,
                        'valor_original': float(desp.valor),
                        'saldo': float(desp.valor) - pago_nesta_despesa
                    })

            texto_zap = f"Olá {familiar.nome}! Fechamento de {mes_selecionado:02d}/{ano_selecionado}.\n\n"
            if despesas_pendentes:
                texto_zap += "*🛒 Seus Gastos:*\n"
                for desp in despesas_pendentes:
                    texto_zap += f"- {desp['descricao']}: R$ {desp['saldo']:.2f}".replace('.', ',') + (" (Restante)\n" if desp['saldo'] < desp['valor_original'] else "\n")
                texto_zap += "\n"
            if sum(float(t.valor) for t in pagamentos_fam) > 0:
                texto_zap += f"*💵 Abatido:* R$ {sum(float(t.valor) for t in pagamentos_fam):.2f}\n".replace('.', ',')
            texto_zap += f"*👉 Saldo Pendente: R$ {saldo_pessoa:.2f}*\n".replace('.', ',')

            digitos_tel = ''.join(filter(str.isdigit, familiar.telefone or ''))
            if digitos_tel and not digitos_tel.startswith('55'):
                digitos_tel = f"55{digitos_tel}"
            url_base = f"https://wa.me/{digitos_tel}" if digitos_tel else "https://wa.me/"

            dividas_detalhadas.append({
                'id': familiar.id,
                'nome': familiar.nome,
                'saldo': saldo_pessoa,
                'whatsapp_link': f"{url_base}?text={urllib.parse.quote(texto_zap)}",
                'tem_telefone': bool(familiar.telefone),
                'despesas': despesas_pendentes
            })

    meta = MetaMensal.objects.filter(conta__iexact=minha_conta).first()
    valor_meta = float(meta.valor) if meta else 0.0
    saldo_restante = valor_meta - meus_gastos

    gastos_pessoais_dict = {}
    for t in lista_transacoes_mes:
        if eu and t.dono_id == eu.id and t.tipo == 'DESPESA':
            cat = t.categoria or 'Outros'
            gastos_pessoais_dict[cat] = gastos_pessoais_dict.get(cat, 0.0) + float(t.valor)

    maior_categoria = max(gastos_pessoais_dict, key=gastos_pessoais_dict.get) if gastos_pessoais_dict else "Nenhuma"
    valor_maior_cat = gastos_pessoais_dict.get(maior_categoria, 0.0)
    percentual_gasto = (meus_gastos / valor_meta * 100) if valor_meta > 0 else 0.0

    contexto = {
        'transacoes': lista_transacoes_mes,
        'pessoas': todas_pessoas,
        'cartoes': todos_cartoes,
        'meus_gastos': meus_gastos,
        'valor_meta': valor_meta,
        'saldo_restante': saldo_restante,
        'mes_atual': mes_selecionado,
        'ano_atual': ano_selecionado,
        'mes_anterior': mes_anterior,
        'ano_anterior': ano_anterior,
        'mes_proximo': mes_proximo,
        'ano_proximo': ano_proximo,
        'dividas_detalhadas': dividas_detalhadas,
        'filtra_pessoa_id': int(filtra_pessoa_id) if filtra_pessoa_id else None,
        'labels_grafico': list(gastos_pessoais_dict.keys()),
        'valores_grafico': list(gastos_pessoais_dict.values()),
        'maior_categoria': maior_categoria,
        'valor_maior_cat': valor_maior_cat,
        'percentual_gasto': percentual_gasto,
        'usuario': request.user
    }
    return render(request, 'gestao/dashboard.html', contexto)


@login_required(login_url='login')
def excluir_transacao(request, id):
    transacao = get_object_or_404(Transacao, id=id, conta__iexact=request.user.username)
    transacao.delete()
    mes = request.GET.get('mes')
    ano = request.GET.get('ano')
    return redirect(f"/?mes={mes}&ano={ano}") if mes and ano else redirect('dashboard')