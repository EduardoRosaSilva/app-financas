from django.db import connection


class PostgresRLSMiddleware:
    """Define a variável de sessão 'app.current_tenant' no PostgreSQL para isolamento via RLS."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if connection.vendor == 'postgresql':
            tenant = (
                request.user.username
                if getattr(request, 'user', None) and request.user.is_authenticated
                else ''
            )
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.current_tenant', %s, false);", [tenant])

        response = self.get_response(request)

        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.current_tenant', '', false);")

        return response