import calendar
import csv
import hashlib
import io
import itertools
import json
import logging
import os
import re
import unicodedata
import urllib.parse
import resend
import uuid
from django.contrib.sessions.models import Session
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.sites import site as admin_site
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .forms import LoginUsuarioForm, RegistroUsuarioForm, VincularEmailForm
from .models import Cartao, MetaMensal, Pessoa, Transacao
from .emails import (
    construir_email_alerta_senha_alterada,
    construir_email_confirmacao,
    construir_email_redefinicao_senha,
)

security_logger = logging.getLogger('finpro.security')

CATEGORIAS_PERMITIDAS = {
    'Alimentação', 'Mercado', 'Transporte', 'Moradia',
    'Saúde', 'Lazer', 'Compras', 'Assinaturas', 'Outros'
}
TIPOS_PERMITIDOS = {'DESPESA', 'PAGAMENTO'}
EMAIL_CONFIRM_SALT = 'finpro-email-confirmation-v1'


# ==========================================
# UTILITÁRIOS DE VERIFICAÇÃO DE E-MAIL E ENVIO SEGURO
# ==========================================

def _assinatura_email_verificado(email: str) -> str:
    limpo = (email or '').strip().lower()
    if not limpo:
        return ''
    digest = hashlib.sha256(limpo.encode('utf-8')).hexdigest()[:32]
    return f"VERIFIED:{digest}"


def is_email_confirmado(user: User) -> bool:
    if not user or not user.is_authenticated or not user.email:
        return False
    return user.last_name == _assinatura_email_verificado(user.email)


def marcar_email_como_confirmado(user: User, email: str):
    email_limpo = email.strip().lower()
    user.email = email_limpo
    user.last_name = _assinatura_email_verificado(email_limpo)
    user.save(update_fields=['email', 'last_name'])


def gerar_token_confirmacao_email(user: User, email: str) -> str:
    payload = {'uid': user.pk, 'u': user.username, 'email': email.strip().lower()}
    return signing.dumps(payload, salt=EMAIL_CONFIRM_SALT)

def _obter_logo_email_url(request) -> str:
    return request.build_absolute_uri('/static/icone.png')


def _formatar_data_hora_seguranca() -> str:
    return timezone.localtime(timezone.now()).strftime('%d/%m/%Y às %H:%M (Brasília)')


def encerrar_todas_sessoes_usuario(user_id: int):
    """Invalida todas as sessões ativas do usuário em outros dispositivos após troca de senha."""
    alvo = str(user_id)
    for sessao in Session.objects.filter(expire_date__gte=timezone.now()).iterator():
        try:
            dados = sessao.get_decoded()
            if str(dados.get('_auth_user_id', '')) == alvo:
                sessao.delete()
        except Exception:
            continue


def enviar_email_seguro(
    assunto: str,
    mensagem: str,
    destinatario: str,
    mensagem_html: str | None = None,
) -> bool:
    resend_api_key = os.environ.get('RESEND_API_KEY', '').strip()
    remetente = settings.DEFAULT_FROM_EMAIL
    destinatario_limpo = destinatario.strip()

    if resend_api_key and not getattr(settings, 'EM_TESTE', False):
        try:
            resend.api_key = resend_api_key
            params = {
                "from": remetente,
                "to": [destinatario_limpo],
                "subject": assunto,
                "text": mensagem,
                "headers": {"X-Entity-Ref-ID": str(uuid.uuid4())},
            }
            if mensagem_html:
                params["html"] = mensagem_html

            resposta = resend.Emails.send(params)
            security_logger.info("E-mail enviado via Resend para %s (ID: %s)", destinatario_limpo, resposta)
            return True
        except Exception as exc:
            security_logger.warning("Falha ao enviar e-mail via Resend API para %s: %s", destinatario_limpo, str(exc))
            return False

    try:
        email_msg = EmailMultiAlternatives(
            subject=assunto,
            body=mensagem,
            from_email=remetente,
            to=[destinatario_limpo],
            headers={"X-Entity-Ref-ID": str(uuid.uuid4())},
        )
        if mensagem_html:
            email_msg.attach_alternative(mensagem_html, "text/html")
        email_msg.send(fail_silently=False)
        return True
    except Exception as exc:
        security_logger.warning("Falha ao enviar e-mail (fallback) para %s: %s", destinatario_limpo, type(exc).__name__)
        return False


def disparar_email_confirmacao(request, user: User, email_destino: str) -> bool:
    token = gerar_token_confirmacao_email(user, email_destino)
    link = request.build_absolute_uri(reverse('confirmar_email', args=[token]))
    assunto, corpo_texto, corpo_html = construir_email_confirmacao(
        username=user.username,
        link_confirmacao=link,
        logo_url=_obter_logo_email_url(request),
    )
    return enviar_email_seguro(assunto, corpo_texto, email_destino, mensagem_html=corpo_html)


# ==========================================
# INFRAESTRUTURA, SEO E PÁGINAS INSTITUCIONAIS
# ==========================================

@never_cache
def health_check(request):
    return JsonResponse({
        'status': 'ok',
        'service': 'finpro-web',
        'timestamp': datetime.now().isoformat()
    })


def robots_txt_view(request):
    base_url = request.build_absolute_uri('/').rstrip('/')
    linhas = [
        "User-agent: *",
        "Allow: /login/",
        "Allow: /registro/",
        "Allow: /privacidade/",
        "Allow: /termos/",
        "Disallow: /lgpd/",
        "Disallow: /exportar-csv/",
        f"Sitemap: {base_url}/sitemap.xml",
    ]
    return HttpResponse("\n".join(linhas), content_type="text/plain; charset=utf-8")


def sitemap_xml_view(request):
    base_url = request.build_absolute_uri('/').rstrip('/')
    hoje_iso = date.today().isoformat()
    rotas_publicas = [
        reverse('login'),
        reverse('registro'),
        reverse('politica_privacidade'),
        reverse('termos_uso'),
    ]
    xml_items = "".join([
        f"<url><loc>{base_url}{rota}</loc><lastmod>{hoje_iso}</lastmod><changefreq>monthly</changefreq><priority>0.8</priority></url>"
        for rota in rotas_publicas
    ])
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{xml_items}</urlset>'
    return HttpResponse(xml, content_type="application/xml; charset=utf-8")


def politica_privacidade_view(request):
    return render(request, 'gestao/privacidade.html')


def termos_uso_view(request):
    return render(request, 'gestao/termos.html')


@login_required(login_url='login')
def pagina_obrigado_view(request):
    return render(request, 'gestao/obrigado.html')


def erro_404_personalizado(request, exception=None):
    return render(request, 'gestao/404.html', status=404)


def erro_500_personalizado(request):
    return render(request, '500.html', status=500)


# ==========================================
# SEGURANÇA, RATE LIMIT E HIGIENIZAÇÃO DE INPUT
# ==========================================

def get_client_ip(request) -> str:
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        ips = [ip.strip() for ip in x_forwarded.split(',') if ip.strip()]
        if ips:
            return ips[-1]
    return request.META.get('REMOTE_ADDR', '127.0.0.1')


def verificar_bloqueio_ip(request, escopo='login', identificador_extra='', max_tentativas=6, tempo_bloqueio=300):
    ip = get_client_ip(request)
    sufixo = re.sub(r'[^a-zA-Z0-9_-]', '', str(identificador_extra).lower())[:40]
    chave_ip = f"finpro_ratelimit_{escopo}_{ip}"
    chave_usr = f"finpro_ratelimit_{escopo}_usr_{sufixo}" if sufixo else None
    try:
        tentativas_ip = cache.get(chave_ip, 0)
        tentativas_usr = cache.get(chave_usr, 0) if chave_usr else 0
    except Exception:
        tentativas_ip, tentativas_usr = 0, 0

    bloqueado = (tentativas_ip >= max_tentativas) or (tentativas_usr >= max_tentativas)
    return bloqueado, chave_ip, chave_usr, tempo_bloqueio


def registrar_falha_ip(chave_ip: str, tempo_bloqueio=300, chave_usr: str = None):
    for ch in (chave_ip, chave_usr):
        if ch:
            try:
                if not cache.add(ch, 1, tempo_bloqueio):
                    cache.incr(ch)
            except Exception:
                pass


def sanitizar_texto_csv(texto: str) -> str:
    if not texto:
        return ""
    limpo = str(texto).strip().replace('—', '-')
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


def parse_int_seguro(valor_raw, padrao=1, minimo=1, maximo=120) -> int:
    try:
        val = int(str(valor_raw).strip())
        return max(minimo, min(maximo, val))
    except (ValueError, TypeError):
        return padrao


def validar_categoria(cat_raw: str) -> str:
    cat = str(cat_raw or 'Outros').strip()
    return cat if cat in CATEGORIAS_PERMITIDAS else 'Outros'


def validar_tipo_transacao(tipo_raw: str) -> str:
    tipo = str(tipo_raw or 'DESPESA').strip().upper()
    return tipo if tipo in TIPOS_PERMITIDOS else 'DESPESA'


# ==========================================
# REGRAS DE NEGÓCIO CONTÁBEIS, DATAS DE CARTÃO E PARSERS
# ==========================================

def parse_moeda_decimal(valor_raw) -> Decimal:
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
        val = Decimal(s).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if val > Decimal('9999999.99'):
            return Decimal('9999999.99')
        return val
    except (InvalidOperation, ValueError):
        return Decimal('0.00')


def parse_moeda(valor_raw) -> float:
    return float(parse_moeda_decimal(valor_raw))


def dividir_em_parcelas_exatas(valor_total: float, qtd_parcelas: int) -> list[Decimal]:
    qtd = max(1, int(qtd_parcelas))
    total_dec = max(Decimal('0.00'), parse_moeda_decimal(valor_total))
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
    """Retorna (mes, ano) da competência da fatura conforme a data da compra e o dia de fechamento."""
    mes = data_compra.month
    ano = data_compra.year
    if data_compra.day >= dia_fechamento:
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1
    return mes, ano


def calcular_datas_ciclo_cartao(mes_competencia: int, ano_competencia: int, dia_fechamento: int, dia_vencimento: int):
    """
    Calcula a data exata de fechamento e a data exata de vencimento para uma fatura.
    Se o dia de vencimento for menor que o dia de fechamento (ex.: fecha dia 25 e vence dia 5),
    o vencimento efetivo ocorre no mês seguinte ao fechamento.
    """
    ult_dia_fech = calendar.monthrange(ano_competencia, mes_competencia)[1]
    data_fechamento = date(ano_competencia, mes_competencia, min(max(1, dia_fechamento), ult_dia_fech))

    mes_venc = mes_competencia
    ano_venc = ano_competencia
    if dia_vencimento < dia_fechamento:
        mes_venc += 1
        if mes_venc > 12:
            mes_venc = 1
            ano_venc += 1

    ult_dia_venc = calendar.monthrange(ano_venc, mes_venc)[1]
    data_vencimento = date(ano_venc, mes_venc, min(max(1, dia_vencimento), ult_dia_venc))
    return data_fechamento, data_vencimento


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


def analisar_inteligencia_cartoes(cartoes, transacoes_mes, mes_sel=None, ano_sel=None):
    """
    Analisa o ciclo de cada cartão considerando data da compra, fechamento, vencimento
    e abatimentos de fatura realizados no período.
    """
    hoje = date.today()
    m_ref = mes_sel or hoje.month
    a_ref = ano_sel or hoje.year
    resumo = []
    melhor_cartao = None
    maior_prazo_pagamento = -9999

    for c in cartoes:
        despesas_cartao = sum(float(t.valor) for t in transacoes_mes if t.cartao_id == c.id and t.tipo == 'DESPESA')
        pagamentos_cartao = sum(float(t.valor) for t in transacoes_mes if t.cartao_id == c.id and t.tipo == 'PAGAMENTO')
        saldo_fatura = max(0.0, despesas_cartao - pagamentos_cartao)

        dt_fech_mes, dt_venc_mes = calcular_datas_ciclo_cartao(m_ref, a_ref, c.dia_fechamento, c.dia_vencimento)

        # Simula uma compra feita HOJE neste cartão para descobrir quando ela vence de verdade
        m_comp_hoje, a_comp_hoje = get_mes_fatura(hoje, c.dia_fechamento, c.dia_vencimento)
        dt_fech_compra_hoje, dt_venc_compra_hoje = calcular_datas_ciclo_cartao(
            m_comp_hoje, a_comp_hoje, c.dia_fechamento, c.dia_vencimento
        )
        dias_prazo_compra_hoje = (dt_venc_compra_hoje - hoje).days
        dias_para_fechar = (dt_fech_compra_hoje - hoje).days
        ja_fechou_este_mes = hoje.day >= c.dia_fechamento

        if saldo_fatura <= 0.01 and despesas_cartao > 0:
            status_ciclo = f"Fatura quitada (vencia {dt_venc_mes.strftime('%d/%m')})"
            cor_badge = "success"
        elif ja_fechou_este_mes:
            status_ciclo = f"Melhor dia de compra! Compras hoje vencem só em {dt_venc_compra_hoje.strftime('%d/%m')} ({dias_prazo_compra_hoje} dias)"
            cor_badge = "success"
        else:
            if dias_para_fechar <= 3:
                status_ciclo = f"Atenção: fecha em {dias_para_fechar} dia(s) ({dt_fech_compra_hoje.strftime('%d/%m')}) • Vence {dt_venc_mes.strftime('%d/%m')}"
                cor_badge = "warning"
            else:
                status_ciclo = f"Aberta: fecha {dt_fech_compra_hoje.strftime('%d/%m')} • Vence {dt_venc_mes.strftime('%d/%m')} ({dias_prazo_compra_hoje}d de prazo)"
                cor_badge = "primary"

        item_cartao = {
            'id': c.id,
            'nome': c.nome,
            'dia_fechamento': c.dia_fechamento,
            'dia_vencimento': c.dia_vencimento,
            'total_fatura_bruto': despesas_cartao,
            'total_pago_cartao': pagamentos_cartao,
            'total_fatura': saldo_fatura,
            'data_fechamento_fmt': dt_fech_mes.strftime('%d/%m/%Y'),
            'data_vencimento_fmt': dt_venc_mes.strftime('%d/%m/%Y'),
            'vencimento_curto': dt_venc_mes.strftime('%d/%m'),
            'fatura_compra_hoje': f"{m_comp_hoje:02d}/{a_comp_hoje}",
            'vencimento_compra_hoje': dt_venc_compra_hoje.strftime('%d/%m/%Y'),
            'dias_prazo_hoje': dias_prazo_compra_hoje,
            'status_ciclo': status_ciclo,
            'cor_badge': cor_badge,
            'ja_fechou': ja_fechou_este_mes,
            'dias_para_fechar': dias_para_fechar,
        }
        resumo.append(item_cartao)

        if dias_prazo_compra_hoje > maior_prazo_pagamento:
            maior_prazo_pagamento = dias_prazo_compra_hoje
            melhor_cartao = item_cartao

    return resumo, melhor_cartao


def calcular_indicadores_contabeis(
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

    if valor_meta <= 0:
        status_orcamento, cor_orcamento = "Meta não definida", "secondary"
    else:
        uso_meta = (meus_gastos / valor_meta) * 100
        if uso_meta <= 75:
            status_orcamento, cor_orcamento = "Dentro do orçamento", "success"
        elif uso_meta <= 100:
            status_orcamento, cor_orcamento = "Próximo ao limite", "warning"
        else:
            status_orcamento, cor_orcamento = "Orçamento excedido", "danger"

    orcamento_diario_seguro = max(0.0, (valor_meta - meus_gastos) / dias_restantes) if valor_meta > meus_gastos else 0.0

    insights = []
    if valor_meta > 0:
        if meus_gastos > valor_meta:
            excesso = meus_gastos - valor_meta
            insights.append({
                'tipo': 'danger',
                'icone': 'bi-exclamation-circle',
                'texto': f"Orçamento excedido em R$ {excesso:.2f} neste período."
            })
        elif projecao_mes > valor_meta and ano_sel == hoje.year and mes_sel == hoje.month:
            insights.append({
                'tipo': 'warning',
                'icone': 'bi-graph-up-arrow',
                'texto': f"Média de R$ {media_diaria:.2f}/dia projeta R$ {projecao_mes:.2f} no mês. Teto diário sugerido: R$ {orcamento_diario_seguro:.2f}."
            })
        else:
            economia_proj = max(0.0, valor_meta - projecao_mes)
            insights.append({
                'tipo': 'success',
                'icone': 'bi-check-circle',
                'texto': f"Margem diária disponível de R$ {orcamento_diario_seguro:.2f}/dia (economia estimada de R$ {economia_proj:.2f})."
            })

    if gastos_por_cat:
        cat_maior = max(gastos_por_cat, key=gastos_por_cat.get)
        val_maior = gastos_por_cat[cat_maior]
        peso_maior = (val_maior / meus_gastos * 100) if meus_gastos > 0 else 0
        if peso_maior >= 40:
            insights.append({
                'tipo': 'secondary',
                'icone': 'bi-pie-chart',
                'texto': f"A categoria '{cat_maior}' representa {peso_maior:.0f}% (R$ {val_maior:.2f}) das despesas pessoais."
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
        'nivel_score': status_orcamento,
        'cor_score': cor_orcamento,
        'lista_insights': insights,
    }


def _pagamento_corresponde_despesa(pagamento_desc: str, despesa_obj: Transacao) -> bool:
    """Verifica se uma transação de PAGAMENTO está vinculada a uma DESPESA específica (por ID ou descrição)."""
    if not pagamento_desc:
        return False
    if f"[REF:{despesa_obj.id}]" in pagamento_desc:
        return True
    if f"(Ref: {despesa_obj.descricao})" in pagamento_desc:
        return True
    return False


# ==========================================
# VIEWS DE AUTENTICAÇÃO, CONFIRMAÇÃO DE E-MAIL E SENHA
# ==========================================

def _obter_email_pendente_sessao(request) -> str:
    uid = request.session.get('reenvio_pendente_uid')
    if not uid:
        return ''
    usuario = User.objects.filter(pk=uid).first()
    if usuario and usuario.email and not is_email_confirmado(usuario):
        return usuario.email
    request.session.pop('reenvio_pendente_uid', None)
    return ''


@never_cache
def admin_login_protegido(request, extra_context=None):
    if request.method == 'POST':
        usr_alvo = request.POST.get('username', '')
        bloqueado, chave_ip, chave_usr, tempo_bloq = verificar_bloqueio_ip(
            request, escopo='admin_login', identificador_extra=usr_alvo, max_tentativas=5, tempo_bloqueio=600
        )
        if bloqueado:
            security_logger.warning("Bloqueio de rate limit no Admin para IP=%s usuario=%s", get_client_ip(request), usr_alvo)
            return HttpResponse("Muitas tentativas falhas. Acesso temporariamente bloqueado.", status=429)
        registrar_falha_ip(chave_ip, tempo_bloq, chave_usr)
    return admin_site.login(request, extra_context=extra_context)


@never_cache
def registro_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        if request.POST.get('website_url_honeypot'):
            security_logger.warning("Honeypot acionado no registro pelo IP=%s", get_client_ip(request))
            return redirect('login')

        usr_tentativa = request.POST.get('username', '')
        bloqueado, chave_ip, chave_usr, tempo_bloq = verificar_bloqueio_ip(
            request, escopo='registro', identificador_extra=usr_tentativa, max_tentativas=10
        )
        if bloqueado:
            security_logger.warning("Bloqueio de rate limit em registro para IP=%s", get_client_ip(request))
            messages.error(request, 'Muitas tentativas realizadas. Aguarde alguns minutos.')
            return render(request, 'gestao/register.html', {'form': RegistroUsuarioForm()}, status=429)

        form = RegistroUsuarioForm(request.POST)
        if form.is_valid():
            usuario = form.save()
            garantir_titular_conta(usuario.username)
            cache.delete(chave_ip)
            if chave_usr:
                cache.delete(chave_usr)

            request.session['reenvio_pendente_uid'] = usuario.pk
            enviado = disparar_email_confirmacao(request, usuario, usuario.email)
            if enviado:
                messages.success(
                    request,
                    f"Conta criada! Enviamos um link de confirmação para {usuario.email}. Verifique sua caixa de entrada (ou spam) para liberar o login."
                )
            else:
                messages.warning(
                    request,
                    f"Conta criada, mas não foi possível enviar o e-mail para {usuario.email} agora. Use o botão abaixo para reenviar."
                )
            return redirect('login')
        else:
            registrar_falha_ip(chave_ip, tempo_bloq, chave_usr)
            messages.error(request, 'Verifique os campos destacados no formulário abaixo.')
    else:
        form = RegistroUsuarioForm()
    return render(request, 'gestao/register.html', {'form': form})


@never_cache
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        usr_tentativa = request.POST.get('username', '')
        bloqueado, chave_ip, chave_usr, tempo_bloq = verificar_bloqueio_ip(
            request, escopo='login', identificador_extra=usr_tentativa, max_tentativas=6
        )
        if bloqueado:
            security_logger.warning(
                "Bloqueio de forca bruta no login para IP=%s usuario=%s", get_client_ip(request), usr_tentativa
            )
            messages.error(request, 'Muitas tentativas de acesso. Aguarde 5 minutos antes de tentar novamente.')
            return render(
                request,
                'gestao/login.html',
                {
                    'form': LoginUsuarioForm(),
                    'email_pendente_confirmacao': _obter_email_pendente_sessao(request),
                },
                status=429,
            )

        form = LoginUsuarioForm(request, data=request.POST)
        if form.is_valid():
            usuario = form.get_user()

            if not is_email_confirmado(usuario):
                security_logger.warning(
                    "Login bloqueado (e-mail nao verificado) para usuario=%s IP=%s",
                    usuario.username, get_client_ip(request)
                )
                if usuario.email:
                    request.session['reenvio_pendente_uid'] = usuario.pk
                    messages.warning(
                        request,
                        f"Seu e-mail ({usuario.email}) ainda não foi verificado. Acesse o link enviado para sua caixa de entrada ou solicite um novo envio abaixo."
                    )
                else:
                    messages.error(
                        request,
                        "Sua conta ainda não possui um e-mail verificado vinculado. Entre em contato com o suporte para liberar o acesso."
                    )
                return redirect('login')

            garantir_titular_conta(usuario.username)
            login(request, usuario)
            request.session.pop('reenvio_pendente_uid', None)
            cache.delete(chave_ip)
            if chave_usr:
                cache.delete(chave_usr)
            return redirect('dashboard')
        else:
            registrar_falha_ip(chave_ip, tempo_bloq, chave_usr)
            security_logger.warning(
                "Falha de autenticacao para identificador=%s IP=%s", usr_tentativa, get_client_ip(request)
            )
            messages.error(request, 'E-mail/usuário ou senha inválidos.')
    else:
        form = LoginUsuarioForm()

    return render(request, 'gestao/login.html', {
        'form': form,
        'email_pendente_confirmacao': _obter_email_pendente_sessao(request),
    })


@require_POST
def logout_view(request):
    logout(request)
    return redirect('login')


@never_cache
@require_POST
def reenviar_confirmacao_publica_view(request):
    uid = request.session.get('reenvio_pendente_uid')
    if not uid:
        messages.error(request, 'Informe suas credenciais de login primeiro para solicitar um novo link de confirmação.')
        return redirect('login')

    usuario = User.objects.filter(pk=uid).first()
    if not usuario or not usuario.email:
        request.session.pop('reenvio_pendente_uid', None)
        messages.error(request, 'Nenhum e-mail pendente de confirmação encontrado nesta sessão.')
        return redirect('login')

    if is_email_confirmado(usuario):
        request.session.pop('reenvio_pendente_uid', None)
        messages.success(request, 'Seu e-mail já está confirmado! Faça login normalmente.')
        return redirect('login')

    bloqueado, ch_ip, ch_usr, t_bloq = verificar_bloqueio_ip(
        request, escopo='reenviar_email_pub', identificador_extra=usuario.username, max_tentativas=5, tempo_bloqueio=300
    )
    if bloqueado:
        messages.error(request, 'Aguarde 5 minutos antes de solicitar um novo reenvio.')
        return redirect('login')

    registrar_falha_ip(ch_ip, t_bloq, ch_usr)
    if disparar_email_confirmacao(request, usuario, usuario.email):
        messages.success(request, f"Novo link de confirmação enviado para {usuario.email}. Verifique sua caixa de entrada e spam.")
    else:
        messages.error(request, "Não foi possível enviar o e-mail no momento. Tente novamente em instantes.")
    return redirect('login')


@login_required(login_url='login')
@require_POST
def vincular_email_view(request):
    bloqueado, ch_ip, ch_usr, t_bloq = verificar_bloqueio_ip(
        request, escopo='vincular_email', identificador_extra=request.user.username, max_tentativas=8, tempo_bloqueio=300
    )
    if bloqueado:
        messages.error(request, 'Muitas solicitações seguidas. Aguarde alguns minutos.')
        return redirect('dashboard')

    form = VincularEmailForm(request.POST, user=request.user)
    if form.is_valid():
        novo_email = form.cleaned_data['email']
        usuario = request.user
        usuario.email = novo_email
        usuario.last_name = ''
        usuario.save(update_fields=['email', 'last_name'])

        registrar_falha_ip(ch_ip, t_bloq, ch_usr)
        enviado = disparar_email_confirmacao(request, usuario, novo_email)
        if enviado:
            messages.success(request, f"Enviamos um link de confirmação para {novo_email}. Verifique sua caixa de entrada.")
        else:
            messages.warning(request, f"E-mail {novo_email} salvo. Não foi possível enviar o link agora; tente reenviar pelo painel.")
    else:
        for erro in form.errors.get('email', []):
            messages.error(request, erro)
    return redirect('dashboard')


@login_required(login_url='login')
@require_POST
def reenviar_confirmacao_email_view(request):
    usuario = request.user
    if not usuario.email:
        messages.error(request, 'Cadastre um endereço de e-mail primeiro.')
        return redirect('dashboard')

    bloqueado, ch_ip, ch_usr, t_bloq = verificar_bloqueio_ip(
        request, escopo='reenviar_email', identificador_extra=usuario.username, max_tentativas=5, tempo_bloqueio=300
    )
    if bloqueado:
        messages.error(request, 'Aguarde alguns minutos antes de solicitar um novo reenvio.')
        return redirect('dashboard')

    registrar_falha_ip(ch_ip, t_bloq, ch_usr)
    if disparar_email_confirmacao(request, usuario, usuario.email):
        messages.success(request, f"Novo link de confirmação enviado para {usuario.email}.")
    else:
        messages.error(request, "Não foi possível enviar o e-mail no momento. Tente novamente em instantes.")
    return redirect('dashboard')


@never_cache
def confirmar_email_view(request, token):
    try:
        dados = signing.loads(token, salt=EMAIL_CONFIRM_SALT, max_age=60 * 60 * 24)
        user_id = dados.get('uid')
        email = (dados.get('email') or '').strip().lower()
        usuario = User.objects.filter(pk=user_id).first()
        if not usuario or not email:
            raise signing.BadSignature()

        if User.objects.filter(email__iexact=email).exclude(pk=usuario.pk).exists():
            messages.error(request, 'Este endereço de e-mail já foi vinculado a outra conta.')
            return redirect('dashboard' if request.user.is_authenticated else 'login')

        marcar_email_como_confirmado(usuario, email)
        request.session.pop('reenvio_pendente_uid', None)
        messages.success(request, f"E-mail ({email}) confirmado com sucesso! Agora você já pode entrar no FinPro.")
    except signing.SignatureExpired:
        messages.error(request, 'O link de confirmação expirou. Tente entrar na sua conta para solicitar um novo link.')
    except signing.BadSignature:
        messages.error(request, 'Link de confirmação inválido.')

    return redirect('dashboard' if request.user.is_authenticated else 'login')


@never_cache
def recuperar_senha_view(request):
    if request.method == 'POST':
        if request.POST.get('website_url_honeypot'):
            security_logger.warning("Honeypot acionado em recuperar_senha pelo IP=%s", get_client_ip(request))
            return redirect('login')

        identificador = (request.POST.get('identificador') or '').strip()
        bloqueado, ch_ip, ch_usr, t_bloq = verificar_bloqueio_ip(
            request, escopo='recuperar_senha', identificador_extra=identificador, max_tentativas=5, tempo_bloqueio=300
        )
        if bloqueado:
            security_logger.warning(
                "Bloqueio de rate limit em recuperar_senha IP=%s identificador=%s",
                get_client_ip(request), identificador
            )
            messages.error(request, 'Muitas solicitações realizadas. Aguarde 5 minutos.')
            return render(request, 'gestao/recuperar_senha.html', status=429)

        registrar_falha_ip(ch_ip, t_bloq, ch_usr)

        if identificador:
            usuario = User.objects.filter(
                Q(email__iexact=identificador) | Q(username__iexact=identificador)
            ).first()
            if usuario and usuario.email:
                uidb64 = urlsafe_base64_encode(force_bytes(usuario.pk))
                token = default_token_generator.make_token(usuario)
                link = request.build_absolute_uri(
                    reverse('redefinir_senha_confirmar', kwargs={'uidb64': uidb64, 'token': token})
                )
                assunto, corpo_texto, corpo_html = construir_email_redefinicao_senha(
                    username=usuario.username,
                    link_redefinicao=link,
                    ip_origem=get_client_ip(request),
                    data_hora_str=_formatar_data_hora_seguranca(),
                    logo_url=_obter_logo_email_url(request),
                )
                enviar_email_seguro(assunto, corpo_texto, usuario.email, mensagem_html=corpo_html)

        messages.success(
            request,
            'Se houver uma conta com e-mail cadastrado correspondente aos dados informados, enviamos um link seguro válido por 30 minutos.'
        )
        return redirect('login')

    return render(request, 'gestao/recuperar_senha.html')


@never_cache
def redefinir_senha_confirmar_view(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        usuario = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        usuario = None

    token_valido = usuario is not None and default_token_generator.check_token(usuario, token)
    if not token_valido:
        messages.error(request, 'O link de redefinição de senha é inválido, expirou (limite de 30 min) ou já foi utilizado.')
        return redirect('recuperar_senha')

    if request.method == 'POST':
        form = SetPasswordForm(usuario, request.POST)
        if form.is_valid():
            form.save()
            if usuario.email and not is_email_confirmado(usuario):
                marcar_email_como_confirmado(usuario, usuario.email)

            encerrar_todas_sessoes_usuario(usuario.pk)
            request.session.pop('reenvio_pendente_uid', None)

            if usuario.email:
                assunto_alerta, texto_alerta, html_alerta = construir_email_alerta_senha_alterada(
                    username=usuario.username,
                    ip_origem=get_client_ip(request),
                    data_hora_str=_formatar_data_hora_seguranca(),
                    link_recuperacao=request.build_absolute_uri(reverse('recuperar_senha')),
                    logo_url=_obter_logo_email_url(request),
                )
                enviar_email_seguro(assunto_alerta, texto_alerta, usuario.email, mensagem_html=html_alerta)

            security_logger.warning(
                "Senha redefinida com sucesso e sessoes encerradas para usuario=%s IP=%s",
                usuario.username, get_client_ip(request)
            )
            messages.success(request, 'Senha atualizada com sucesso! Por segurança, faça login novamente com a nova senha.')
            return redirect('login')
    else:
        form = SetPasswordForm(usuario)

    response = render(request, 'gestao/redefinir_senha.html', {'form': form, 'usuario_alvo': usuario})
    response['Referrer-Policy'] = 'same-origin'
    return response


# ==========================================
# DASHBOARD PRINCIPAL
# ==========================================

@login_required(login_url='login')
def dashboard(request):
    minha_conta = request.user.username
    eu = garantir_titular_conta(minha_conta)

    hoje = date.today()
    mes_selecionado = parse_int_seguro(request.GET.get('mes'), padrao=hoje.month, minimo=1, maximo=12)
    ano_selecionado = parse_int_seguro(request.GET.get('ano'), padrao=hoje.year, minimo=2000, maximo=2100)

    if request.method == 'POST':
        tipo_formulario = request.POST.get('form_type')

        # 0. LANÇAMENTO RÁPIDO POR TEXTO
        if tipo_formulario in ('lancamento_inteligente', 'lancamento_rapido'):
            comando = (request.POST.get('comando_ia') or request.POST.get('texto_rapido') or '').strip()
            if comando:
                cartoes_usr = list(Cartao.objects.filter(conta__iexact=minha_conta))
                dados_cmd = interpretar_comando_inteligente(comando, cartoes_usr)
                if dados_cmd['valor'] <= 0:
                    messages.error(request, "Informe a descrição e um valor maior que zero. Exemplo: Uber 24,90")
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
            bloqueado_csv, ch_csv_ip, ch_csv_usr, t_bloq_csv = verificar_bloqueio_ip(
                request, escopo='upload_csv', identificador_extra=minha_conta, max_tentativas=15, tempo_bloqueio=300
            )
            if bloqueado_csv:
                security_logger.warning("Rate limit de upload CSV atingido para usuario=%s", minha_conta)
                messages.error(request, 'Muitos envios de arquivo em sequência. Aguarde alguns minutos.')
                return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')
            registrar_falha_ip(ch_csv_ip, t_bloq_csv, ch_csv_usr)

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

            for index, row in enumerate(itertools.islice(reader, 1000)):
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
            ids_marcados = request.POST.getlist('importar_chk')[:1000]
            cartao_id = request.POST.get('cartao_lote')
            novo_cartao_nome = request.POST.get('novo_cartao_lote_nome', '').strip()

            cartao_obj = None
            if cartao_id == 'NOVO_CARTAO' and novo_cartao_nome:
                cartao_obj = Cartao.objects.create(
                    nome=sanitizar_texto_csv(novo_cartao_nome),
                    dia_fechamento=parse_int_seguro(request.POST.get('novo_cartao_fechamento'), padrao=25, minimo=1, maximo=31),
                    dia_vencimento=parse_int_seguro(request.POST.get('novo_cartao_vencimento'), padrao=5, minimo=1, maximo=31),
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
                    categoria = validar_categoria(request.POST.get(f'cat_{t_id}', 'Outros'))

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

        # 3. NOVA TRANSAÇÃO (SUPORTA DESPESAS E TAMBÉM PAGAMENTOS VINCULADOS OU AVULSOS)
        elif tipo_formulario == 'nova_transacao':
            descricao_digitada = sanitizar_texto_csv(request.POST.get('descricao', ''))
            valor_dec = parse_moeda_decimal(request.POST.get('valor', '0'))
            tipo_escolhido = validar_tipo_transacao(request.POST.get('tipo', 'DESPESA'))
            divida_vinc_id = request.POST.get('divida_vinculada_id', '').strip()

            if valor_dec <= Decimal('0.00'):
                messages.error(request, "Informe um valor maior que zero.")
                return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

            dono_id_escolhido = request.POST.get('dono_id')
            categoria_escolhida = validar_categoria(request.POST.get('categoria', 'Outros'))
            data_digitada = request.POST.get('data')
            cartao_id_escolhido = request.POST.get('cartao_id')

            dono_obj = get_object_or_404(Pessoa, id=dono_id_escolhido, conta__iexact=minha_conta) if (dono_id_escolhido and str(dono_id_escolhido).isdigit()) else eu
            cartao_obj = get_object_or_404(Cartao, id=cartao_id_escolhido, conta__iexact=minha_conta) if (cartao_id_escolhido and str(cartao_id_escolhido).isdigit()) else None
            data_base = parse_data_flexivel(data_digitada) or hoje

            if tipo_escolhido == 'PAGAMENTO':
                divida_alvo = None
                if divida_vinc_id and divida_vinc_id.isdigit():
                    divida_alvo = Transacao.objects.filter(
                        id=int(divida_vinc_id), conta__iexact=minha_conta, tipo='DESPESA'
                    ).select_related('cartao', 'dono').first()

                if divida_alvo:
                    dono_obj = divida_alvo.dono
                    cartao_obj = cartao_obj or divida_alvo.cartao
                    data_salvar = divida_alvo.data
                    base_desc = descricao_digitada or f"Pagamento de {divida_alvo.descricao}"
                    desc_final = sanitizar_texto_csv(f"{base_desc} (Ref: {divida_alvo.descricao}) [REF:{divida_alvo.id}]")
                else:
                    data_salvar = data_base
                    desc_final = sanitizar_texto_csv(descricao_digitada or f"Pagamento ({dono_obj.nome})")

                Transacao.objects.create(
                    descricao=desc_final,
                    valor=valor_dec,
                    dono=dono_obj,
                    tipo='PAGAMENTO',
                    categoria=categoria_escolhida,
                    data=data_salvar,
                    cartao=cartao_obj,
                    conta=minha_conta,
                    recorrente=False
                )
                if cartao_obj:
                    mes_red, ano_red = get_mes_fatura(data_salvar, cartao_obj.dia_fechamento, cartao_obj.dia_vencimento)
                else:
                    mes_red, ano_red = data_salvar.month, data_salvar.year
                messages.success(request, f"Pagamento de R$ {float(valor_dec):.2f} registrado e abatido com sucesso.")
                return redirect(f'/?mes={mes_red}&ano={ano_red}')

            if not descricao_digitada:
                messages.error(request, "Informe uma descrição válida para o lançamento.")
                return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

            recorrente = request.POST.get('recorrente') == '1'
            parcelas = 1 if recorrente else parse_int_seguro(request.POST.get('parcelas', 1), padrao=1, minimo=1, maximo=120)

            donos_extras = request.POST.getlist('dono_id_extra')
            valores_extras_str = request.POST.getlist('valor_extra')

            parcelas_totais_dec = dividir_em_parcelas_exatas(float(valor_dec), parcelas)
            matriz_extras_dec = [dividir_em_parcelas_exatas(max(0.0, parse_moeda(v)), parcelas) for v in valores_extras_str]

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
                        if dono_extra_id and str(dono_extra_id).isdigit() and valor_deste_amigo > Decimal('0.00'):
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
            valor_dec = parse_moeda_decimal(request.POST.get('valor', '0'))
            if not descricao or valor_dec <= Decimal('0.00'):
                messages.error(request, "Informe uma descrição válida e um valor maior que zero.")
                return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

            data_val = request.POST.get('data')
            tipo = validar_tipo_transacao(request.POST.get('tipo', 'DESPESA'))
            categoria = validar_categoria(request.POST.get('categoria', 'Outros'))
            recorrente = request.POST.get('recorrente') == '1'
            parcelas = 1 if recorrente else parse_int_seguro(request.POST.get('parcelas', 1), padrao=1, minimo=1, maximo=120)
            dono_id = request.POST.get('dono_id')
            cartao_id = request.POST.get('cartao_id')

            donos_extras = request.POST.getlist('dono_id_extra')
            valores_extras_str = request.POST.getlist('valor_extra')

            parcelas_totais_dec = dividir_em_parcelas_exatas(float(valor_dec), parcelas)
            valores_extras_dec = [
                (max(Decimal('0.00'), parse_moeda_decimal(v)) / Decimal(parcelas)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
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
            transacao.cartao = get_object_or_404(Cartao, id=cartao_id, conta__iexact=minha_conta) if (cartao_id and str(cartao_id).isdigit()) else None
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
                    if extra_id and str(extra_id).isdigit() and extra_val > Decimal('0.00'):
                        amigo_obj = get_object_or_404(Pessoa, id=extra_id, conta__iexact=minha_conta)
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

        # 5. OUTROS GERENCIAMENTOS E PAGAMENTO/ABATIMENTO DE DÍVIDAS
        elif tipo_formulario == 'atualizar_meta':
            novo_valor = max(Decimal('0.00'), parse_moeda_decimal(request.POST.get('valor_meta')))
            meta = MetaMensal.objects.filter(conta__iexact=minha_conta).first()
            if meta:
                meta.valor = novo_valor
                meta.save()
            else:
                MetaMensal.objects.create(valor=novo_valor, conta=minha_conta)
            messages.success(request, "Meta mensal atualizada.")

        elif tipo_formulario == 'nova_pessoa':
            nome_p = sanitizar_texto_csv(request.POST.get('nome', ''))
            if nome_p:
                Pessoa.objects.create(
                    nome=nome_p,
                    telefone=re.sub(r'[^\d+]', '', request.POST.get('telefone', ''))[:20],
                    is_titular=not Pessoa.objects.filter(conta__iexact=minha_conta, is_titular=True).exists(),
                    conta=minha_conta
                )
                messages.success(request, "Pessoa cadastrada.")

        elif tipo_formulario == 'editar_pessoa':
            p_id = request.POST.get('pessoa_id')
            p = get_object_or_404(Pessoa, id=p_id, conta__iexact=minha_conta)
            nome_p = sanitizar_texto_csv(request.POST.get('nome', ''))
            if nome_p:
                p.nome = nome_p
                p.telefone = re.sub(r'[^\d+]', '', request.POST.get('telefone', ''))[:20]
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
            nome_c = sanitizar_texto_csv(request.POST.get('nome', ''))
            if nome_c:
                Cartao.objects.create(
                    nome=nome_c,
                    dia_fechamento=parse_int_seguro(request.POST.get('dia_fechamento'), padrao=25, minimo=1, maximo=31),
                    dia_vencimento=parse_int_seguro(request.POST.get('dia_vencimento'), padrao=5, minimo=1, maximo=31),
                    conta=minha_conta
                )
                messages.success(request, "Cartão adicionado.")

        elif tipo_formulario == 'registrar_pagamento':
            dono_id_pag = request.POST.get('dono_id')
            dono_obj = get_object_or_404(Pessoa, id=dono_id_pag, conta__iexact=minha_conta) if (dono_id_pag and str(dono_id_pag).isdigit()) else eu
            valor_pag = parse_moeda_decimal(request.POST.get('valor'))

            if valor_pag > Decimal('0.00'):
                divida_id = (request.POST.get('divida_id') or '').strip()
                referencia = sanitizar_texto_csv(request.POST.get('referencia_gasto', ''))
                descricao_custom = sanitizar_texto_csv(request.POST.get('descricao_pagamento', ''))
                cartao_id_pag = request.POST.get('cartao_id')
                cartao_pag_obj = Cartao.objects.filter(id=cartao_id_pag, conta__iexact=minha_conta).first() if (cartao_id_pag and str(cartao_id_pag).isdigit()) else None

                divida_obj = None
                if divida_id and divida_id.isdigit():
                    divida_obj = Transacao.objects.filter(
                        id=int(divida_id), conta__iexact=minha_conta, tipo='DESPESA'
                    ).select_related('cartao', 'dono').first()

                if divida_obj:
                    dono_obj = divida_obj.dono
                    cartao_pag_obj = cartao_pag_obj or divida_obj.cartao
                    data_pag = divida_obj.data
                    titulo_base = descricao_custom or f"Pagamento: {divida_obj.descricao}"
                    descricao_pagamento = sanitizar_texto_csv(
                        f"{titulo_base} (Ref: {divida_obj.descricao}) [REF:{divida_obj.id}]"
                    )
                else:
                    data_pag = parse_data_flexivel(request.POST.get('data')) or date(
                        ano_selecionado, mes_selecionado, min(hoje.day, calendar.monthrange(ano_selecionado, mes_selecionado)[1])
                    )
                    if referencia:
                        descricao_pagamento = sanitizar_texto_csv(f"Pagamento de {dono_obj.nome} (Ref: {referencia})")
                    elif descricao_custom:
                        descricao_pagamento = descricao_custom
                    elif dono_obj.is_titular:
                        descricao_pagamento = "Pagamento / Abatimento avulso"
                    else:
                        descricao_pagamento = f"Pagamento de {dono_obj.nome}"

                Transacao.objects.create(
                    descricao=descricao_pagamento,
                    valor=valor_pag,
                    dono=dono_obj,
                    tipo='PAGAMENTO',
                    categoria='Outros',
                    data=data_pag,
                    cartao=cartao_pag_obj,
                    conta=minha_conta,
                    recorrente=False
                )
                messages.success(request, f"Pagamento de R$ {float(valor_pag):.2f} registrado e subtraído do saldo devedor.")
            else:
                messages.error(request, "Informe um valor de pagamento maior que zero.")

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

    # Mapeia pagamentos realizados no mês para calcular saldos pendentes por dívida e abatimento pessoal
    pagamentos_do_mes = [t for t in lista_transacoes_mes if t.tipo == 'PAGAMENTO']
    minhas_dividas_abertas = []

    for t in lista_transacoes_mes:
        if t.tipo == 'DESPESA':
            pago_vinculado = sum(
                float(p.valor) for p in pagamentos_do_mes
                if p.dono_id == t.dono_id and _pagamento_corresponde_despesa(p.descricao, t)
            )
            t.valor_pago = round(min(float(t.valor), pago_vinculado), 2)
            t.saldo_pendente = round(max(0.0, float(t.valor) - pago_vinculado), 2)
            t.quitado = t.saldo_pendente <= 0.01

            if t.cartao:
                _, dt_venc_item = calcular_datas_ciclo_cartao(
                    mes_selecionado, ano_selecionado, t.cartao.dia_fechamento, t.cartao.dia_vencimento
                )
                t.vencimento_fatura_str = dt_venc_item.strftime('%d/%m')
            else:
                t.vencimento_fatura_str = ''

            if eu and t.dono_id == eu.id and not t.quitado:
                minhas_dividas_abertas.append({
                    'id': t.id,
                    'descricao': t.descricao,
                    'valor_original': float(t.valor),
                    'valor_pago': t.valor_pago,
                    'saldo_pendente': t.saldo_pendente,
                    'cartao_id': t.cartao_id or '',
                    'cartao_nome': t.cartao.nome if t.cartao else 'Pix/Conta',
                })

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
        desp_h = sum(float(t.valor) for t in itens_h if eu and t.dono_id == eu.id and t.tipo == 'DESPESA')
        pag_h = sum(float(t.valor) for t in itens_h if eu and t.dono_id == eu.id and t.tipo == 'PAGAMENTO')
        total_pessoal_h = max(0.0, desp_h - pag_h)
        labels_evolucao.append(f"{nomes_meses[m_hist]}/{str(a_hist)[2:]}")
        valores_evolucao.append(round(total_pessoal_h, 2))

    gastos_mes_anterior = valores_evolucao[-2] if len(valores_evolucao) >= 2 else 0.0

    if filtra_pessoa_id and str(filtra_pessoa_id).isdigit():
        lista_transacoes_mes = [t for t in lista_transacoes_mes if str(t.dono.id) == str(filtra_pessoa_id)]

    meus_gastos_brutos = round(sum(
        float(t.valor) for t in lista_transacoes_mes if eu and t.dono_id == eu.id and t.tipo == 'DESPESA'
    ), 2)
    meus_pagamentos = round(sum(
        float(t.valor) for t in lista_transacoes_mes if eu and t.dono_id == eu.id and t.tipo == 'PAGAMENTO'
    ), 2)
    # Subtrai os pagamentos (vinculados ou avulsos) do valor que o usuário deve no mês
    meus_gastos = round(max(0.0, meus_gastos_brutos - meus_pagamentos), 2)

    familiares = Pessoa.objects.filter(is_titular=False, conta__iexact=minha_conta)
    dividas_detalhadas = []

    for familiar in familiares:
        despesas_fam = [t for t in lista_transacoes_mes if t.dono_id == familiar.id and t.tipo == 'DESPESA']
        pagamentos_fam = [t for t in lista_transacoes_mes if t.dono_id == familiar.id and t.tipo == 'PAGAMENTO']
        saldo_pessoa = sum(float(t.valor) for t in despesas_fam) - sum(float(t.valor) for t in pagamentos_fam)

        if abs(saldo_pessoa) > 0.01:
            despesas_pendentes = []
            for desp in despesas_fam:
                pago_nesta_despesa = sum(
                    float(p.valor) for p in pagamentos_fam if _pagamento_corresponde_despesa(p.descricao, desp)
                )
                if float(desp.valor) - pago_nesta_despesa > 0.01:
                    despesas_pendentes.append({
                        'id': desp.id,
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

    resumo_cartoes, melhor_cartao_hoje = analisar_inteligencia_cartoes(
        todos_cartoes, lista_transacoes_mes, mes_selecionado, ano_selecionado
    )
    cartoes_js_meta = [
        {
            'id': c.id,
            'nome': c.nome,
            'fechamento': c.dia_fechamento,
            'vencimento': c.dia_vencimento,
        }
        for c in todos_cartoes
    ]

    indicadores = calcular_indicadores_contabeis(
        meus_gastos, gastos_mes_anterior, valor_meta, gastos_pessoais_dict, lista_transacoes_mes, eu, mes_selecionado, ano_selecionado
    )

    contexto = {
        'transacoes': lista_transacoes_mes,
        'pessoas': todas_pessoas,
        'titular_id': eu.id if eu else None,
        'cartoes': todos_cartoes,
        'cartoes_js_meta': cartoes_js_meta,
        'resumo_cartoes': resumo_cartoes,
        'melhor_cartao_hoje': melhor_cartao_hoje,
        'meus_gastos': meus_gastos,
        'meus_gastos_brutos': meus_gastos_brutos,
        'meus_pagamentos': meus_pagamentos,
        'minhas_dividas_abertas': minhas_dividas_abertas,
        'valor_meta': valor_meta,
        'saldo_restante': saldo_restante,
        'mes_atual': mes_selecionado,
        'ano_atual': ano_selecionado,
        'mes_anterior': mes_anterior,
        'ano_anterior': ano_anterior,
        'mes_proximo': mes_proximo,
        'ano_proximo': ano_proximo,
        'dividas_detalhadas': dividas_detalhadas,
        'filtra_pessoa_id': int(filtra_pessoa_id) if (filtra_pessoa_id and str(filtra_pessoa_id).isdigit()) else None,
        'labels_grafico': list(gastos_pessoais_dict.keys()),
        'valores_grafico': list(gastos_pessoais_dict.values()),
        'labels_evolucao': labels_evolucao,
        'valores_evolucao': valores_evolucao,
        'maior_categoria': maior_categoria,
        'valor_maior_cat': valor_maior_cat,
        'percentual_gasto': percentual_gasto,
        'percentual_barra': min(100, int(percentual_gasto)),
        'analise_ia': indicadores,
        'indicadores': indicadores,
        'ga_measurement_id': os.environ.get('GA_MEASUREMENT_ID', ''),
        'usuario': request.user,
        'tem_email': bool(request.user.email),
        'email_confirmado': is_email_confirmado(request.user),
    }
    return render(request, 'gestao/dashboard.html', contexto)


# ==========================================
# ROTAS DE EXCLUSÃO E CONFORMIDADE LGPD
# ==========================================

@login_required(login_url='login')
@require_POST
def excluir_transacao(request, id):
    transacao = Transacao.objects.filter(id=id, conta__iexact=request.user.username).first()
    if not transacao:
        security_logger.warning(
            "Tentativa de exclusao de transacao inexistente ou de outra conta (id=%s) por usuario=%s",
            id, request.user.username
        )
        return render(request, 'gestao/404.html', status=404)

    nome_desc = transacao.descricao
    transacao.delete()
    messages.success(request, f"Lançamento '{nome_desc}' removido.")
    mes = request.POST.get('mes')
    ano = request.POST.get('ano')
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
            'email': request.user.email,
            'email_confirmado': is_email_confirmado(request.user),
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
    usuario = request.user
    bloqueado, ch_ip, ch_usr, t_bloq = verificar_bloqueio_ip(
        request, escopo='excluir_conta', identificador_extra=usuario.username, max_tentativas=5, tempo_bloqueio=600
    )
    if bloqueado:
        security_logger.warning("Bloqueio de rate limit na exclusao de conta para usuario=%s", usuario.username)
        messages.error(request, 'Muitas tentativas incorretas. Aguarde 10 minutos.')
        return redirect('dashboard')

    senha_confirmacao = request.POST.get('senha_confirmacao', '')
    if not senha_confirmacao or not usuario.check_password(senha_confirmacao):
        registrar_falha_ip(ch_ip, t_bloq, ch_usr)
        security_logger.warning("Tentativa falha de exclusao de conta (senha incorreta) para usuario=%s", usuario.username)
        messages.error(request, 'Senha incorreta. A exclusão da conta foi cancelada por segurança.')
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