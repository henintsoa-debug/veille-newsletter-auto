"""Échantillons FICTIFS de newsletters pour les tests (aucun contenu réel)."""

from email.message import EmailMessage

HTML_BODY = """<html><head><title>Titre fictif</title></head><body>
<script>var x = 1;</script>
<h1>Grande une fictive</h1>
<p>Texte de l'article A. <a href="https://example.org/a?utm_source=nl&id=3">Lire la suite</a></p>
<h2>Deuxième sujet</h2>
<a href="https://sponsor.example.com/promo"><img src="https://img.example.com/b.png"
   alt="Bannière fictive" width="600" height="100"></a>
<a href="mailto:contact@example.com">Contact</a>
<a href="javascript:void(0)">Ignoré</a>
<img src="cid:logo123">
</body></html>"""


def make_eml(subject="Édition fictive n°1", sender="Rédaction <redaction@example.net>",
             date="Tue, 10 Jun 2025 08:30:00 +0200", message_id="<abc123@example.net>",
             html=HTML_BODY, text=None) -> bytes:
    msg = EmailMessage()
    if subject is not None:
        msg["Subject"] = subject
    if sender is not None:
        msg["From"] = sender
    if date is not None:
        msg["Date"] = date
    if message_id is not None:
        msg["Message-ID"] = message_id
    msg["To"] = "lecteur@example.com"
    if html is None:
        msg.set_content(text or "Texte brut fictif. Voir https://example.org/plain")
    else:
        msg.set_content(text or "Version texte")
        msg.add_alternative(html, subtype="html")
    return msg.as_bytes()
