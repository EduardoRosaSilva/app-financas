import html as html_lib


def _montar_layout_email_html(
    *,
    titulo_aba: str,
    selo_texto: str,
    selo_bg: str,
    selo_cor: str,
    titulo_principal: str,
    saudacao: str,
    paragrafo_intro: str,
    texto_botao: str,
    url_botao: str,
    cor_botao: str,
    texto_validade: str,
    detalhes_seguranca: list[tuple[str, str]] | None = None,
    aviso_rodape: str = "",
    logo_url: str = "https://finpro.app.br/static/icone.png",
) -> str:
    titulo_aba_esc = html_lib.escape(titulo_aba)
    selo_esc = html_lib.escape(selo_texto)
    titulo_esc = html_lib.escape(titulo_principal)
    saudacao_esc = html_lib.escape(saudacao)
    intro_esc = html_lib.escape(paragrafo_intro)
    botao_esc = html_lib.escape(texto_botao)
    url_esc = html_lib.escape(url_botao, quote=True)
    validade_esc = html_lib.escape(texto_validade)
    aviso_esc = html_lib.escape(aviso_rodape)
    logo_esc = html_lib.escape(logo_url, quote=True)

    bloco_detalhes = ""
    if detalhes_seguranca:
        linhas = []
        for rotulo, valor in detalhes_seguranca:
            r_esc = html_lib.escape(rotulo)
            v_esc = html_lib.escape(valor)
            linhas.append(
                f'<tr>'
                f'<td style="padding:4px 0;color:#64748b;font-size:13px;width:130px;vertical-align:top;">{r_esc}</td>'
                f'<td style="padding:4px 0;color:#0f172a;font-size:13px;font-weight:600;vertical-align:top;">{v_esc}</td>'
                f'</tr>'
            )
        bloco_detalhes = f"""
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:20px 0;background-color:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;">
            <tr>
                <td style="padding:14px 16px;">
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                        {''.join(linhas)}
                    </table>
                </td>
            </tr>
        </table>
        """

    bloco_botao = ""
    if texto_botao and url_botao:
        bloco_botao = f"""
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:24px 0 20px 0;">
            <tr>
                <td align="center">
                    <a href="{url_esc}" target="_blank" rel="noopener noreferrer"
                       style="display:inline-block;background-color:{cor_botao};color:#ffffff;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:15px;font-weight:600;text-decoration:none;padding:13px 28px;border-radius:8px;letter-spacing:-0.01em;">
                        {botao_esc}
                    </a>
                </td>
            </tr>
        </table>
        <p style="margin:0 0 18px 0;font-size:12px;line-height:1.5;color:#64748b;">
            Se o botão acima não abrir, copie e cole o endereço abaixo no navegador:<br>
            <a href="{url_esc}" style="color:#1d4ed8;word-break:break-all;text-decoration:underline;">{url_esc}</a>
        </p>
        """

    return f"""<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{titulo_aba_esc}</title>
</head>
<body style="margin:0;padding:0;background-color:#f4f6f9;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0f172a;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f6f9;padding:32px 14px;">
        <tr>
            <td align="center">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background-color:#ffffff;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;">
                    <!-- Cabeçalho corporativo -->
                    <tr>
                        <td style="padding:22px 28px;border-bottom:1px solid #e2e8f0;background-color:#ffffff;">
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                                <tr>
                                    <td style="vertical-align:middle;">
                                        <img src="{logo_esc}" alt="FinPro" width="28" height="28" style="vertical-align:middle;border-radius:6px;margin-right:8px;">
                                        <span style="font-size:17px;font-weight:700;color:#0f172a;letter-spacing:-0.02em;vertical-align:middle;">FinPro</span>
                                    </td>
                                    <td align="right" style="vertical-align:middle;">
                                        <span style="display:inline-block;background-color:{selo_bg};color:{selo_cor};font-size:11px;font-weight:700;padding:4px 10px;border-radius:6px;letter-spacing:0.04em;text-transform:uppercase;">
                                            {selo_esc}
                                        </span>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <!-- Conteúdo principal -->
                    <tr>
                        <td style="padding:28px;">
                            <h1 style="margin:0 0 12px 0;font-size:20px;line-height:1.3;font-weight:700;color:#0f172a;letter-spacing:-0.02em;">
                                {titulo_esc}
                            </h1>
                            <p style="margin:0 0 12px 0;font-size:15px;line-height:1.6;color:#334155;">
                                {saudacao_esc}
                            </p>
                            <p style="margin:0 0 8px 0;font-size:15px;line-height:1.6;color:#334155;">
                                {intro_esc}
                            </p>

                            {bloco_detalhes}
                            {bloco_botao}

                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:16px;border-top:1px solid #e2e8f0;">
                                <tr>
                                    <td style="padding-top:16px;font-size:13px;line-height:1.55;color:#475569;">
                                        <strong style="color:#0f172a;">Segurança:</strong> {validade_esc}
                                        {f'<br>{aviso_esc}' if aviso_esc else ''}
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <!-- Rodapé institucional -->
                    <tr>
                        <td style="padding:18px 28px;background-color:#f8fafc;border-top:1px solid #e2e8f0;text-align:center;">
                            <p style="margin:0 0 4px 0;font-size:12px;color:#64748b;font-weight:600;">
                                FinPro Sistemas de Gestão Pessoal
                            </p>
                            <p style="margin:0;font-size:11px;color:#94a3b8;line-height:1.5;">
                                Mensagem automática enviada por nao-responda@finpro.app.br • Não responda a este e-mail.<br>
                                <a href="https://finpro.app.br/privacidade/" style="color:#64748b;text-decoration:underline;">Política de Privacidade</a>
                                &nbsp;•&nbsp;
                                <a href="https://finpro.app.br/termos/" style="color:#64748b;text-decoration:underline;">Termos de Uso</a>
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""


def construir_email_confirmacao(username: str, link_confirmacao: str, logo_url: str) -> tuple[str, str, str]:
    assunto = "[FinPro] Confirme seu e-mail para ativar sua conta"
    texto = (
        f"Olá, {username}!\n\n"
        f"Bem-vindo ao FinPro. Para ativar o acesso à sua conta e habilitar a recuperação de senha, "
        f"confirme seu endereço de e-mail acessando o link abaixo:\n\n"
        f"{link_confirmacao}\n\n"
        f"Validade: este link expira em 24 horas.\n"
        f"Se você não criou uma conta no FinPro, pode ignorar este e-mail com segurança.\n\n"
        f"FinPro Sistemas de Gestão Pessoal — https://finpro.app.br"
    )
    html = _montar_layout_email_html(
        titulo_aba=assunto,
        selo_texto="Verificação de conta",
        selo_bg="#dbeafe",
        selo_cor="#1d4ed8",
        titulo_principal="Confirme seu endereço de e-mail",
        saudacao=f"Olá, {username}.",
        paragrafo_intro=(
            "Recebemos o cadastro deste e-mail na sua conta FinPro. "
            "Clique no botão abaixo para validar sua identidade e liberar o acesso ao painel financeiro."
        ),
        texto_botao="Confirmar meu e-mail",
        url_botao=link_confirmacao,
        cor_botao="#1d4ed8",
        texto_validade="Este link é pessoal e válido por 24 horas.",
        aviso_rodape="Se você não solicitou este cadastro, basta ignorar esta mensagem — nenhum acesso será liberado sem a confirmação.",
        logo_url=logo_url,
    )
    return assunto, texto, html


def construir_email_redefinicao_senha(
    username: str,
    link_redefinicao: str,
    ip_origem: str,
    data_hora_str: str,
    logo_url: str,
) -> tuple[str, str, str]:
    assunto = "[FinPro] Solicitação de redefinição de senha"
    texto = (
        f"Olá, {username}!\n\n"
        f"Recebemos uma solicitação para redefinir a senha da sua conta no FinPro.\n\n"
        f"Detalhes da solicitação:\n"
        f"- Conta: {username}\n"
        f"- Data e hora: {data_hora_str}\n"
        f"- IP de origem: {ip_origem}\n\n"
        f"Para definir uma nova senha, acesse o link abaixo:\n\n"
        f"{link_redefinicao}\n\n"
        f"Validade: este link é de uso único e expira em 30 minutos.\n"
        f"Ao alterar sua senha, todas as sessões abertas em outros dispositivos serão encerradas automaticamente.\n"
        f"Se você não fez esta solicitação, ignore este e-mail — sua senha atual permanece protegida.\n\n"
        f"FinPro Sistemas de Gestão Pessoal — https://finpro.app.br"
    )
    html = _montar_layout_email_html(
        titulo_aba=assunto,
        selo_texto="Redefinição de senha",
        selo_bg="#fef3c7",
        selo_cor="#b45309",
        titulo_principal="Solicitação de nova senha",
        saudacao=f"Olá, {username}.",
        paragrafo_intro=(
            "Recebemos um pedido para redefinir a senha da sua conta no FinPro. "
            "Use o botão abaixo para criar uma nova senha segura."
        ),
        texto_botao="Definir nova senha",
        url_botao=link_redefinicao,
        cor_botao="#0f172a",
        texto_validade="Link de uso único com validade de 30 minutos. Após a troca, todas as sessões ativas em outros dispositivos serão desconectadas.",
        detalhes_seguranca=[
            ("Conta", username),
            ("Data e hora", data_hora_str),
            ("IP de origem", ip_origem),
        ],
        aviso_rodape="Não solicitou esta alteração? Ignore este e-mail — sua senha atual continua inalterada e ninguém terá acesso à sua conta.",
        logo_url=logo_url,
    )
    return assunto, texto, html


def construir_email_alerta_senha_alterada(
    username: str,
    ip_origem: str,
    data_hora_str: str,
    link_recuperacao: str,
    logo_url: str,
) -> tuple[str, str, str]:
    assunto = "[FinPro] Sua senha foi alterada com sucesso"
    texto = (
        f"Olá, {username}!\n\n"
        f"Confirmamos que a senha da sua conta FinPro foi alterada com sucesso e todas as sessões anteriores foram encerradas.\n\n"
        f"Detalhes da alteração:\n"
        f"- Conta: {username}\n"
        f"- Data e hora: {data_hora_str}\n"
        f"- IP de origem: {ip_origem}\n\n"
        f"Se foi você quem realizou a alteração, nenhuma ação adicional é necessária.\n"
        f"Se você NÃO reconhece esta ação, redefina sua senha imediatamente em:\n"
        f"{link_recuperacao}\n\n"
        f"FinPro Sistemas de Gestão Pessoal — https://finpro.app.br"
    )
    html = _montar_layout_email_html(
        titulo_aba=assunto,
        selo_texto="Alerta de segurança",
        selo_bg="#dcfce7",
        selo_cor="#15803d",
        titulo_principal="Sua senha foi alterada",
        saudacao=f"Olá, {username}.",
        paragrafo_intro=(
            "Este é um aviso automático confirmando que a senha da sua conta FinPro foi atualizada "
            "e todas as sessões abertas em outros dispositivos foram encerradas por segurança."
        ),
        texto_botao="Não reconheço — recuperar acesso",
        url_botao=link_recuperacao,
        cor_botao="#b91c1c",
        texto_validade="Se foi você quem alterou a senha, pode desconsiderar o botão acima e entrar normalmente no FinPro.",
        detalhes_seguranca=[
            ("Conta", username),
            ("Data e hora", data_hora_str),
            ("IP de origem", ip_origem),
        ],
        aviso_rodape="Nunca compartilhe sua senha ou links de acesso com terceiros.",
        logo_url=logo_url,
    )
    return assunto, texto, html