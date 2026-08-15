from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Sum
from datetime import datetime, date
import calendar
import json
import urllib.parse

from .models import Transacao, Pessoa, MetaMensal, Cartao

# Importações de Segurança
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout
from django.contrib import messages
from .forms import RegistroUsuarioForm, LoginUsuarioForm

# ==========================================
# TELAS DE AUTENTICAÇÃO (LOGIN / REGISTRO)
# ==========================================
def registro_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        form = RegistroUsuarioForm(request.POST)
        if form.is_valid():
            usuario = form.save()
            login(request, usuario)
            return redirect('dashboard')
    else:
        form = RegistroUsuarioForm()
    return render(request, 'gestao/register.html', {'form': form})

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
# DASHBOARD PRINCIPAL
# ==========================================
@login_required(login_url='login')
def dashboard(request):
    minha_conta = request.user.username
    
    hoje = date.today()
    mes_selecionado = int(request.GET.get('mes', hoje.month))
    ano_selecionado = int(request.GET.get('ano', hoje.year))

    # ------------------------------------------
    # PROCESSAMENTO DE FORMULÁRIOS (POST)
    # ------------------------------------------
    if request.method == 'POST':
        tipo_formulario = request.POST.get('form_type')
        
        # --- 1. NOVA TRANSAÇÃO ---
        if tipo_formulario == 'nova_transacao':
            descricao_digitada = request.POST.get('descricao')
            valor_str = request.POST.get('valor', '0')
            valor_digitado = float(valor_str.replace(',', '.')) if valor_str else 0.0
            
            dono_id_escolhido = request.POST.get('dono_id')
            tipo_escolhido = request.POST.get('tipo')
            data_digitada = request.POST.get('data')
            cartao_id_escolhido = request.POST.get('cartao_id')
            
            donos_extras = request.POST.getlist('dono_id_extra')
            valores_extras_str = request.POST.getlist('valor_extra')
            
            dono_obj = get_object_or_404(Pessoa, id=dono_id_escolhido, conta=minha_conta)
            cartao_obj = None
            if cartao_id_escolhido:
                cartao_obj = get_object_or_404(Cartao, id=cartao_id_escolhido, conta=minha_conta)
            
            data_base = datetime.strptime(data_digitada, '%Y-%m-%d').date()
            
            valores_finais_extras = [float(v.replace(',', '.')) if v else 0.0 for v in valores_extras_str]
            soma_extras = sum(valores_finais_extras)
            valor_dono_1 = valor_digitado - soma_extras
            
            Transacao.objects.create(
                descricao=descricao_digitada, 
                valor=valor_dono_1, 
                dono=dono_obj, 
                tipo=tipo_escolhido, 
                data=data_base, 
                cartao=cartao_obj, 
                conta=minha_conta
            )
            
            for idx, dono_extra_id in enumerate(donos_extras):
                valor_deste_amigo = valores_finais_extras[idx]
                if dono_extra_id and valor_deste_amigo > 0:
                    d_obj = get_object_or_404(Pessoa, id=dono_extra_id, conta=minha_conta)
                    Transacao.objects.create(
                        descricao=f"{descricao_digitada} (Parte de {d_obj.nome})", 
                        valor=valor_deste_amigo, 
                        dono=d_obj, 
                        tipo=tipo_escolhido, 
                        data=data_base, 
                        cartao=cartao_obj, 
                        conta=minha_conta
                    )
        
        # --- 2. EDITAR TRANSAÇÃO ---
        elif tipo_formulario == 'editar_transacao':
            t_id = request.POST.get('transacao_id')
            transacao = get_object_or_404(Transacao, id=t_id, conta=minha_conta)
            
            transacao.descricao = request.POST.get('descricao')
            transacao.valor = float(request.POST.get('valor').replace(',', '.'))
            transacao.data = request.POST.get('data')
            transacao.tipo = request.POST.get('tipo')
            
            dono_id = request.POST.get('dono_id')
            transacao.dono = get_object_or_404(Pessoa, id=dono_id, conta=minha_conta)
            
            cartao_id = request.POST.get('cartao_id')
            if cartao_id:
                transacao.cartao = get_object_or_404(Cartao, id=cartao_id, conta=minha_conta)
            else:
                transacao.cartao = None
                
            transacao.save()
        
        # --- 3. ATUALIZAR META ---
        elif tipo_formulario == 'atualizar_meta':
            novo_valor = request.POST.get('valor_meta')
            meta = MetaMensal.objects.filter(conta=minha_conta).first()
            if meta:
                meta.valor = novo_valor
                meta.save()
            else:
                MetaMensal.objects.create(valor=novo_valor, conta=minha_conta)
        
        # --- 4. NOVA PESSOA (Com WhatsApp) ---
        elif tipo_formulario == 'nova_pessoa':
            nome_digitado = request.POST.get('nome')
            telefone_digitado = request.POST.get('telefone', '')
            ja_tem_titular = Pessoa.objects.filter(conta=minha_conta, is_titular=True).exists()
            
            Pessoa.objects.create(
                nome=nome_digitado, 
                telefone=telefone_digitado, 
                is_titular=not ja_tem_titular, 
                conta=minha_conta
            )

        # --- 5. NOVO CARTÃO ---
        elif tipo_formulario == 'novo_cartao':
            nome_cartao = request.POST.get('nome')
            Cartao.objects.create(nome=nome_cartao, conta=minha_conta)
        
        # Recarrega a página mantendo o mês visualizado
        return redirect(f'/?mes={mes_selecionado}&ano={ano_selecionado}')

    # ------------------------------------------
    # MONTAGEM DOS DADOS DA TELA (GET)
    # ------------------------------------------
    todas_pessoas = Pessoa.objects.filter(conta=minha_conta)
    todos_cartoes = Cartao.objects.filter(conta=minha_conta)
    eu = Pessoa.objects.filter(is_titular=True, conta=minha_conta).first()
    
    filtra_pessoa_id = request.GET.get('pessoa')
    
    mes_anterior = mes_selecionado - 1 if mes_selecionado > 1 else 12
    ano_anterior = ano_selecionado if mes_selecionado > 1 else ano_selecionado - 1
    mes_proximo = mes_selecionado + 1 if mes_selecionado < 12 else 1
    ano_proximo = ano_selecionado if mes_selecionado < 12 else ano_selecionado + 1
    
    todas_transacoes = Transacao.objects.filter(conta=minha_conta, data__month=mes_selecionado, data__year=ano_selecionado).order_by('-data')
    
    meus_gastos = 0
    if eu:
        meus_gastos = Transacao.objects.filter(conta=minha_conta, dono=eu, tipo='DESPESA', data__month=mes_selecionado, data__year=ano_selecionado).aggregate(total=Sum('valor'))['total'] or 0
    
    gastos_terceiros = Transacao.objects.filter(conta=minha_conta, dono__is_titular=False, tipo='DESPESA', data__month=mes_selecionado, data__year=ano_selecionado).aggregate(total=Sum('valor'))['total'] or 0
    pagamentos_terceiros = Transacao.objects.filter(conta=minha_conta, dono__is_titular=False, tipo='PAGAMENTO', data__month=mes_selecionado, data__year=ano_selecionado).aggregate(total=Sum('valor'))['total'] or 0
    
    divida_real_familia = gastos_terceiros - pagamentos_terceiros
    dividas_detalhadas = []
    familiares = Pessoa.objects.filter(is_titular=False, conta=minha_conta)
    
    for familiar in familiares:
        despesas_fam = Transacao.objects.filter(conta=minha_conta, dono=familiar, tipo='DESPESA', data__month=mes_selecionado, data__year=ano_selecionado)
        pagamentos_fam = Transacao.objects.filter(conta=minha_conta, dono=familiar, tipo='PAGAMENTO', data__month=mes_selecionado, data__year=ano_selecionado)
        
        gasto_fam = despesas_fam.aggregate(total=Sum('valor'))['total'] or 0
        pago_fam = pagamentos_fam.aggregate(total=Sum('valor'))['total'] or 0
        saldo_pessoa = gasto_fam - pago_fam
        
        if saldo_pessoa != 0:
            texto_zap = f"Olá {familiar.nome}! Passando para fechar as contas de {mes_selecionado}/{ano_selecionado}.\n\n"
            
            if despesas_fam.exists():
                texto_zap += "*🛒 Seus Gastos:*\n"
                for desp in despesas_fam:
                    valor_formatado = f"{desp.valor:.2f}".replace('.', ',')
                    texto_zap += f"- {desp.descricao}: R$ {valor_formatado}\n"
                texto_zap += "\n"
            
            if pago_fam > 0:
                pago_formatado = f"{pago_fam:.2f}".replace('.', ',')
                texto_zap += f"*💸 Já pago/abatido:* R$ {pago_formatado}\n"
                
            saldo_formatado = f"{saldo_pessoa:.2f}".replace('.', ',')
            texto_zap += f"*⚖️ Saldo Pendente: R$ {saldo_formatado}*\n\n"
            texto_zap += "Qualquer dúvida, só me falar!"

            url_base = "https://wa.me/"
            if familiar.telefone:
                num_limpo = ''.join(filter(str.isdigit, familiar.telefone))
                if not num_limpo.startswith('55') and len(num_limpo) >= 10:
                    num_limpo = f"55{num_limpo}"
                url_base = f"https://wa.me/{num_limpo}"
            
            link_zap = f"{url_base}?text={urllib.parse.quote(texto_zap)}"
            
            dividas_detalhadas.append({
                'id': familiar.id, 'nome': familiar.nome, 'saldo': saldo_pessoa,
                'whatsapp_link': link_zap,
                'tem_telefone': bool(familiar.telefone)
            })

    meta = MetaMensal.objects.filter(conta=minha_conta).first()
    valor_meta = meta.valor if meta else 0
    saldo_restante = float(valor_meta) - float(meus_gastos)
    
    gastos_grafico = []
    labels_grafico, valores_grafico = [], []
    if eu:
        gastos_grafico = Transacao.objects.filter(conta=minha_conta, dono=eu, tipo='DESPESA', data__month=mes_selecionado, data__year=ano_selecionado).values('descricao').annotate(total=Sum('valor'))
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
        'usuario': request.user
    }
    return render(request, 'gestao/dashboard.html', contexto)

# ==========================================
# EXCLUIR TRANSAÇÃO
# ==========================================
@login_required(login_url='login')
def excluir_transacao(request, id):
    transacao = get_object_or_404(Transacao, id=id, conta=request.user.username)
    transacao.delete()
    mes = request.GET.get('mes')
    ano = request.GET.get('ano')
    if mes and ano:
        return redirect(f'/?mes={mes}&ano={ano}')
    return redirect('dashboard')