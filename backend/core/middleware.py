from gestao.models import Conta

class ContaInvisivelMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 1. Tenta buscar o ID da conta no navegador do usuário
        conta_id = request.COOKIES.get('finpro_conta_id')
        conta = None

        if conta_id:
            try:
                conta = Conta.objects.get(id=conta_id)
            except Conta.DoesNotExist:
                conta = None

        # 2. Se for o primeiro acesso, cria uma nova conta no banco
        if not conta:
            conta = Conta.objects.create()

        # 3. Pendura a conta na requisição (para usarmos nas views depois)
        request.conta = conta

        # Processa a página normal do site
        response = self.get_response(request)

        # 4. Crava o cookie criptografado no celular do usuário por 10 anos
        if not conta_id or conta_id != str(conta.id):
            response.set_cookie(
                'finpro_conta_id',
                str(conta.id),
                max_age=10 * 365 * 24 * 60 * 60, # 10 anos
                httponly=True, # Bloqueia ataques XSS (JavaScript malicioso)
                samesite='Lax'
            )

        return response