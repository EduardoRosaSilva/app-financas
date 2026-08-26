from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Sum
from datetime import datetime, date
import calendar
import json
import urllib.parse
import csv
import io
import re
from collections import defaultdict
from .models import Transacao, Pessoa, MetaMensal, Cartao
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout
from django.contrib import messages
from .forms import RegistroUsuarioForm, LoginUsuarioForm
from django.views.decorators.cache import never_cache
import datetime

# ==========================================
# MOTORES INTELIGENTES
# ==========================================
def get_mes_fatura(data_compra, dia_fechamento):
    if data_compra.day >= dia_fechamento:
        mes = data_compra.month + 1
        ano = data_compra.year
        if mes > 12:
            mes = 1
            ano += 1
        return mes, ano
    return data_compra.month, data_compra.year

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
    if 'restaurante' in cat_lower or 'food' in cat_lower or 'alimenta' in cat_lower: return 'Alimentação'
    if 'supermercado' in cat_lower or 'grocery' in cat_lower or 'mercado' in cat_lower: return 'Mercado'
    if 'transporte' in cat_lower or 'auto' in cat_lower or 'uber' in cat_lower or 'posto' in cat_lower: return 'Transporte'
    if 'saúde' in cat_lower or 'farm' in cat_lower or 'médic' in cat_lower: return 'Saúde'
    return 'Outros'

# ==========================================
# VIEWS DE AUTENTICAÇÃO BLINDADAS
# ==========================================
@never_cache
def registro_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
        
    # BOT PROTECTION (Honeypot)
    if request.method == 'POST':
        if request.POST.get('website_url_honeypot'):
            return redirect('login')
            
        form = RegistroUsuarioForm(request.POST)
        if form.is_valid():
            usuario = form.save()
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
# DASHBOARD (O CORAÇÃO DO SISTEMA)
# ==========================================
@login_required(login_url='login')
def dashboard(request):
    minha_conta = request.user.username
    hoje = date.today()
    mes_selecionado = int(request.GET.get('mes', hoje.month))
    ano_selecionado = int(request.GET.get('ano', hoje.year))

    if request.method == 'POST':
        tipo_formulario = request.POST.get('form_type')
        
        # IMPORTAÇÃO SEGURA DE ARQUIVO
        if tipo_formulario == 'upload_fatura':
            arquivo_csv = request.FILES.get('arquivo_csv')
            if arquivo_csv:
                # TRAVA 2: Tamanho máximo (5MB)
                if arquivo_csv.size > 5242880: 
                    messages.error(request, 'Arquivo muito grande. O limite é 5MB.')
                    return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

                try:
                    decoded_file = arquivo_csv.read().decode('utf-8-sig')
                except UnicodeDecodeError:
                    messages.error(request, 'Erro na leitura. Certifique-se que é um CSV válido.')
                    return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

                primeira_linha = decoded_file.split('\n')[0]
                delimitador = ';' if ';' in primeira_linha else ','
                io_string = io.StringIO(decoded_file)
                reader = csv.DictReader(io_string, delimiter=delimitador)
                
                historico = list(Transacao.objects.filter(conta=minha_conta).exclude(categoria='Outros'))
                transacoes_fatura = []
                
                for index, row in enumerate(reader):
                    row_norm = {str(k).strip().lower(): str(v).strip() for k, v in row.items() if k}
                    data_str = row_norm.get('date', row_norm.get('data', ''))
                    titulo = row_norm.get('title', row_norm.get('titulo', ''))
                    valor_str = row_norm.get('amount', row_norm.get('valor', '0'))
                    
                    if not data_str or not titulo: continue
                    
                    if titulo and titulo[0] in ['=', '+', '-', '@']:
                        titulo = "'" + titulo
                        
                    valor_str = valor_str.replace('R$', '').replace(' ', '')
                    if ',' in valor_str and '.' not in valor_str: valor_str = valor_str.replace(',', '.')
                    elif ',' in valor_str and '.' in valor_str:
                        if valor_str.rfind(',') > valor_str.rfind('.'): valor_str = valor_str.replace('.', '').replace(',', '.')
                        else: valor_str = valor_str.replace(',', '')
                        
                    try: valor = float(valor_str)
                    except ValueError: valor = 0.0
                        
                    if valor <= 0: continue
                        
                    if '/' in data_str:
                        try:
                            d = datetime.strptime(data_str, '%d/%m/%Y')
                            data_str = d.strftime('%Y-%m-%d')
                        except ValueError: pass

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
                    'pessoas': Pessoa.objects.filter(conta=minha_conta),
                    'cartoes': Cartao.objects.filter(conta=minha_conta),
                }
                return render(request, 'gestao/revisao_fatura.html', contexto_revisao)

        # CORREÇÃO DA FATURA INVISÍVEL E ERRO 404
        elif tipo_formulario == 'salvar_fatura_lote':
            ids_marcados = request.POST.getlist('importar_chk')
            cartao_id = request.POST.get('cartao_lote')
            cartao_obj = Cartao.objects.filter(id=cartao_id, conta=minha_conta).first() if cartao_id else None
            
            # PROTEÇÃO: Garante que exista pelo menos uma pessoa para ser dona do gasto
            pessoa_padrao = Pessoa.objects.filter(conta=minha_conta, is_titular=True).first() or Pessoa.objects.filter(conta=minha_conta).first()
            if not pessoa_padrao:
                messages.error(request, "Erro: Você precisa cadastrar pelo menos uma Pessoa no painel antes de importar faturas!")
                return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

            qtd_salva = 0
            ultimo_mes = mes_selecionado
            ultimo_ano = ano_selecionado
            
            for t_id in ids_marcados:
                desc = request.POST.get(f'desc_{t_id}')
                valor_str = request.POST.get(f'valor_{t_id}', '0').replace(',', '.')
                data = request.POST.get(f'data_{t_id}')
                dono_id = request.POST.get(f'dono_{t_id}')
                categoria = request.POST.get(f'cat_{t_id}', 'Outros')
                
                # Busca segura do dono sem gerar tela de erro 404
                dono_obj = None
                if dono_id and str(dono_id).isdigit():
                    dono_obj = Pessoa.objects.filter(id=dono_id, conta=minha_conta).first()
                dono_obj = dono_obj or pessoa_padrao
                
                Transacao.objects.create(
                    descricao=desc, valor=float(valor_str), data=data, tipo='DESPESA',
                    categoria=categoria, dono=dono_obj, cartao=cartao_obj, conta=minha_conta, recorrente=False
                )
                qtd_salva += 1
                
                # Descobre para qual mês o gasto foi jogado
                if data:
                    data_obj = datetime.strptime(data, '%Y-%m-%d')
                    if cartao_obj:
                        ultimo_mes, ultimo_ano = get_mes_fatura(data_obj, cartao_obj.dia_fechamento)
                    else:
                        ultimo_mes = data_obj.month
                        ultimo_ano = data_obj.year

            messages.success(request, f"{qtd_salva} transações importadas com sucesso!")
            return redirect(f'/?mes={ultimo_mes}&ano={ultimo_ano}')

        elif tipo_formulario == 'nova_transacao':
            descricao_digitada = request.POST.get('descricao')
            valor_str = request.POST.get('valor', '0')
            valor_digitado = float(valor_str.replace(',', '.')) if valor_str else 0.0
            
            dono_id_escolhido = request.POST.get('dono_id')
            tipo_escolhido = request.POST.get('tipo')
            categoria_escolhida = request.POST.get('categoria', 'Outros')
            data_digitada = request.POST.get('data')
            cartao_id_escolhido = request.POST.get('cartao_id')
            
            recorrente = True if request.POST.get('recorrente') == '1' else False
            parcelas = 1 if recorrente else int(request.POST.get('parcelas', 1))
            
            donos_extras = request.POST.getlist('dono_id_extra')
            valores_extras_str = request.POST.getlist('valor_extra')
            
            dono_obj = get_object_or_404(Pessoa, id=dono_id_escolhido, conta=minha_conta)
            cartao_obj = get_object_or_404(Cartao, id=cartao_id_escolhido, conta=minha_conta) if cartao_id_escolhido else None
            data_base = datetime.strptime(data_digitada, '%Y-%m-%d').date()
            
            if parcelas > 1:
                valor_final_parcela = round(valor_digitado / parcelas, 2)
                valores_finais_extras = [round(float(v.replace(',', '.')) / parcelas, 2) if v else 0.0 for v in valores_extras_str]
            else:
                valor_final_parcela = valor_digitado
                valores_finais_extras = [float(v.replace(',', '.')) if v else 0.0 for v in valores_extras_str]
            
            for i in range(parcelas):
                mes_atual = data_base.month + i
                ano_atual_loop = data_base.year + ((mes_atual - 1) // 12)
                mes_calculado = ((mes_atual - 1) % 12) + 1
                ultimo_dia_mes = calendar.monthrange(ano_atual_loop, mes_calculado)[1]
                dia_calculado = min(data_base.day, ultimo_dia_mes)
                nova_data = date(ano_atual_loop, mes_calculado, dia_calculado)
                
                desc_salvar = f"{descricao_digitada} ({i+1}/{parcelas})" if parcelas > 1 else descricao_digitada
                soma_extras = sum(valores_finais_extras)
                valor_dono_1 = valor_final_parcela - soma_extras
                
                Transacao.objects.create(
                    descricao=desc_salvar, valor=valor_dono_1, dono=dono_obj, tipo=tipo_escolhido, 
                    categoria=categoria_escolhida, data=nova_data, cartao=cartao_obj, conta=minha_conta, recorrente=recorrente
                )
                
                for idx, dono_extra_id in enumerate(donos_extras):
                    valor_deste_amigo = valores_finais_extras[idx]
                    if dono_extra_id and valor_deste_amigo > 0:
                        d_obj = get_object_or_404(Pessoa, id=dono_extra_id, conta=minha_conta)
                        Transacao.objects.create(
                            descricao=f"{desc_salvar} (Parte de {d_obj.nome})", valor=valor_deste_amigo, dono=d_obj, 
                            tipo=tipo_escolhido, categoria=categoria_escolhida, data=nova_data, cartao=cartao_obj, conta=minha_conta, recorrente=recorrente
                        )

        elif tipo_formulario == 'editar_transacao':
            t_id = request.POST.get('transacao_id')
            transacao = get_object_or_404(Transacao, id=t_id, conta=minha_conta)
            transacao.descricao = request.POST.get('descricao')
            transacao.valor = float(request.POST.get('valor').replace(',', '.'))
            transacao.data = request.POST.get('data')
            transacao.tipo = request.POST.get('tipo')
            transacao.categoria = request.POST.get('categoria', 'Outros')
            transacao.recorrente = True if request.POST.get('recorrente') == '1' else False
            transacao.dono = get_object_or_404(Pessoa, id=request.POST.get('dono_id'), conta=minha_conta)
            cartao_id = request.POST.get('cartao_id')
            transacao.cartao = get_object_or_404(Cartao, id=cartao_id, conta=minha_conta) if cartao_id else None
            transacao.save()

        elif tipo_formulario == 'atualizar_meta':
            novo_valor = request.POST.get('valor_meta')
            meta = MetaMensal.objects.filter(conta=minha_conta).first()
            if meta:
                meta.valor = novo_valor
                meta.save()
            else:
                MetaMensal.objects.create(valor=novo_valor, conta=minha_conta)

        # GERENCIAMENTO DE PESSOAS (NOVO, EDITAR E EXCLUIR)
        elif tipo_formulario == 'nova_pessoa':
            Pessoa.objects.create(
                nome=request.POST.get('nome'), telefone=request.POST.get('telefone', ''), 
                is_titular=not Pessoa.objects.filter(conta=minha_conta, is_titular=True).exists(), conta=minha_conta
            )
            messages.success(request, "Pessoa adicionada com sucesso!")
            
        elif tipo_formulario == 'editar_pessoa':
            p_id = request.POST.get('pessoa_id')
            p = get_object_or_404(Pessoa, id=p_id, conta=minha_conta)
            p.nome = request.POST.get('nome')
            p.telefone = request.POST.get('telefone', '')
            p.save()
            messages.success(request, f"Dados de {p.nome} atualizados!")
            
        elif tipo_formulario == 'excluir_pessoa':
            p_id = request.POST.get('pessoa_id')
            p = get_object_or_404(Pessoa, id=p_id, conta=minha_conta)
            if p.is_titular:
                messages.error(request, "Segurança: Você não pode excluir o Titular da conta.")
            else:
                p.delete()
                messages.success(request, "Pessoa excluída com sucesso.")

        elif tipo_formulario == 'novo_cartao':
            Cartao.objects.create(
                nome=request.POST.get('nome'), 
                dia_fechamento=int(request.POST.get('dia_fechamento', 25)),
                dia_vencimento=int(request.POST.get('dia_vencimento', 5)),
                conta=minha_conta
            )

        elif tipo_formulario == 'registrar_pagamento':
            dono_obj = get_object_or_404(Pessoa, id=request.POST.get('dono_id'), conta=minha_conta)
            referencia = request.POST.get('referencia_gasto')
            descricao_pagamento = f"Pagamento de {dono_obj.nome}" + (f" (Ref: {referencia})" if referencia else "")
            
            Transacao.objects.create(
                descricao=descricao_pagamento, valor=float(request.POST.get('valor').replace(',', '.')),
                dono=dono_obj, tipo='PAGAMENTO', categoria='Outros',
                data=datetime.strptime(request.POST.get('data'), '%Y-%m-%d').date(), conta=minha_conta, recorrente=False
            )
            
        return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

    todas_pessoas = Pessoa.objects.filter(conta=minha_conta)
    todos_cartoes = Cartao.objects.filter(conta=minha_conta)
    eu = Pessoa.objects.filter(is_titular=True, conta=minha_conta).first()
    filtra_pessoa_id = request.GET.get('pessoa')
    
    mes_anterior = mes_selecionado - 1 if mes_selecionado > 1 else 12
    ano_anterior = ano_selecionado if mes_selecionado > 1 else ano_selecionado - 1
    mes_proximo = mes_selecionado + 1 if mes_selecionado < 12 else 1
    ano_proximo = ano_selecionado if mes_selecionado < 12 else ano_selecionado + 1
    
    ultimo_dia_mes = calendar.monthrange(ano_selecionado, mes_selecionado)[1]
    
    trans_normais = Transacao.objects.filter(conta=minha_conta, recorrente=False)
    trans_rec = Transacao.objects.filter(conta=minha_conta, recorrente=True)
    
    lista_transacoes_mes = []
    
    for t in trans_normais:
        if t.cartao:
            m_fat, a_fat = get_mes_fatura(t.data, t.cartao.dia_fechamento)
            if m_fat == mes_selecionado and a_fat == ano_selecionado:
                lista_transacoes_mes.append(t)
        else:
            if t.data.month == mes_selecionado and t.data.year == ano_selecionado:
                lista_transacoes_mes.append(t)
                
    for t in trans_rec:
        if t.cartao:
            m_fat_orig, a_fat_orig = get_mes_fatura(t.data, t.cartao.dia_fechamento)
            if a_fat_orig < ano_selecionado or (a_fat_orig == ano_selecionado and m_fat_orig <= mes_selecionado):
                dia_original = t.data.day
                dia_valido = min(dia_original, ultimo_dia_mes)
                t.data_exibicao = date(ano_selecionado, mes_selecionado, dia_valido)
                lista_transacoes_mes.append(t)
        else:
            if t.data.year < ano_selecionado or (t.data.year == ano_selecionado and t.data.month <= mes_selecionado):
                dia_original = t.data.day
                dia_valido = min(dia_original, ultimo_dia_mes)
                t.data_exibicao = date(ano_selecionado, mes_selecionado, dia_valido)
                lista_transacoes_mes.append(t)
                
    lista_transacoes_mes.sort(key=lambda x: x.data_exibicao if hasattr(x, 'data_exibicao') else x.data, reverse=True)
    
    if filtra_pessoa_id: 
        lista_transacoes_mes = [t for t in lista_transacoes_mes if str(t.dono.id) == str(filtra_pessoa_id)]

    meus_gastos = sum(t.valor for t in lista_transacoes_mes if eu and t.dono == eu and t.tipo == 'DESPESA')
    
    familiares = Pessoa.objects.filter(is_titular=False, conta=minha_conta)
    dividas_detalhadas = []
    
    for familiar in familiares:
        despesas_fam = [t for t in lista_transacoes_mes if t.dono == familiar and t.tipo == 'DESPESA']
        pagamentos_fam = [t for t in lista_transacoes_mes if t.dono == familiar and t.tipo == 'PAGAMENTO']
        saldo_pessoa = sum(t.valor for t in despesas_fam) - sum(t.valor for t in pagamentos_fam)
        
        if abs(saldo_pessoa) > 0.01:
            despesas_pendentes = []
            for desp in despesas_fam:
                pago_nesta_despesa = sum(float(p.valor) for p in pagamentos_fam if f"(Ref: {desp.descricao})" in p.descricao)
                if float(desp.valor) - pago_nesta_despesa > 0.01:
                    despesas_pendentes.append({'descricao': desp.descricao, 'valor_original': float(desp.valor), 'saldo': float(desp.valor) - pago_nesta_despesa})

            texto_zap = f"Olá {familiar.nome}! Fechamento de {mes_selecionado:02d}/{ano_selecionado}.\n\n"
            if despesas_pendentes:
                texto_zap += "*🛒 Seus Gastos:*\n"
                for desp in despesas_pendentes:
                    texto_zap += f"- {desp['descricao']}: R$ {desp['saldo']:.2f}".replace('.', ',') + (" (Restante)\n" if desp['saldo'] < desp['valor_original'] else "\n")
                texto_zap += "\n"
            if sum(t.valor for t in pagamentos_fam) > 0: texto_zap += f"*💸 Abatido:* R$ {sum(t.valor for t in pagamentos_fam):.2f}\n".replace('.', ',')
            texto_zap += f"*⚠️ Saldo Pendente: R$ {saldo_pessoa:.2f}*\n".replace('.', ',')

            url_base = f"https://wa.me/{''.join(filter(str.isdigit, familiar.telefone))}" if familiar.telefone else "https://wa.me/"
            if familiar.telefone and not url_base.endswith('55') and len(url_base) >= 23: url_base = url_base.replace('https://wa.me/', 'https://wa.me/55')
            
            dividas_detalhadas.append({
                'id': familiar.id, 'nome': familiar.nome, 'saldo': saldo_pessoa,
                'whatsapp_link': f"{url_base}?text={urllib.parse.quote(texto_zap)}", 'tem_telefone': bool(familiar.telefone), 'despesas': despesas_pendentes
            })

    meta = MetaMensal.objects.filter(conta=minha_conta).first()
    valor_meta = meta.valor if meta else 0
    saldo_restante = float(valor_meta) - float(meus_gastos)
    
    gastos_pessoais_dict = {}
    for t in lista_transacoes_mes:
        if eu and t.dono == eu and t.tipo == 'DESPESA':
            gastos_pessoais_dict[t.categoria or 'Outros'] = gastos_pessoais_dict.get(t.categoria or 'Outros', 0) + float(t.valor)
            
    maior_categoria = max(gastos_pessoais_dict, key=gastos_pessoais_dict.get) if gastos_pessoais_dict else "Nenhuma"
    valor_maior_cat = gastos_pessoais_dict.get(maior_categoria, 0)
    percentual_gasto = (float(meus_gastos) / float(valor_meta) * 100) if valor_meta > 0 else 0

    contexto = {
        'transacoes': lista_transacoes_mes, 'pessoas': todas_pessoas, 'cartoes': todos_cartoes, 
        'meus_gastos': meus_gastos, 'valor_meta': valor_meta, 'saldo_restante': saldo_restante, 
        'mes_atual': mes_selecionado, 'ano_atual': ano_selecionado, 'mes_anterior': mes_anterior, 'ano_anterior': ano_anterior, 
        'mes_proximo': mes_proximo, 'ano_proximo': ano_proximo, 'dividas_detalhadas': dividas_detalhadas, 
        'filtra_pessoa_id': int(filtra_pessoa_id) if filtra_pessoa_id else None,
        
        'labels_grafico': list(gastos_pessoais_dict.keys()), 
        'valores_grafico': list(gastos_pessoais_dict.values()),
        
        'maior_categoria': maior_categoria, 'valor_maior_cat': valor_maior_cat, 'percentual_gasto': percentual_gasto,
        'usuario': request.user
    }
    return render(request, 'gestao/dashboard.html', contexto)

@login_required(login_url='login')
def excluir_transacao(request, id):
    transacao = get_object_or_404(Transacao, id=id, conta=request.user.username)
    transacao.delete()
    return redirect(f"/?mes={request.GET.get('mes')}&ano={request.GET.get('ano')}") if request.GET.get('mes') else redirect('dashboard')


def calcular_mes_fatura(data_compra, dia_fechamento, dia_vencimento):
    """
    Calcula exatamente em qual mês a despesa vai ser cobrada,
    baseado nas regras de fechamento e vencimento de cartões no Brasil.
    """
    mes = data_compra.month
    ano = data_compra.year

    # 1. Se a compra foi feita no dia ou após o fechamento, ela cai no próximo ciclo
    if data_compra.day >= dia_fechamento:
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1

    # 2. Se o dia do vencimento é MENOR que o dia de fechamento (ex: Fecha dia 25, Vence dia 5)
    # Significa que a fatura só será paga no mês SEGUINTE ao ciclo atual.
    if dia_vencimento < dia_fechamento:
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1
            
    return mes, ano