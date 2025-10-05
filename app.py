
import os
from datetime import datetime, timedelta, date
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr

load_dotenv()
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.environ.get('DATABASE_URL_SQLITE', os.path.join(BASE_DIR, 'app.db'))

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')  # override via env in prod
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + DB_PATH
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- Models ---
class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    tax_id = db.Column(db.String(50), unique=True, nullable=True)  # CNPJ
    sector = db.Column(db.String(120), nullable=True)
    state = db.Column(db.String(2), nullable=True)  # UF
    city = db.Column(db.String(120), nullable=True)
    contact_email = db.Column(db.String(200), nullable=True)
    contact_phone = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    licenses = db.relationship('License', backref='company', lazy=True, cascade="all, delete-orphan")

class License(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    authority = db.Column(db.String(200), nullable=False)  # ex.: SEMAD, IBAMA
    license_type = db.Column(db.String(200), nullable=False)  # LO, LP, LI, Outorga, etc.
    number = db.Column(db.String(200), nullable=True)
    issue_date = db.Column(db.Date, nullable=True)
    expiry_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(50), default='Ativa')  # Ativa, Pendente, Suspensa, Vencida
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# --- Alert/Notifications ---
class AlertSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    smtp_host = db.Column(db.String(200), nullable=False, default='smtp.example.com')
    smtp_port = db.Column(db.Integer, nullable=False, default=587)
    security = db.Column(db.String(20), nullable=False, default='starttls')  # starttls|ssl|none
    smtp_user = db.Column(db.String(200), nullable=True)
    smtp_pass = db.Column(db.String(200), nullable=True)
    from_email = db.Column(db.String(200), nullable=False, default='alertas@example.com')
    recipients = db.Column(db.Text, nullable=False, default='seuemail@example.com')
    thresholds = db.Column(db.String(100), nullable=False, default='15,30,60')

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    license_id = db.Column(db.Integer, nullable=False)
    threshold = db.Column(db.Integer, nullable=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()
    if not AlertSettings.query.first():
        db.session.add(AlertSettings())
        db.session.commit()

# --- Helpers ---
def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except ValueError:
        return None

def months_window(n=12):
    today = datetime.utcnow().date()
    months = []
    cursor = today.replace(day=1)
    for i in range(n):
        y = cursor.year + (cursor.month - 1 + i) // 12
        m = (cursor.month - 1 + i) % 12 + 1
        months.append(f"{y:04d}-{m:02d}")
    return months

def calc_stats(items):
    today = datetime.utcnow().date()
    months = months_window(12)
    data = {
        "by_status": {},
        "by_authority": {},
        "by_type": {},
        "expiries_per_month": {},
        "by_type_per_month": {mo: {} for mo in months},
        "heatmap": {mo: {} for mo in months},
        "sla": {},
        "months": months,
    }
    total = len(items)
    ok_30 = 0
    ok_60 = 0
    for lic in items:
        data["by_status"][lic.status] = data["by_status"].get(lic.status, 0) + 1
        data["by_authority"][lic.authority] = data["by_authority"].get(lic.authority, 0) + 1
        ltype = (lic.license_type or '').upper().strip() or 'OUTROS'
        data["by_type"][ltype] = data["by_type"].get(ltype, 0) + 1

        if lic.expiry_date:
            key = lic.expiry_date.strftime("%Y-%m")
            data["expiries_per_month"][key] = data["expiries_per_month"].get(key, 0) + 1
            if key in data["by_type_per_month"]:
                data["by_type_per_month"][key][ltype] = data["by_type_per_month"][key].get(ltype, 0) + 1
            if key in data["heatmap"]:
                d = lic.expiry_date.day
                data["heatmap"][key][d] = data["heatmap"][key].get(d, 0) + 1
            if lic.expiry_date > today + timedelta(days=30):
                ok_30 += 1
            if lic.expiry_date > today + timedelta(days=60):
                ok_60 += 1
    data["expiries_per_month"] = dict(sorted(data["expiries_per_month"].items()))
    data["sla"] = {
        "ok_30_ratio": (ok_30 / total) * 100 if total else 0.0,
        "ok_60_ratio": (ok_60 / total) * 100 if total else 0.0,
        "total": total
    }
    return data

def company_stats(company_id: int):
    items = License.query.filter_by(company_id=company_id).all()
    return calc_stats(items)

def global_stats():
    items = License.query.all()
    return calc_stats(items)

# --- Email utilities ---
def get_settings():
    return AlertSettings.query.first()

def send_email(settings, subject, body):
    recipients = [r.strip() for r in (settings.recipients or '').split(',') if r.strip()]
    if not recipients:
        return False, "Sem destinatários"
    msg = MIMEText(body, _charset='utf-8')
    msg['Subject'] = subject
    msg['From'] = formataddr(('Alertas Licenças', settings.from_email))
    msg['To'] = ", ".join(recipients)
    try:
        if settings.security == 'ssl':
            import ssl
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context)
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port)
            if settings.security == 'starttls':
                server.starttls()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_pass or '')
        server.sendmail(settings.from_email, recipients, msg.as_string())
        server.quit()
        return True, None
    except Exception as e:
        return False, str(e)

def format_digest(items_by_threshold):
    lines = []
    lines.append("Resumo de licenças com vencimento próximo:\n")
    for thr, items in sorted(items_by_threshold.items()):
        if not items: continue
        lines.append(f"== Em até {thr} dia(s) ==")
        for lic in items:
            comp = lic.company.name if lic.company else '—'
            lines.append(f"- {comp} | {lic.license_type} ({lic.authority}) nº {lic.number or '—'} -> vence em {lic.expiry_date.strftime('%d/%m/%Y')}")
        lines.append("")
    return "\n".join(lines)

def run_alerts():
    s = get_settings()
    if not s:
        return False, "Configurações de alerta não encontradas."
    try:
        thresholds = sorted(set(int(x.strip()) for x in (s.thresholds or '').split(',') if x.strip()))
    except Exception:
        thresholds = [15, 30, 60]
    today = date.today()

    items_by_threshold = {t: [] for t in thresholds}
    for t in thresholds:
        limit = today + timedelta(days=t)
        qs = License.query.filter(License.expiry_date >= today, License.expiry_date <= limit).all()
        for lic in qs:
            sent = Notification.query.filter_by(license_id=lic.id, threshold=t).first()
            if not sent:
                items_by_threshold[t].append(lic)

    if not any(items_by_threshold.values()):
        return True, "Nenhum alerta a enviar."

    body = format_digest(items_by_threshold)
    ok, err = send_email(s, "Alertas de Vencimento de Licenças", body)
    if not ok:
        return False, err

    for t, items in items_by_threshold.items():
        for lic in items:
            db.session.add(Notification(license_id=lic.id, threshold=t))
    db.session.commit()
    return True, None

# --- Routes ---
@app.route('/')
def dashboard():
    today = datetime.utcnow().date()
    in_30 = today + timedelta(days=30)
    in_60 = today + timedelta(days=60)

    total_companies = Company.query.count()
    total_licenses = License.query.count()
    due_30 = License.query.filter(License.expiry_date <= in_30).count()
    due_60 = License.query.filter(License.expiry_date > in_30, License.expiry_date <= in_60).count()
    expired = License.query.filter(License.expiry_date < today).count()

    upcoming = License.query.filter(License.expiry_date >= today).order_by(License.expiry_date.asc()).limit(10).all()
    return render_template('dashboard.html',
                           total_companies=total_companies,
                           total_licenses=total_licenses,
                           due_30=due_30,
                           due_60=due_60,
                           expired=expired,
                           upcoming=upcoming,
                           today=today)

@app.route('/companies')
def companies():
    q = request.args.get('q', '').strip()
    qry = Company.query
    if q:
        like = f"%{q}%"
        from sqlalchemy import or_
        qry = qry.filter(or_(Company.name.ilike(like), Company.tax_id.ilike(like), Company.city.ilike(like)))
    items = qry.order_by(Company.created_at.desc()).all()
    return render_template('companies.html', items=items, q=q)

@app.route('/companies/new', methods=['GET', 'POST'])
def companies_new():
    if request.method == 'POST':
        c = Company(
            name=request.form['name'],
            tax_id=request.form.get('tax_id') or None,
            sector=request.form.get('sector'),
            state=request.form.get('state'),
            city=request.form.get('city'),
            contact_email=request.form.get('contact_email'),
            contact_phone=request.form.get('contact_phone'),
        )
        db.session.add(c)
        db.session.commit()
        flash('Empresa criada com sucesso!', 'success')
        return redirect(url_for('companies'))
    return render_template('company_form.html', item=None)

@app.route('/companies/<int:cid>/edit', methods=['GET', 'POST'])
def companies_edit(cid):
    item = Company.query.get_or_404(cid)
    if request.method == 'POST':
        item.name = request.form['name']
        item.tax_id = request.form.get('tax_id') or None
        item.sector = request.form.get('sector')
        item.state = request.form.get('state')
        item.city = request.form.get('city')
        item.contact_email = request.form.get('contact_email')
        item.contact_phone = request.form.get('contact_phone')
        db.session.commit()
        flash('Empresa atualizada!', 'success')
        return redirect(url_for('companies'))
    return render_template('company_form.html', item=item)

@app.route('/companies/<int:cid>/delete', methods=['POST'])
def companies_delete(cid):
    item = Company.query.get_or_404(cid)
    db.session.delete(item)
    db.session.commit()
    flash('Empresa removida.', 'warning')
    return redirect(url_for('companies'))

@app.route('/companies/<int:cid>')
def company_detail(cid):
    item = Company.query.get_or_404(cid)
    licenses = License.query.filter_by(company_id=cid).order_by(License.expiry_date.asc()).all()
    return render_template('company_detail.html', item=item, licenses=licenses, today=datetime.utcnow().date())

@app.route('/licenses')
def licenses():
    status = request.args.get('status', '')
    horizon = request.args.get('horizon', '30')
    q = request.args.get('q', '').strip()

    from sqlalchemy import or_
    qry = License.query.join(Company)
    if status:
        qry = qry.filter(License.status == status)
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(License.number.ilike(like), License.license_type.ilike(like), Company.name.ilike(like)))

    today = datetime.utcnow().date()
    if horizon and horizon.isdigit():
        limit = today + timedelta(days=int(horizon))
        qry = qry.filter(License.expiry_date <= limit)

    items = qry.order_by(License.expiry_date.asc()).all()
    return render_template('licenses.html', items=items, status=status, horizon=horizon, q=q, today=today)

@app.route('/licenses/new', methods=['GET', 'POST'])
def licenses_new():
    companies = Company.query.order_by(Company.name.asc()).all()
    if not companies:
        flash('Cadastre uma empresa antes de criar uma licença.', 'warning')
        return redirect(url_for('companies_new'))
    if request.method == 'POST':
        lic = License(
            company_id=int(request.form['company_id']),
            authority=request.form['authority'],
            license_type=request.form['license_type'],
            number=request.form.get('number') or None,
            issue_date=parse_date(request.form.get('issue_date')),
            expiry_date=parse_date(request.form.get('expiry_date')),
            status=request.form.get('status') or 'Ativa',
            notes=request.form.get('notes') or None
        )
        if not lic.expiry_date:
            flash('Data de vencimento é obrigatória.', 'danger')
            return render_template('license_form.html', item=None, companies=companies)
        db.session.add(lic)
        db.session.commit()
        flash('Licença criada!', 'success')
        return redirect(url_for('licenses'))
    return render_template('license_form.html', item=None, companies=companies)

@app.route('/licenses/<int:lid>/edit', methods=['GET', 'POST'])
def licenses_edit(lid):
    item = License.query.get_or_404(lid)
    companies = Company.query.order_by(Company.name.asc()).all()
    if request.method == 'POST':
        item.company_id = int(request.form['company_id'])
        item.authority = request.form['authority']
        item.license_type = request.form['license_type']
        item.number = request.form.get('number') or None
        item.issue_date = parse_date(request.form.get('issue_date'))
        item.expiry_date = parse_date(request.form.get('expiry_date'))
        item.status = request.form.get('status') or 'Ativa'
        item.notes = request.form.get('notes') or None
        db.session.commit()
        flash('Licença atualizada!', 'success')
        return redirect(url_for('licenses'))
    return render_template('license_form.html', item=item, companies=companies)

@app.route('/licenses/<int:lid>/delete', methods=['POST'])
def licenses_delete(lid):
    item = License.query.get_or_404(lid)
    db.session.delete(item)
    db.session.commit()
    flash('Licença removida.', 'warning')
    return redirect(url_for('licenses'))

# --- API Stats ---
@app.route('/api/companies/<int:cid>/stats')
def api_company_stats(cid):
    _ = Company.query.get_or_404(cid)
    return company_stats(cid)

@app.route('/api/stats')
def api_global_stats():
    return global_stats()

# --- Alerts UI/Actions ---
@app.route('/settings/alerts', methods=['GET','POST'])
def alerts_settings():
    s = AlertSettings.query.first()
    if request.method == 'POST':
        if not s:
            s = AlertSettings()
        s.smtp_host = request.form['smtp_host']
        s.smtp_port = int(request.form['smtp_port'])
        s.security = request.form.get('security') or 'starttls'
        s.smtp_user = request.form.get('smtp_user') or None
        if request.form.get('smtp_pass'):
            s.smtp_pass = request.form.get('smtp_pass')
        s.from_email = request.form['from_email']
        s.recipients = request.form['recipients']
        s.thresholds = request.form['thresholds']
        db.session.add(s); db.session.commit()
        flash('Configurações salvas!', 'success')
        return redirect(url_for('alerts_settings'))
    return render_template('alerts_settings.html', s=s)

@app.route('/alerts/run')
def alerts_run():
    ok, msg = run_alerts()
    if ok:
        flash(msg or 'Alertas enviados.', 'success')
    else:
        flash(f'Falha ao enviar: {msg}', 'danger')
    return redirect(url_for('alerts_settings'))

@app.route('/alerts/test')
def alerts_send_test():
    s = get_settings()
    if not s:
        flash('Configure primeiro.', 'warning')
        return redirect(url_for('alerts_settings'))
    ok, err = send_email(s, "Teste de Alertas - Licenças", "Este é um e-mail de teste do protótipo de alertas.")
    if ok:
        flash('E-mail de teste enviado.', 'success')
    else:
        flash(f'Falha ao enviar teste: {err}', 'danger')
    return redirect(url_for('alerts_settings'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
