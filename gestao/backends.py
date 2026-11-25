import os
import uuid
import resend
from django.core.mail.backends.base import BaseEmailBackend


class ResendEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        resend_api_key = os.environ.get('RESEND_API_KEY', '').strip()
        if not resend_api_key or not email_messages:
            return 0

        resend.api_key = resend_api_key
        enviados = 0

        for message in email_messages:
            try:
                html_body = None
                for content, mimetype in getattr(message, 'alternatives', []):
                    if mimetype == 'text/html':
                        html_body = content
                        break

                params = {
                    "from": message.from_email or os.environ.get(
                        'DEFAULT_FROM_EMAIL', 'FinPro <nao-responda@finpro.app.br>'
                    ),
                    "to": list(message.recipients()),
                    "subject": message.subject,
                    "text": message.body,
                    "headers": {"X-Entity-Ref-ID": str(uuid.uuid4())},
                }
                if html_body:
                    params["html"] = html_body

                resend.Emails.send(params)
                enviados += 1
            except Exception as e:
                if not self.fail_silently:
                    raise e
        return enviados