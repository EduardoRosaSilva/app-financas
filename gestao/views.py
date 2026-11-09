import calendar
import csv
import io
import json
import re
import unicodedata
import urllib.parse
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

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
# UTILITÁRIOS DE SEGURANÇA E CONVERSÃO
# ==========================================

def get_client_ip(request) -> str:
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '127.0.0.1')


def verificar_bloqueio_ip(request, escopo='login', max_tentativas=6, tempo_bloqueio=300):
    """Proteção nativa contra Brute Force usando o cache em memória do Django."""
    ip = get_client_ip(request)
    chave = f"finpro_ratelimit_{escopo}_{ip}"
    tentativas = cache.get(chave, 0)
    return tentativas >= max_tentativas, chave, tentativas, tempo_bloqueio


def sanitizar_texto_csv(texto: str) -> str:
    """Previne ataques de CSV Injection / Formula Injection e XSS."""
    if not texto:
        return ""
    limpo = str(texto).strip()
    if limpo and limpo[0] in ('=', '+', '-', '@', '\t', '\r'):
        limpo = "'" + limpo
    return limpo[:200]


def normalizar_chave_coluna(chave: str) -> str:
    """Remove acentos e caracteres especiais dos nomes de colunas do CSV."""
    if not chave:
        return ""
    sem_acento = ''.join(
        c for c in unicodedata.normalize('NFD', str(chave).strip().lower())
        if unicodedata.category(c) != 'Mn'
    )
    return sem_acento


def parse_moeda(valor_raw) -> float:
    """Converte strings monetárias de qualquer banco ('R$ 1.250,50', '-45.90', '50,00')."""
    if valor_raw is None or valor_raw == '':
        return 0.0
    s = str(valor_raw).replace('R$', '').replace('r$', '').replace(' ', '').strip()
    if not s:
        return 0.0
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


def parse_data_flexivel(data_raw: str):
    """Reconhece formatos de data de todos os bancos brasileiros (YYYY-MM-DD, DD/MM/YYYY, DD/MM/YY, DD-MM-YYYY)."""
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


def garantir_titular_conta(username: str) -> Pessoa:
    """Garante que toda conta possua uma Pessoa titular."""
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
    """Classificador Naive Bayes com Laplace Smoothing + Regras de Fallback."""
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
    if not palavras_alvo or not vocabulario:
        return regra_direta

    # Se nenhuma palavra da descrição nova apareceu no histórico, usa o fallback inteligente
    if not any(p in vocabulario for p in palavras_alvo):
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


def gerar_insights_inteligentes(meus_gastos, valor_meta, gastos_por_cat, transacoes_mes, mes_sel, ano_sel):
    """Gera diagnósticos inteligentes (Burn Rate, Projeção e Alertas) para o usuário."""
    hoje = date.today()
    dias_no_mes = calendar.monthrange(ano_sel, mes_sel)[1]
    if ano_sel == hoje.year and mes_sel == hoje.month:
        dias_decorridos = max(1, hoje.day)
    elif (ano_sel, mes_sel) < (hoje.year, hoje.month):
        dias_decorridos = dias_no_mes
    else:
        dias_decorridos = 1

    media_diaria = meus_gastos / dias_decorridos if meus_gastos > 0 else 0.0
    projecao_mes = media_diaria * dias_no_mes if (ano_sel == hoje.year and mes_sel == hoje.month) else meus_gastos

    gastos_fixos = sum(float(t.valor) for t in transacoes_mes if t.recorrente and t.tipo == 'DESPESA')
    perc_fixos = (gastos_fixos / meus_gastos * 100) if meus_gastos > 0 else 0.0

    insights = []
    if valor_meta > 0:
        if meus_gastos > valor_meta:
            excesso = meus_gastos - valor_meta
            insights.append({
                'tipo': 'danger',
                'icone': 'bi-exclamation-triangle-fill',
                'texto': f"Meta ultrapassada em R$ {excesso:.2f}. Evite novos gastos não essenciais neste ciclo."
            })
        elif projecao_mes > valor_meta and ano_sel == hoje.year and mes_sel == hoje.month:
            limite_diario_restante = max(0.0, (valor_meta - meus_gastos) / max(1, dias_no_mes - hoje.day))
            insights.append({
                'tipo': 'warning',
                'icone': 'bi-graph-up-arrow',
                'texto': f"Atenção ao ritmo: mantendo R$ {media_diaria:.2f}/dia, a projeção é fechar o mês em R$ {projecao_mes:.2f}. Para cumprir a meta, limite-se a R$ {limite_diario_restante:.2f}/dia."
            })
        else:
            economia_proj = max(0.0, valor_meta - projecao_mes)
            insights.append({
                'tipo': 'success',
                'icone': 'bi-shield-check',
                'texto': f"Ritmo saudável! Seu gasto médio é de R$ {media_diaria:.2f}/dia, com potencial de economizar R$ {economia_proj:.2f} na meta."
            })

    if gastos_por_cat:
        cat_vila = max(gastos_por_cat, key=gastos_por_cat.get)
        val_vila = gastos_por_cat[cat_vila]
        peso_vila = (val_vila / meus_gastos * 100) if meus_gastos > 0 else 0
        if peso_vila >= 35:
            insights.append({
                'tipo': 'info',
                'icone': 'bi-lightbulb-fill',
                'texto': f"Concentração detectada: '{cat_vila}' representa {peso_vila:.0f}% (R$ {val_vila:.2f}) das suas despesas pessoais neste mês."
            })

    return {
        'media_diaria': media_diaria,
        'projecao_mes': projecao_mes,
        'gastos_fixos': gastos_fixos,
        'perc_fixos': perc_fixos,
        'lista_insights': insights,
    }


# ==========================================
# VIEWS DE AUTENTICAÇÃO COM PROTEÇÃO BRUTE FORCE
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
            messages.error(request, 'Muitas tentativas de cadastro. Aguarde alguns minutos.')
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
            messages.error(request, 'Segurança: Muitas tentativas falhas. Aguarde 5 minutos antes de tentar novamente.')
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
# DASHBOARD E IMPORTADOR INTELIGENTE DE FATURA
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

        # 1. IMPORTAÇÃO UNIVERSAL INTELIGENTE DE FATURA CSV
        if tipo_formulario == 'upload_fatura':
            arquivo_csv = request.FILES.get('arquivo_csv')
            if not arquivo_csv:
                messages.error(request, 'Selecione um arquivo CSV válido.')
                return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

            if arquivo_csv.size > 5 * 1024 * 1024:
                messages.error(request, 'Arquivo muito grande. O limite de segurança é 5MB.')
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
                messages.error(request, 'Não foi possível ler a codificação do arquivo CSV.')
                return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

            linhas_nao_vazias = [ln for ln in decoded_file.splitlines() if ln.strip()]
            if not linhas_nao_vazias:
                messages.error(request, 'O arquivo CSV enviado está vazio.')
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

                # Fallback por posição caso o CSV tenha nomes de colunas fora do padrão
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

                # Ignora linhas de pagamento da própria fatura para não somar como despesa
                titulo_lower = titulo.lower()
                if any(ign in titulo_lower for ign in ('pagamento recebido', 'pagamento de fatura', 'pagamento da fatura', 'inclusao de pagamento')):
                    continue

                valor = parse_moeda(valor_str)
                # Em extratos onde despesas vêm negativas (ex: -45.90), converte para positivo
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
                    'Nenhum gasto válido foi encontrado neste CSV. Verifique se o arquivo possui colunas de Data, Descrição e Valor.'
                )
                return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

            contexto_revisao = {
                'transacoes_fatura': transacoes_fatura,
                'total_fatura_importada': sum(t['valor'] for t in transacoes_fatura if not t['duplicada']),
                'pessoas': Pessoa.objects.filter(conta__iexact=minha_conta),
                'cartoes': Cartao.objects.filter(conta__iexact=minha_conta),
            }
            return render(request, 'gestao/revisao_fatura.html', contexto_revisao)

        # 2. SALVAR ITENS IMPORTADOS EM LOTE (ATÔMICO E COM SUPORTE A CRIAÇÃO DE CARTÃO)
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
                    desc = sanitizar_texto_csv(request.POST.get(f'desc_{t_id}', 'Gasto Importado'))
                    valor = parse_moeda(request.POST.get(f'valor_{t_id}', '0'))
                    if valor <= 0:
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
                        valor=valor,
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

            messages.success(request, f"{len(novas_transacoes)} transações importadas com sucesso para {ultimo_mes:02d}/{ultimo_ano}!")
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

            if parcelas > 1:
                valor_final_parcela = round(valor_digitado / parcelas, 2)
                valores_finais_extras = [round(parse_moeda(v) / parcelas, 2) for v in valores_extras_str]
            else:
                valor_final_parcela = valor_digitado
                valores_finais_extras = [parse_moeda(v) for v in valores_extras_str]

            with transaction.atomic():
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
            valores_extras = [parse_moeda(v) for v in valores_extras_str]

            if parcelas > 1:
                valor_parcela = round(valor / parcelas, 2)
                valores_extras = [round(v / parcelas, 2) for v in valores_extras]
            else:
                valor_parcela = valor

            soma_extras = sum(valores_extras)
            valor_principal = round(valor_parcela - soma_extras, 2) if (0 < soma_extras < valor_parcela) else valor_parcela

            data_base = parse_data_flexivel(data_val) or transacao.data
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
            messages.success(request, "Meta mensal atualizada!")

        elif tipo_formulario == 'nova_pessoa':
            Pessoa.objects.create(
                nome=sanitizar_texto_csv(request.POST.get('nome', '')),
                telefone=re.sub(r'[^\d+]', '', request.POST.get('telefone', '')),
                is_titular=not Pessoa.objects.filter(conta__iexact=minha_conta, is_titular=True).exists(),
                conta=minha_conta
            )
            messages.success(request, "Pessoa adicionada com sucesso!")

        elif tipo_formulario == 'editar_pessoa':
            p_id = request.POST.get('pessoa_id')
            p = get_object_or_404(Pessoa, id=p_id, conta__iexact=minha_conta)
            p.nome = sanitizar_texto_csv(request.POST.get('nome', ''))
            p.telefone = re.sub(r'[^\d+]', '', request.POST.get('telefone', ''))
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
                nome=sanitizar_texto_csv(request.POST.get('nome', '')),
                dia_fechamento=max(1, min(31, int(request.POST.get('dia_fechamento', 25) or 25))),
                dia_vencimento=max(1, min(31, int(request.POST.get('dia_vencimento', 5) or 5))),
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
                data=parse_data_flexivel(request.POST.get('data')) or hoje,
                conta=minha_conta,
                recorrente=False
            )
            messages.success(request, "Pagamento registrado com sucesso!")

        return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

    # ==========================================
    # CÁLCULOS E CONSULTAS OTIMIZADAS (COM SUPORTE A FECHAMENTO DE CARTÃO)
    # ==========================================
    todas_pessoas = Pessoa.objects.filter(conta__iexact=minha_conta)
    todos_cartoes = Cartao.objects.filter(conta__iexact=minha_conta)
    filtra_pessoa_id = request.GET.get('pessoa')

    mes_anterior = mes_selecionado - 1 if mes_selecionado > 1 else 12
    ano_anterior = ano_selecionado if mes_selecionado > 1 else ano_selecionado - 1
    mes_proximo = mes_selecionado + 1 if mes_selecionado < 12 else 1
    ano_proximo = ano_selecionado if mes_selecionado < 12 else ano_selecionado + 1

    ultimo_dia_mes = calendar.monthrange(ano_selecionado, mes_selecionado)[1]

    # Janela SQL de 62 dias (mês anterior + mês atual) para capturar compras feitas após o fechamento do mês anterior
    data_inicio_janela = date(ano_anterior, mes_anterior, 1)
    data_fim_janela = date(ano_selecionado, mes_selecionado, ultimo_dia_mes)

    trans_normais = Transacao.objects.filter(
        conta__iexact=minha_conta,
        recorrente=False,
        data__gte=data_inicio_janela,
        data__lte=data_fim_janela
    ).select_related('cartao', 'dono')

    trans_rec = Transacao.objects.filter(
        conta__iexact=minha_conta,
        recorrente=True,
        data__lte=data_fim_janela
    ).select_related('cartao', 'dono')

    lista_transacoes_mes = []

    for t in trans_normais:
        if t.cartao:
            m_fat, a_fat = get_mes_fatura(t.data, t.cartao.dia_fechamento, t.cartao.dia_vencimento)
            if m_fat == mes_selecionado and a_fat == ano_selecionado:
                lista_transacoes_mes.append(t)
        else:
            if t.data.month == mes_selecionado and t.data.year == ano_selecionado:
                lista_transacoes_mes.append(t)

    for t in trans_rec:
        if t.cartao:
            m_fat_orig, a_fat_orig = get_mes_fatura(t.data, t.cartao.dia_fechamento, t.cartao.dia_vencimento)
            if a_fat_orig < ano_selecionado or (a_fat_orig == ano_selecionado and m_fat_orig <= mes_selecionado):
                dia_valido = min(t.data.day, ultimo_dia_mes)
                t.data_exibicao = date(ano_selecionado, mes_selecionado, dia_valido)
                lista_transacoes_mes.append(t)
        else:
            if t.data.year < ano_selecionado or (t.data.year == ano_selecionado and t.data.month <= mes_selecionado):
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

    analise_ia = gerar_insights_inteligentes(
        meus_gastos, valor_meta, gastos_pessoais_dict, lista_transacoes_mes, mes_selecionado, ano_selecionado
    )

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
        'percentual_barra': min(100, int(percentual_gasto)),
        'analise_ia': analise_ia,
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


# ==========================================
# ROTAS DE CONFORMIDADE LGPD (ART. 18) E RELATÓRIOS
# ==========================================

@login_required(login_url='login')
def exportar_relatorio_csv(request):
    """Exporta as transações do usuário em CSV seguro (UTF-8 com BOM para abrir direto no Excel)."""
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
    """Direito de Portabilidade e Acesso (Art. 18, II e V da LGPD): exporta todos os dados pessoais em JSON."""
    minha_conta = request.user.username
    dados = {
        'titular_conta': {
            'username': request.user.username,
            'data_criacao': request.user.date_joined.isoformat() if request.user.date_joined else None,
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
    """Direito à Eliminação / Esquecimento (Art. 18, VI da LGPD): apaga todos os dados e o usuário."""
    senha_confirmacao = request.POST.get('senha_confirmacao', '')
    usuario = request.user

    if not usuario.check_password(senha_confirmacao):
        messages.error(request, 'Senha incorreta. Por segurança, a exclusão da conta foi cancelada.')
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