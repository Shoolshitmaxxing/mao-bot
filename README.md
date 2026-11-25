[README.md](https://github.com/user-attachments/files/35435323/README.md)
# Mao-bot (prototyp)

Discord-bot för att spela Mao på distans, i text. Boten sköter korten, högen och straffkorten,
men **ni avgör själva vems tur det är**. Boten visar bara hur ni "sitter i cirkeln".

## Så spelar man
- `/mao ny` → alla skriver `/mao delta` → regelhållaren skriver `/mao starta` (5 kort var).
- Boten slumpar cirkelns ordning. Startmeddelandet visar bara cirkelordningen, därefter visar
  den översta kortet och ordningen med antal kort kvar.
- Högen börjar tom. En spelare lägger det första kortet från sin hand.
- När draghögen tar slut blandas 52 nya kort med korten under översta kortet. Översta kortet ligger kvar.
- **Föreslå nytt kort** startar en omröstning. Den som föreslår räknas automatiskt som ja-röst. Alla andra kan rösta ja eller nej; omröstningen avslutas när alla har röstat eller efter 20 sekunder. Fler ja än nej bland rösterna krävs. Vid godkänt förslag vänds ett kort från draghögen upp på högen. Om spelet ändras under omröstningen avbryts den.
- Tryck **Visa min hand** under bordsmeddelandet. Där väljer du kort i dropdown-menyn, eller
  trycker **Dra kort** / **Passa**. Handen uppdateras live när något händer (t.ex. straffkort).
- **Boten dömer aldrig själv.** Du kan lägga vilket kort som helst när som helst. Alla spelare
  kan bedöma om ett drag är fel.
- Under bordsmeddelandet har alla spelare knapparna **⚠️ Inte din tur**, **🗣️ Pratade**, **⚠️ Straff** och **Föreslå nytt kort**.
  **Pratade** låter dig välja spelare och ger ett straffkort utan att ta tillbaka kort.
  **Straff** låter dig välja spelare, skriva en egen anledning och välja om spelarens senast lagda kort ska tas tillbaka.
  Kortet kan bara tas tillbaka från den som lade det senast. **Inte din tur** gäller automatiskt senaste spelaren och tar tillbaka kortet.
  Du kan också döma eller straffa dig själv.
- En **Felbedömning**-knapp under bordsmeddelandet ångrar den senaste dömningen, inklusive kort och straffkort. Straffkortet som redan delats ut flyttas till den som dömde; inget extra kort dras. Även en felbedömning kan felbedömas. Knappen visas efter en vinst: den tar tillbaka vinnarens sista kort och ger spelaren ett straffkort. Om vinstinvändningen var felaktig kan den också felbedömas, vilket flyttar samma kort vidare.

## Kommandon
| Kommando | Vem | Vad |
|---|---|---|
| `/mao ny` | alla | Skapar ett spel i kanalen, du blir regelhållare |
| `/mao delta` | spelare | Gå med |
| `/mao starta [kort]` | regelhållare | Delar ut (standard 5 kort) och slumpar ordningen |
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

## Railway
- Sätt **Root Directory** till `mao-bot` om hela projektet ligger i repositoryt.
- Sätt miljövariabeln `DISCORD_TOKEN`.
- Starta som **Worker**. Railway-konfigurationsfilerna installerar DejaVu-fonten och kör `python bot.py`.
- Om bilderna saknas, kontrollera deploymentloggen efter fel med `PIL`, `Pillow`, `PNG` eller felmeddelandet
  `Ogiltig PNG-data`.

## Känt i prototypen
- Spelet ligger i minnet: startar du om boten försvinner pågående spel.
- `/mao starta` kan inte starta om en runda som redan pågår; avsluta först med `/mao slut`.
- Handvyn är ett privat (ephemeral) meddelande. Discord kan inte fästa sådana längst ner, och
  live-uppdateringen fungerar i ca 15 minuter efter ditt senaste klick. Därefter: **Visa min hand** igen.
- Spelhandlingar behandlas en i taget. Om ett annat drag hinner uppdatera spelet avvisas ett gammalt handklick och handen uppdateras; välj kortet igen.
- Dropdown-menyn rymmer 25 kort per sida; fler visas med ◀ ▶.
- Ett spel per kanal.
