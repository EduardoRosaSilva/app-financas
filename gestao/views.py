import calendar
import csv
import io
import json
import re
import unicodedata
import urllib.parse
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .forms import LoginUsuarioForm, RegistroUsuarioForm
from .models import Cartao, MetaMensal, Pessoa, Transacao


# ==========================================
# INFRAESTRUTURA E HEALTH CHECK
# ==========================================

@never_cache
def health_check(request):
    """Endpoint de baixa latência para monitoramento de disponibilidade (keep-alive)."""
    return JsonResponse({
        'status': 'ok',
        'service': 'finpro-web',
        'timestamp': datetime.now().isoformat()
    })


# ==========================================
# SEGURANÇA E HIGIENIZAÇÃO DE DADOS
# ==========================================

def get_client_ip(request) -> str:
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '127.0.0.1')


def verificar_bloqueio_ip(request, escopo='login', max_tentativas=6, tempo_bloqueio=300):
    """Controle de taxa de requisições por IP para mitigação de força bruta."""
    ip = get_client_ip(request)
    chave = f"finpro_ratelimit_{escopo}_{ip}"
    tentativas = cache.get(chave, 0)
    return tentativas >= max_tentativas, chave, tentativas, tempo_bloqueio


def sanitizar_texto_csv(texto: str) -> str:
    """Neutraliza vetores de Formula Injection (CSV Injection) e limita comprimento."""
    if not texto:
        return ""
    limpo = str(texto).strip()
    if limpo and limpo[0] in ('=', '+', '-', '@', '\t', '\r'):
        limpo = "'" + limpo
    return limpo[:200]


def normalizar_chave_coluna(chave: str) -> str:
    if not chave:
        return ""
    return ''.join(
        c for c in unicodedata.normalize('NFD', str(chave).strip().lower())
        if unicodedata.category(c) != 'Mn'
    )


# ==========================================
# REGRAS DE NEGÓCIO CONTÁBEIS E PARSERS
# ==========================================

def parse_moeda_decimal(valor_raw) -> Decimal:
    """Converte entradas monetárias textuais ou numéricas em Decimal exato (2 casas)."""
    if valor_raw is None or valor_raw == '':
        return Decimal('0.00')
    if isinstance(valor_raw, Decimal):
        return valor_raw.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    s = str(valor_raw).replace('R$', '').replace('r$', '').replace(' ', '').strip()
    if not s:
        return Decimal('0.00')
    if ',' in s and '.' in s:
        if s.rfind(',') > s.rfind('.'):
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
    elif ',' in s:
        s = s.replace(',', '.')
    try:
        return Decimal(s).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return Decimal('0.00')


def parse_moeda(valor_raw) -> float:
    return float(parse_moeda_decimal(valor_raw))


def dividir_em_parcelas_exatas(valor_total: float, qtd_parcelas: int) -> list[Decimal]:
    """
    Divide um montante em N parcelas preservando a integridade contábil do total.
    Qualquer centavo residual é alocado na primeira parcela.
    """
    qtd = max(1, int(qtd_parcelas))
    total_dec = parse_moeda_decimal(valor_total)
    if qtd == 1:
        return [total_dec]

    parcela_base = (total_dec / Decimal(qtd)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    soma_base = parcela_base * Decimal(qtd)
    diferenca = total_dec - soma_base

    lista = [parcela_base for _ in range(qtd)]
    lista[0] = (lista[0] + diferenca).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return lista


def parse_data_flexivel(data_raw: str):
    if not data_raw:
        return None
    s = str(data_raw).strip().split(' ')[0].split('T')[0]
    formatos = ('%Y-%m-%d', '%d/%m/%Y', '%d/%m/%y', '%d-%m-%Y', '%Y/%m/%d')
    for fmt in formatos:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def interpretar_comando_inteligente(texto: str, cartoes_usuario):
    """Extrai descrição, valor, parcelas, recorrência e cartão a partir de texto livre."""
    texto_limpo = texto.strip()
    recorrente = bool(re.search(r'\b(fixo|fixa|mensal|recorrente|assinatura)\b', texto_limpo, re.IGNORECASE))
    texto_limpo = re.sub(r'\b(fixo|fixa|mensal|recorrente|assinatura)\b', '', texto_limpo, flags=re.IGNORECASE)

    parcelas = 1
    match_parc = re.search(r'\b(\d{1,2})\s*[xX]\b', texto_limpo)
    if match_parc and not recorrente:
        parcelas = max(1, min(120, int(match_parc.group(1))))
        texto_limpo = texto_limpo[:match_parc.start()] + texto_limpo[match_parc.end():]

    cartao_detectado = None
    for c in cartoes_usuario:
        nome_c = c.nome.strip()
        if nome_c and re.search(r'\b' + re.escape(nome_c) + r'\b', texto_limpo, re.IGNORECASE):
            cartao_detectado = c
            texto_limpo = re.sub(r'\b' + re.escape(nome_c) + r'\b', '', texto_limpo, flags=re.IGNORECASE)
            break

    matches_valor = list(re.finditer(r'(?:R\$\s*)?(\d+(?:[.,]\d{3})*(?:[.,]\d{1,2})|\d+)', texto_limpo))
    valor = 0.0
    if matches_valor:
        ultimo_match = matches_valor[-1]
        valor = parse_moeda(ultimo_match.group(1))
        texto_limpo = texto_limpo[:ultimo_match.start()] + texto_limpo[ultimo_match.end():]

    descricao = re.sub(r'\s+', ' ', texto_limpo).strip(' -/,;')
    descricao = re.sub(r'\b(no|na|em|de|do|da)\s*$', '', descricao, flags=re.IGNORECASE).strip()

    return {
        'descricao': sanitizar_texto_csv(descricao or 'Despesa'),
        'valor': valor,
        'parcelas': parcelas,
        'recorrente': recorrente,
        'cartao': cartao_detectado,
    }


def garantir_titular_conta(username: str) -> Pessoa:
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
    mes = data_compra.month
    ano = data_compra.year
    if data_compra.day >= dia_fechamento:
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1
    return mes, ano


def prever_categoria(descricao_nova, transacoes_historicas):
    regra_direta = fallback_rules(descricao_nova)
    if not transacoes_historicas:
        return regra_direta

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
    if not palavras_alvo or not vocabulario or not any(p in vocabulario for p in palavras_alvo):
        return regra_direta

    melhor_cat = regra_direta
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
    if any(k in cat_lower for k in ('restaurante', 'food', 'alimenta', 'ifd', 'ifood', 'lanch', 'padaria', 'burger', 'pizza', 'cafe', 'açaí', 'acai', 'bar ')):
        return 'Alimentação'
    if any(k in cat_lower for k in ('supermercado', 'grocery', 'mercado', 'conveniencia', 'atacad', 'mart', 'carrefour', 'assai', 'bahamas', 'hortifruti', 'açougue')):
        return 'Mercado'
    if any(k in cat_lower for k in ('transporte', 'auto', 'uber', 'posto', 'combustivel', '99app', '99*', 'gasolina', 'estacionamento', 'pedagio', 'ipva', 'oficina')):
        return 'Transporte'
    if any(k in cat_lower for k in ('saude', 'farm', 'medic', 'odonto', 'drogaria', 'droga', 'raia', 'pacheco', 'unimed', 'consulta', 'exame', 'academia')):
        return 'Saúde'
    if any(k in cat_lower for k in ('aluguel', 'condominio', 'energia', 'cemig', 'luz', 'agua', 'copasa', 'saneago', 'internet', 'claro', 'vivo', 'tim', 'iptu', 'gas')):
        return 'Moradia'
    if any(k in cat_lower for k in ('netflix', 'spotify', 'prime', 'assinatura', 'youtube', 'disney', 'hbo', 'icloud', 'google one', 'chatgpt', 'openai', 'steam')):
        return 'Assinaturas'
    if any(k in cat_lower for k in ('loja', 'shopee', 'mercado livre', 'mercadolivre', 'meli', 'pernambucanas', 'amazon', 'magalu', 'shein', 'aliexpress', 'renner', 'cea', 'roupa')):
        return 'Compras'
    if any(k in cat_lower for k in ('cinema', 'ingresso', 'show', 'viagem', 'hotel', 'airbnb', 'lazer', 'festa', 'clube')):
        return 'Lazer'
    return 'Outros'


def analisar_inteligencia_cartoes(cartoes, transacoes_mes):
    hoje = date.today()
    resumo = []
    melhor_cartao = None
    maior_prazo = -1

    for c in cartoes:
        total_cartao = sum(float(t.valor) for t in transacoes_mes if t.cartao_id == c.id and t.tipo == 'DESPESA')
        ja_fechou_este_mes = hoje.day >= c.dia_fechamento

        if ja_fechou_este_mes:
            status_ciclo = "Melhor dia de compra (próxima fatura)"
            cor_badge = "success"
            dias_para_fechar = (calendar.monthrange(hoje.year, hoje.month)[1] - hoje.day) + c.dia_fechamento
        else:
            dias_para_fechar = c.dia_fechamento - hoje.day
            if dias_para_fechar <= 3:
                status_ciclo = f"Fecha em {dias_para_fechar} dia(s)"
                cor_badge = "warning"
            else:
                status_ciclo = f"Fatura aberta (fecha dia {c.dia_fechamento})"
                cor_badge = "secondary"

        item_cartao = {
            'id': c.id,
            'nome': c.nome,
            'dia_fechamento': c.dia_fechamento,
            'dia_vencimento': c.dia_vencimento,
            'total_fatura': total_cartao,
            'status_ciclo': status_ciclo,
            'cor_badge': cor_badge,
            'ja_fechou': ja_fechou_este_mes,
            'dias_para_fechar': dias_para_fechar,
        }
        resumo.append(item_cartao)

        if dias_para_fechar > maior_prazo:
            maior_prazo = dias_para_fechar
            melhor_cartao = item_cartao

    return resumo, melhor_cartao


def gerar_insights_inteligentes(
    meus_gastos, gastos_mes_anterior, valor_meta, gastos_por_cat, transacoes_mes, eu, mes_sel, ano_sel
):
    hoje = date.today()
    dias_no_mes = calendar.monthrange(ano_sel, mes_sel)[1]
    if ano_sel == hoje.year and mes_sel == hoje.month:
        dias_decorridos = max(1, hoje.day)
        dias_restantes = max(1, dias_no_mes - hoje.day)
    elif (ano_sel, mes_sel) < (hoje.year, hoje.month):
        dias_decorridos = dias_no_mes
        dias_restantes = 1
    else:
        dias_decorridos = 1
        dias_restantes = dias_no_mes

    minhas_despesas_lista = [
        t for t in transacoes_mes if eu and t.dono_id == eu.id and t.tipo == 'DESPESA'
    ]
    qtd_transacoes_pessoais = len(minhas_despesas_lista)
    ticket_medio = (meus_gastos / qtd_transacoes_pessoais) if qtd_transacoes_pessoais > 0 else 0.0

    media_diaria = meus_gastos / dias_decorridos if meus_gastos > 0 else 0.0
    projecao_mes = media_diaria * dias_no_mes if (ano_sel == hoje.year and mes_sel == hoje.month) else meus_gastos

    gastos_fixos = sum(float(t.valor) for t in minhas_despesas_lista if t.recorrente)
    gastos_variaveis = max(0.0, meus_gastos - gastos_fixos)
    custo_fixo_anual = gastos_fixos * 12
    perc_fixos = (gastos_fixos / meus_gastos * 100) if meus_gastos > 0 else 0.0
    perc_variaveis = (gastos_variaveis / meus_gastos * 100) if meus_gastos > 0 else 0.0

    if gastos_mes_anterior > 0:
        variacao_mom = ((meus_gastos - gastos_mes_anterior) / gastos_mes_anterior) * 100.0
    else:
        variacao_mom = 0.0

    cat_essenciais = {'Moradia', 'Mercado', 'Saúde', 'Transporte'}
    gastos_essenciais = sum(v for k, v in gastos_por_cat.items() if k in cat_essenciais)
    gastos_estilo_vida = sum(v for k, v in gastos_por_cat.items() if k not in cat_essenciais)

    base_calculo = valor_meta if valor_meta > 0 else (meus_gastos if meus_gastos > 0 else 1.0)
    perc_essenciais = min(100.0, (gastos_essenciais / base_calculo) * 100)
    perc_estilo_vida = min(100.0, (gastos_estilo_vida / base_calculo) * 100)
    economia_atual = max(0.0, valor_meta - meus_gastos) if valor_meta > 0 else 0.0
    perc_economia = min(100.0, (economia_atual / valor_meta) * 100) if valor_meta > 0 else 0.0

    if valor_meta <= 0 and meus_gastos == 0:
        score, nivel_score, cor_score = 750, "Sem meta", "secondary"
    elif valor_meta <= 0:
        score, nivel_score, cor_score = 600, "Defina a meta", "warning"
    else:
        uso_meta = meus_gastos / valor_meta
        if uso_meta <= 0.5:
            score = 950
        elif uso_meta <= 0.8:
            score = int(900 - ((uso_meta - 0.5) * 400))
        elif uso_meta <= 1.0:
            score = int(780 - ((uso_meta - 0.8) * 900))
        else:
            score = max(150, int(600 - ((uso_meta - 1.0) * 600)))

        if (gastos_fixos / valor_meta) > 0.5:
            score = max(100, score - 80)

        if score >= 800:
            nivel_score, cor_score = "Saudável", "success"
        elif score >= 600:
            nivel_score, cor_score = "Equilibrado", "primary"
        elif score >= 450:
            nivel_score, cor_score = "Atenção", "warning"
        else:
            nivel_score, cor_score = "Acima da meta", "danger"

    orcamento_diario_seguro = max(0.0, (valor_meta - meus_gastos) / dias_restantes) if valor_meta > meus_gastos else 0.0

    insights = []
    if valor_meta > 0:
        if meus_gastos > valor_meta:
            excesso = meus_gastos - valor_meta
            insights.append({
                'tipo': 'danger',
                'icone': 'bi-exclamation-circle',
                'texto': f"Orçamento excedido em R$ {excesso:.2f} neste mês."
            })
        elif projecao_mes > valor_meta and ano_sel == hoje.year and mes_sel == hoje.month:
            insights.append({
                'tipo': 'warning',
                'icone': 'bi-graph-up-arrow',
                'texto': f"Média atual de R$ {media_diaria:.2f}/dia projeta fechamento em R$ {projecao_mes:.2f}. Limite diário recomendado: R$ {orcamento_diario_seguro:.2f}."
            })
        else:
            economia_proj = max(0.0, valor_meta - projecao_mes)
            insights.append({
                'tipo': 'success',
                'icone': 'bi-check-circle',
                'texto': f"Dentro do planejado. Limite disponível de R$ {orcamento_diario_seguro:.2f}/dia (economia projetada de R$ {economia_proj:.2f})."
            })

    if gastos_por_cat:
        cat_maior = max(gastos_por_cat, key=gastos_por_cat.get)
        val_maior = gastos_por_cat[cat_maior]
        peso_maior = (val_maior / meus_gastos * 100) if meus_gastos > 0 else 0
        if peso_maior >= 40:
            insights.append({
                'tipo': 'secondary',
                'icone': 'bi-pie-chart',
                'texto': f"A categoria '{cat_maior}' concentra {peso_maior:.0f}% (R$ {val_maior:.2f}) das suas despesas no período."
            })

    return {
        'media_diaria': media_diaria,
        'projecao_mes': projecao_mes,
        'orcamento_diario_seguro': orcamento_diario_seguro,
        'ticket_medio': ticket_medio,
        'qtd_transacoes_pessoais': qtd_transacoes_pessoais,
        'gastos_fixos': gastos_fixos,
        'gastos_variaveis': gastos_variaveis,
        'custo_fixo_anual': custo_fixo_anual,
        'perc_fixos': perc_fixos,
        'perc_variaveis': perc_variaveis,
        'variacao_mom': variacao_mom,
        'gastos_mes_anterior': gastos_mes_anterior,
        'gastos_essenciais': gastos_essenciais,
        'gastos_estilo_vida': gastos_estilo_vida,
        'perc_essenciais': perc_essenciais,
        'perc_estilo_vida': perc_estilo_vida,
        'perc_economia': perc_economia,
        'score': score,
        'nivel_score': nivel_score,
        'cor_score': cor_score,
        'lista_insights': insights,
    }


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

        bloqueado, chave, tentativas, _ = verificar_bloqueio_ip(request, escopo='registro', max_tentativas=10)
        if bloqueado:
            messages.error(request, 'Muitas tentativas realizadas. Aguarde alguns minutos.')
            return render(request, 'gestao/register.html', {'form': RegistroUsuarioForm()})

        form = RegistroUsuarioForm(request.POST)
        if form.is_valid():
            usuario = form.save()
            garantir_titular_conta(usuario.username)
            login(request, usuario)
            cache.delete(chave)
            return redirect('dashboard')
        else:
            cache.set(chave, tentativas + 1, 300)
    else:
        form = RegistroUsuarioForm()
    return render(request, 'gestao/register.html', {'form': form})


@never_cache
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        bloqueado, chave, tentativas, _ = verificar_bloqueio_ip(request, escopo='login', max_tentativas=6)
        if bloqueado:
            messages.error(request, 'Muitas tentativas de acesso. Aguarde 5 minutos antes de tentar novamente.')
            return render(request, 'gestao/login.html', {'form': LoginUsuarioForm()})

        form = LoginUsuarioForm(request, data=request.POST)
        if form.is_valid():
            usuario = form.get_user()
            garantir_titular_conta(usuario.username)
            login(request, usuario)
            cache.delete(chave)
            return redirect('dashboard')
        else:
            cache.set(chave, tentativas + 1, 300)
            messages.error(request, 'Usuário ou senha inválidos.')
    else:
        form = LoginUsuarioForm()
    return render(request, 'gestao/login.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('login')


# ==========================================
# DASHBOARD PRINCIPAL
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

        # 0. LANÇAMENTO RÁPIDO POR TEXTO
        if tipo_formulario == 'lancamento_inteligente':
            comando = request.POST.get('comando_ia', '').strip()
            if comando:
                cartoes_usr = list(Cartao.objects.filter(conta__iexact=minha_conta))
                dados_cmd = interpretar_comando_inteligente(comando, cartoes_usr)
                if dados_cmd['valor'] <= 0:
                    messages.error(request, "Informe a descrição e o valor. Exemplo: Uber 24,90")
                    return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

                historico = list(Transacao.objects.filter(conta__iexact=minha_conta).exclude(categoria='Outros'))
                cat_prevista = prever_categoria(dados_cmd['descricao'], historico)
                parcelas = dados_cmd['parcelas']
                lista_parcelas_dec = dividir_em_parcelas_exatas(dados_cmd['valor'], parcelas)

                with transaction.atomic():
                    for i, val_parc in enumerate(lista_parcelas_dec):
                        mes_loop = hoje.month + i
                        ano_loop = hoje.year + ((mes_loop - 1) // 12)
                        mes_calc = ((mes_loop - 1) % 12) + 1
                        ult_dia = calendar.monthrange(ano_loop, mes_calc)[1]
                        nova_dt = date(ano_loop, mes_calc, min(hoje.day, ult_dia))
                        desc_final = f"{dados_cmd['descricao']} ({i+1}/{parcelas})" if parcelas > 1 else dados_cmd['descricao']

                        Transacao.objects.create(
                            descricao=desc_final,
                            valor=val_parc,
                            data=nova_dt,
                            tipo='DESPESA',
                            categoria=cat_prevista,
                            dono=eu,
                            cartao=dados_cmd['cartao'],
                            conta=minha_conta,
                            recorrente=dados_cmd['recorrente']
                        )

                if dados_cmd['cartao']:
                    m_dest, a_dest = get_mes_fatura(hoje, dados_cmd['cartao'].dia_fechamento, dados_cmd['cartao'].dia_vencimento)
                else:
                    m_dest, a_dest = hoje.month, hoje.year

                messages.success(
                    request,
                    f"Lançamento registrado: {dados_cmd['descricao']} (R$ {dados_cmd['valor']:.2f}) em {cat_prevista}."
                )
                return redirect(f'/?mes={m_dest}&ano={a_dest}')

        # 1. IMPORTAÇÃO DE FATURA CSV
        elif tipo_formulario == 'upload_fatura':
            arquivo_csv = request.FILES.get('arquivo_csv')
            if not arquivo_csv:
                messages.error(request, 'Selecione um arquivo CSV válido.')
                return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

            if arquivo_csv.size > 5 * 1024 * 1024:
                messages.error(request, 'Arquivo excede o limite máximo de 5MB.')
                return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

            raw_bytes = arquivo_csv.read()
            decoded_file = None
            for encoding in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
                try:
                    decoded_file = raw_bytes.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue

            if not decoded_file:
                messages.error(request, 'Formato de arquivo incompatível.')
                return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

            linhas_nao_vazias = [ln for ln in decoded_file.splitlines() if ln.strip()]
            if not linhas_nao_vazias:
                messages.error(request, 'O arquivo CSV está vazio.')
                return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

            primeira_linha = linhas_nao_vazias[0]
            if ';' in primeira_linha and primeira_linha.count(';') >= primeira_linha.count(','):
                delimitador = ';'
            elif '\t' in primeira_linha:
                delimitador = '\t'
            else:
                delimitador = ','

            io_string = io.StringIO('\n'.join(linhas_nao_vazias))
            reader = csv.DictReader(io_string, delimiter=delimitador)

            historico = list(Transacao.objects.filter(conta__iexact=minha_conta).exclude(categoria='Outros'))
            existentes_set = set(
                (t.data.strftime('%Y-%m-%d'), round(float(t.valor), 2), t.descricao.lower().strip())
                for t in Transacao.objects.filter(conta__iexact=minha_conta)
            )

            transacoes_fatura = []
            possiveis_datas = ('date', 'data', 'data da compra', 'dt', 'dia', 'lancamento data')
            possiveis_titulos = ('title', 'titulo', 'descricao', 'estabelecimento', 'historico', 'lancamento', 'nome', 'memo', 'detalhe')
            possiveis_valores = ('amount', 'valor', 'valor (r$)', 'valor r$', 'total', 'preco', 'debito')
            possiveis_categorias = ('category', 'categoria', 'tipo')

            for index, row in enumerate(reader):
                row_norm = {normalizar_chave_coluna(k): str(v).strip() for k, v in row.items() if k is not None and v is not None}
                if not row_norm:
                    continue

                data_str = next((row_norm[k] for k in possiveis_datas if k in row_norm and row_norm[k]), '')
                titulo = next((row_norm[k] for k in possiveis_titulos if k in row_norm and row_norm[k]), '')
                valor_str = next((row_norm[k] for k in possiveis_valores if k in row_norm and row_norm[k]), '')
                cat_csv = next((row_norm[k] for k in possiveis_categorias if k in row_norm and row_norm[k]), '')

                valores_lista = list(row_norm.values())
                if not data_str and len(valores_lista) >= 3:
                    data_str = valores_lista[0]
                if not titulo and len(valores_lista) >= 3:
                    titulo = valores_lista[-2] if len(valores_lista) >= 3 else valores_lista[1]
                if not valor_str and len(valores_lista) >= 3:
                    valor_str = valores_lista[-1]

                data_parsed = parse_data_flexivel(data_str)
                if not data_parsed or not titulo:
                    continue

                titulo_lower = titulo.lower()
                if any(ign in titulo_lower for ign in ('pagamento recebido', 'pagamento de fatura', 'pagamento da fatura', 'inclusao de pagamento')):
                    continue

                valor = parse_moeda(valor_str)
                if valor < 0:
                    valor = abs(valor)
                if valor <= 0:
                    continue

                titulo_seguro = sanitizar_texto_csv(titulo)
                categoria_prevista = prever_categoria(titulo_seguro, historico)
                if categoria_prevista == 'Outros' and cat_csv:
                    categoria_prevista = fallback_rules(cat_csv)

                data_iso = data_parsed.strftime('%Y-%m-%d')
                ja_existe = (data_iso, round(valor, 2), titulo_seguro.lower()) in existentes_set

                transacoes_fatura.append({
                    'id_temp': index,
                    'data': data_iso,
                    'descricao': titulo_seguro,
                    'valor': round(valor, 2),
                    'categoria': categoria_prevista,
                    'duplicada': ja_existe,
                })

            if not transacoes_fatura:
                messages.error(
                    request,
                    'Nenhum lançamento válido identificado no CSV. Verifique as colunas Data, Descrição e Valor.'
                )
                return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

            contexto_revisao = {
                'transacoes_fatura': transacoes_fatura,
                'total_fatura_importada': sum(t['valor'] for t in transacoes_fatura if not t['duplicada']),
                'pessoas': Pessoa.objects.filter(conta__iexact=minha_conta),
                'cartoes': Cartao.objects.filter(conta__iexact=minha_conta),
            }
            return render(request, 'gestao/revisao_fatura.html', contexto_revisao)

        # 2. SALVAR ITENS IMPORTADOS EM LOTE
        elif tipo_formulario == 'salvar_fatura_lote':
            ids_marcados = request.POST.getlist('importar_chk')
            cartao_id = request.POST.get('cartao_lote')
            novo_cartao_nome = request.POST.get('novo_cartao_lote_nome', '').strip()

            cartao_obj = None
            if cartao_id == 'NOVO_CARTAO' and novo_cartao_nome:
                cartao_obj = Cartao.objects.create(
                    nome=sanitizar_texto_csv(novo_cartao_nome),
                    dia_fechamento=max(1, min(31, int(request.POST.get('novo_cartao_fechamento', 25) or 25))),
                    dia_vencimento=max(1, min(31, int(request.POST.get('novo_cartao_vencimento', 5) or 5))),
                    conta=minha_conta
                )
            elif cartao_id and cartao_id not in ('', 'SEM_CARTAO', 'NOVO_CARTAO') and str(cartao_id).isdigit():
                cartao_obj = Cartao.objects.filter(id=cartao_id, conta__iexact=minha_conta).first()

            novas_transacoes = []
            ultimo_mes = mes_selecionado
            ultimo_ano = ano_selecionado

            with transaction.atomic():
                for t_id in ids_marcados:
                    desc = sanitizar_texto_csv(request.POST.get(f'desc_{t_id}', 'Despesa importada'))
                    valor_dec = parse_moeda_decimal(request.POST.get(f'valor_{t_id}', '0'))
                    if valor_dec <= Decimal('0.00'):
                        continue
                    data_val = request.POST.get(f'data_{t_id}')
                    dono_id = request.POST.get(f'dono_{t_id}')
                    categoria = request.POST.get(f'cat_{t_id}', 'Outros')

                    dono_obj = None
                    if dono_id and str(dono_id).isdigit():
                        dono_obj = Pessoa.objects.filter(id=dono_id, conta__iexact=minha_conta).first()
                    dono_obj = dono_obj or eu

                    data_parsed = parse_data_flexivel(data_val) or hoje

                    novas_transacoes.append(Transacao(
                        descricao=desc,
                        valor=valor_dec,
                        data=data_parsed,
                        tipo='DESPESA',
                        categoria=categoria,
                        dono=dono_obj,
                        cartao=cartao_obj,
                        conta=minha_conta,
                        recorrente=False
                    ))

                    if cartao_obj:
                        ultimo_mes, ultimo_ano = get_mes_fatura(data_parsed, cartao_obj.dia_fechamento, cartao_obj.dia_vencimento)
                    else:
                        ultimo_mes = data_parsed.month
                        ultimo_ano = data_parsed.year

                if novas_transacoes:
                    Transacao.objects.bulk_create(novas_transacoes)

            messages.success(request, f"{len(novas_transacoes)} lançamentos importados para {ultimo_mes:02d}/{ultimo_ano}.")
            return redirect(f'/?mes={ultimo_mes}&ano={ultimo_ano}')

        # 3. NOVA TRANSAÇÃO
        elif tipo_formulario == 'nova_transacao':
            descricao_digitada = sanitizar_texto_csv(request.POST.get('descricao', ''))
            valor_digitado = parse_moeda(request.POST.get('valor', '0'))

            dono_id_escolhido = request.POST.get('dono_id')
            tipo_escolhido = request.POST.get('tipo', 'DESPESA')
            categoria_escolhida = request.POST.get('categoria', 'Outros')
            data_digitada = request.POST.get('data')
            cartao_id_escolhido = request.POST.get('cartao_id')

            recorrente = request.POST.get('recorrente') == '1'
            parcelas = 1 if recorrente else max(1, min(120, int(request.POST.get('parcelas', 1) or 1)))

            donos_extras = request.POST.getlist('dono_id_extra')
            valores_extras_str = request.POST.getlist('valor_extra')

            dono_obj = get_object_or_404(Pessoa, id=dono_id_escolhido, conta__iexact=minha_conta) if dono_id_escolhido else eu
            cartao_obj = get_object_or_404(Cartao, id=cartao_id_escolhido, conta__iexact=minha_conta) if cartao_id_escolhido else None
            data_base = parse_data_flexivel(data_digitada) or hoje

            parcelas_totais_dec = dividir_em_parcelas_exatas(valor_digitado, parcelas)
            matriz_extras_dec = [dividir_em_parcelas_exatas(parse_moeda(v), parcelas) for v in valores_extras_str]

            with transaction.atomic():
                for i in range(parcelas):
                    mes_atual = data_base.month + i
                    ano_atual_loop = data_base.year + ((mes_atual - 1) // 12)
                    mes_calculado = ((mes_atual - 1) % 12) + 1
                    ultimo_dia_mes = calendar.monthrange(ano_atual_loop, mes_calculado)[1]
                    dia_calculado = min(data_base.day, ultimo_dia_mes)
                    nova_data = date(ano_atual_loop, mes_calculado, dia_calculado)

                    desc_salvar = f"{descricao_digitada} ({i+1}/{parcelas})" if parcelas > 1 else descricao_digitada
                    soma_extras_i = sum((col[i] for col in matriz_extras_dec), Decimal('0.00'))
                    valor_dono_1 = max(Decimal('0.00'), parcelas_totais_dec[i] - soma_extras_i)

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
                        valor_deste_amigo = matriz_extras_dec[idx][i] if idx < len(matriz_extras_dec) else Decimal('0.00')
                        if dono_extra_id and valor_deste_amigo > Decimal('0.00'):
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
            messages.success(request, "Lançamento salvo com sucesso.")
            return redirect(f'/?mes={mes_red}&ano={ano_red}')

        # 4. EDITAR TRANSAÇÃO
        elif tipo_formulario == 'editar_transacao':
            t_id = request.POST.get('transacao_id')
            transacao = get_object_or_404(Transacao, id=t_id, conta__iexact=minha_conta)

            descricao = sanitizar_texto_csv(request.POST.get('descricao', ''))
            valor = parse_moeda(request.POST.get('valor', '0'))
            data_val = request.POST.get('data')
            tipo = request.POST.get('tipo', 'DESPESA')
            categoria = request.POST.get('categoria', 'Outros')
            recorrente = request.POST.get('recorrente') == '1'
            parcelas = 1 if recorrente else max(1, min(120, int(request.POST.get('parcelas', 1) or 1)))
            dono_id = request.POST.get('dono_id')
            cartao_id = request.POST.get('cartao_id')

            donos_extras = request.POST.getlist('dono_id_extra')
            valores_extras_str = request.POST.getlist('valor_extra')

            parcelas_totais_dec = dividir_em_parcelas_exatas(valor, parcelas)
            valores_extras_dec = [
                (parse_moeda_decimal(v) / Decimal(parcelas)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                for v in valores_extras_str
            ]
            soma_extras = sum(valores_extras_dec, Decimal('0.00'))
            valor_principal_1 = (
                parcelas_totais_dec[0] - soma_extras
                if (Decimal('0.00') < soma_extras < parcelas_totais_dec[0])
                else parcelas_totais_dec[0]
            )

            data_base = parse_data_flexivel(data_val) or transacao.data
            transacao.descricao = f"{descricao} (1/{parcelas})" if parcelas > 1 else descricao
            transacao.valor = valor_principal_1
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
                val_parc_i = (
                    parcelas_totais_dec[i] - soma_extras
                    if (Decimal('0.00') < soma_extras < parcelas_totais_dec[i])
                    else parcelas_totais_dec[i]
                )

                Transacao.objects.create(
                    descricao=f"{descricao} ({i+1}/{parcelas})",
                    valor=val_parc_i,
                    data=nova_data,
                    tipo=tipo,
                    categoria=categoria,
                    dono=transacao.dono,
                    cartao=transacao.cartao,
                    conta=minha_conta,
                    recorrente=False
                )

            if donos_extras and any(v > Decimal('0.00') for v in valores_extras_dec):
                for extra_id, extra_val in zip(donos_extras, valores_extras_dec):
                    if extra_id and extra_val > Decimal('0.00'):
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

            messages.success(request, "Lançamento atualizado.")

        # 5. OUTROS GERENCIAMENTOS
        elif tipo_formulario == 'atualizar_meta':
            novo_valor = parse_moeda_decimal(request.POST.get('valor_meta'))
            meta = MetaMensal.objects.filter(conta__iexact=minha_conta).first()
            if meta:
                meta.valor = novo_valor
                meta.save()
            else:
                MetaMensal.objects.create(valor=novo_valor, conta=minha_conta)
            messages.success(request, "Meta mensal atualizada.")

        elif tipo_formulario == 'nova_pessoa':
            Pessoa.objects.create(
                nome=sanitizar_texto_csv(request.POST.get('nome', '')),
                telefone=re.sub(r'[^\d+]', '', request.POST.get('telefone', '')),
                is_titular=not Pessoa.objects.filter(conta__iexact=minha_conta, is_titular=True).exists(),
                conta=minha_conta
            )
            messages.success(request, "Pessoa cadastrada.")

        elif tipo_formulario == 'editar_pessoa':
            p_id = request.POST.get('pessoa_id')
            p = get_object_or_404(Pessoa, id=p_id, conta__iexact=minha_conta)
            p.nome = sanitizar_texto_csv(request.POST.get('nome', ''))
            p.telefone = re.sub(r'[^\d+]', '', request.POST.get('telefone', ''))
            p.save()
            messages.success(request, f"Cadastro de {p.nome} atualizado.")

        elif tipo_formulario == 'excluir_pessoa':
            p_id = request.POST.get('pessoa_id')
            p = get_object_or_404(Pessoa, id=p_id, conta__iexact=minha_conta)
            if p.is_titular:
                messages.error(request, "O titular da conta não pode ser removido.")
            else:
                p.delete()
                messages.success(request, "Pessoa removida.")

        elif tipo_formulario == 'novo_cartao':
            Cartao.objects.create(
                nome=sanitizar_texto_csv(request.POST.get('nome', '')),
                dia_fechamento=max(1, min(31, int(request.POST.get('dia_fechamento', 25) or 25))),
                dia_vencimento=max(1, min(31, int(request.POST.get('dia_vencimento', 5) or 5))),
                conta=minha_conta
            )
            messages.success(request, "Cartão adicionado.")

        elif tipo_formulario == 'registrar_pagamento':
            dono_obj = get_object_or_404(Pessoa, id=request.POST.get('dono_id'), conta__iexact=minha_conta)
            referencia = request.POST.get('referencia_gasto')
            descricao_pagamento = f"Pagamento de {dono_obj.nome}" + (f" (Ref: {referencia})" if referencia else "")

            Transacao.objects.create(
                descricao=descricao_pagamento,
                valor=parse_moeda_decimal(request.POST.get('valor')),
                dono=dono_obj,
                tipo='PAGAMENTO',
                categoria='Outros',
                data=parse_data_flexivel(request.POST.get('data')) or hoje,
                conta=minha_conta,
                recorrente=False
            )
            messages.success(request, "Recebimento registrado.")

        return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

    # ==========================================
    # CONSULTAS E CONSOLIDAÇÃO DO PERÍODO
    # ==========================================
    todas_pessoas = Pessoa.objects.filter(conta__iexact=minha_conta)
    todos_cartoes = list(Cartao.objects.filter(conta__iexact=minha_conta))
    filtra_pessoa_id = request.GET.get('pessoa')

    mes_anterior = mes_selecionado - 1 if mes_selecionado > 1 else 12
    ano_anterior = ano_selecionado if mes_selecionado > 1 else ano_selecionado - 1
    mes_proximo = mes_selecionado + 1 if mes_selecionado < 12 else 1
    ano_proximo = ano_selecionado if mes_selecionado < 12 else ano_selecionado + 1

    ultimo_dia_mes = calendar.monthrange(ano_selecionado, mes_selecionado)[1]

    mes_6_atras = mes_selecionado - 6
    ano_6_atras = ano_selecionado
    while mes_6_atras <= 0:
        mes_6_atras += 12
        ano_6_atras -= 1

    data_inicio_janela = date(ano_6_atras, mes_6_atras, 1)
    data_fim_janela = date(ano_selecionado, mes_selecionado, ultimo_dia_mes)

    trans_normais = list(Transacao.objects.filter(
        conta__iexact=minha_conta,
        recorrente=False,
        data__gte=data_inicio_janela,
        data__lte=data_fim_janela
    ).select_related('cartao', 'dono'))

    trans_rec = list(Transacao.objects.filter(
        conta__iexact=minha_conta,
        recorrente=True,
        data__lte=data_fim_janela
    ).select_related('cartao', 'dono'))

    def obter_transacoes_competencia(m_alvo, a_alvo):
        ult_dia = calendar.monthrange(a_alvo, m_alvo)[1]
        itens = []
        for t in trans_normais:
            if t.cartao:
                m_fat, a_fat = get_mes_fatura(t.data, t.cartao.dia_fechamento, t.cartao.dia_vencimento)
                if m_fat == m_alvo and a_fat == a_alvo:
                    itens.append(t)
            else:
                if t.data.month == m_alvo and t.data.year == a_alvo:
                    itens.append(t)

        for t in trans_rec:
            if t.cartao:
                m_fat_orig, a_fat_orig = get_mes_fatura(t.data, t.cartao.dia_fechamento, t.cartao.dia_vencimento)
                if a_fat_orig < a_alvo or (a_fat_orig == a_alvo and m_fat_orig <= m_alvo):
                    if m_alvo == mes_selecionado and a_alvo == ano_selecionado:
                        t.data_exibicao = date(a_alvo, m_alvo, min(t.data.day, ult_dia))
                    itens.append(t)
            else:
                if t.data.year < a_alvo or (t.data.year == a_alvo and t.data.month <= m_alvo):
                    if m_alvo == mes_selecionado and a_alvo == ano_selecionado:
                        t.data_exibicao = date(a_alvo, m_alvo, min(t.data.day, ult_dia))
                    itens.append(t)
        return itens

    lista_transacoes_mes = obter_transacoes_competencia(mes_selecionado, ano_selecionado)
    lista_transacoes_mes.sort(key=lambda x: getattr(x, 'data_exibicao', x.data), reverse=True)

    nomes_meses = ['', 'Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
    labels_evolucao = []
    valores_evolucao = []
    for offset in range(5, -1, -1):
        m_hist = mes_selecionado - offset
        a_hist = ano_selecionado
        while m_hist <= 0:
            m_hist += 12
            a_hist -= 1
        itens_h = obter_transacoes_competencia(m_hist, a_hist)
        total_pessoal_h = sum(float(t.valor) for t in itens_h if eu and t.dono_id == eu.id and t.tipo == 'DESPESA')
        labels_evolucao.append(f"{nomes_meses[m_hist]}/{str(a_hist)[2:]}")
        valores_evolucao.append(round(total_pessoal_h, 2))

    gastos_mes_anterior = valores_evolucao[-2] if len(valores_evolucao) >= 2 else 0.0

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

            texto_zap = f"Olá {familiar.nome}, segue o resumo de {mes_selecionado:02d}/{ano_selecionado}:\n\n"
            if despesas_pendentes:
                texto_zap += "*Lançamentos:*\n"
                for desp in despesas_pendentes:
                    texto_zap += f"- {desp['descricao']}: R$ {desp['saldo']:.2f}".replace('.', ',') + (" (Restante)\n" if desp['saldo'] < desp['valor_original'] else "\n")
                texto_zap += "\n"
            if sum(float(t.valor) for t in pagamentos_fam) > 0:
                texto_zap += f"*Valor já abatido:* R$ {sum(float(t.valor) for t in pagamentos_fam):.2f}\n".replace('.', ',')
            texto_zap += f"*Total pendente: R$ {saldo_pessoa:.2f}*\n".replace('.', ',')

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

    resumo_cartoes, melhor_cartao_hoje = analisar_inteligencia_cartoes(todos_cartoes, lista_transacoes_mes)
    analise_ia = gerar_insights_inteligentes(
        meus_gastos, gastos_mes_anterior, valor_meta, gastos_pessoais_dict, lista_transacoes_mes, eu, mes_selecionado, ano_selecionado
    )

    contexto = {
        'transacoes': lista_transacoes_mes,
        'pessoas': todas_pessoas,
        'cartoes': todos_cartoes,
        'resumo_cartoes': resumo_cartoes,
        'melhor_cartao_hoje': melhor_cartao_hoje,
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
        'labels_evolucao': labels_evolucao,
        'valores_evolucao': valores_evolucao,
        'maior_categoria': maior_categoria,
        'valor_maior_cat': valor_maior_cat,
        'percentual_gasto': percentual_gasto,
        'percentual_barra': min(100, int(percentual_gasto)),
        'analise_ia': analise_ia,
        'usuario': request.user
    }
    return render(request, 'gestao/dashboard.html', contexto)


# ==========================================
# ROTAS DE EXCLUSÃO E CONFORMIDADE LGPD
# ==========================================

@login_required(login_url='login')
def excluir_transacao(request, id):
    transacao = get_object_or_404(Transacao, id=id, conta__iexact=request.user.username)
    nome_desc = transacao.descricao
    transacao.delete()
    messages.success(request, f"Lançamento '{nome_desc}' removido.")
    mes = request.POST.get('mes') or request.GET.get('mes')
    ano = request.POST.get('ano') or request.GET.get('ano')
    return redirect(f"/?mes={mes}&ano={ano}") if (mes and ano) else redirect('dashboard')


@login_required(login_url='login')
@require_POST
def excluir_cartao(request, id):
    cartao = get_object_or_404(Cartao, id=id, conta__iexact=request.user.username)
    nome = cartao.nome
    cartao.delete()
    messages.success(request, f"Cartão '{nome}' removido.")
    return redirect('dashboard')


@login_required(login_url='login')
@require_POST
def limpar_dados_financeiros(request):
    """Remove transações do ciclo selecionado (considerando fechamento de cartões) ou de toda a conta."""
    minha_conta = request.user.username
    escopo = request.POST.get('escopo', 'tudo')
    mes = request.POST.get('mes')
    ano = request.POST.get('ano')

    with transaction.atomic():
        if escopo == 'mes' and mes and ano and str(mes).isdigit() and str(ano).isdigit():
            m_alvo, a_alvo = int(mes), int(ano)
            ids_para_apagar = []
            for t in Transacao.objects.filter(conta__iexact=minha_conta, recorrente=False).select_related('cartao'):
                if t.cartao:
                    m_fat, a_fat = get_mes_fatura(t.data, t.cartao.dia_fechamento, t.cartao.dia_vencimento)
                    if m_fat == m_alvo and a_fat == a_alvo:
                        ids_para_apagar.append(t.id)
                elif t.data.month == m_alvo and t.data.year == a_alvo:
                    ids_para_apagar.append(t.id)

            apagados, _ = Transacao.objects.filter(id__in=ids_para_apagar).delete()
            messages.success(request, f"{apagados} lançamentos da competência {m_alvo:02d}/{a_alvo} foram removidos.")
            return redirect(f"/?mes={m_alvo}&ano={a_alvo}")
        else:
            apagados, _ = Transacao.objects.filter(conta__iexact=minha_conta).delete()
            if request.POST.get('zerar_cartoes') == '1':
                Cartao.objects.filter(conta__iexact=minha_conta).delete()
                Pessoa.objects.filter(conta__iexact=minha_conta, is_titular=False).delete()
                MetaMensal.objects.filter(conta__iexact=minha_conta).delete()
            messages.success(request, f"Histórico financeiro ({apagados} registros) reiniciado.")
            return redirect('dashboard')


@login_required(login_url='login')
def exportar_relatorio_csv(request):
    minha_conta = request.user.username
    mes = request.GET.get('mes')
    ano = request.GET.get('ano')

    qs = Transacao.objects.filter(conta__iexact=minha_conta).select_related('dono', 'cartao').order_by('-data')
    if mes and ano and str(mes).isdigit() and str(ano).isdigit():
        qs = qs.filter(data__month=int(mes), data__year=int(ano))

    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    sufixo = f"{mes}_{ano}" if (mes and ano) else "completo"
    response['Content-Disposition'] = f'attachment; filename="finpro_relatorio_{sufixo}.csv"'

    writer = csv.writer(response, delimiter=';')
    writer.writerow(['Data', 'Descricao', 'Categoria', 'Tipo', 'Valor (R$)', 'Responsavel', 'Cartao/Forma', 'Recorrente'])

    for t in qs:
        writer.writerow([
            t.data.strftime('%d/%m/%Y'),
            sanitizar_texto_csv(t.descricao),
            t.categoria or 'Outros',
            t.tipo,
            f"{float(t.valor):.2f}".replace('.', ','),
            t.dono.nome if t.dono else '',
            t.cartao.nome if t.cartao else 'Pix/Dinheiro',
            'Sim' if t.recorrente else 'Nao'
        ])

    return response


@login_required(login_url='login')
def exportar_dados_lgpd(request):
    minha_conta = request.user.username
    dados = {
        'titular_conta': {
            'username': request.user.username,
            'data_criacao': request.user.date_joined.isoformat() if request.user.date_joined else None,
            'ultimo_login': request.user.last_login.isoformat() if request.user.last_login else None,
            'exportado_em': datetime.now().isoformat(),
            'base_legal_lgpd': 'Lei 13.709/2018 - Art. 18 (Portabilidade de Dados Pessoais)',
        },
        'meta_mensal': [
            {'valor': float(m.valor)} for m in MetaMensal.objects.filter(conta__iexact=minha_conta)
        ],
        'pessoas_vinculadas': [
            {'nome': p.nome, 'telefone': p.telefone, 'is_titular': p.is_titular}
            for p in Pessoa.objects.filter(conta__iexact=minha_conta)
        ],
        'cartoes': [
            {'nome': c.nome, 'dia_fechamento': c.dia_fechamento, 'dia_vencimento': c.dia_vencimento}
            for c in Cartao.objects.filter(conta__iexact=minha_conta)
        ],
        'transacoes': [
            {
                'data': t.data.isoformat(),
                'descricao': t.descricao,
                'categoria': t.categoria,
                'valor': float(t.valor),
                'tipo': t.tipo,
                'dono': t.dono.nome if t.dono else None,
                'cartao': t.cartao.nome if t.cartao else 'Pix/Dinheiro',
                'recorrente': t.recorrente,
            }
            for t in Transacao.objects.filter(conta__iexact=minha_conta).select_related('dono', 'cartao')
        ]
    }
    response = HttpResponse(
        json.dumps(dados, ensure_ascii=False, indent=2),
        content_type='application/json; charset=utf-8'
    )
    response['Content-Disposition'] = f'attachment; filename="finpro_portabilidade_lgpd_{minha_conta}.json"'
    return response


@login_required(login_url='login')
@require_POST
def excluir_conta_lgpd(request):
    senha_confirmacao = request.POST.get('senha_confirmacao', '')
    confirmacao_texto = request.POST.get('confirmacao_texto', '').strip().upper()
    usuario = request.user

    senha_valida = (
        usuario.check_password(senha_confirmacao)
        or usuario.check_password(senha_confirmacao.strip())
        or confirmacao_texto == 'EXCLUIR'
    )

    if not senha_valida:
        messages.error(request, 'Confirmação inválida. Digite EXCLUIR ou informe sua senha atual.')
        return redirect('dashboard')

    minha_conta = usuario.username
    with transaction.atomic():
        Transacao.objects.filter(conta__iexact=minha_conta).delete()
        Cartao.objects.filter(conta__iexact=minha_conta).delete()
        Pessoa.objects.filter(conta__iexact=minha_conta).delete()
        MetaMensal.objects.filter(conta__iexact=minha_conta).delete()
        logout(request)
        usuario.delete()

    return redirect('login')