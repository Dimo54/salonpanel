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
- probni period: `14 dana`.

Online naplata još nije povezana. Status pretplate se i dalje može menjati ručno iz super admin panela.

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
TRIAL_DAYS=14
MONTHLY_PRICE_EUR=19.99
YEARLY_PRICE_EUR=199.99
```

Ove vrednosti postoje kao Environment promenljive da bi cena kasnije mogla da se promeni bez menjanja više delova koda.

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
CRON_SECRET=druga_duga_nasumicna_vrednost
```

`CRON_SECRET` štiti rutu za podsetnike da je ne bi mogao pokretati bilo ko. Servis koji poziva rutu treba da pošalje tajnu u zaglavlju `X-Cron-Secret` ili kao `secret` parametar.

### Opciona zaštita prijave

```text
LOGIN_MAX_ATTEMPTS=5
LOGIN_WINDOW_MINUTES=15
BOOKING_MAX_REQUESTS=8
BOOKING_WINDOW_MINUTES=30
```

Prve dve vrednosti privremeno zaustavljaju veliki broj pogrešnih pokušaja prijave sa iste internet adrese. Druge dve ograničavaju spam javnog obrasca za zakazivanje sa iste internet adrese.

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

- Paddle naplata još nije povezana.
- Podsetnici ne mogu da se šalju sami dok se ne podesi satni cron poziv.
- Politika privatnosti i Uslovi korišćenja su nacrti.
- Pre puštanja većeg broja salona treba napraviti i proveriti strategiju rezervnih kopija Supabase baze.
