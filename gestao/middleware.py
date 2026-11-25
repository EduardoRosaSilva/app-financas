from django.db import connection


class PostgresRLSMiddleware:
    """Define 'app.current_tenant' no PostgreSQL e bloqueia cache de rotas autenticadas."""
    ROTAS_SEM_BANCO = ('/health/', '/robots.txt', '/sitemap.xml', '/sw.js')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path in self.ROTAS_SEM_BANCO:
            return self.get_response(request)

        autenticado = getattr(request, 'user', None) and request.user.is_authenticated
        if connection.vendor == 'postgresql' and autenticado:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.current_tenant', %s, false);", [request.user.username])

        response = self.get_response(request)

        if connection.vendor == 'postgresql' and autenticado:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.current_tenant', '', false);")

        autenticado_depois = getattr(request, 'user', None) and request.user.is_authenticated
        if autenticado or autenticado_depois:
            response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0, private'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'

        return response