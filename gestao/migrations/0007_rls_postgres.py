from django.db import migrations


def ativar_rls_postgres(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return

    tabelas = [
        'gestao_pessoa',
        'gestao_cartao',
        'gestao_transacao',
        'gestao_metamensal',
    ]

    with schema_editor.connection.cursor() as cursor:
        for tabela in tabelas:
            cursor.execute(f"ALTER TABLE {tabela} ENABLE ROW LEVEL SECURITY;")
            cursor.execute(f"DROP POLICY IF EXISTS isolamento_{tabela} ON {tabela};")
            cursor.execute(f"""
                CREATE POLICY isolamento_{tabela} ON {tabela}
                FOR ALL
                USING (
                    current_setting('app.current_tenant', true) IS NULL
                    OR current_setting('app.current_tenant', true) = ''
                    OR lower(conta) = lower(current_setting('app.current_tenant', true))
                )
                WITH CHECK (
                    current_setting('app.current_tenant', true) IS NULL
                    OR current_setting('app.current_tenant', true) = ''
                    OR lower(conta) = lower(current_setting('app.current_tenant', true))
                );
            """)


def desativar_rls_postgres(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for tabela in ('gestao_pessoa', 'gestao_cartao', 'gestao_transacao', 'gestao_metamensal'):
        schema_editor.execute(f"ALTER TABLE {tabela} DISABLE ROW LEVEL SECURITY;")


class Migration(migrations.Migration):

    dependencies = [
        ('gestao', '0006_delete_conta_alter_metamensal_conta_and_more'),
    ]

    operations = [
        migrations.RunPython(ativar_rls_postgres, desativar_rls_postgres),
    ]