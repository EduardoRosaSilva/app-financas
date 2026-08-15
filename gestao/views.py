from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Sum
from datetime import datetime, date
import calendar
import json
import urllib.parse
from .models import Transacao, Pessoa, MetaMensal, Cartao

def dashboard(request):
    if request.method == 'POST':
        tipo_formulario = request.POST.get('form_type')
        
        # 1. CADASTRAR NOVA TRANSAÇÃO (RECORRÊNCIA + SPLIT DINÂMICO MÚLTIPLO)
        if tipo_formulario == 'nova_transacao':
            descricao_digitada = request.POST.get('descricao')
            
            valor_str = request.POST.get('valor', '0')
            valor_digitado = float(valor_str.replace(',', '.')) if valor_str else 0.0
            
            dono_id_escolhido = request.POST.get('dono_id')
            tipo_escolhido = request.POST.get('tipo')
            data_digitada = request.POST.get('data') 
            cartao_id_escolhido = request.POST.get('cartao_id')
            
            parcelas = int(request.POST.get('parcelas', 1))
            tipo_repeticao = request.POST.get('tipo_repeticao', 'dividir')
            
            # Recebendo a lista dinâmica de amigos na divisão
            donos_extras = request.POST.getlist('dono_id_extra')
            valores_extras_str = request.POST.getlist('valor_extra')
            
            # Segurança do Titular e Cartão atrelados ao UUID
            dono_obj = get_object_or_404(Pessoa, id=dono_id_escolhido, conta=request.conta)
            cartao_obj = None
            if cartao_id_escolhido:
                cartao_obj = get_object_or_404(Cartao, id=cartao_id_escolhido, conta=request.conta)
            
            data_base = datetime.strptime(data_digitada, '%Y-%m-%d').date()
            
            # Matemática da Divisão vs Repetição para múltiplos valores
            if parcelas > 1 and tipo_repeticao == 'dividir':
                valor_final_parcela = round(valor_digitado / parcelas, 2)
                valores_finais_extras = [
                    round(float(v.replace(',', '.')) / parcelas, 2) if v else 0.0 
                    for v in valores_extras_str
                ]
            else:
                valor_final_parcela = valor_digitado
                valores_finais_extras = [
                    float(v.replace(',', '.')) if v else 0.0 
                    for v in valores_extras_str
                ]
            
            # Loop do Piloto Automático
            for i in range(parcelas):
                mes_atual = data_base.month + i
                ano_atual = data_base.year + ((mes_atual - 1) // 12)
                mes_calculado = ((mes_atual - 1) % 12) + 1
                
                ultimo_dia_mes = calendar.monthrange(ano_atual, mes_calculado)[1]
                dia_calculado = min(data_base.day, ultimo_dia_mes)
                nova_data = date(ano_atual, mes_calculado, dia_calculado)
                
                desc_salvar = descricao_digitada
                if parcelas > 1:
                    desc_salvar = f"{descricao_digitada} ({i+1}/{parcelas})"
                
                # O Titular paga o total menos a soma da parte de todos os amigos
                soma_extras = sum(valores_finais_extras)
                valor_dono_1 = valor_final_parcela - soma_extras
                
                # Salva a parte principal do Titular
                Transacao.objects.create(
                    descricao=desc_salvar,
                    valor=valor_dono_1,
                    dono=dono_obj,
                    tipo=tipo_escolhido,
                    data=nova_data,
                    cartao=cartao_obj,
                    conta=request.conta 
                )
                
                # Salva a parte de CADA amigo adicionado no split
                for idx, dono_extra_id in enumerate(donos_extras):
                    valor_deste_amigo = valores_finais_extras[idx]
                    
                    if dono_extra_id and valor_deste_amigo > 0:
                        d_obj = get_object_or_404(Pessoa, id=dono_extra_id, conta=request.conta)
                        Transacao.objects.create(
                            descricao=f"{desc_salvar} (Parte de {d_obj.nome})",
                            valor=valor_deste_amigo,
                            dono=d_obj,
                            tipo=tipo_escolhido,
                            data=nova_data,
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
                
        # 3. CADASTRAR NOVA PESSOA
        elif tipo_formulario == 'nova_pessoa':
            nome_digitado = request.POST.get('nome')
            ja_tem_titular = Pessoa.objects.filter(conta=request.conta, is_titular=True).exists()
            Pessoa.objects.create(
                nome=nome_digitado, 
                is_titular=not ja_tem_titular, 
                conta=request.conta
            )

        # 4. CADASTRAR NOVO CARTÃO
        elif tipo_formulario == 'novo_cartao':
            nome_cartao = request.POST.get('nome')
            Cartao.objects.create(nome=nome_cartao, conta=request.conta)
                
        mes = request.GET.get('mes')
        ano = request.GET.get('ano')
        if mes and ano:
            return redirect(f'/?mes={mes}&ano={ano}')
        return redirect('dashboard')

    # ==========================================
    # CARREGAMENTO (MÉTODO GET)
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

def excluir_transacao(request, id):
    transacao = get_object_or_404(Transacao, id=id, conta=request.conta)
    transacao.delete()
    mes = request.GET.get('mes')
    ano = request.GET.get('ano')
    if mes and ano:
        return redirect(f'/?mes={mes}&ano={ano}')
    return redirect('dashboard')