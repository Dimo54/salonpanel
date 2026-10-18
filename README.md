# SalonPanel — Paket 1

SalonPanel je višesalonska web aplikacija za zakazivanje termina, vođenje klijenata, radnika, usluga i pretplata. Ova verzija završava prvi paket funkcija koje su važne pre uključivanja pravih salona.

## Šta je novo u Paketu 1

### 1. Stvarno radno vreme

- Salon podešava radno vreme posebno za svaki dan u nedelji.
- Dan može biti radni ili neradni.
- Svaki radnik može da:
  - nasledi radno vreme salona;
  - ima posebno radno vreme;
  - ne radi određenog dana.
- Svaki radnik može imati redovnu pauzu za svaki dan.
- Postojeća odsustva i neradni periodi ostaju dostupni na posebnoj stranici.

Ovo je važno zato što se klijentu prikazuju samo termini koji stvarno staju u radno vreme radnika i ne preklapaju se sa pauzama, odsustvima ili drugim terminima.

### 2. Pravi kalendar termina

- Dnevni prikaz sa posebnom kolonom za svakog radnika.
- Nedeljni prikaz termina.
- Filter po radniku.
- Prikaz termina, pauza i odsustava.
- Stari kalendar je sačuvan kao stranica **Odsustva**.

### 3. Bezbednost naloga i obrazaca

- CSRF zaštita svih obrazaca koji menjaju podatke.
- Ograničavanje neuspešnih pokušaja prijave.
- Ograničavanje prevelikog broja javnih zahteva za termin sa iste internet adrese.
- Obavezna jaka `SECRET_KEY` vrednost na Renderu.
- Sigurnija podešavanja korisničke sesije i sigurnosna HTTP zaglavlja.
- Potvrda email adrese.
- Funkcija „Zaboravljena šifra“ i bezbedan link za promenu šifre.
- Javni ekran uspešnog zakazivanja koristi nasumičan token, pa se tuđi termini ne mogu pregledati pogađanjem rednih brojeva.

### 4. Email obaveštenja

Preko Brevo servisa aplikacija može slati:

- klijentu da je zahtev primljen;
- klijentu da je termin potvrđen;
- klijentu da je termin izmenjen;
- klijentu da je termin otkazan;
- podsetnik 24 sata pre termina;
- podsetnik 2 sata pre termina;
- salonu da je stigao novi zahtev ili automatski zakazan termin;
- potvrdu email adrese;
- link za promenu zaboravljene šifre.

Podsetnici zahtevaju da neki spoljašnji servis pozove zaštićenu adresu `/tasks/send-reminders` jednom na sat.

Podsetnici koriste pravila dospelosti, pa propušten cron ne preskače termin samo
zato što nije pozvan u uskom vremenskom prozoru: 24h podsetnik važi od 24h do
2h pre početka, a 2h podsetnik od 2h do samog početka. Termin koji je prvi put
zakazan unutar 24h ne dobija neadekvatan „sutra” podsetnik, ali ostaje kandidat
za 2h podsetnik. Svaka promena datuma ili vremena pravi novu verziju rasporeda.

Konvencija vremena za podsetnike je namerna: `appointments.date`/`time`,
`schedule_started_at` i `reminder_events` `TIMESTAMP` kolone su naive lokalni
wall-clock vreme u `APP_TIMEZONE`; novi reminder upisi eksplicitno uklanjaju
timezone tek nakon konverzije u tu zonu. Samo vrednosti koje predstavljaju
globalni trenutak (npr. Paddle) koriste `TIMESTAMPTZ`/UTC. Istorijski naive
`created_at` nije sam po sebi dokaz svoje zone i ne konvertuje se bez operatorove
izričite odluke u rollout koraku.

### Objavljivanje pouzdanih podsetnika

1. Napraviti proverljiv Supabase backup i sačuvati izlaz `pg_dump`/backup job-a.
2. Zaustaviti reminder cron tokom migracije i deploy prelaza. Proveriti da je
   PostgreSQL `TimeZone` jednak `APP_TIMEZONE` (podrazumevano `Europe/Belgrade`).
3. Pre migracije pregledati stvarni tip i semantiku istorijskog
   `appointments.created_at`. Naive `timestamp` nije dokaz ni UTC ni lokalnog
   vremena: operator mora na osnovu poznatog istorijskog zapisa odabrati
   `local-wall-clock` ili `utc`; bez toga se migracija namerno prekida. Jedini
   izuzetak je eksplicitni `legacy-past-only`: dozvoljen je samo kada read-only
   dokaz pokaže da je svaki postojeći termin strogo pre današnjeg datuma u
   `APP_TIMEZONE`. Migracija tada pod transakcionim lock-om ponavlja proveru,
   ne čita niti konvertuje `created_at`, i upisuje neutralni legacy anchor
   `date 00:00` u `schedule_started_at`. To nije vreme zakazivanja niti
   stvaranja termina; prošli termini nikada nisu reminder-due.
   Isključivo read-only provere su: `SELECT data_type FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'appointments' AND
   column_name = 'created_at';`, `SHOW TimeZone;` i poređenje nekoliko poznatih
   vremena nastanka termina sa `created_at`. Uporediti i tadašnji `APP_TIMEZONE`
   u Render konfiguraciji/logovima; zaključak i uzorke zabeležiti u rollout
   zapisu pre bilo kakve migracije. Ne birati konvenciju na osnovu pretpostavke.
4. Za `legacy-past-only` maintenance prozor mora da blokira i nova zakazivanja
   od početka migracije do deploy-a novog koda. Sam PostgreSQL lock štiti samo
   trajanje transakcije; stari kod bi posle commita mogao da kreira budući
   termin sa host/session-vremenskim default-om. Cron mora ostati zaustavljen.
5. Iz kontrolisanog operator okruženja uraditi dry-run, zatim `--apply` sa
   tačnim `--expected-database`, `--expected-timezone`, deklarisanom
   `--history-timestamp-convention` i potvrdom oblika
   `APPLY-REMINDER-MIGRATIONS:production:IME_BAZE`; zatim uraditi `--check`.
   Runner nikada ne stavlja `DATABASE_URL` u process argv i proverava stvarni
   `current_database()`, timezone, tipove/ograničenja/indekse i backfill.
   Migracija ima `lock_timeout = 5s`; aktivni PostgreSQL lock može tražiti
   ponavljanje u mirnom prozoru, umesto beskonačnog čekanja.
6. Migracija je idempotentna: istorijske `email_events` uspehe prepisuje u
   `reminder_events`, bez NULL/orphan zapisa. Novi kod pre claim-a proverava
   legacy v1 evidenciju i dual-write-uje uspešne poruke u `email_events`.
7. Deploy-ovati novu verziju. `init_db()` na postojećoj bazi više ne sme napraviti
   reminder šemu; endpoint vraća 503 dok kontrolisana migracija nije kompletna.
8. Ponovo uključiti cron tek posle uspešnog `--check`. Pozvati ga sa
   testnom/staging bazom i mock Brevo kredencijalima, zatim u produkciji proveriti
   samo logove i redove `reminder_events` bez slanja test poruka stvarnim primaocima.
9. Za rollback zaustaviti novi cron, vratiti prethodni kod, pa uključiti stari
   cron. Ne brisati nove kolone/tabelu: dual-write uspeha čuva staru deduplikaciju.

Rezervacija pre poziva Brevo-u sprečava paralelne cron duplikate i omogućava
ograničene retry-je posle istekle rezervacije. Potpuni exactly-once email ipak
nije moguće garantovati: proces može pasti nakon što Brevo prihvati poruku, a
pre nego što aplikacija upiše uspeh. Za to bi bila potrebna provajderova
idempotency podrška ili potvrđen outbox API. Isto važi za pad između novog i
legacy dual-write-a; rollback zato zahteva zaustavljen cron i proveru evidencije.

### 5. Pravila online zakazivanja

Salon može podesiti:

- ručno ili automatsko potvrđivanje;
- koliko minuta unapred klijent mora da zakaže;
- koliko dana unapred klijent može da vidi termine;
- informativni rok za otkazivanje.

### 6. Srpski format datuma

Polja za datum prihvataju format:

```text
20.07.2026.
```

Aplikacija i dalje razume i tehnički format `2026-07-20`, što olakšava rad API-ja i migraciju starih podataka.

### 7. Privatnost

- Klijent mora prihvatiti obradu podataka pre slanja zahteva.
- Marketinška saglasnost je odvojena i nije obavezna.
- Registracija salona zahteva prihvatanje Uslova korišćenja i Politike privatnosti.
- Dodate su početne stranice `/privacy` i `/terms`.

**Važno:** tekstovi na tim stranicama su radni nacrti. Pre komercijalnog lansiranja treba da ih pregleda pravnik koji poznaje propise Srbije i zaštitu podataka o ličnosti.

### 8. Pretplata

Podrazumevane cene su:

- mesečni plan: `19.99 EUR`;
- godišnji plan: `199.99 EUR`;
- probni period: `7 dana`.

Mesečna i godišnja kupovina otvaraju Paddle Billing checkout. Potpisani webhook događaji autoritativno sinhronizuju lokalni status, a Paddle Customer Portal omogućava prijavljenom salonu da upravlja naplatom. Neaktivni nalozi se centralno preusmeravaju na stranicu pretplate; javno zakazivanje i operativni webhook pozivi ostaju dostupni.

## Da li je domen potreban?

Nije. Sve funkcije mogu da rade preko postojeće Render adrese, na primer:

```text
https://salonpanel.onrender.com
```

Domen `salonpanel.rs` može da se kupi i poveže kasnije, kada aplikacija počne da donosi prihod.

## Environment promenljive na Renderu

Environment promenljiva je tajno ili promenljivo podešavanje koje Render čuva izvan GitHub koda. Tako šifre i API ključevi ne postaju javni.

### Obavezne promenljive

```text
DATABASE_URL=postgresql://...
SECRET_KEY=duga_nasumicna_vrednost
APP_BASE_URL=https://salonpanel.onrender.com
APP_TIMEZONE=Europe/Belgrade
SUPER_ADMIN_EMAIL=tvoj-email@example.com
SUPER_ADMIN_PASSWORD=tvoja-jaka-sifra
SUPER_ADMIN_NAME=Tvoje ime
```

- `DATABASE_URL` povezuje aplikaciju sa Supabase/PostgreSQL bazom.
- `SECRET_KEY` potpisuje korisničke sesije i bezbednosne tokene. Mora biti duga i tajna.
- `APP_BASE_URL` omogućava da linkovi u emailovima vode na tačnu javnu adresu aplikacije.
- `APP_TIMEZONE` određuje lokalno vreme za termine i podsetnike.
- `SUPER_ADMIN_*` određuju nalog vlasnika platforme.

Jaku `SECRET_KEY` vrednost možeš napraviti lokalno komandom:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Dobijenu vrednost stavi samo u Render Environment. Nemoj je postavljati na GitHub i nemoj je slati drugim ljudima.

### Cene i probni period

```text
TRIAL_DAYS=7
MONTHLY_PRICE_EUR=19.99
YEARLY_PRICE_EUR=199.99
```

Ove vrednosti postoje kao Environment promenljive da bi cena kasnije mogla da se promeni bez menjanja više delova koda.

### Paddle Billing

```text
PADDLE_ENVIRONMENT=sandbox
PADDLE_CLIENT_TOKEN=test_...
PADDLE_API_KEY=pdl_sdbx_apikey_...
PADDLE_MONTHLY_PRICE_ID=pri_...
PADDLE_YEARLY_PRICE_ID=pri_...
PADDLE_WEBHOOK_SECRET=pdl_ntfset_...
PADDLE_WEBHOOK_TOLERANCE_SECONDS=5
SUBSCRIPTION_GRACE_DAYS=7
```

- `PADDLE_ENVIRONMENT` mora biti `sandbox` ili `production`.
- `PADDLE_CLIENT_TOKEN` i ID-jevi cena prosleđuju se Paddle.js stranici za checkout.
- `PADDLE_API_KEY` ostaje isključivo na serveru i koristi se za proveru trenutnog stanja pretplate preko Paddle API-ja pre izmene salona.
- `PADDLE_WEBHOOK_SECRET` je tajna notification destination-a iz Paddle kontrolne table. Ona je različita od API ključa i koristi se za proveru `Paddle-Signature` zaglavlja na `/webhooks/paddle`.
- `PADDLE_WEBHOOK_TOLERANCE_SECONDS` određuje dozvoljeno odstupanje vremena potpisa i podrazumevano je `5` sekundi.
- `SUBSCRIPTION_GRACE_DAYS` određuje jednokratni rok posle prvog `past_due` statusa. Podrazumevano je `7`; neispravna, negativna ili nerazumno velika vrednost bezbedno se vraća na `7`.

Webhook proverava potpis, idempotentno čuva podržane događaje i sinhronizuje salon isključivo iz autoritativne Paddle pretplate. Nepodržani događaji se beleže i ignorišu. API ključ mora imati Paddle dozvole `subscription.read` i `customer_portal_session.write`.

Detaljan redosled sandbox i produkcionog puštanja nalazi se u `docs/PADDLE_ROLLOUT.md`.

### Brevo email

```text
BREVO_API_KEY=...
BREVO_SENDER_EMAIL=...
BREVO_SENDER_NAME=SalonPanel
```

- `BREVO_API_KEY` dozvoljava aplikaciji da pošalje email preko tvog Brevo naloga.
- `BREVO_SENDER_EMAIL` je verifikovana adresa pošiljaoca.
- `BREVO_SENDER_NAME` je ime koje primalac vidi.

Aplikacija će raditi i bez ovih promenljivih, ali emailovi neće biti poslati.

### Automatski podsetnici

```text
REMINDER_SECRET=druga_duga_nasumicna_vrednost
```

`REMINDER_SECRET` štiti rutu za podsetnike da je ne bi mogao pokretati bilo ko. Servis koji poziva rutu mora da koristi `POST` i pošalje tajnu samo kroz zaglavlje `Authorization: Bearer ...`. Stari naziv `CRON_SECRET` privremeno ostaje podržan samo radi bezbedne rotacije; nova okruženja treba da koriste `REMINDER_SECRET`.

### Opciona zaštita prijave

```text
LOGIN_MAX_ATTEMPTS=5
LOGIN_WINDOW_MINUTES=15
LOGIN_REQUEST_MAX_ATTEMPTS=20
PASSWORD_RESET_MAX_REQUESTS=5
PASSWORD_RESET_WINDOW_MINUTES=30
AVAILABILITY_MAX_REQUESTS=60
AVAILABILITY_WINDOW_MINUTES=10
BOOKING_MAX_REQUESTS=8
BOOKING_WINDOW_MINUTES=30
RATE_LIMIT_RETENTION_HOURS=48
RATE_LIMIT_CLEANUP_BATCH_SIZE=500
```

Ove vrednosti ograničavaju veliki broj zahteva za prijavu, promenu zaboravljene šifre, proveru slobodnih termina i slanje javnog obrasca sa iste internet adrese. Podrazumevane vrednosti su dovoljno visoke za normalno korišćenje javnog formulara, ali zaustavljaju automatizovano ponavljanje zahteva.

Nova generička tabela `rate_limit_attempts` čuva HMAC identifikatore umesto sirovih identifikatora zahteva. Njeni zapisi se zadržavaju 48 sati, a pri svakom novom generički ograničenom zahtevu briše se najviše 500 najstarijih isteklih zapisa. Postojeća zaštita neuspešne prijave u tabeli `login_attempts` i dalje privremeno beleži email i internet adresu radi otkrivanja ponovljenih pokušaja, a zapise starije od dva dana briše tokom narednih pokušaja prijave. Ovo je u skladu sa Politikom privatnosti, koja navodi kratkotrajno beleženje tehničkih podataka radi sprečavanja zloupotrebe.

Kontrolisana SQL migracija za novu generičku tabelu i indekse nalazi se u `migrations/20260723_create_rate_limit_attempts.sql`. Izvršavanje te migracije kao posebnog koraka pre pokretanja nove verzije je preporučeni postupak za produkciju. Postojeća inicijalizacija šeme pri pokretanju aplikacije privremeno ostaje prisutna radi kompatibilnosti sa ranijim instalacijama.

## Lokalno pokretanje testova

Testovi ne pokreću inicijalizaciju baze i ne zahtevaju vezu sa Supabase projektom:

```bash
SALONPANEL_SKIP_DB_INIT=1 SECRET_KEY=test-secret-key DATABASE_URL=postgresql://test:test@localhost/test \
python -m unittest discover -s tests -v
```

Paddle migracije se pre nove verzije izvršavaju redom:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20260805_add_paddle_event_id.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20260805_add_paddle_subscription_updated_at.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20260805_add_subscription_grace_ends_at.sql
```

Za kontrolisano izvršavanje koristi `scripts/run_paddle_migrations.py`; podrazumevani režim je samo dry-run. Kompletan redosled validacije, sandbox provere, rezervne kopije, smoke testa i rollback-a nalazi se u [Paddle rollout vodiču](docs/PADDLE_ROLLOUT.md).

## Render podešavanja

Build command:

```text
pip install -r requirements.txt
```

Start command:

```text
gunicorn app:app
```

Pri prvom pokretanju nove verzije aplikacija automatski:

- dodaje nove kolone i tabele u postojeću bazu;
- čuva stare salone, klijente, usluge, radnike i termine;
- pravi početno radno vreme za stare salone;
- postavlja da postojeći radnici nasleđuju radno vreme salona.

Ipak, pre svake veće izmene preporučuje se rezervna kopija baze.

## Supabase veza

Za Render je najbolje koristiti pooled PostgreSQL connection string koji dobijaš u Supabase projektu. Primer formata:

```text
postgresql://postgres.xxxxx:YOUR_PASSWORD@aws-0-eu-north-1.pooler.supabase.com:6543/postgres
```

Aplikacija automatski dodaje `sslmode=require` ako ga nema.

## Kontrolna lista posle objavljivanja

1. Prijavi se kao super admin.
2. Otvori postojeći salon i proveri da su podaci ostali sačuvani.
3. U **Podešavanja** sačuvaj radno vreme za svaki dan.
4. U **Radnici** otvori svakog radnika i podesi njegovo radno vreme i pauzu.
5. Otvori **Kalendar** i proveri dnevni i nedeljni prikaz.
6. Otvori **Odsustva** i napravi probno odsustvo.
7. Sa javnog linka napravi probni termin.
8. Proveri da zauzeto vreme više nije ponuđeno drugom klijentu.
9. Proveri ručno i automatsko potvrđivanje.
10. Proveri email potvrde, otkazivanje i promenu termina.
11. Proveri izgled na telefonu.
12. Proveri „Zaboravljena šifra“ i potvrdu email adrese.

## Važna ograničenja ove verzije

- Paddle proizvodi, cene, API ključ, customer portal i webhook destination moraju se posebno podesiti u Paddle kontrolnoj tabli.
- Podsetnici ne mogu da se šalju sami dok se ne podesi satni cron poziv.
- Politika privatnosti i Uslovi korišćenja su nacrti.
- Pre puštanja većeg broja salona treba napraviti i proveriti strategiju rezervnih kopija Supabase baze.
