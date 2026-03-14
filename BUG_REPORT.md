# Bug Report – BIP (Books In Pieces)

Analisi statica del codice eseguita su tutti i moduli principali.

---

## BUG-01 · Email non normalizzata a minuscolo in `forgot_password`
**File:** `routes/auth.py:130`
**Severità:** Alta

```python
# forgot_password — solo strip(), manca .lower()
email = (request.form.get("email") or "").strip()

# register e login — correttamente normalizzate
email = (request.form.get("email") or "").strip().lower()
```

Le email vengono salvate in minuscolo alla registrazione. In `forgot_password` manca `.lower()`, perciò se l'utente inserisce `User@Example.com` la query `User.query.filter_by(email=email).first()` non trova il record e la mail di recupero non viene inviata.

**Fix:** aggiungere `.lower()` alla riga 130.

---

## BUG-02 · Libri inattivi visibili nella ricerca
**File:** `routes/core.py:165–203`, `routes/reading.py:82–85`
**Severità:** Media

`_build_book_filters()` non include mai il filtro `Book.is_active == True`. La query in `select_book` esegue `Book.query.filter(*filters)` senza tale condizione. I libri disattivati compaiono quindi nei risultati di ricerca. Il caso `selected_book_id` è corretto (filtra `is_active=True`) ma il percorso della ricerca generale non lo è.

**Fix:** aggiungere `filters.append(Book.is_active.is_(True))` all'inizio di `_build_book_filters`.

---

## BUG-03 · File del libro non eliminato se la copertina è invalida
**File:** `routes/books.py:61, 79–83`
**Severità:** Media

In `upload_book` il file del libro viene salvato su disco (riga 61) prima della validazione della copertina. Se il formato della copertina non è supportato, la funzione esegue un redirect anticipato lasciando il file orfano su disco senza alcun record nel database.

```python
file.save(file_path)          # ← salvato qui
...
if cover_image_file and cover_image_file.filename and not cover_filename:
    flash("Formato copertina non supportato...")
    return redirect(...)      # ← il file rimane su disco
```

**Fix:** validare la copertina prima di salvare il file del libro, oppure aggiungere la rimozione del file prima del redirect.

---

## BUG-04 · Collisione di nome file sovrascrive libro esistente
**File:** `routes/books.py:58–59`
**Severità:** Alta

```python
filename = secure_filename(file.filename)
file_path = os.path.join(UPLOAD_FOLDER, filename)
```

`secure_filename` non garantisce l'unicità. Se viene caricato un file con lo stesso nome di un libro già esistente, il file su disco viene silenziosamente sovrascritto. Il vecchio record del database punta ora a contenuto corrotto.

**Fix:** generare un nome univoco (es. con `uuid.uuid4().hex`) prima di salvare il file, analogamente a quanto già fatto per le copertine in `_store_cover_on_disk`.

---

## BUG-05 · Aggiornamento profilo sovrascrive il canale di consegna di tutti gli schedule
**File:** `routes/user_profile.py:62–64`
**Severità:** Media

```python
schedules = ReadingSchedule.query.filter_by(user_id=user.id).all()
for schedule in schedules:
    schedule.delivery_channel = preferred_delivery_channel
```

Quando l'utente aggiorna il canale preferito nel profilo, tutti gli schedule esistenti vengono aggiornati al nuovo canale, ignorando qualsiasi configurazione per-schedule impostata intenzionalmente (es. un libro via email e un altro via Telegram).

**Fix:** non propagare automaticamente il canale agli schedule esistenti, oppure aggiornare solo gli schedule che coincidevano con il vecchio canale preferito.

---

## BUG-06 · Codice morto nel controllo `travel_pause_until` dello scheduler
**File:** `scheduler.py:66–67`
**Severità:** Bassa

`_due_schedules()` filtra già gli schedule dove `travel_pause_until > now` (riga 49):
```python
db.or_(ReadingSchedule.travel_pause_until.is_(None), ReadingSchedule.travel_pause_until <= now)
```
La chiamata successiva `_defer_until_if_needed(schedule, now, schedule.travel_pause_until)` (riga 66) non può mai restituire `True` per la stessa condizione, perché quegli schedule sono già esclusi dalla query. La riga 66 è dead code.

---

## BUG-07 · Aggiornamento di `book.word_count` mai persistito in caso di mancata consegna
**File:** `email_sender.py:449–451, 473`
**Severità:** Bassa

```python
total_words = book.word_count or count_total_words(file_path)
if total_words != (book.word_count or 0):
    book.word_count = total_words          # ← modifica in memoria
...
if not delivered:
    return                                 # ← nessun commit, modifica persa
```

Se `book.word_count` è 0/None, viene ricalcolato dal file ma l'aggiornamento non viene mai committato quando la consegna fallisce. Ogni invio fallito ricalcola il conteggio inutilmente.

---

## BUG-08 · Doppia eliminazione di `DeliveryEvent` (ORM cascade + DELETE SQL manuale)
**File:** `routes/reading.py:427–428`, `routes/admin.py:147–148`, `routes/user_profile.py:96–97`
**Severità:** Bassa

Il modello `DeliveryEvent` ha `cascade="all, delete-orphan"` (models.py:89). In più punti viene eseguita prima una DELETE SQL diretta con `synchronize_session=False`, poi `db.session.delete(schedule)` che tenta di applicare il cascade ORM sugli stessi eventi già eliminati.

```python
DeliveryEvent.query.filter_by(schedule_id=schedule.id).delete(synchronize_session=False)
db.session.delete(schedule)   # ← ORM tenta di eliminare eventi già eliminati
```

Con `synchronize_session=False` la sessione SQLAlchemy non è aggiornata, quindi il cascade ORM emette DELETE ridondanti. Non causa crash (DELETE su righe inesistenti è un no-op) ma è inefficiente e incoerente. La cancellazione manuale è superflua grazie al cascade già definito.

---

## Riepilogo

| # | File | Severità | Descrizione |
|---|------|----------|-------------|
| 01 | `routes/auth.py:130` | **Alta** | Email non lowercased in forgot_password |
| 02 | `routes/core.py:165` | **Media** | Libri inattivi visibili in ricerca |
| 03 | `routes/books.py:61,80` | **Media** | File libro orfano se copertina invalida |
| 04 | `routes/books.py:58` | **Alta** | Collisione filename sovrascrive file esistente |
| 05 | `routes/user_profile.py:62` | **Media** | Canale consegna sovrascritto su tutti gli schedule |
| 06 | `scheduler.py:66` | Bassa | Dead code su travel_pause_until |
| 07 | `email_sender.py:449` | Bassa | word_count non persistito se consegna fallisce |
| 08 | `routes/reading.py:427` | Bassa | Doppia eliminazione DeliveryEvent |
