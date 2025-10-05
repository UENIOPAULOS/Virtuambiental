
# Licenças Ambientais – Protótipo (deploy-ready)
- Flask + SQLAlchemy (SQLite)
- UI responsiva (Bootstrap 5)
- Gráficos (Chart.js): pizza/linha, empilhado por tipo, heatmap
- KPIs SLA (30/60 dias)
- Alertas por e-mail (SMTP, teste e rodar agora)
- PWA (instalável no celular)
- Pronto para **cPanel/Passenger**, **Render/Railway** (Procfile) e **Docker**

## Local
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py  # http://localhost:5000
```

## Hospedar em cPanel (Passenger WSGI)
1. No cPanel, crie um **Subdomínio** (ex.: demo.seudominio.com) e ative **SSL** (AutoSSL/Let's Encrypt).
2. Abra **Setup Python App** (ou **Application Manager / Passenger**):
   - Python 3.11
   - App Directory: apontar para a pasta com estes arquivos
   - Application startup file: `passenger_wsgi.py`
3. No terminal do cPanel (ou via SSH), dentro da pasta do app:
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   touch tmp/restart.txt  # reiniciar Passenger quando necessário
   ```
4. Configure variáveis de ambiente (opcional): `SECRET_KEY`, `DATABASE_URL_SQLITE`.
5. Acesse o subdomínio. O SQLite `app.db` fica na raiz do app (garanta permissão de escrita).

## Hospedar no Render (rápido)
1. Envie este repositório para o GitHub.
2. Em **render.com**: New → **Blueprint** → aponte para este repo (usa `render.yaml`).
3. O serviço sobe com `gunicorn`. Adicione o domínio e ative SSL. Pronto para compartilhar.

## Docker
```bash
docker build -t licencas .
docker run -p 5000:5000 -e SECRET_KEY=minha-chave licencas
```

## Alertas por e-mail
No menu **Alertas**, configure SMTP e clique em **Enviar e-mail de teste**. Depois use **Rodar agora** para enviar o resumo.
> Em produção, prefira armazenar segredos em variáveis de ambiente/cofres.

## PWA
Acesse HTTPS no domínio (ex.: demo.seudominio.com), depois:
- Android/Chrome: menu ⋮ → **Instalar app**
- iPhone/Safari: **Compartilhar** → **Adicionar à Tela de Início**
