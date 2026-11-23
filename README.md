# Mao-bot (prototyp)

Discord-bot för att spela Mao på distans, i text. Boten sköter kort, turordning och
de mekaniska straffen ("Inte din tur", "Fel kort"). Regelhållaren delar ut övriga
straffkort manuellt med `/straff`.

## Kommandon
| Kommando | Vem | Vad |
|---|---|---|
| `/mao ny` | alla | Skapar ett spel i kanalen, du blir regelhållare |
| `/mao delta` | spelare | Gå med |
| `/mao starta [kort]` | regelhållare | Delar ut (standard 7 kort) |
| `/mao hand` | spelare | Visar din hand privat (eller tryck **Visa min hand** på bordet) |
| `/straff spelare anledning [egen_text]` | regelhållare | Straffkort med vag anledning |
| `/mao effekt valor effekt` | regelhållare | Hemlig effekt (hoppa över / byt riktning / dra 2). Syns bara för dig |
| `/mao regelhallare spelare` | regelhållare | Lägg till/ta bort regelhållare |
| `/mao slut` | regelhållare | Avsluta spelet |

Man lägger kort via dropdown-menyn i den privata handvyn. **Dra kort** drar ett kort och
avslutar din tur. Matchar kortet varken färg eller valör, eller lägger man ur tur, får
man automatiskt straffkort. Allt annat ("Pratade", "Glömde något" osv.) dömer regelhållaren.

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
- Max en handsida är 25 kort i dropdown; fler visas med ◀ ▶.
- Ett spel per kanal.
