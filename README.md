# Mao-bot (prototyp)

Discord-bot för att spela Mao på distans, i text. Boten sköter korten, högen och straffkorten,
men **ni avgör själva vems tur det är**. Boten visar bara hur ni "sitter i cirkeln".

## Så spelar man
- `/mao ny` → alla skriver `/mao delta` → regelhållaren skriver `/mao starta` (5 kort var).
- Boten slumpar cirkelns ordning och säger vem som börjar. Därefter visar den bara översta kortet
  och ordningen med antal kort kvar.
- Tryck **Visa min hand** under bordsmeddelandet. Där väljer du kort i dropdown-menyn, eller
  trycker **Dra kort** / **Passa**. Handen uppdateras live när något händer (t.ex. straffkort).
- **Boten dömer aldrig själv.** Du kan lägga vilket kort som helst när som helst. Regelhållarna är
  domarna och bestämmer vad som är fel.
- Under bordsmeddelandet har domarna knapparna **⚠️ Inte din tur** och **⚠️ Fel kort**. De tar tillbaka
  det senaste draget (kortet går tillbaka till spelaren, eventuell hemlig effekt ångras) och ger spelaren
  ett straffkort, så nästa spelare spelar på samma kort som förut. Knappen gäller bara det allra senaste
  draget, och en domare kan inte döma sig själv (be en annan regelhållare).
- Övriga straff ("Pratade", "Glömde något" osv.) delas ut med `/straff`. Lägg till `ta_tillbaka:True`
  om spelarens senaste kort också ska tas tillbaka.

## Kommandon
| Kommando | Vem | Vad |
|---|---|---|
| `/mao ny` | alla | Skapar ett spel i kanalen, du blir regelhållare |
| `/mao delta` | spelare | Gå med |
| `/mao starta [kort]` | regelhållare | Delar ut (standard 5 kort) och slumpar ordningen |
| `/mao hand` | spelare | Visar din hand privat (eller tryck **Visa min hand**) |
| `/straff spelare [anledning] [egen_text] [ta_tillbaka]` | regelhållare | Straffkort med vag anledning |
| `/mao effekt valor effekt` | regelhållare | Hemlig effekt: nästa i cirkeln drar 1 eller 2 kort. Syns bara för dig |
| `/mao regelhallare spelare` | regelhållare | Lägg till/ta bort regelhållare |
| `/mao slut` | regelhållare | Avsluta spelet |

## 1. Skapa boten
1. Gå till https://discord.com/developers/applications och skapa en ny application.
2. Under **Bot**: kopiera token (Reset Token). Inga *privileged intents* behövs.
3. Under **OAuth2 → URL Generator**: välj scopes `bot` och `applications.commands`,
   behörigheter **View Channels**, **Send Messages**, **Attach Files**, **Embed Links**.
4. Öppna länken och lägg till boten på servern (kräver rätt att hantera servern, så be moderatorn).
5. Be moderatorn skapa en spelkanal där boten får skriva. Tips: slå på Developer Mode i
   Discord, högerklicka på servern och välj "Copy Server ID" till `GUILD_ID`.

## 2. Installera på Raspberry Pi
```bash
sudo apt update && sudo apt install -y python3-venv fonts-dejavu-core
cd ~ && mkdir mao-bot && cd mao-bot      # lägg filerna här
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp env.example .env && nano .env         # fyll i token (och GUILD_ID)
./venv/bin/python bot.py                 # testkör
```

## 3. Kör som tjänst (startar vid omstart)
Skapa `/etc/systemd/system/mao-bot.service` (byt `pi` mot ditt användarnamn):
```ini
[Unit]
Description=Mao Discord bot
After=network-online.target
Wants=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/mao-bot
ExecStart=/home/pi/mao-bot/venv/bin/python bot.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mao-bot
journalctl -u mao-bot -f                 # loggar
```

## Känt i prototypen
- Spelet ligger i minnet: startar du om boten försvinner pågående spel.
- Handvyn är ett privat (ephemeral) meddelande. Discord kan inte fästa sådana längst ner, och
  live-uppdateringen fungerar i ca 15 minuter efter ditt senaste klick. Därefter: **Visa min hand** igen.
- Dropdown-menyn rymmer 25 kort per sida; fler visas med ◀ ▶.
- Ett spel per kanal.
