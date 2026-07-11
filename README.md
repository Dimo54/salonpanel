# SalonPanel SaaS

Ova verzija pretvara pocetni SalonPanel u multi-salon SaaS aplikaciju.

## Sta je promenjeno

- SQLite je zamenjen PostgreSQL bazom preko `DATABASE_URL`.
- Svaki salon ima svoj nalog, svoje klijente, usluge i termine.
- Svaki salon ima javni link: `/s/<slug>/zakazi`.
- Vlasnik platforme ima poseban super admin nalog.
- Super admin vidi sve salone, njihove statuse, pakete i osnovne metrike.
- Pretplate su pripremljene za 10 EUR mesecno i 80 EUR godisnje.
- Paddle online naplata jos nije povezana; status se za sada menja rucno iz super admin panela.

## Environment variables za Render

U Render dashboardu, na servisu `salonpanel`, otvori **Environment** i dodaj:

```text
DATABASE_URL=postgresql://...
SECRET_KEY=dug-random-string
SUPER_ADMIN_EMAIL=tvoj-email@example.com
SUPER_ADMIN_PASSWORD=tvoja-jaka-sifra
SUPER_ADMIN_NAME=Tvoje ime
TRIAL_DAYS=14
MONTHLY_PRICE_EUR=10
YEARLY_PRICE_EUR=80
```

Nemoj slati ove vrednosti u chat i nemoj ih commitovati na GitHub.

## Supabase connection string

U Supabase projektu idi na **Connect** ili **Project Settings > Database** i kopiraj Postgres connection string.
Za Render koristi pooled connection string kada je dostupan. U stringu zameni placeholder za password stvarnom database sifrom koju si sacuvao pri kreiranju projekta.

Primer formata:

```text
postgresql://postgres.xxxxx:YOUR_PASSWORD@aws-0-eu-north-1.pooler.supabase.com:6543/postgres
```

Aplikacija ce automatski dodati `sslmode=require` ako ga nema u URL-u.

## Deploy na Render

Build command:

```text
pip install -r requirements.txt
```

Start command:

```text
gunicorn app:app
```

Nakon prvog pokretanja aplikacija sama pravi tabele u PostgreSQL bazi i kreira super admin nalog ako su podeseni `SUPER_ADMIN_EMAIL` i `SUPER_ADMIN_PASSWORD`.

## Prvo logovanje

1. Otvori sajt na Renderu.
2. Uloguj se emailom i sifrom iz `SUPER_ADMIN_EMAIL` i `SUPER_ADMIN_PASSWORD`.
3. Otvori `/register` da kreiras test salon.
4. U super admin panelu mozes menjati status pretplate test salona.

## Javno zakazivanje

Svaki salon ima svoj link:

```text
/s/naziv-salona/zakazi
```

Stari `/zakazi` link radi samo ako postoji jedan salon u bazi. Za vise salona koristi se novi link sa slugom.

## Migracija starih SQLite podataka

Ako imas staru `salonpanel.sqlite3` bazu, mozes kasnije pokrenuti:

```bash
export DATABASE_URL="postgresql://..."
python scripts/import_sqlite.py /putanja/do/salonpanel.sqlite3 "Naziv salona" "email@salona.com" "Ime vlasnika"
```

Skripta ce napraviti salon u PostgreSQL bazi i prebaciti stare klijente, usluge i termine.
