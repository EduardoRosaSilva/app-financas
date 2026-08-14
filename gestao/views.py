from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Sum
from datetime import date
import json
import urllib.parse
from .models import Transacao, Pessoa, MetaMensal, Cartao # <-- Importamos o Cartao aqui

def dashboard(request):
    if request.method == 'POST':
        tipo_formulario = request.POST.get('form_type')
        
        if tipo_formulario == 'nova_transacao':
            descricao_digitada = request.POST.get('descricao')
            valor_digitado = request.POST.get('valor')
            dono_id_escolhido = request.POST.get('dono_id')
            tipo_escolhido = request.POST.get('tipo')
            data_digitada = request.POST.get('data') 
            
            # Capturamos o cartão escolhido
            cartao_id_escolhido = request.POST.get('cartao_id')
            
            dono_obj = Pessoa.objects.get(id=dono_id_escolhido)
            cartao_obj = Cartao.objects.get(id=cartao_id_escolhido) if cartao_id_escolhido else None
            
            Transacao.objects.create(
                descricao=descricao_digitada,
                valor=valor_digitado,
                dono=dono_obj,
                tipo=tipo_escolhido,
                data=data_digitada,
                cartao=cartao_obj # Salvamos o cartão vinculado
            )
            
        elif tipo_formulario == 'atualizar_meta':
            novo_valor = request.POST.get('valor_meta')
            meta = MetaMensal.objects.first()
            
            if meta:
                meta.valor = novo_valor
                meta.save()
            else:
                MetaMensal.objects.create(valor=novo_valor)
                
        mes = request.GET.get('mes')
        ano = request.GET.get('ano')
        if mes and ano:
            return redirect(f'/?mes={mes}&ano={ano}')
        return redirect('dashboard')

    # 2. SE O USUÁRIO APENAS ABRIU A PÁGINA (Método GET)
    todas_pessoas = Pessoa.objects.all()
    todos_cartoes = Cartao.objects.all() # Buscamos os cartões cadastrados
    eu = Pessoa.objects.filter(is_titular=True).first()
    
    hoje = date.today()
    mes_selecionado = int(request.GET.get('mes', hoje.month))
    ano_selecionado = int(request.GET.get('ano', hoje.year))
    
    filtra_pessoa_id = request.GET.get('pessoa')
    
    mes_anterior = mes_selecionado - 1 if mes_selecionado > 1 else 12
    ano_anterior = ano_selecionado if mes_selecionado > 1 else ano_selecionado - 1
    mes_proximo = mes_selecionado + 1 if mes_selecionado < 12 else 1
    ano_proximo = ano_selecionado if mes_selecionado < 12 else ano_selecionado + 1
    
    todas_transacoes = Transacao.objects.filter(data__month=mes_selecionado, data__year=ano_selecionado)
    
    if filtra_pessoa_id:
        todas_transacoes = todas_transacoes.filter(dono_id=filtra_pessoa_id)
        
    todas_transacoes = todas_transacoes.order_by('-data')
    
    meus_gastos = Transacao.objects.filter(dono=eu, tipo='DESPESA', data__month=mes_selecionado, data__year=ano_selecionado).aggregate(total=Sum('valor'))['total'] or 0
    gastos_terceiros = Transacao.objects.filter(dono__is_titular=False, tipo='DESPESA', data__month=mes_selecionado, data__year=ano_selecionado).aggregate(total=Sum('valor'))['total'] or 0
    pagamentos_terceiros = Transacao.objects.filter(dono__is_titular=False, tipo='PAGAMENTO', data__month=mes_selecionado, data__year=ano_selecionado).aggregate(total=Sum('valor'))['total'] or 0
    
    divida_real_familia = gastos_terceiros - pagamentos_terceiros
    
    dividas_detalhadas = []
    familiares = Pessoa.objects.filter(is_titular=False)
    
    for familiar in familiares:
        gasto_fam = Transacao.objects.filter(dono=familiar, tipo='DESPESA', data__month=mes_selecionado, data__year=ano_selecionado).aggregate(total=Sum('valor'))['total'] or 0
        pago_fam = Transacao.objects.filter(dono=familiar, tipo='PAGAMENTO', data__month=mes_selecionado, data__year=ano_selecionado).aggregate(total=Sum('valor'))['total'] or 0
        
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
    
    meta = MetaMensal.objects.first()
    valor_meta = meta.valor if meta else 0
    saldo_restante = valor_meta - meus_gastos
    
    gastos_grafico = Transacao.objects.filter(
        dono=eu, tipo='DESPESA', data__month=mes_selecionado, data__year=ano_selecionado
    ).values('descricao').annotate(total=Sum('valor'))
    
    labels_grafico = [gasto['descricao'] for gasto in gastos_grafico]
    valores_grafico = [float(gasto['total']) for gasto in gastos_grafico]
    
    contexto = {
        'transacoes': todas_transacoes,
        'pessoas': todas_pessoas, 
        'cartoes': todos_cartoes, # Mandamos os cartões para o HTML
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

def excluir_transacao(request, id):
    transacao = get_object_or_404(Transacao, id=id)
    transacao.delete()
    
    mes = request.GET.get('mes')
    ano = request.GET.get('ano')
    if mes and ano:
        return redirect(f'/?mes={mes}&ano={ano}')
    return redirect('dashboard')