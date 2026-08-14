from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Sum
from datetime import date
import json
import urllib.parse
from .models import Transacao, Pessoa, MetaMensal, Cartao

def dashboard(request):
    if request.method == 'POST':
        tipo_formulario = request.POST.get('form_type')
        
        # 1. CADASTRAR NOVA TRANSAÇÃO
        if tipo_formulario == 'nova_transacao':
            descricao_digitada = request.POST.get('descricao')
            valor_digitado = request.POST.get('valor')
            dono_id_escolhido = request.POST.get('dono_id')
            tipo_escolhido = request.POST.get('tipo')
            data_digitada = request.POST.get('data') 
            cartao_id_escolhido = request.POST.get('cartao_id')
            
            dono_obj = get_object_or_404(Pessoa, id=dono_id_escolhido, conta=request.conta)
            
            cartao_obj = None
            if cartao_id_escolhido:
                cartao_obj = get_object_or_404(Cartao, id=cartao_id_escolhido, conta=request.conta)
            
            Transacao.objects.create(
                descricao=descricao_digitada,
                valor=valor_digitado,
                dono=dono_obj,
                tipo=tipo_escolhido,
                data=data_digitada,
                cartao=cartao_obj,
                conta=request.conta 
            )
            
        # 2. ATUALIZAR META
        elif tipo_formulario == 'atualizar_meta':
            novo_valor = request.POST.get('valor_meta')
            meta = MetaMensal.objects.filter(conta=request.conta).first()
            
            if meta:
                meta.valor = novo_valor
                meta.save()
            else:
                MetaMensal.objects.create(valor=novo_valor, conta=request.conta)
                
        # 3. CADASTRAR NOVA PESSOA (Caso seu formulário envie isso para a dashboard)
        elif tipo_formulario == 'nova_pessoa':
            nome_digitado = request.POST.get('nome')
            # Se for a primeira pessoa a ser cadastrada, define como titular automaticamente
            ja_tem_titular = Pessoa.objects.filter(conta=request.conta, is_titular=True).exists()
            Pessoa.objects.create(
                nome=nome_digitado, 
                is_titular=not ja_tem_titular, 
                conta=request.conta
            )

        # 4. CADASTRAR NOVO CARTÃO (Caso seu formulário envie isso para a dashboard)
        elif tipo_formulario == 'novo_cartao':
            nome_cartao = request.POST.get('nome')
            Cartao.objects.create(nome=nome_cartao, conta=request.conta)
                
        mes = request.GET.get('mes')
        ano = request.GET.get('ano')
        if mes and ano:
            return redirect(f'/?mes={mes}&ano={ano}')
        return redirect('dashboard')

    # ==========================================
    # CARREGAMENTO DA TELA COM FILTROS DA CONTA
    # ==========================================
    todas_pessoas = Pessoa.objects.filter(conta=request.conta)
    todos_cartoes = Cartao.objects.filter(conta=request.conta) 
    
    eu = Pessoa.objects.filter(is_titular=True, conta=request.conta).first()
    
    hoje = date.today()
    mes_selecionado = int(request.GET.get('mes', hoje.month))
    ano_selecionado = int(request.GET.get('ano', hoje.year))
    
    filtra_pessoa_id = request.GET.get('pessoa')
    
    mes_anterior = mes_selecionado - 1 if mes_selecionado > 1 else 12
    ano_anterior = ano_selecionado if mes_selecionado > 1 else ano_selecionado - 1
    mes_proximo = mes_selecionado + 1 if mes_selecionado < 12 else 1
    ano_proximo = ano_selecionado if mes_selecionado < 12 else ano_selecionado + 1
    
    todas_transacoes = Transacao.objects.filter(conta=request.conta, data__month=mes_selecionado, data__year=ano_selecionado)
    
    if filtra_pessoa_id:
        todas_transacoes = todas_transacoes.filter(dono_id=filtra_pessoa_id)
        
    todas_transacoes = todas_transacoes.order_by('-data')
    
    meus_gastos = 0
    if eu:
        meus_gastos = Transacao.objects.filter(conta=request.conta, dono=eu, tipo='DESPESA', data__month=mes_selecionado, data__year=ano_selecionado).aggregate(total=Sum('valor'))['total'] or 0
    
    gastos_terceiros = Transacao.objects.filter(conta=request.conta, dono__is_titular=False, tipo='DESPESA', data__month=mes_selecionado, data__year=ano_selecionado).aggregate(total=Sum('valor'))['total'] or 0
    pagamentos_terceiros = Transacao.objects.filter(conta=request.conta, dono__is_titular=False, tipo='PAGAMENTO', data__month=mes_selecionado, data__year=ano_selecionado).aggregate(total=Sum('valor'))['total'] or 0
    
    divida_real_familia = gastos_terceiros - pagamentos_terceiros
    
    dividas_detalhadas = []
    familiares = Pessoa.objects.filter(is_titular=False, conta=request.conta)
    
    for familiar in familiares:
        gasto_fam = Transacao.objects.filter(conta=request.conta, dono=familiar, tipo='DESPESA', data__month=mes_selecionado, data__year=ano_selecionado).aggregate(total=Sum('valor'))['total'] or 0
        pago_fam = Transacao.objects.filter(conta=request.conta, dono=familiar, tipo='PAGAMENTO', data__month=mes_selecionado, data__year=ano_selecionado).aggregate(total=Sum('valor'))['total'] or 0
        
        saldo_pessoa = gasto_fam - pago_fam
        
        if saldo_pessoa != 0:
            texto_zap = f"Olá {familiar.nome}! Passando para fechar as contas de {mes_selecionado}/{ano_selecionado}. O saldo pendente no cartão ficou em R$ {saldo_pessoa:.2f}. Segue o acerto quando puder!"
            link_zap = f"https://wa.me/?text={urllib.parse.quote(texto_zap)}"
            
            dividas_detalhadas.append({
                'id': familiar.id,
                'nome': familiar.nome, 
                'saldo': saldo_pessoa,
                'whatsapp_link': link_zap
            })
    
    meta = MetaMensal.objects.filter(conta=request.conta).first()
    valor_meta = meta.valor if meta else 0
    saldo_restante = valor_meta - meus_gastos
    
    gastos_grafico = []
    labels_grafico = []
    valores_grafico = []
    
    if eu:
        gastos_grafico = Transacao.objects.filter(
            conta=request.conta, dono=eu, tipo='DESPESA', data__month=mes_selecionado, data__year=ano_selecionado
        ).values('descricao').annotate(total=Sum('valor'))
        
        labels_grafico = [gasto['descricao'] for gasto in gastos_grafico]
        valores_grafico = [float(gasto['total']) for gasto in gastos_grafico]
    
    contexto = {
        'transacoes': todas_transacoes,
        'pessoas': todas_pessoas, 
        'cartoes': todos_cartoes, 
        'meus_gastos': meus_gastos,
        'gastos_terceiros': divida_real_familia, 
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
        'labels_grafico': json.dumps(labels_grafico),
        'valores_grafico': json.dumps(valores_grafico),
    }
    
    return render(request, 'gestao/dashboard.html', contexto)


# ==========================================
# VIEWS SECUNDÁRIAS BLINDADAS
# ==========================================

def excluir_transacao(request, id):
    transacao = get_object_or_404(Transacao, id=id, conta=request.conta)
    transacao.delete()
    
    mes = request.GET.get('mes')
    ano = request.GET.get('ano')
    if mes and ano:
        return redirect(f'/?mes={mes}&ano={ano}')
    return redirect('dashboard')

def adicionar_pessoa(request):
    """View caso você use uma URL separada para adicionar pessoas"""
    if request.method == 'POST':
        nome = request.POST.get('nome')
        ja_tem_titular = Pessoa.objects.filter(conta=request.conta, is_titular=True).exists()
        Pessoa.objects.create(nome=nome, is_titular=not ja_tem_titular, conta=request.conta)
        return redirect('dashboard')
    return render(request, 'gestao/adicionar_pessoa.html')

def adicionar_cartao(request):
    """View caso você use uma URL separada para adicionar cartões"""
    if request.method == 'POST':
        nome = request.POST.get('nome')
        Cartao.objects.create(nome=nome, conta=request.conta)
        return redirect('dashboard')
    return render(request, 'gestao/adicionar_cartao.html')