import os
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
                params = {
                    "from": message.from_email or os.environ.get('DEFAULT_FROM_EMAIL', 'onboarding@resend.dev'),
                    "to": list(message.recipients()),
                    "subject": message.subject,
                    "text": message.body,
                }
                resend.Emails.send(params)
                enviados += 1
            except Exception as e:
                if not self.fail_silently:
                    raise e
        return enviados