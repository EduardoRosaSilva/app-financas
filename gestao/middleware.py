from gestao.models import Conta

class ContaInvisivelMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        conta_id = request.COOKIES.get('finpro_conta_id')
        conta = None

        if conta_id:
            try:
                conta = Conta.objects.get(id=conta_id)
            except Conta.DoesNotExist:
                conta = None

        if not conta:
            conta = Conta.objects.create()

        request.conta = conta
        response = self.get_response(request)

        if not conta_id or conta_id != str(conta.id):
            response.set_cookie(
                'finpro_conta_id',
                str(conta.id),
                max_age=10 * 365 * 24 * 60 * 60,
                httponly=True,
                samesite='Lax'
            )

        return response